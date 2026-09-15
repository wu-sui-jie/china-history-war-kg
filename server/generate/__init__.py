"""F06 回答生成对外入口（server/generate/__init__.py）。

生成器分三层：
1. LLM 可用（deepseek/deepseek-v4.1-flash）→ 流式真实回答，失败重试→备用模型。
2. LLM 不可用（无密钥）→ 启发式摘要回答器（把融合证据组织为可读回答，
   带引用编号），保证离线端到端验收与演示链路完整；model_used='heuristic-offline'。
3. 无证据 → 拒答路径（finish_reason=refused）；模型自判证据不足（自拒文本，
   见 refusal.detect_model_refusal）同样返回 refused。

回答缓存命中时不再调用模型，直接回放 answer/citations/panel。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from config.settings import Settings
from contracts.evidence import Evidence
from contracts.sse import FinishReason, SSECitation
from server.generate import prompts as prompts_mod
from server.generate import refusal as refusal_mod
from server.generate.cache import AnswerCache, cache_key
from server.generate.llm_client import LLMClient

# SSE 事件发射器：给编排层回调
_Emitter = object


class AnswerGenerator:
    def __init__(self, settings: Settings, cache_dir: Path, source_version: str):
        self.settings = settings
        self.source_version = source_version
        self.cache = AnswerCache(cache_dir, ttl=settings.cache_ttl_seconds)
        self.llm = LLMClient(settings)
        self._data_version = source_version
        # 最近一次生成的 token 用量（含 reasoning_tokens）；供评测/日志归因，不进 SSE 契约
        self.last_usage: Optional[dict] = None
        # 最近一次生成是否因 max_tokens 被截断（finish_reason=length）；示例题筛选用
        self.last_truncated: bool = False

    def check_cache(self, rewritten: str, history: list | None,
                    filters: dict | None):
        key = cache_key(rewritten, history, filters, self._data_version,
                        self.settings.llm_model, self.settings.text_mode)
        return self.cache.get(key), key

    # ---- 主流程：返回 (finish_reason, model_used, full_answer) ----
    async def generate(self, question: str, rewritten: str,
                       evidence: list[Evidence],
                       history: list | None = None,
                       filters: dict | None = None,
                       on_delta=None,
                       on_thinking=None) -> tuple[str, str, str]:
        """执行生成。on_delta(text) 收到回答增量（SSE answer 事件用）；
        on_thinking(text) 收到推理增量（SSE thinking 事件用；仅官方 endpoint 会流式输出思考内容）。
        """
        self.last_usage = None
        self.last_truncated = False
        # 拒答硬规则：无证据
        if not evidence:
            reason = refusal_mod.refusal_reply(question)
            if on_delta:
                on_delta(reason)
            return FinishReason.REFUSED.value, "", reason

        # 提示词泄露请求 → 固定回复（不调用模型）
        if prompts_mod.detect_leak_request(question):
            fixed = prompts_mod.refusal_fixed_reply()
            if on_delta:
                on_delta(fixed)
            return FinishReason.NORMAL.value, "", fixed

        messages, _ = prompts_mod.build_messages(
            question, rewritten, evidence, history=history)

        if self.llm.available:
            resp = await self.llm.stream_chat(
                messages, on_delta or (lambda _: None),
                on_thinking=on_thinking,
                max_tokens=self.settings.llm_max_tokens,
            )
            self.last_usage = resp.usage
            self.last_truncated = (resp.api_finish_reason == "length")
            if resp.error:
                # LLM 调用失败 → 降级启发式回答器
                # 2026-09-15 冒烟实践：此前降级原因不落日志，部署排障只能看到 degraded 结果，
                # 分不清是网络/密钥/额度问题；补一条 warning（含错误摘要）。
                logging.getLogger("rag.generate").warning(
                    "LLM 生成失败，降级离线摘要回答器：%s", str(resp.error)[:200])
                text = self._heuristic_answer(question, evidence)
                if on_delta:
                    on_delta(text)
                return FinishReason.DEGRADED.value, "heuristic-offline", text
            if not (resp.text or "").strip():
                # 推理模型把 max_tokens 全花在 reasoning 上会导致**正文为空**（v5 实测：
                # 28 题里有 2 题如此，而 finish_reason 仍是 length 不是 error）。
                # 空回答对演示是致命的 → 一律降级到离线摘要回答器兜底（degraded 不入缓存）。
                text = self._heuristic_answer(question, evidence)
                if on_delta:
                    on_delta(text)
                return FinishReason.DEGRADED.value, "heuristic-offline", text
            # 模型自拒识别（2026-09-15 全项目审核 P0-1）：命中固定拒答句式且无引用编号
            # → 按拒答路径返回，前端据此显示"依据不足"。拒答语义优先于降级标记：
            # 备用模型返回的自拒文本同样记 refused（refused 是确定性结果，可入缓存）。
            if refusal_mod.detect_model_refusal(resp.text):
                return FinishReason.REFUSED.value, resp.model_used, resp.text
            return (FinishReason.DEGRADED.value if resp.degraded else FinishReason.NORMAL.value,
                    resp.model_used, resp.text)
        # 无 LLM → 启发式离线回答器
        text = self._heuristic_answer(question, evidence)
        if on_delta:
            on_delta(text)
        return FinishReason.NORMAL.value, "heuristic-offline", text

    # ---- 启发式离线回答器（无密钥演示/降级用）----
    @staticmethod
    def _heuristic_answer(question: str, evidence: list[Evidence]) -> str:
        lines = [f"关于「{question}」，基于检索到的资料整理如下："]
        seen = set()
        for ev in evidence[:10]:
            idx = ev.citation_index
            c = ev.content or {}
            kind_val = ev.kind.value if hasattr(ev.kind, "value") else ev.kind
            if kind_val == "graph_triple":
                line = f"[{idx}] {c.get('subject')} 与 {c.get('object')} 的关系为「{c.get('relation')}」。"
            else:
                text = (c.get("text") or "").strip()
                # 摘要：取前 60 字
                snip = text[:60] + ("…" if len(text) > 60 else "")
                line = f"[{idx}] {snip}"
            if line not in seen:
                seen.add(line)
                lines.append(line)
        lines.append("（当前为离线摘要模式，接入 deepseek-v4-flash 后将生成完整作答。）")
        return "\n".join(lines)

    # ---- citations 组装 ----
    @staticmethod
    def build_citations(evidence: list[Evidence]) -> list[SSECitation]:
        out = []
        for ev in evidence:
            idx = ev.citation_index or 0
            c = ev.content or {}
            kind_val = ev.kind.value if hasattr(ev.kind, "value") else ev.kind
            title = {
                "graph_triple": f"{c.get('subject')}—{c.get('relation')}→{c.get('object')}",
                "event_card": c.get("event_name") or c.get("doc_id") or "事件卡片",
                "raw_text": c.get("doc_id") or "原文",
                "evidence": c.get("doc_id") or "关系证据",
            }.get(kind_val, kind_val)
            snippet = (c.get("text") or "")[:120]
            out.append(SSECitation(
                index=idx,
                evidence_id=ev.evidence_id,
                kind=kind_val,
                title=title,
                snippet=snippet,
            ).to_dict())
        return out
