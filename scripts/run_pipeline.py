"""F09 + F11 一键离线流水线：治理快照 → 文本/向量索引。

用法：
  python scripts/run_pipeline.py
  python scripts/run_pipeline.py --snapshot-version 20260904_v2 --no-embeddings
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_settings  # noqa: E402
from data.snapshot import governance  # noqa: E402
from data.index import build as index_build  # noqa: E402
from lib.logging_util import get_logger  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="RAG 离线流水线 F09→F11")
    ap.add_argument("--snapshot-version", default=None, help="治理版本（默认自动生成新版本）")
    ap.add_argument("--no-embeddings", action="store_true", help="跳过向量索引")
    args = ap.parse_args()

    settings = get_settings()
    logger = get_logger("rag.pipeline", settings.log_dir)

    snap = governance.run_governance(settings, version=args.snapshot_version, logger=logger)
    index_build.run_index_build(
        settings,
        snapshot_version=snap.name,
        build_embeddings=False if args.no_embeddings else None,
        logger=logger,
    )
    print(f"\n离线流水线完成:\n  快照 {snap}\n  索引 {settings.index_dir / snap.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
