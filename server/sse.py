"""SSE 事件序列化与查询编排（server/sse.py）。

`run_query(runtime, req)` 是纯异步生成器：逐步产出 SSE 事件字符串，
由 api.py 用 StreamingResponse 输出。这样编排逻辑不依赖 web 框架，便于测试。

事件顺序严格按 data-contract.md：
session_start → status(entity_linking) → entities →
  命中：status(cache_hit) → answer → citations → panel → done；
  未命中：status(graph_search) → graph_results → status(text_search) → text_results →
    status(fusion) → fusion → status(generating) → answer 增量 → citations → panel → done
"""

from __future__ import annotations

import json
import time
from typing import AsyncIterator

from config.settings import Settings
from contracts.request import QueryRequest
from contracts.sse import (
    ErrorCode,
    FinishReason,
    SSEEvent,
    SSEEventType,
    StatusStage,
)
from contracts.question import QuestionType
from server.runtime import Runtime

# query 级错误（不可恢复，直接 error+done）
_RECOVERABLE = set()


def _event(type_: SSEEventType, session_id: str, stage=None, data=None) -> dict:
    ev = SSEEvent(type=type_, session_id=session_id, stage=stage, data=data)
    return ev.to_dict()


def sse_format(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def _chunk_answer_stream(text: str, chunk_chars: int = 160):
    """把完整答案切成小段，模拟 SSE 增量（真实 LLM 接入后按 token 事件替换）。

    切分优先找句子边界（。！？换行），避免把一句劈成两半产生生硬断句。
    """
    if not text:
        return
    import re
    # 按 句子分隔符 + 换行 保留标点切
    parts = re.split(r"(?<=[。！？!?；;])\s*|\n+", text)
    buf = ""
    for p in parts:
        if not p:
            continue
        buf += p
        if len(buf) >= chunk_chars or p[-1] in "。！？!?；;":
            yield buf
            buf = ""
    if buf:
        yield buf


def _text_shares_any(evidence_list, q_words: set) -> bool:
    """判断任一文本证据与查询词是否有 ≥1 个共享词（≥2 字）。"""
    from data.index import fts as _fts
    for ev in evidence_list:
        text = (ev.content or {}).get("text") or ""
        ev_words = {w for w in _fts.tokenize(text) if len(w) >= 2}
        if ev_words & q_words:
            return True
    return False


async def run_query(runtime: Runtime, req: QueryRequest) -> AsyncIterator[str]:
    """编排一次完整查询，产出 SSE 帧序列。"""
    sid = req.session_id
    gen = runtime.generate
    cache_dir = runtime.settings.cache_dir
    settings = runtime.settings

    # ---- session_start ----
    yield sse_format(_event(SSEEventType.SESSION_START, sid,
                            stage=StatusStage.START.value))
    try:
        # ---- F02：问题理解 ----
        yield sse_format(_event(SSEEventType.STATUS, sid,
                                stage=StatusStage.ENTITY_LINKING.value))
        t0 = time.time()
        # 历史统一口径：F02 理解、缓存键、F06 提示词共用同一份裁剪后历史
        # （history_max_turns 个 user 轮及其后助手消息），避免三个窗口不一致
        # 造成“实际上下文相同却互不命中缓存”的效率损耗。
        hist = runtime.question.trim_history(req.history)
        out = runtime.question.understand(
            req.question, history=hist,
            corrected=req.corrected_entities,
            filters=req.filters,
        )
        yield sse_format(_event(
            SSEEventType.ENTITIES, sid, stage=StatusStage.ENTITY_LINKING.value,
            data={"entities": [e.to_dict() for e in out.entities],
                  "candidates": [c.to_dict() for c in out.candidates],
                  "question_type": out.question_type.value if out.question_type else "unknown",
                  "rewritten_question": out.rewritten_question,
                  "dynasty_bias": list(out.dynasty_bias or []),
                  "elapsed_ms": int((time.time() - t0) * 1000)}))

        # 缓存检查（在检索前查，命中则回放）
        # 缓存键需包含王朝偏置：它会影响图谱/文本排序结果
        cache_filters = dict(out.filters.to_dict() if out.filters else {})
        if out.dynasty_bias:
            cache_filters["dynasty_bias"] = list(out.dynasty_bias)
        cache_key = gen.check_cache(out.rewritten_question, hist,
                                    cache_filters or None)
        cached = cache_key[0]

        if cached:
            yield sse_format(_event(SSEEventType.STATUS, sid,
                                    stage=StatusStage.CACHE_HIT.value))
            yield sse_format(_event(SSEEventType.ANSWER, sid,
                                    stage=StatusStage.CACHE_HIT.value,
                                    data={"delta": cached["answer"]}))
            yield sse_format(_event(SSEEventType.CITATIONS, sid,
                                    stage=StatusStage.CACHE_HIT.value,
                                    data={"citations": cached["citations"],
                                          "conflicts": cached["conflicts"]}))
            if cached.get("panel") is not None:
                yield sse_format(_event(SSEEventType.PANEL, sid,
                                        stage=StatusStage.CACHE_HIT.value,
                                        data=cached["panel"]))
            yield sse_format(_event(
                SSEEventType.DONE, sid,
                data={"finish_reason": cached["finish_reason"],
                      "model_used": cached.get("model_used", ""),
                      "cache_hit": True}))
            return

        # ---- F03 + F04：图谱/文本（事件顺序 graph_search 先于 text_search）----
        names = [e.standard_name or e.name for e in out.entities if e.standard_name or e.name]
        qtype: QuestionType = out.question_type or QuestionType.UNKNOWN
        filters = out.filters.to_dict() if out.filters else None
        bias = list(out.dynasty_bias or [])

        graph_result = None
        text_result = None

        from server.graph import search as gsearch
        from server.text import search as tsearch

        yield sse_format(_event(SSEEventType.STATUS, sid,
                                stage=StatusStage.GRAPH_SEARCH.value))
        graph_result = gsearch(runtime.graph, names, qtype, filters=filters,
                               top_k=settings.query_top_k_graph,
                               dynasty_bias=bias)

        yield sse_format(_event(SSEEventType.STATUS, sid,
                                stage=StatusStage.TEXT_SEARCH.value))
        text_result = tsearch(runtime.text, out.rewritten_question or req.question,
                              filters=filters, mode="keyword",
                              top_k=settings.query_top_k_text,
                              dynasty_bias=bias)

        # 推送检索结果事件
        yield sse_format(_event(
            SSEEventType.GRAPH_RESULTS, sid, stage=StatusStage.GRAPH_SEARCH.value,
            data={"evidence": [e.to_dict() for e in (graph_result.evidence if graph_result else [])],
                  "hit_entities": [h for h in (graph_result.hit_entities if graph_result else [])]}))

        yield sse_format(_event(
            SSEEventType.TEXT_RESULTS, sid, stage=StatusStage.TEXT_SEARCH.value,
            data={"evidence": [e.to_dict() for e in (text_result.evidence if text_result else [])],
                  "mode": (text_result.mode if text_result else "none")}))

        # ---- F05 融合 ----
        yield sse_format(_event(SSEEventType.STATUS, sid,
                                stage=StatusStage.FUSION.value))
        fused, panel = runtime.fusion.assemble(graph_result, text_result, qtype)

        yield sse_format(_event(
            SSEEventType.FUSION, sid, stage=StatusStage.FUSION.value,
            data={
                "evidence_count": len(fused.evidence),
                "conflicts": [c.to_dict() for c in fused.conflicts],
                "citation_index": [a.to_dict() for a in fused.citation_index],
            }))

        # ---- 拒答硬规则 ----
        # 1) 完全无证据 → 直接拒答
        if not fused.evidence:
            reason = f"知识库未检索到与「{req.question}」相关的史料，无法给出有依据的回答。"
            yield sse_format(_event(SSEEventType.ANSWER, sid,
                                    stage=StatusStage.GENERATING.value,
                                    data={"delta": reason}))
            yield sse_format(_event(
                SSEEventType.DONE, sid,
                data={"finish_reason": FinishReason.REFUSED.value, "model_used": ""}))
            return

        # 2) 实体为空 + 文本证据与问题无共享词 → 依据不足（拒绝给噪音回答）
        #    词法层面判断"证据与问题不相关"，避免 OR 兜底召回完全无关片段。
        #    阈值保守：query 与任一证据 text 共享 ≥1 个长度≥2 的关键词即放行。
        if not names and text_result is not None and text_result.evidence:
            from data.index import fts as _fts
            q_words = {w for w in _fts.tokenize(req.question) if len(w) >= 2}
            if q_words and not _text_shares_any(text_result.evidence, q_words):
                reason = (f"「{req.question}」未识别出知识库实体，且检索到的文本与问题不相关，"
                          "当前无法给出有依据的回答。建议换个说法或补充具体事件/人物名。")
                yield sse_format(_event(SSEEventType.ANSWER, sid,
                                        stage=StatusStage.GENERATING.value,
                                        data={"delta": reason}))
                yield sse_format(_event(
                    SSEEventType.DONE, sid,
                    data={"finish_reason": FinishReason.REFUSED.value, "model_used": ""}))
                return

        # ---- F06 生成 ----
        yield sse_format(_event(SSEEventType.STATUS, sid,
                                stage=StatusStage.GENERATING.value))

        # 缓存 key 计算 & 写入
        finish_reason, model_used, full_answer = await gen.generate(
            req.question, out.rewritten_question, fused.evidence,
            history=hist,
            filters=out.filters.to_dict() if out.filters else None,
            on_delta=lambda _: None,   # 见下：真实 answer 增量用统一发射
        )

        # 缓存 payload（含 citations + panel，供命中时完整回放）
        from server.generate.cache import build_cache_payload
        citations = gen.build_citations(fused.evidence)
        payload = build_cache_payload(
            full_answer,
            [c for c in citations],
            [c.to_dict() for c in fused.conflicts],
            finish_reason,
            model_used,
            panel=panel.to_dict() if hasattr(panel, "to_dict") else panel,
        )
        if cache_key[1]:
            gen.cache.put(cache_key[1], payload)

        # 逐句增量发射 answer（模拟流式：真实 LLM 接入后按 token 切）
        # 先发 status(generating)，再按 句子/换行 拆段推送
        for part in _chunk_answer_stream(full_answer):
            yield sse_format(_event(SSEEventType.ANSWER, sid,
                                    stage=StatusStage.GENERATING.value,
                                    data={"delta": part}))

        # citations + panel
        yield sse_format(_event(SSEEventType.CITATIONS, sid,
                                stage=StatusStage.GENERATING.value,
                                data={"citations": citations,
                                      "conflicts": [c.to_dict() for c in fused.conflicts]}))
        yield sse_format(_event(SSEEventType.PANEL, sid,
                                stage=StatusStage.GENERATING.value,
                                data=panel.to_dict() if hasattr(panel, "to_dict") else panel))
        yield sse_format(_event(
            SSEEventType.DONE, sid,
            data={"finish_reason": finish_reason, "model_used": model_used}))

    except Exception as e:  # noqa: BLE001
        # 内部错误：error 事件 + done（同时打印 traceback 到服务日志便于排查）
        import traceback
        traceback.print_exc()
        yield sse_format(_event(SSEEventType.ERROR, sid,
                                data={"error_code": ErrorCode.INTERNAL.value,
                                      "message": f"内部错误: {e}"}))
        yield sse_format(_event(
            SSEEventType.DONE, sid,
            data={"finish_reason": FinishReason.CANCELLED.value, "model_used": ""}))
