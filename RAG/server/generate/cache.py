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


def cache_key(rewritten: str, history: list | None, filters: dict | None,
              source_version: str, model: str, text_mode: str = "keyword") -> str:
    def _as_dicts(h):
        out = []
        for t in h or []:
            out.append(t.to_dict() if hasattr(t, "to_dict") else t)
        return out

    hist_sig = json.dumps(_as_dicts(history), ensure_ascii=False)[-400:]
    filt_sig = json.dumps(filters or {}, ensure_ascii=False, sort_keys=True)
    raw = f"{rewritten}|{hist_sig}|{filt_sig}|{source_version}|{model}|{(text_mode or 'keyword').lower()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


class AnswerCache:
    """进程内 TTL 缓存（演示阶段足够；多实例部署再换 redis）。"""

    def __init__(self, cache_dir: Path, ttl: int = _CACHE_TTL_DEFAULT):
        self.ttl = ttl
        self._store: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

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
        with self._lock:
            self._store[key] = (time.time(), payload)

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
