"""向量可用性的诚实报告。

问题不是"向量会失败"——查询侧 embedding 是网络调用，失败是正常状态，代码也一直会
降级关键词。问题是**降级之后没人知道**：`vector_available=True` 只表示制品能加载、
客户端已装配，health 却把它当成"向量检索可用"，于是密钥失效的部署照样显示健康，
真实走的是关键词——而且这个假阳性会一路掩盖进评测指标。

这里钉住三段口径：
1. 制品/客户端/探针是**三个独立字段**，不能用一个布尔概括；
2. 查询期向量化失败时，探针转为 False、错误原文被保留，且 `TextResult.mode` 如实
   报成 keyword（不是"因为关键词兜底出了结果就继续报 vector"）；
3. health 带着 `vector` 子对象，并在"声明可用但探针失败"时给出告警。

用合成小索引 + 桩/故障向量客户端，全程不碰网络。
"""

from __future__ import annotations

import json

import numpy as np

from data.index import chroma_store
from data.index import fts as fts_mod
from server.text import search as text_search
from server.text.searcher import TextSearcher

DIM = 8
N = 5
COLLECTION = "test_chunks"


def _stub_embed(dim: int = DIM):
    def _embed(texts):
        return [[0.1] * dim for _ in texts]
    return _embed


def _failing_embed(exc: Exception):
    def _embed(texts):
        raise exc
    return _embed


def _make_index(index_dir) -> None:
    """建一个最小可用索引：Chroma 集合 + ids.json + FTS（关键词兜底需要它）。"""
    ids = [f"chunk_{i:06d}" for i in range(N)]
    rng = np.random.RandomState(7)
    vecs = rng.rand(N, DIM).astype(np.float32)
    chroma_store.build_collection(index_dir, ids, vecs,
                                  [{"chunk_type": "raw"} for _ in ids],
                                  COLLECTION, rebuild=True)
    (index_dir / "vectors").mkdir(parents=True, exist_ok=True)
    (index_dir / "vectors" / "ids.json").write_text(json.dumps(ids), encoding="utf-8")

    chunks = [
        {"chunk_id": cid, "chunk_type": "raw", "text": f"赤壁之战 第{i}段 曹操 孙权",
         "source_version": "test"}
        for i, cid in enumerate(ids)
    ]
    fts_mod.build_fts5(index_dir / "chunks_fts.db", chunks)


def _searcher(index_dir, embed_fn) -> TextSearcher:
    return TextSearcher(index_dir, "test", top_k=N, collection_name=COLLECTION,
                        embed_fn=embed_fn)


def test_三个状态字段各自独立(tmp_path):
    """制品就绪 + 客户端已装配 ≠ 探针通过：探针必须先为 None，成功后才转 True。"""
    _make_index(tmp_path)
    searcher = _searcher(tmp_path, _stub_embed())

    status = searcher.vector_status()
    assert status["artifact_ready"] is True
    assert status["embedding_client_configured"] is True
    assert status["embedding_probe_ok"] is None, "还没查过就不该声称探针通过"
    assert status["last_vector_error"] == ""
    assert status["declared_available"] is True

    text_search(searcher, "赤壁之战", mode="vector")

    after = searcher.vector_status()
    assert after["embedding_probe_ok"] is True
    assert after["effective_text_mode"] == "vector"


def test_缺客户端时不再声称向量可用(tmp_path):
    """embed_fn 为 None（未配密钥）→ 声明与探针都不能是"可用"。"""
    _make_index(tmp_path)
    searcher = _searcher(tmp_path, None)

    status = searcher.vector_status()
    assert status["embedding_client_configured"] is False
    assert status["declared_available"] is False
    # 制品本身是好的——所以 artifact_ready 与"能不能用"必须分开报
    assert status["artifact_ready"] is False


