"""F06 回答缓存（server/generate/cache.py）。

缓存键（features/06 + data-contract + RAGv5 审核 C3）：
rewritten_question + 会话历史摘要 + 筛选条件 + 数据版本 + 模型版本 + **文本检索模式**。
文本模式必须进键：同题在 keyword / vector / hybrid 下证据不同，答案不能跨模式复用。
缓存 payload 除 answer/citations 外还保存 panel，供 SSE 缓存命中时完整回放：
session_start → status(entity_linking) → entities → status(cache_hit) →
answer → citations → panel → done。
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Optional

from contracts.sse import FinishReason

_CACHE_TTL_DEFAULT = 3600


def _normalize_history(history: list | None) -> list[dict]:
    """历史规范化：只保留 role/content 两个契约字段，去掉空白差异带来的伪差异。"""
    out: list[dict] = []
    for turn in history or []:
        d = turn.to_dict() if hasattr(turn, "to_dict") else dict(turn or {})
        out.append({"role": str(d.get("role") or ""),
                    "content": str(d.get("content") or "")})
    return out


def _normalize_entities(entities: list | None) -> list[dict]:
    """实体规范化：保留 id / 标准名 / 类型 / 朝代，按 (id, 标准名) 排序保证顺序无关。"""
    out: list[dict] = []
    for e in entities or []:
        d = e.to_dict() if hasattr(e, "to_dict") else dict(e or {})
        out.append({
            "entity_id": d.get("entity_id") or "",
            "standard_name": d.get("standard_name") or d.get("name") or "",
            "type": d.get("type") or "",
            "dynasty": d.get("dynasty") or "",
        })
    out.sort(key=lambda x: (x["entity_id"], x["standard_name"], x["type"]))
    return out


def _normalize_corrections(corrections: list | None) -> list[dict]:
    """纠正指令规范化：顺序敏感（后端按顺序执行），字段固定。"""
    out: list[dict] = []
    for c in corrections or []:
        d = c.to_dict() if hasattr(c, "to_dict") else dict(c or {})
        action = d.get("action")
        action = action.value if hasattr(action, "value") else action
        out.append({
            "action": action or "",
            # 源/目标两个 ID 都要进键（工作单 P1-3）：同名不同朝代时，
            # 只有目标 ID 能区分"改成哪一个"，只按名字做键会命中旧答案。
            "source_entity_id": d.get("source_entity_id") or "",
            "replacement_entity_id": d.get("replacement_entity_id") or "",
            "entity_id": d.get("entity_id") or "",
            "entity_type": d.get("entity_type") or "",
            "name": d.get("name") or "",
            "original": d.get("original") or "",
            "replacement": d.get("replacement") or "",
        })
    return out


def cache_key(rewritten: str, history: list | None, filters: dict | None,
              source_version: str, model: str, text_mode: str = "keyword",
              entities: list | None = None,
              corrections: list | None = None,
              dynasty_bias: list | None = None) -> str:
    """回答缓存键。

    第四轮复核 P1-1 修正两处：
    - **纠正实体进键**：`add` 类纠正不一定改变 rewritten question，但会改变实体、
      图谱与证据；旧键让"纠正前"和"纠正后"命中同一条缓存，用户看到纠正没生效。
    - **历史不再截尾**：旧实现只取 JSON 尾部 400 字符，两份历史只要尾部相同就碰撞；
      现在对裁剪后的完整历史做 SHA-256（调用方已按 history_max_turns 裁剪）。
    """
    payload = {
        "question": rewritten or "",
        "history": _normalize_history(history),
        "filters": filters or {},
        "dynasty_bias": sorted(str(b) for b in (dynasty_bias or []) if b),
        "entities": _normalize_entities(entities),
        "corrections": _normalize_corrections(corrections),
        "source_version": source_version or "",
        "model": model or "",
        "text_mode": (text_mode or "keyword").lower(),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class AnswerCache:
    """进程内 TTL 缓存（演示阶段足够；多实例部署再换 redis）。

    容量与清扫（2026-09-15 审核 P1-4）：旧实现只在"命中同一个 key"时才检查过期，
    不同 key 写入多少就留多少，公开接口长期运行会被慢速刷爆内存。现在：
    - 每次 put 前按"每 N 次或超限时"触发一次全表过期清扫；
    - 清扫后仍超出 max_entries 时按写入时间淘汰最旧条目（LRU 语义近似：写入序 + 命中不刷新）。
    """

    # 每多少次写入做一次全表清扫（避免每次 put 都 O(n) 扫描）
    _SWEEP_EVERY = 64

    def __init__(self, cache_dir: Path, ttl: int = _CACHE_TTL_DEFAULT,
                 max_entries: int = 2048):
        self.ttl = ttl
        self.max_entries = max(1, int(max_entries))
        self._store: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()
        self._puts_since_sweep = 0
        self.evictions = 0

    def get(self, key: str) -> Optional[dict]:
        with self._lock:
            item = self._store.get(key)
            if not item:
                return None
            ts, payload = item
            if time.time() - ts > self.ttl:
                self._store.pop(key, None)
                return None
            return payload

    def put(self, key: str, payload: dict) -> None:
        now = time.time()
        with self._lock:
            self._store[key] = (now, payload)
            self._puts_since_sweep += 1
            if self._puts_since_sweep >= self._SWEEP_EVERY:
                self._sweep_locked(now)
            if len(self._store) > self.max_entries:
                self._sweep_locked(now)
                # 仍超限：按写入时间淘汰最旧（dict 保序，弹出头部即可）
                overflow = len(self._store) - self.max_entries
                for old_key in list(self._store.keys())[:overflow]:
                    self._store.pop(old_key, None)
                    self.evictions += 1

    def _sweep_locked(self, now: float) -> None:
        """调用方必须已持锁：清掉所有过期条目。"""
        self._puts_since_sweep = 0
        expired = [k for k, (ts, _) in self._store.items() if now - ts > self.ttl]
        for k in expired:
            self._store.pop(k, None)

    def stats(self) -> dict:
        with self._lock:
            return {"entries": len(self._store), "max_entries": self.max_entries,
                    "evictions": self.evictions, "ttl_seconds": self.ttl}

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


def build_cache_payload(answer_text: str, citations: list, conflicts: list,
                        finish_reason: FinishReason, model_used: str,
                        panel: Optional[dict] = None) -> dict:
    """构造统一缓存 payload；panel 可选（旧缓存无 panel 时回放可降级跳过）。"""
    payload = {
        "answer": answer_text,
        "citations": citations,
        "conflicts": conflicts,
        "finish_reason": finish_reason.value if hasattr(finish_reason, "value") else finish_reason,
        "model_used": model_used,
        "cached_at": time.time(),
    }
    if panel is not None:
        payload["panel"] = panel
    return payload
