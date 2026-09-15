"""向量后端降级路径的守护用例（RAGv5 §2.3-3）。

用**合成小索引**（8 维、5 条）构造三种破坏，验证 `vector_available` 都会变成 False
（即"索引损坏/缺失 → 自动降级关键词"），且不抛异常：

1. 集合目录被删（Chroma 不可加载）；
2. `ids.json` 被改短（条数与集合不一致）；
3. 集合里少了几条（count 与 ids 不一致）。

之所以要"降级"而不是"报错"：演示/生产环境里向量不可用是**允许状态**（F04 需求：
最终模式以评测为准），但错位检索（行对不上）是绝对不能发生的。
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from data.index import chroma_store
from server.text.searcher import TextSearcher

DIM = 8
N = 5
COLLECTION = "test_chunks"


def _make_index(index_dir, n: int = N) -> list[str]:
    ids = [f"chunk_{i:06d}" for i in range(n)]
    rng = np.random.RandomState(42)
    vecs = rng.rand(n, DIM).astype(np.float32)
    chroma_store.build_collection(index_dir, ids, vecs,
                                  [{"chunk_type": "raw"} for _ in ids],
                                  COLLECTION, rebuild=True)
    (index_dir / "vectors").mkdir(parents=True, exist_ok=True)
    (index_dir / "vectors" / "ids.json").write_text(json.dumps(ids), encoding="utf-8")
    return ids


def _searcher(index_dir) -> TextSearcher:
    return TextSearcher(index_dir, "test", top_k=N, collection_name=COLLECTION,
                        embed_fn=lambda texts: [[0.1] * DIM for _ in texts])


def test_healthy_index_vector_available(tmp_path):
    _make_index(tmp_path)
    assert _searcher(tmp_path).vector_available is True


def test_missing_collection_dir_downgrades(tmp_path):
    """破坏 1：向量目录不存在（未构建/被删）→ 降级，不抛异常。

    注：Windows 上 Chroma 的 sqlite 文件被进程持有，测试里不真删已加载的目录，
    改为构造"只有 ids.json、没有 chroma/"的场景——与"目录被删"走同一条判定分支。
    """
    (tmp_path / "vectors").mkdir(parents=True, exist_ok=True)
    (tmp_path / "vectors" / "ids.json").write_text(
        json.dumps([f"chunk_{i:06d}" for i in range(N)]), encoding="utf-8")
    assert _searcher(tmp_path).vector_available is False


def test_unknown_collection_name_downgrades(tmp_path):
    """破坏 1': 集合名不匹配（重建换了名字/集合被删）→ 降级。"""
    _make_index(tmp_path)
    s = TextSearcher(tmp_path, "test", top_k=N, collection_name="no_such_collection",
                     embed_fn=lambda texts: [[0.1] * DIM for _ in texts])
    assert s.vector_available is False


def test_truncated_ids_downgrades(tmp_path):
    """破坏 2：ids.json 被改短（条数不一致）→ 降级（绝不能错位检索）。"""
    ids = _make_index(tmp_path)
    (tmp_path / "vectors" / "ids.json").write_text(
        json.dumps(ids[:-1]), encoding="utf-8")
    assert _searcher(tmp_path).vector_available is False


def test_collection_row_missing_downgrades(tmp_path):
    """破坏 3：集合里删掉几条（count 与 ids 不一致）→ 降级。"""
    ids = _make_index(tmp_path)
    col = chroma_store.load_collection(tmp_path, COLLECTION)
    assert col is not None
    col.delete(ids=ids[:2])
    assert _searcher(tmp_path).vector_available is False


def test_no_embed_fn_means_no_vector(tmp_path):
    """没有查询侧向量化能力（未配密钥）时向量不可用 → 关键词通道。"""
    _make_index(tmp_path)
    s = TextSearcher(tmp_path, "test", top_k=N, collection_name=COLLECTION, embed_fn=None)
    assert s.vector_available is False


def test_search_vector_returns_empty_when_unavailable(tmp_path):
    """不可用时 search_vector 返回空（由上层 resolve_mode 降级），不抛异常。"""
    (tmp_path / "vectors").mkdir(parents=True, exist_ok=True)
    (tmp_path / "vectors" / "ids.json").write_text(
        json.dumps([f"chunk_{i:06d}" for i in range(N)]), encoding="utf-8")
    s = _searcher(tmp_path)
    assert s.search_vector("任意问题", limit=3) == []
