"""评测运行器：在进程内按检索配置复跑问答链（evaluation/chain.py）。

与 server/sse.py 的编排口径保持一致（同一批底层函数、同一顺序、同一拒答硬规则），
差异点：
- 不查询/不写入回答缓存（评测要对比多种检索配置，缓存键不区分检索通道，
  跨配置共享缓存会污染对比结论）；
- 检索通道可按 EvalConfig 开关与取词模式替换（双通道 / 单文本 / 关键词 AND / OR）；
- 输出结构化 trace（JSON），保存每问每配置下的实体、图谱/文本证据、融合、回答与
  客观指标，供 report 汇总与人工评分复核。

跑法：python -m evaluation.cli run ...（从 RAG/ 根目录）
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from contracts.question import QuestionType
from contracts.request import Filters
from server.runtime import Runtime


@dataclass(frozen=True)
class EvalConfig:
    """一次评测的检索配置。"""

    label: str                       # 配置标识（写入 trace / 对比表）
    use_graph: bool = True           # False = 单文本通道（纯文本 RAG 对比）
    keyword_mode: str = "and_or"     # and_or = 生产口径(AND优先OR兜底)；and/or 专项对比
    text_top_k: int = 30
    mode: str = "keyword"            # 文本检索模式：keyword / vector / hybrid（v5 对照用）
    fusion_limit: int = 0            # 0 = 取 runtime.settings.query_fusion_limit（与生产同源）
    graph_top_k: int = 0             # 0 = 取 runtime.settings.query_top_k_graph（与生产同源）
    note: str = ""

    @property
    def describe(self) -> str:
        parts = []
        parts.append("图谱+文本双通道" if self.use_graph else "单文本通道")
        if self.keyword_mode == "and":
            parts.append("关键词纯AND")
        elif self.keyword_mode == "or":
            parts.append("关键词纯OR")
        else:
            parts.append("关键词AND优先/OR兜底")
        if self.mode != "keyword":
            parts.append(f"文本模式={self.mode}")
        if self.note:
            parts.append(self.note)
        return " / ".join(parts)


# 预设配置：dual 为生产口径基线；其余用于专项/对比（RAGv5 T6 新增 vector/hybrid）
CONFIG_DEFAULT = EvalConfig(label="dual")
CONFIG_TEXT_ONLY = EvalConfig(label="text-only", use_graph=False)
CONFIG_TEXT_ONLY_AND = EvalConfig(label="text-only-and", use_graph=False, keyword_mode="and")
CONFIG_TEXT_ONLY_OR = EvalConfig(label="text-only-or", use_graph=False, keyword_mode="or")
CONFIG_VECTOR = EvalConfig(label="vector", use_graph=False, mode="vector",
                           note="向量通道（Chroma 余弦）")
CONFIG_HYBRID = EvalConfig(label="hybrid", use_graph=False, mode="hybrid",
                           note="关键词+向量融合（策略见 TEXT_HYBRID_STRATEGY）")

CONFIG_PRESETS: dict[str, EvalConfig] = {
    c.label: c for c in
    (CONFIG_DEFAULT, CONFIG_TEXT_ONLY, CONFIG_TEXT_ONLY_AND, CONFIG_TEXT_ONLY_OR,
     CONFIG_VECTOR, CONFIG_HYBRID)
}


def _to_filters(flt: Optional[dict]) -> Filters:
    flt = flt or {}
    return Filters(
        dynasty=list(flt.get("dynasty") or []),
        event_type=list(flt.get("event_type") or []),
    )


def _refusal_text_rule2(question: str, text_evidence: list) -> bool:
    """与 sse.py 第二条拒答硬规则同阈值：实体为空 + 文本证据与问题无共享词。"""
    from data.index import fts as _fts

    q_words = {w for w in _fts.tokenize(question) if len(w) >= 2}
    if not q_words:
        return False
    for ev in text_evidence:
        text = (ev.content or {}).get("text") or ""
        ev_words = {w for w in _fts.tokenize(text) if len(w) >= 2}
        if ev_words & q_words:
            return False
    return True


def _snip(text: str, limit: int = 300) -> str:
    text = text or ""
    return text[:limit] + ("…" if len(text) > limit else "")


def _text_ev_compact(ev, limit: int = 800) -> dict:
    """文本证据的压缩记录（保留全文最多 800 字供覆盖统计；报告只用片段）。"""
    c = ev.content or {}
    return {
        "evidence_id": ev.evidence_id,
        "kind": ev.kind.value if hasattr(ev.kind, "value") else str(ev.kind),
        "doc_id": c.get("doc_id", ""),
        "chunk_type": c.get("chunk_type", ""),
        "score": round(ev.score, 4) if ev.score is not None else None,
        "text": (c.get("text") or "")[:limit],
    }


def _graph_ev_compact(ev) -> dict:
    c = ev.content or {}
    return {
        "evidence_id": ev.evidence_id,
        "relation": c.get("relation", ""),
        "subject": c.get("subject", ""),
        "object": c.get("object", ""),
        "subject_type": c.get("subject_type", ""),
        "object_type": c.get("object_type", ""),
        "score": round(ev.score, 4) if ev.score is not None else None,
    }


async def run_question(runtime: Runtime, cfg: EvalConfig,
                       question: str,
                       filters: Optional[dict] = None,
                       expected_names: Optional[list[str]] = None) -> dict:
    """复跑一次问答链并返回结构化 trace。

    事件顺序与 sse.run_query 的非缓存路径一致：
    understand → graph_search/text_search → fusion → （拒答判定）→ generate。
    """
    from server import graph as gmod
    from server.graph import search as gsearch
    from server.text import search as tsearch

    expected_names = expected_names or []
    stage_ms: dict[str, int] = {}
    out_filters = _to_filters(filters)

    # ---- F02（与 sse 同口径：空历史、无纠正；filters 显式传入）----
    t0 = time.time()
    out = runtime.question.understand(
        question, history=[], corrected=[], filters=out_filters,
    )
    stage_ms["understand"] = int((time.time() - t0) * 1000)

    names = [e.standard_name or e.name
             for e in out.entities if e.standard_name or e.name]
    qtype: QuestionType = out.question_type or QuestionType.UNKNOWN
    flt = out.filters.to_dict() if out.filters else None
    bias = list(getattr(out, "dynasty_bias", []) or [])

    entities_trace = [
        {"name": e.name, "standard_name": e.standard_name,
         "type": e.type, "entity_id": e.entity_id, "dynasty": e.dynasty}
        for e in out.entities
    ]
    # ---- F03 + F04（通道开关由 cfg 控制）----
    graph_trace: dict = {
        "enabled": cfg.use_graph, "hit_entities": [], "evidence": [],
        "related_event_ids": [], "n": 0,
    }
    text_trace: dict = {"enabled": True, "mode": "", "evidence": [], "n": 0}

    graph_result = None
    text_result = None

    if cfg.use_graph:
        t0 = time.time()
        graph_result = gsearch(runtime.graph, names, qtype,
                               filters=flt,
                               top_k=(cfg.graph_top_k
                                      or runtime.settings.query_top_k_graph),
                               dynasty_bias=bias)
        stage_ms["graph"] = int((time.time() - t0) * 1000)
        graph_trace = {
            "enabled": True,
            "hit_entities": [
                {"entity_id": h.get("entity_id"), "name": h.get("name"),
                 "type": h.get("type")} for h in (graph_result.hit_entities or [])
            ],
            "evidence": [_graph_ev_compact(ev) for ev in (graph_result.evidence or [])],
            "related_event_ids": graph_result.related_event_ids or [],
            "n": len(graph_result.evidence or []),
        }

    t0 = time.time()
    text_result = tsearch(
        runtime.text, out.rewritten_question or question, filters=flt,
        mode=cfg.mode, top_k=cfg.text_top_k, keyword_mode=cfg.keyword_mode,
        dynasty_bias=bias,
        hybrid_strategy=runtime.settings.text_hybrid_strategy,
        hybrid_keyword_weight=runtime.settings.text_hybrid_keyword_weight,
    )
    stage_ms["text"] = int((time.time() - t0) * 1000)
    text_trace = {
        "enabled": True,
        "mode": text_result.mode,
        "evidence": [_text_ev_compact(ev) for ev in (text_result.evidence or [])],
        "n": len(text_result.evidence or []),
    }

    # ---- F05 融合（唯一装配方 FusionService.assemble）----
    t0 = time.time()
    if graph_result is None:
        graph_result = gmod.GraphResult()
    fused, panel = runtime.fusion.assemble(
        graph_result, text_result, qtype,
        limit=cfg.fusion_limit or runtime.settings.query_fusion_limit,
    )
    stage_ms["fusion"] = int((time.time() - t0) * 1000)

    fused_compact: list[dict] = []
    for ev in fused.evidence:
        kind_val = ev.kind.value if hasattr(ev.kind, "value") else str(ev.kind)
        if kind_val == "graph_triple":
            fused_compact.append(_graph_ev_compact(ev))
        else:
            item = _text_ev_compact(ev, limit=800)
            item["citation_index"] = ev.citation_index
            fused_compact.append(item)
    fused_trace = {
        "n": len(fused.evidence),
        "by_kind": _count_by_kind(fused.evidence),
        "evidence": fused_compact,
        "conflicts": [c.to_dict() for c in (fused.conflicts or [])],
    }

    # ---- 拒答硬规则（与 sse.py 相同的两条）----
    refusal: Optional[dict] = None
    finish_reason = "normal"
    model_used = ""
    answer_text = ""
    if not fused.evidence:
        reason = f"知识库未检索到与「{question}」相关的史料，无法给出有依据的回答。"
        refusal = {"rule": "no_evidence", "text": reason}
    elif not names and text_result is not None and text_result.evidence:
        # 与 sse.py 同步：向量/hybrid 下按"无共享词 + 最高分低于阈值"才拒答，
        # 避免把语义命中但词面不重合的证据误判为不相关（RAGv5 §四.2）
        _mode = (text_result.mode or "keyword").lower()
        _top = max((e.score or 0.0) for e in text_result.evidence)
        _weak = _top < runtime.settings.vector_refusal_min_score
        if _refusal_text_rule2(question, text_result.evidence) and (_mode == "keyword" or _weak):
            reason = (f"「{question}」未识别出知识库实体，且检索到的文本与问题不相关，"
                      "当前无法给出有依据的回答。建议换个说法或补充具体事件/人物名。")
            refusal = {"rule": "no_shared_word", "text": reason}

    # 与 sse.py 同步的第三条规则：领域外谓词（RAGv5 §4.6）
    if refusal is None:
        from server.generate import refusal as _refusal_mod

        _scope = _refusal_mod.out_of_scope_reason(question, fused.evidence)
        if _scope:
            refusal = {"rule": "out_of_scope_predicate", "text": _scope}

    # ---- F06 生成（无缓存；无 key 时离线摘要回答器保证确定性）----
    t0 = time.time()
    if refusal is None:
        finish_reason, model_used, answer_text = await runtime.generate.generate(
            question, out.rewritten_question, fused.evidence,
            history=[], filters=flt, on_delta=None,
        )
    else:
        answer_text = refusal["text"]
        finish_reason = "refused"
    stage_ms["generate"] = int((time.time() - t0) * 1000)

    citations = runtime.generate.build_citations(fused.evidence)

    # ---- 客观指标（详见 evaluation/metrics.py）----
    from evaluation.metrics import compute_metrics

    auto = compute_metrics(
        expected_names=expected_names,
        entity_objs=[e for e in out.entities],
        graph_evidence=list(graph_result.evidence or []),
        hit_entity_names=[h.get("name") for h in (graph_result.hit_entities or [])],
        text_evidence=list(text_result.evidence or []),
        fused_evidence=list(fused.evidence or []),
        citations=citations,
        answer_text=answer_text,
        answerable=None,      # verdict 在 report 层结合 answerable 判定
        refusal=refusal,
    )

    return {
        "understand": {
            "question_type": out.question_type.value if out.question_type else "unknown",
            "rewritten_question": out.rewritten_question,
            "filters": flt,
            "dynasty_bias": bias,
            "entities": entities_trace,
            "candidates_n": len(out.candidates),
            "llm_entity_used": bool(getattr(out, "llm_entity_used", False)),
            "dynasty_disambiguated": bool(getattr(out, "dynasty_disambiguated", False)),
        },
        "graph": graph_trace,
        "text": text_trace,
        "fused": fused_trace,
        "refusal": refusal,
        "answer": {
            "finish_reason": finish_reason,
            "model_used": model_used,
            "text": answer_text,
        },
        "citations": citations,
        "stage_ms": stage_ms,
        "auto": auto,
    }


def _count_by_kind(evidence_list) -> dict:
    from collections import Counter

    c: Counter = Counter()
    for ev in evidence_list:
        kind = ev.kind.value if hasattr(ev.kind, "value") else str(ev.kind)
        c[kind] += 1
    return dict(c)


# ---- 批量运行 ----
async def run_batch(runtime: Runtime, items, configs: list[EvalConfig],
                    progress=None):
    """对 (题库条 × 配置) 逐条运行，返回 {config_label: {question_id: trace}}。"""
    results: dict[str, dict] = {}
    for cfg in configs:
        results.setdefault(cfg.label, {})
    total = len(items) * len(configs)
    done = 0
    for item in items:
        for cfg in configs:
            trace = await run_question(
                runtime, cfg, item.question,
                filters=item.filters, expected_names=item.expected_entities,
            )
            trace["config"] = cfg.label
            trace["suite"] = item.suite
            results[cfg.label][item.id] = trace
            done += 1
            if progress:
                progress(done, total, item.id, cfg.label)
    return results
