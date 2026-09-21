"""向量索引构建流水线（RAGv5 T3）：嵌入 → 断点续跑 → ids.json / npy 审计副本 + Chroma。

- 复用既有 `chunks.jsonl`（`--vectors-only` 路径），不重切分、不重建 FTS5
- 分批（百炼上限 10 条/请求）调用云端向量模型，**每批落盘 `vectors/_parts/`**，
  中断后重跑只补缺失批次（不重复计费）
- 全部完成后：拼 `embeddings.npy`（float32 审计副本，不参与检索）+ 写 Chroma 集合
- manifest.json 的 `vectors` 段被更新为 `mode=chroma`（启动可见性的唯一来源）
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, List, Optional

import numpy as np

from config.settings import Settings
from data.index import chroma_store
from data.index.embeddings import build_embed_fn
from lib.json_io import read_json, write_json


def _parts_dir(index_dir: Path) -> Path:
    return index_dir / "vectors" / "_parts"


def _load_chunks(index_dir: Path) -> List[dict]:
    chunks: List[dict] = []
    with open(index_dir / "chunks.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))
    return chunks


def _parts_signature(embed_fn_model: str, dim: int, batch: int, limit: Optional[int]) -> dict:
    return {"model": embed_fn_model, "dim": dim, "batch": batch, "limit": limit}


def build_vector_store(
    settings: Settings,
    index_dir: Path,
    *,
    embed_fn: Optional[Callable[[List[str]], list]] = None,
    limit: Optional[int] = None,
    resume: bool = True,
    concurrency: int = 4,
    logger=None,
) -> dict:
    """构建向量库，返回 manifest.vectors 段的 dict（同时写入 manifest.json）。

    limit: 只处理前 N 条（小样验证维度/质量，并显著降低费用）
    resume: 复用 `_parts/` 已完成批次（参数变化时自动作废重来）
    concurrency: 并发批次数（默认 4；过高可能触发服务端限流，失败会中断且可续跑）
    """
    import logging

    logger = logger or logging.getLogger("rag.index")
    index_dir = Path(index_dir)
    chunks = _load_chunks(index_dir)
    if limit:
        chunks = chunks[:limit]
    if not chunks:
        raise ValueError(f"索引目录缺少可用片段: {index_dir / 'chunks.jsonl'}")

    embed_fn = embed_fn or build_embed_fn(settings, logger)
    if embed_fn is None:
        raise RuntimeError(
            "未配置云端向量模型（EMBEDDING_BASE_URL / EMBEDDING_MODEL / 密钥），"
            "无法构建向量；如只想建关键词索引请用 --no-embeddings"
        )

    batch_size = max(1, min(10, settings.embedding_batch_size))   # 百炼硬上限 10
    dim_cfg = settings.embedding_dim or 0
    vec_dir = index_dir / "vectors"
    parts = _parts_dir(index_dir)
    parts.mkdir(parents=True, exist_ok=True)

    sig = _parts_signature(settings.embedding_model, dim_cfg, batch_size, limit)
    sig_path = parts / "signature.json"
    if resume and sig_path.exists():
        old = read_json(sig_path)
        if old != sig:
            logger.info("向量参数已变化 → 作废既有分批缓存（_parts/）")
            for p in parts.glob("batch_*.npy"):
                p.unlink()
    write_json(sig_path, sig)

    texts = [c.get("text") or "" for c in chunks]
    n_batches = (len(texts) + batch_size - 1) // batch_size
    t0 = time.time()
    pending = [bi for bi in range(n_batches)
               if not (resume and (parts / f"batch_{bi:05d}.npy").exists())]
    logger.info(f"待向量化 {len(pending)}/{n_batches} 批（已缓存 {n_batches - len(pending)} 批）；"
                f"并发 {concurrency}")

    counter = {"done": len(pending), "cached": n_batches - len(pending)}

    def _do_batch(bi: int) -> None:
        lo, hi = bi * batch_size, min(len(texts), (bi + 1) * batch_size)
        vecs = embed_fn(texts[lo:hi])
        arr = np.asarray(vecs, dtype=np.float32)
        if arr.ndim != 2 or arr.shape[0] != hi - lo:
            raise ValueError(f"第 {bi} 批返回形状异常: {arr.shape}（期望 {hi - lo} 行）")
        if dim_cfg and arr.shape[1] != dim_cfg:
            raise ValueError(f"第 {bi} 批维度 {arr.shape[1]} != EMBEDDING_DIM {dim_cfg}")
        # 先写临时文件再改名：避免并发中断留下半截 npy 被当成"已完成"。
        # 注意：np.save 对不以 .npy 结尾的文件名会自动补 .npy，故临时名必须以 .npy 结尾。
        tmp = parts / f"batch_{bi:05d}.tmp.npy"
        np.save(tmp, arr)
        tmp.replace(parts / f"batch_{bi:05d}.npy")
        counter["cached"] += 1
        if counter["cached"] % 25 == 0 or counter["cached"] == n_batches:
            logger.info(f"  向量化进度 {counter['cached']}/{n_batches} 批"
                        f"（{counter['cached'] * batch_size}/{len(texts)} 片段，{time.time() - t0:.0f}s）")

    if pending:
        if concurrency <= 1:
            for bi in pending:
                _do_batch(bi)
        else:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                # 逐批提交并即时抛出异常（失败即中断，已落盘的批次仍可续跑）
                for _ in pool.map(_do_batch, pending):
                    pass
    # 拼装：按批次顺序拼接，行序 == chunks 顺序（ids 以此为对齐依据）
    mats = [np.load(parts / f"batch_{bi:05d}.npy") for bi in range(n_batches)]
    mat = np.concatenate(mats, axis=0) if mats else np.zeros((0, 0), dtype=np.float32)
    ids = [c["chunk_id"] for c in chunks]
    if mat.shape[0] != len(ids):
        raise ValueError(f"向量行数 {mat.shape[0]} != 片段数 {len(ids)}")

    write_json(vec_dir / "ids.json", ids)
    np.save(vec_dir / "embeddings.npy", mat)     # 审计副本（不参与检索；换机器时免重嵌入）
    stats_chroma = chroma_store.build_collection(
        index_dir, ids, mat, chroma_store.to_metadatas(chunks),
        settings.chroma_collection, rebuild=True, logger=logger)

    stats = {
        "mode": "chroma",
        "chunks": len(ids),
        "dim": int(mat.shape[1]) if mat.ndim == 2 else 0,
        "model": settings.embedding_model,
        "space": "cosine",
        "collection": stats_chroma["collection"],
        "count": stats_chroma["count"],
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "elapsed_s": round(time.time() - t0, 1),
    }
    _persist_stats(index_dir, stats)
    logger.info(f"向量构建完成: {stats['count']} 条 / dim={stats['dim']} / "
                f"{stats['elapsed_s']}s → {chroma_store.chroma_dir(index_dir)}")
    return stats


def _persist_stats(index_dir: Path, stats: dict) -> None:
    """把 vectors 段写回 manifest.json 与 build_report.json（启动时 vector_available 判定读它）。"""
    for name in ("manifest.json", "build_report.json"):
        path = index_dir / name
        if path.exists():
            doc = read_json(path)
            doc["vectors"] = stats
            write_json(path, doc)


def rebuild_chroma_from_npy(settings: Settings, index_dir: Path, logger=None) -> dict:
    """用既有审计副本（ids.json + embeddings.npy）重建 Chroma：不调用云端、不重新嵌入。

    适用：`vectors/chroma/` 丢失或损坏，或换机器只带了审计副本（ids.json + npy）时。
    元数据按 chunk_id 从 chunks.jsonl 对齐（`--sample` 小样时 npy 行数少于 chunks）。
    """
    import logging

    logger = logger or logging.getLogger("rag.index")
    index_dir = Path(index_dir)
    vec_dir = index_dir / "vectors"
    ids_path, npy_path = vec_dir / "ids.json", vec_dir / "embeddings.npy"
    if not ids_path.exists() or not npy_path.exists():
        raise FileNotFoundError(f"缺少审计副本（先跑一次向量构建）: {ids_path} / {npy_path}")
    ids = list(read_json(ids_path))
    mat = np.load(npy_path)
    if not ids or mat.ndim != 2 or mat.shape[0] != len(ids):
        raise ValueError(f"审计副本不一致: npy {getattr(mat, 'shape', None)} vs ids {len(ids)}")
    dim_cfg = settings.embedding_dim or 0
    if dim_cfg and mat.shape[1] != dim_cfg:
        raise ValueError(f"审计副本维度 {mat.shape[1]} != EMBEDDING_DIM {dim_cfg}")

    t0 = time.time()
    try:
        by_id = {c["chunk_id"]: c for c in _load_chunks(index_dir)}
    except FileNotFoundError:      # 只有审计副本时元数据留空，检索仍可用（筛选项失效）
        by_id = {}
    metas = [chroma_store.to_metadatas([by_id[i]])[0] if i in by_id else {} for i in ids]
    stats_chroma = chroma_store.build_collection(
        index_dir, ids, mat, metas, settings.chroma_collection, rebuild=True, logger=logger)
    stats = {
        "mode": "chroma",
        "chunks": len(ids),
        "dim": int(mat.shape[1]),
        "model": settings.embedding_model,
        "space": "cosine",
        "collection": stats_chroma["collection"],
        "count": stats_chroma["count"],
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "elapsed_s": round(time.time() - t0, 1),
        "rebuilt_from": "embeddings.npy",
    }
    _persist_stats(index_dir, stats)
    logger.info(f"Chroma 已从审计副本重建: {stats['count']} 条 / dim={stats['dim']} / "
                f"{stats['elapsed_s']}s → {chroma_store.chroma_dir(index_dir)}")
    return stats
