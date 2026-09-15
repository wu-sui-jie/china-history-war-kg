"""F11 索引构建编排：读取快照 → 切分 → 写 chunks.jsonl → 建 FTS5 → 向量。

用法见 scripts/build_index.py 与 data/index/README.md。
"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path

from config.settings import Settings, get_settings
from lib import versions
from lib.json_io import write_json, write_jsonl

from data.index.chunking import build_chunks
from data.index import fts as fts_mod
from data.index import vectors as vec_mod


def _resolve_snapshot(settings: Settings, snapshot_version: str | None) -> Path:
    """定位快照目录：显式版本 或 最新版本。"""
    if snapshot_version:
        p = settings.snapshot_dir / snapshot_version
        if not p.exists():
            raise FileNotFoundError(f"快照版本不存在: {p}")
        return p
    latest = versions.list_versions(settings.snapshot_dir)
    if not latest:
        raise FileNotFoundError(f"无可用快照: {settings.snapshot_dir}（先跑 export_snapshot）")
    return settings.snapshot_dir / latest[0]


def _build_vectors(settings: Settings, index_dir: Path, chunks: list[dict],
                   build_embeddings: bool, logger) -> dict:
    """向量步骤：有密钥且开关为真 → 云端嵌入 + Chroma；否则写空占位。

    占位态（mode=placeholder）在在线侧被识别为"无向量"，F04 自动降级关键词，不报错。
    """
    from data.index.embeddings import build_embed_fn

    logger = logger or logging.getLogger("rag.index")
    embed_fn = build_embed_fn(settings, logger) if build_embeddings else None
    if embed_fn is None:
        if build_embeddings:
            logger.warning("  未读到向量模型密钥（EMBEDDING_* 或别名）→ 写空占位；"
                           "在线检索将自动降级 keyword")
        return vec_mod.build_vector_placeholder(index_dir, chunks)
    from data.index import vector_pipeline

    return vector_pipeline.build_vector_store(
        settings, index_dir, embed_fn=embed_fn, logger=logger)


def run_index_build(settings: Settings, snapshot_version: str | None = None,
                    build_embeddings: bool | None = None, index_suffix: str = "",
                    logger=None) -> Path:
    """构建索引。index_suffix 非空时生成"索引变体"：目录 <版本><后缀>，manifest 回指真实快照。"""
    logger = logger or logging.getLogger("rag.index")
    snap = _resolve_snapshot(settings, snapshot_version)
    version = snap.name
    index_name = f"{version}{index_suffix}"
    index_dir = settings.index_dir / index_name
    if index_dir.exists():
        raise FileExistsError(f"索引目录已存在: {index_dir}（勿覆盖，请用新快照版本或换 --index-suffix）")
    index_dir.mkdir(parents=True, exist_ok=True)

    build_embeddings = (
        settings.index_build_embeddings if build_embeddings is None else build_embeddings
    )

    # 1) 切分
    logger.info(f"step1 切分语料（快照 {version}；索引 {index_name}）…")
    chunks, chunk_stats = build_chunks(settings, snap, logger)
    # 统一写 chunks.jsonl
    write_jsonl(index_dir / "chunks.jsonl", chunks)

    # 2) FTS5
    logger.info("step2 构建 FTS5 关键词索引…")
    fts_mod.load_jieba_dicts(snap)
    searchable = fts_mod.build_fts5(index_dir / "chunks_fts.db", chunks, logger)

    # 3) 向量：开关为真且有密钥 → 云端嵌入 + Chroma；否则空占位（在线侧自动降级 keyword）
    logger.info("step3 向量索引…")
    vec_stats = _build_vectors(settings, index_dir, chunks, build_embeddings, logger)

    # 4) manifest + 报告
    manifest = {
        "index_version": index_name,
        "source_snapshot": version,
        "built_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "chunk_params": {
            "max_chars": settings.chunk_max_chars,
            "overlap_chars": settings.chunk_overlap_chars,
        },
        "counts": {
            "chunks_total": len(chunks),
            "raw": chunk_stats.get("raw_paras", 0),
            "event_card": chunk_stats.get("event_cards", 0),
            "evidence": chunk_stats.get("evidences", 0),
            "fts_searchable": searchable,
        },
        "vectors": vec_stats,
    }
    if index_suffix:
        manifest["variant"] = index_suffix
    write_json(index_dir / "manifest.json", manifest)
    write_json(index_dir / "build_report.json", {
        "index_version": index_name,
        "source_snapshot": version,
        "chunk_stats": chunk_stats,
        "vectors": vec_stats,
        "missing_texts": 0,
    })

    logger.info(
        f"索引构建完成 → {index_dir}\n"
        f"  片段 {len(chunks)} (raw {chunk_stats.get('raw_paras')}/card "
        f"{chunk_stats.get('event_cards')}/ev {chunk_stats.get('evidences')}) | "
        f"FTS5 可检索 {searchable} | 向量 {vec_stats.get('mode')}"
    )
    return index_dir
