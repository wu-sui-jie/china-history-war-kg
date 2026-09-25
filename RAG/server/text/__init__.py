"""F04 文本检索对外入口。

load_searcher(index_dir, source_version) 在服务启动时调用一次，缓存在 app.state。
search(searcher, query, filters, mode, top_k) → TextResult。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from contracts.evidence import Confidence, Evidence, EvidenceKind, SourceType
from contracts.retrieval import TextResult, build_text_content
from server.text.scoring import resolve_mode
from server.text.searcher import TextSearcher

# chunk_type → evidence kind / source_type / confidence 映射（F04 统一装配）
_CHUNK_META = {
    "raw": (EvidenceKind.RAW_TEXT, SourceType.ORIGINAL_TEXT, Confidence.MEDIUM),
    "event_card": (EvidenceKind.EVENT_CARD, SourceType.EVENT_CARD_JSON, Confidence.HIGH),
    "evidence": (EvidenceKind.EVIDENCE, SourceType.RELATION_EVIDENCE, Confidence.MEDIUM),
}


def load_searcher(index_dir: Path, source_version: str, top_k: int = 30,
                  collection_name: str = "chunks_v1",
                  embed_fn=None,
                  query_max_words: Optional[int] = None,
                  query_and_words: Optional[int] = None,
                  query_or_words: Optional[int] = None,
                  and_min_hits: Optional[int] = None) -> TextSearcher:
    """构造检索器。embed_fn 用于查询侧向量化；为 None 时向量不可用（自动降级关键词）。

    query_* / and_min_hits 由调用方从 Settings（TEXT_QUERY_*）注入；
    不传时沿用 TextSearcher 里的默认值（原先硬编码的那组）。
    """
    kwargs = {}
    if query_max_words is not None:
        kwargs["query_max_words"] = query_max_words
    if query_and_words is not None:
        kwargs["query_and_words"] = query_and_words
    if query_or_words is not None:
        kwargs["query_or_words"] = query_or_words
    if and_min_hits is not None:
        kwargs["and_min_hits"] = and_min_hits
    return TextSearcher(index_dir=index_dir, source_version=source_version, top_k=top_k,
                        collection_name=collection_name, embed_fn=embed_fn, **kwargs)


def search(
    searcher: TextSearcher,
    query: str,
    filters: Optional[dict] = None,
    mode: str = "keyword",
    top_k: Optional[int] = None,
    keyword_mode: str = "and_or",
    dynasty_bias: Optional[list] = None,
    hybrid_strategy: str = "weighted",
    hybrid_keyword_weight: float = 0.5,
) -> TextResult:
    """关键词/向量/混合检索 → 统一 Evidence 列表。

    filters: {"dynasty": [...], "event_type": [...], "chunk_type": [...]}（空数组不过滤，硬过滤）。
    keyword_mode: and_or（生产口径）/ and / or（F10 评测拆分关键词策略用）。
    hybrid_strategy: weighted（线性加权，默认）/ rrf（倒数排名融合）/ fallback（仅关键词为空才用向量）。
    dynasty_bias: 问句自动识别的朝代，仅用于检索返回序（软偏置，不剔除结果）；
        最终文本证据顺序由 F05 决定，文本侧偏置不影响最终排序
        （图谱侧在 F03 策略前生效），见 docs/features/02-entity-linking.md。
    模式降级：请求 vector/hybrid 但向量不可用（Chroma 集合缺失/条数不一致/无密钥）→ keyword。
    """
    eff_mode = resolve_mode(mode, searcher.vector_available)
    requested_mode = (mode or "keyword").lower()
    degraded_reason = ""
    fallback_used = False
    limit = top_k or searcher.top_k
    results: list[dict] = []
    # 本次调用里向量通道报错了几次。用计数增量判断"这一问是否真的降级了"，
    # 而不是看 vector_available（启动声明）或 last_vector_error（粘性历史值）。
    vector_errors_before = searcher.vector_error_count

    if eff_mode == "keyword":
        if requested_mode in ("vector", "hybrid"):
            degraded_reason = (f"请求的 {requested_mode} 已降级关键词："
                               "向量制品或 embedding 客户端未就绪")
        results = searcher.search_keyword(query, limit=limit, metadata_filter=filters,
                                         keyword_mode=keyword_mode,
                                         dynasty_bias=dynasty_bias)
    elif eff_mode == "vector":
        results = searcher.search_vector(query, limit=limit, metadata_filter=filters,
                                         dynasty_bias=dynasty_bias)
        # 向量通道查询期失败（如向量化调用异常）→ 兜底关键词，避免"整条链路无证据"
        if not results:
            results = searcher.search_keyword(query, limit=limit, metadata_filter=filters,
                                              keyword_mode=keyword_mode,
                                              dynasty_bias=dynasty_bias)
            fallback_used = True
    elif eff_mode == "hybrid":
        results = searcher.search_hybrid(query, limit=limit, metadata_filter=filters,
                                        keyword_mode=keyword_mode,
                                        dynasty_bias=dynasty_bias,
                                        strategy=hybrid_strategy,
                                        keyword_weight=hybrid_keyword_weight)
        if not results:
            results = searcher.search_keyword(query, limit=limit, metadata_filter=filters,
                                              keyword_mode=keyword_mode,
                                              dynasty_bias=dynasty_bias)
            fallback_used = True

    # 实际执行的是关键词时，模式必须如实记成 keyword——**不能**只因为关键词兜底出了结果
    # 就继续报 vector/hybrid，那正是本次要消掉的假阳性（"健康检查说向量可用、实际走关键词"）。
    # 两种情况都算降级，但含义不同、都要留原因：
    #   1) 向量通道报错（错误计数增加）——链路故障，`last_vector_error` 是现场；
    #   2) 向量通道没报错但没返回结果，于是走了关键词兜底——本次结果确实来自关键词。
    if searcher.vector_error_count > vector_errors_before:
        eff_mode = "keyword"
        degraded_reason = searcher.last_vector_error or "向量通道报错，已降级关键词"
    elif fallback_used:
        eff_mode = "keyword"
        degraded_reason = degraded_reason or "向量通道未返回结果，已降级关键词"
    elif eff_mode == "keyword" and requested_mode in ("vector", "hybrid") and not degraded_reason:
        # 执行前就被 resolve_mode 降级（向量制品/客户端未就绪）
        degraded_reason = (f"请求的 {requested_mode} 已降级关键词："
                           "向量制品或 embedding 客户端未就绪")

    # 回写"实际执行了什么"：health 与排查看这里，而不是看"请求了什么"或"声明能用什么"。
    searcher.effective_text_mode = eff_mode

    evidence_list: list[Evidence] = []
    for i, r in enumerate(results):
        ctype = r.get("chunk_type") or "raw"
        kind, source_type, confidence = _CHUNK_META.get(
            ctype, (EvidenceKind.RAW_TEXT, SourceType.ORIGINAL_TEXT, Confidence.MEDIUM)
        )
        chunk_id = r["chunk_id"]
        content = build_text_content(r)
        rel_entities = []
        if r.get("event_name"):
            rel_entities.append(r["event_name"])
        ev = Evidence(
            evidence_id=f"text_{chunk_id}",
            kind=kind,
            source_type=source_type,
            source_version=searcher.source_version,
            confidence=confidence,
            content=content,
            related_entities=rel_entities,
            score=float(r.get("_score", 0.0)),
        )
        evidence_list.append(ev)

    return TextResult(
        evidence=evidence_list,
        mode=eff_mode,
        vector_available=searcher.vector_available,
        vector_degraded=bool(degraded_reason),
        vector_note=degraded_reason,
    )
