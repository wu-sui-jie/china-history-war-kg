"""F11 离线入口：基于快照构建文本与向量索引。

用法：
  python scripts/build_index.py                 # 用最新快照（有向量密钥则同时嵌入并写 Chroma）
  python scripts/build_index.py --version 20260904_v2
  python scripts/build_index.py --no-embeddings # 无向量密钥时只建 FTS5
  python scripts/build_index.py --index-suffix _c500o100   # 索引变体（分块参数对比等）
  python scripts/build_index.py --vectors-only --version <索引目录名>   # 只补/重建向量
  python scripts/build_index.py --rebuild-chroma --version <索引目录名> # 从 npy 审计副本重建 Chroma

索引变体：索引目录为 data/index/<快照版本><后缀>/，manifest 记 source_snapshot=<快照版本>
与 variant=<后缀>；加载侧（server/runtime.py::resolve_version）据此回指真实快照。
同一快照因此可以并存多套不同切分/向量参数的索引，互不覆盖、可随时清理。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_settings  # noqa: E402
from data.index import build as index_build  # noqa: E402
from lib.logging_util import get_logger  # noqa: E402


def _resolve_index_dir(settings, version: str | None):
    """定位索引目录：显式版本/变体名，或"最新快照的同版本索引"。"""
    from pathlib import Path as _P

    if version:
        p = _P(settings.index_dir) / version
        if not p.exists():
            raise FileNotFoundError(f"索引目录不存在: {p}")
        return p
    from server.runtime import resolve_version

    return resolve_version(settings, None)[2]


def main() -> int:
    ap = argparse.ArgumentParser(description="F11 文本切分与索引构建")
    ap.add_argument("--version", default=None,
                    help="快照版本号（默认最新）；--vectors-only 时可为索引目录名（含变体后缀）")
    ap.add_argument("--no-embeddings", action="store_true",
                    help="跳过向量索引（无云端向量密钥时）")
    ap.add_argument("--index-suffix", default="",
                    help="索引目录后缀（如 _c500o100）：生成索引变体，不覆盖同名快照的既有索引")
    ap.add_argument("--chunk-max-chars", type=int, default=None,
                    help="切分：单片段最大字符数（默认取配置 CHUNK_MAX_CHARS）")
    ap.add_argument("--chunk-overlap-chars", type=int, default=None,
                    help="切分：相邻片段重叠字符数（默认取配置 CHUNK_OVERLAP_CHARS）")
    grp_vec = ap.add_mutually_exclusive_group()
    grp_vec.add_argument("--vectors-only", action="store_true",
                         help="只重建向量（复用既有 chunks.jsonl，不动切分与 FTS5）")
    grp_vec.add_argument("--rebuild-chroma", action="store_true",
                         help="用既有 vectors/ids.json + embeddings.npy 重建 Chroma"
                              "（不重新嵌入、不调用云端；chroma/ 丢失或换机器时用）")
    ap.add_argument("--sample", type=int, default=None,
                    help="只向量化前 N 条片段（小样验证维度/质量，降低费用）")
    ap.add_argument("--no-resume", action="store_true",
                    help="忽略 vectors/_parts/ 的已完成批次，从头嵌入（会重复计费）")
    ap.add_argument("--concurrency", type=int, default=4,
                    help="向量化并发批次数（默认 4；过高可能触发限流）")
    args = ap.parse_args()

    settings = get_settings()
    if args.chunk_max_chars:
        settings.chunk_max_chars = args.chunk_max_chars
    if args.chunk_overlap_chars is not None:
        settings.chunk_overlap_chars = args.chunk_overlap_chars
    logger = get_logger("rag.index", settings.log_dir)

    if args.vectors_only:
        from data.index import vector_pipeline

        index_dir = _resolve_index_dir(settings, args.version)
        print(f"[vectors-only] 索引目录: {index_dir}")
        stats = vector_pipeline.build_vector_store(
            settings, index_dir, limit=args.sample,
            resume=not args.no_resume, concurrency=args.concurrency, logger=logger,
        )
        print(f"\n向量构建完成: {stats}")
        return 0

    if args.rebuild_chroma:
        from data.index import vector_pipeline

        index_dir = _resolve_index_dir(settings, args.version)
        print(f"[rebuild-chroma] 索引目录: {index_dir}")
        stats = vector_pipeline.rebuild_chroma_from_npy(settings, index_dir, logger=logger)
        print(f"\nChroma 重建完成: {stats}")
        return 0

    out = index_build.run_index_build(
        settings,
        snapshot_version=args.version,
        build_embeddings=False if args.no_embeddings else None,
        index_suffix=args.index_suffix,
        logger=logger,
    )
    print(f"\n索引已生成: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