def test_查询期向量化失败会如实降级并留下原因(tmp_path):
    """核心场景：制品与客户端都就绪，但查询时 embedding 端点不可用。"""
    _make_index(tmp_path)
    searcher = _searcher(tmp_path, _failing_embed(RuntimeError("embedding 端点 401")))

    assert searcher.vector_available is True, "启动时制品与客户端确实就绪"

    result = text_search(searcher, "赤壁之战", mode="vector")

    # 1) 结果仍可用（关键词兜底），2) 模式如实报 keyword，3) 降级原因带出来
    assert result.mode == "keyword"
    assert result.vector_degraded is True
    assert "401" in result.vector_note
    assert result.evidence, "降级之后仍应有证据，不能因为向量挂了整条链路无结果"

    status = searcher.vector_status()
    assert status["embedding_probe_ok"] is False
    assert "401" in status["last_vector_error"]
    assert status["vector_error_count"] >= 1
    assert status["effective_text_mode"] == "keyword"


def test_hybrid_下向量通道故障也如实降级(tmp_path):
    """hybrid 的向量半边挂掉时，报 hybrid 就是假阳性——执行的是关键词结果。"""
    _make_index(tmp_path)
    searcher = _searcher(tmp_path, _failing_embed(RuntimeError("连接被重置")))

    result = text_search(searcher, "赤壁之战", mode="hybrid")

    assert result.mode == "keyword"
    assert result.vector_degraded is True
    assert searcher.vector_status()["effective_text_mode"] == "keyword"


def test_向量返回空结果时如实报降级且不记成故障(tmp_path):
    """没有近邻 ≠ 链路故障：探针记成功、错误计数不增，但本次结果来自关键词兜底。"""
    _make_index(tmp_path)
    searcher = _searcher(tmp_path, _stub_embed())

    # 让 Chroma 查询恒返回空，模拟"确实没有近邻"（而不是调用失败）
    class _EmptyCollection:
        def query(self, **kwargs):
            return {"ids": [[]], "distances": [[]]}

    searcher.collection = _EmptyCollection()

    result = text_search(searcher, "赤壁之战", mode="vector")

    status = searcher.vector_status()
    # 链路本身是通的：探针成功、没有错误
    assert status["embedding_probe_ok"] is True
    assert status["vector_error_count"] == 0
    assert status["last_vector_error"] == ""
    # 但本次拿到的结果确实来自关键词，模式要如实报
    assert result.mode == "keyword"
    assert result.vector_degraded is True
    assert "未返回结果" in result.vector_note


def test_health_带向量子对象并在探针失败时告警(tmp_path, monkeypatch):
    """health 的向量段不能只有一个布尔：假阳性要么被消掉，要么被显式告警。"""
    from types import SimpleNamespace

    from server import api as api_mod

    _make_index(tmp_path)
    searcher = _searcher(tmp_path, _failing_embed(RuntimeError("密钥失效")))

    # health 只读 runtime 的几个属性；用 SimpleNamespace 拼一个最小替身，
    # 免得把整条 runtime 加载链拉进单元用例（制品与 embedding 都不需要真跑）。
    fake_runtime = SimpleNamespace(
        version="test",
        meta={},
        index_dir=tmp_path,
        # _demo_status 会读 settings.data_dir（示例题清单路径）
        settings=SimpleNamespace(data_dir=tmp_path),
        text=SimpleNamespace(vector_status=searcher.vector_status,
                             vector_available=searcher.vector_available),
        generate=SimpleNamespace(llm=SimpleNamespace(available=False),
                                 cache=SimpleNamespace(stats=lambda: {})),
    )

    monkeypatch.setattr(api_mod, "_runtime", lambda: fake_runtime)
    payload = api_mod.health()
    assert "vector" in payload, "health 必须带向量子对象"
    for key in ("artifact_ready", "embedding_client_configured", "embedding_probe_ok",
                "last_vector_error", "effective_text_mode"):
        assert key in payload["vector"], f"health.vector 缺少 {key}"
    # 还没查过：探针必须是 None，不能预设成"通过"
    assert payload["vector"]["embedding_probe_ok"] is None

    # 真发生一次查询期向量化失败 → 探针转 False，且告警必须出现
    text_search(searcher, "赤壁之战", mode="vector")
    payload = api_mod.health()
    assert payload["vector"]["embedding_probe_ok"] is False
    assert payload["vector"]["declared_available"] is True, "声明仍是可用的——正是要告警的错位"
    assert any("向量声明可用但查询期失败" in w for w in payload.get("warnings", [])), \
        payload.get("warnings")
