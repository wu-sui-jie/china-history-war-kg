"""hybrid 融合与向量分数换算的单元守护（RAGv5 T3）。

覆盖：
- 线性加权（weighted）与倒数排名融合（rrf）的口径；
- 归一化边界（max==min → 0.5，与 data-contract 一致）；
- 只被单通道命中的片段按 0 分参与加权。
"""

from __future__ import annotations

from server.text.scoring import fuse_hybrid, fuse_rrf, fuse_weighted


def test_fuse_weighted_basic_and_renormalize():
    """融合分在加权后**还要再归一化一次**（契约要求输出 ∈ [0,1]，F05 靠它排序）。"""
    kw = [("a", 1.0), ("b", 0.0)]
    vec = [("b", 1.0), ("c", 0.0)]
    out = fuse_weighted(kw, vec, keyword_weight=0.5)
    # 加权原始值：a = 0.5*1.0 + 0.5*0（向量未命中记 0）= 0.5；b = 0.5*0 + 0.5*1.0 = 0.5；c = 0.0
    # 再归一化（lo=0, hi=0.5）→ a = b = 1.0，c = 0.0
    assert out["a"] == 1.0 and out["b"] == 1.0
    assert out["c"] == 0.0


def test_fuse_weighted_single_hit_gets_zero_not_dropped():
    """只被一个通道命中的片段要保留（记 0 分参与融合），不能丢。"""
    out = fuse_weighted([("kw_only", 1.0)], [("vec_only", 1.0)], keyword_weight=0.5)
    assert set(out) == {"kw_only", "vec_only"}


def test_fuse_weighted_all_equal_gets_half():
    out = fuse_weighted([("a", 0.7)], [("a", 0.7)], keyword_weight=0.5)
    assert out == {"a": 0.5}          # 只有一个候选且分数相同 → 记 0.5


def test_fuse_weighted_weight_edges():
    kw, vec = [("a", 1.0)], [("b", 1.0)]
    # 权重全给关键词：a 应为 1（归一化后），b 为 0
    out = fuse_weighted(kw, vec, keyword_weight=1.0)
    assert out["a"] == 1.0 and out["b"] == 0.0
    # 权重全给向量：反过来
    out = fuse_weighted(kw, vec, keyword_weight=0.0)
    assert out["b"] == 1.0 and out["a"] == 0.0


def test_fuse_rrf_uses_rank_not_score():
    """RRF 只看名次：分数差 100 倍也不影响结果（这是它规避量纲差异的原理）。"""
    kw = [("a", 0.9), ("b", 0.0001)]
    vec = [("b", 100.0), ("a", 0.001)]
    out = fuse_rrf(kw, vec)
    # a: 1/(60+1) + 1/(60+2)；b: 1/(60+2) + 1/(60+1) → 两者相等
    assert abs(out["a"] - out["b"]) < 1e-9


def test_fuse_hybrid_unknown_strategy_falls_back_weighted():
    kw, vec = [("a", 1.0)], [("a", 0.0)]
    assert fuse_hybrid(kw, vec, "not-a-strategy") == fuse_weighted(kw, vec, 0.5)


def test_fuse_hybrid_rrf_selected():
    kw, vec = [("a", 1.0)], [("a", 1.0)]
    assert fuse_hybrid(kw, vec, "rrf") == fuse_rrf(kw, vec)


def test_distance_to_similarity_conversion():
    """Chroma 返回 distance = 1 − 余弦相似度；换算方向不能写反。"""
    from server.text.searcher import _norm_minmax

    dists = [0.0, 0.5, 1.0]                 # 完全相同 / 正交 / 相反
    sims = [1.0 - d for d in dists]
    assert sims == [1.0, 0.5, 0.0]
    assert _norm_minmax(sims) == [1.0, 0.5, 0.0]
    assert _norm_minmax([0.3, 0.3]) == [0.5, 0.5]     # max==min → 0.5
