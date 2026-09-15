"""F04 文本检索：模式选择 / 分数归一化 / 可用性探测。

三种模式：keyword（FTS5）、vector（云端向量，需 F11 已构建且校验通过）、hybrid。
当前基线：vector_available=False 时无论请求何种模式都回落 keyword。
"""

from __future__ import annotations

from enum import Enum


class TextMode(str, Enum):
    KEYWORD = "keyword"
    VECTOR = "vector"
    HYBRID = "hybrid"
    NONE = "none"


def resolve_mode(requested: str | None, vector_available: bool) -> str:
    """返回实际执行模式；请求 vector/hybrid 但向量不可用 → 自动降级 keyword。"""
    req = (requested or "keyword").lower()
    if req in ("vector", "hybrid") and not vector_available:
        return "keyword"
    if req not in ("keyword", "vector", "hybrid"):
        return "keyword"
    return req


# ---- hybrid 融合（RAGv5 §四.2）----
def _norm_dict(raw: dict) -> dict:
    """按值 min-max 归一化到 [0,1]；全相等时记 0.5（与 data-contract 的关键词/向量归一化同口径）。"""
    if not raw:
        return {}
    vals = list(raw.values())
    lo, hi = min(vals), max(vals)
    if hi <= lo:
        return {k: 0.5 for k in raw}
    return {k: (v - lo) / (hi - lo) for k, v in raw.items()}


def fuse_weighted(keyword: list, vector: list, keyword_weight: float = 0.5) -> dict:
    """线性加权：两通道各自已归一化到 [0,1]，加权后再次归一化。

    keyword / vector 均为 [(chunk_id, score), ...]（score 已在该通道内归一化）。
    只被一个通道命中的片段，另一通道记 0 分。
    """
    w = max(0.0, min(1.0, float(keyword_weight)))
    kw = dict(keyword)
    vec = dict(vector)
    raw = {cid: w * kw.get(cid, 0.0) + (1.0 - w) * vec.get(cid, 0.0)
           for cid in set(kw) | set(vec)}
    return _norm_dict(raw)


def fuse_rrf(keyword: list, vector: list, k: int = 60) -> dict:
    """倒数排名融合（RRF）：只看名次不看分数，天然规避 BM25 与余弦的量纲差异。"""
    raw: dict = {}
    for rank, (cid, _s) in enumerate(sorted(keyword, key=lambda x: -x[1]), start=1):
        raw[cid] = raw.get(cid, 0.0) + 1.0 / (k + rank)
    for rank, (cid, _s) in enumerate(sorted(vector, key=lambda x: -x[1]), start=1):
        raw[cid] = raw.get(cid, 0.0) + 1.0 / (k + rank)
    return _norm_dict(raw)


HYBRID_STRATEGIES = ("weighted", "rrf", "fallback")


def fuse_hybrid(keyword: list, vector: list, strategy: str = "weighted",
                keyword_weight: float = 0.5) -> dict:
    """按策略融合；未知策略回退 weighted（不静默产出空结果）。"""
    st = (strategy or "weighted").lower()
    if st == "rrf":
        return fuse_rrf(keyword, vector)
    return fuse_weighted(keyword, vector, keyword_weight)
