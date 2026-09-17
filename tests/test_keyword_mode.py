"""F04 keyword_mode 参数回归测试（RAGv4 为拆解 AND/OR 新增可选参数）。

- 默认路径行为不变：keyword_mode 缺省 = and_or（AND 优先、OR 兜底）；
- and / or 可独立取词，用于评测量化 AND 失效面与 OR 兜底。
索引数据 data/index/<v>/ 不入 Git，缺失时跳过。

运行：在 RAG/ 根目录执行  python -m pytest tests/test_keyword_mode.py -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.text.searcher import TextSearcher

ROOT = Path(__file__).resolve().parent.parent
INDEX_DIR = ROOT / "data" / "index" / "20260904_v2"

pytestmark = pytest.mark.skipif(
    not (INDEX_DIR / "chunks_fts.db").exists(),
    reason="本地 FTS 索引缺失（data/index/ 不入 Git）",
)


@pytest.fixture(scope="module")
def searcher() -> TextSearcher:
    return TextSearcher(INDEX_DIR, source_version="20260904_v2", top_k=30)


def test_default_is_and_or(searcher: TextSearcher):
    """缺省 keyword_mode 等价于显式 and_or（AND 命中时两者一致）。"""
    query = "赤壁之战的主帅是谁？"  # 短问句，AND 可命中（首轮评测冒烟验证过）
    default = searcher.search_keyword(query, limit=30)
    explicit = searcher.search_keyword(query, limit=30, keyword_mode="and_or")
    assert [c["chunk_id"] for c in default] == [c["chunk_id"] for c in explicit]


def test_long_rewrite_and_fails_or_recovers(searcher: TextSearcher):
    """长改写问题：纯 AND 命中 0，OR 兜底能召回（专项口径的稳定证据）。"""
    query = "长平之战的主要经过和结果是什么？"
    and_hits = searcher.search_keyword(query, limit=30, keyword_mode="and")
    or_hits = searcher.search_keyword(query, limit=30, keyword_mode="or")
    default = searcher.search_keyword(query, limit=30)  # and_or → OR 兜底
    assert len(and_hits) == 0
    assert len(or_hits) > 0
    assert len(default) == len(or_hits)


def test_scores_normalized(searcher: TextSearcher):
    hits = searcher.search_keyword("官渡之战", limit=10)
    assert hits
    for c in hits:
        assert 0.0 <= c["_score"] <= 1.0
        assert c["chunk_id"].startswith("chunk_")
