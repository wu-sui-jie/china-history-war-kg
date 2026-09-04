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


def load_searcher(index_dir: Path, source_version: str, top_k: int = 30) -> TextSearcher:
    return TextSearcher(index_dir=index_dir, source_version=source_version, top_k=top_k)


def search(
    searcher: TextSearcher,
    query: str,
    filters: Optional[dict] = None,
    mode: str = "keyword",
    top_k: Optional[int] = None,
) -> TextResult:
    """关键词/向量/混合检索 → 统一 Evidence 列表。

    filters: {"dynasty": [...], "event_type": [...], "chunk_type": [...]}（空数组不过滤）。
    当前向量不可用；若 F11 已构建向量且校验通过，vector/hybrid 需在 searcher 内补充实现。
    """
    eff_mode = resolve_mode(mode, searcher.vector_available)
    limit = top_k or searcher.top_k
    results: list[dict] = []
    if eff_mode in ("keyword", "hybrid"):
        results = searcher.search_keyword(query, limit=limit, metadata_filter=filters)
    elif eff_mode == "vector":
        # 向量检索实现接入点：当 vector_available=True 时在此补余弦 top-k
        results = []

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
    )
