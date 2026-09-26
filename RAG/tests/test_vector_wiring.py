"""向量接线与审计副本重建的守护用例。

覆盖两处"文档写了、行为上必须成立"的路径：

1. `data/index/build.py::_build_vectors` 的三条分支——开关关 / 开但无密钥 → 写空占位
   （在线侧自动降级 keyword，**不得**因缺密钥中断整条离线构建）；开关开且有密钥 →
   委托 `vector_pipeline.build_vector_store`（云端嵌入 + 写 Chroma）。
2. `vector_pipeline.rebuild_chroma_from_npy`——只用审计副本
   （`ids.json` + `embeddings.npy`）重建 Chroma，不调用云端；
   行数、维度不一致必须报错；元数据按 `chunk_id` 对齐。
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import numpy as np
import pytest

from data.index import build as index_build
from data.index import chroma_store, embeddings, vector_pipeline

DIM = 8
_SETTINGS = SimpleNamespace(embedding_dim=DIM, embedding_model="test-embed",
                            chroma_collection="test_col")
_LOG = logging.getLogger("test")


def _chunks(n: int = 3) -> list[dict]:
    return [{"chunk_id": f"c{i}", "text": f"text-{i}", "chunk_type": "raw", "doc_id": "d0"}
            for i in range(n)]


def _audit_copy(index_dir, n: int = 3, dim: int = DIM) -> list[str]:
    """造一份审计副本：vectors/ids.json + vectors/embeddings.npy + chunks.jsonl。"""
    vec = index_dir / "vectors"
    vec.mkdir(parents=True, exist_ok=True)
    ids = [f"c{i}" for i in range(n)]
    (vec / "ids.json").write_text(json.dumps(ids), encoding="utf-8")
    np.save(vec / "embeddings.npy", np.zeros((n, dim), dtype=np.float32))
    with open(index_dir / "chunks.jsonl", "w", encoding="utf-8") as f:
        for c in _chunks(n):
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    return ids


# ---------- _build_vectors：三条分支 ----------

def test_switch_off_writes_placeholder(tmp_path, monkeypatch):
    """开关关闭（INDEX_BUILD_EMBEDDINGS=false）→ 占位，且不触达密钥读取。"""
    calls: list = []
    monkeypatch.setattr(embeddings, "build_embed_fn",
                        lambda *a, **k: calls.append(1) or None)
    stats = index_build._build_vectors(_SETTINGS, tmp_path, _chunks(), False, _LOG)
    assert stats["mode"] == "placeholder" and stats["dim"] == 0
    assert calls == []
    assert json.loads((tmp_path / "vectors" / "ids.json").read_text(encoding="utf-8")) == \
        ["c0", "c1", "c2"]


def test_no_key_writes_placeholder(tmp_path, monkeypatch):
    """开关打开但读不到密钥 → 占位（在线侧降级 keyword），不抛异常。"""
    monkeypatch.setattr(embeddings, "build_embed_fn", lambda *a, **k: None)
    stats = index_build._build_vectors(_SETTINGS, tmp_path, _chunks(), True, _LOG)
    assert stats["mode"] == "placeholder" and stats["chunks"] == 3


def test_key_present_delegates_to_pipeline(tmp_path, monkeypatch):
    """开关打开且有密钥 → 委托 vector_pipeline（云端嵌入 + 写 Chroma）。"""
    fake_embed = lambda texts: [[0.0] * DIM for _ in texts]  # noqa: E731
    monkeypatch.setattr(embeddings, "build_embed_fn", lambda *a, **k: fake_embed)
    seen: dict = {}

    def _fake_pipeline(settings, index_dir, **kw):
        seen.update(index_dir=index_dir, **kw)
        return {"mode": "chroma", "count": 3, "dim": DIM}

    monkeypatch.setattr(vector_pipeline, "build_vector_store", _fake_pipeline)
    stats = index_build._build_vectors(_SETTINGS, tmp_path, _chunks(), True, _LOG)
    assert stats["mode"] == "chroma" and stats["count"] == 3
    assert seen["index_dir"] == tmp_path and callable(seen["embed_fn"])
    assert not (tmp_path / "vectors" / "ids.json").exists()   # 占位未被写（走了 Chroma 分支）


# ---------- rebuild_chroma_from_npy：只读审计副本 ----------

def test_rebuild_chroma_from_npy(tmp_path, monkeypatch):
    """正常路径：ids/npy/chunks 对齐 → 建集合、回写 manifest。"""
    _audit_copy(tmp_path)
    (tmp_path / "manifest.json").write_text(
        json.dumps({"vectors": {"mode": "placeholder"}}), encoding="utf-8")
    seen: dict = {}

    def _fake_build(index_dir, ids, mat, metas, name, **kw):
        seen.update(ids=list(ids), metas=list(metas), shape=mat.shape, name=name)
        return {"collection": name, "count": len(ids)}

    monkeypatch.setattr(chroma_store, "build_collection", _fake_build)
    stats = vector_pipeline.rebuild_chroma_from_npy(_SETTINGS, tmp_path, logger=_LOG)
    assert stats["mode"] == "chroma" and stats["count"] == 3
    assert stats["rebuilt_from"] == "embeddings.npy" and stats["dim"] == DIM
    assert seen["shape"] == (3, DIM) and seen["name"] == "test_col"
    assert seen["metas"][0]["chunk_type"] == "raw"      # 元数据按 chunk_id 对齐
    doc = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert doc["vectors"]["mode"] == "chroma"           # 启动侧 vector_available 判定读这里


def test_rebuild_missing_audit_copy_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        vector_pipeline.rebuild_chroma_from_npy(_SETTINGS, tmp_path, logger=_LOG)


def test_rebuild_row_mismatch_raises(tmp_path):
    """npy 行数与 ids 不一致 → 报错（绝不能错位检索）。"""
    _audit_copy(tmp_path, n=3)
    np.save(tmp_path / "vectors" / "embeddings.npy", np.zeros((2, DIM), dtype=np.float32))
    with pytest.raises(ValueError):
        vector_pipeline.rebuild_chroma_from_npy(_SETTINGS, tmp_path, logger=_LOG)


def test_rebuild_dim_mismatch_raises(tmp_path):
    """npy 维度与 EMBEDDING_DIM 不一致 → 报错。"""
    _audit_copy(tmp_path, n=3)
    np.save(tmp_path / "vectors" / "embeddings.npy",
            np.zeros((3, DIM + 1), dtype=np.float32))
    with pytest.raises(ValueError):
        vector_pipeline.rebuild_chroma_from_npy(_SETTINGS, tmp_path, logger=_LOG)


def test_rebuild_placeholder_copy_raises(tmp_path):
    """占位态（0 行）不可当审计副本用 → 报错而不是建出空集合。"""
    _audit_copy(tmp_path, n=3)
    np.save(tmp_path / "vectors" / "embeddings.npy", np.zeros((0, 0), dtype=np.float32))
    with pytest.raises(ValueError):
        vector_pipeline.rebuild_chroma_from_npy(_SETTINGS, tmp_path, logger=_LOG)
