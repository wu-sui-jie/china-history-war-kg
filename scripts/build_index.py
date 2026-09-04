"""F11 离线入口：基于快照构建文本与向量索引。

用法：
  python scripts/build_index.py                 # 用最新快照
  python scripts/build_index.py --version 20260904_v2
  python scripts/build_index.py --no-embeddings # 无向量密钥时只建 FTS5
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_settings  # noqa: E402
from data.index import build as index_build  # noqa: E402
from lib.logging_util import get_logger  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="F11 文本切分与索引构建")
    ap.add_argument("--version", default=None, help="快照版本号（默认最新）")
    ap.add_argument("--no-embeddings", action="store_true",
                    help="跳过向量索引（无云端向量密钥时）")
    args = ap.parse_args()

    settings = get_settings()
    logger = get_logger("rag.index", settings.log_dir)
    out = index_build.run_index_build(
        settings,
        snapshot_version=args.version,
        build_embeddings=False if args.no_embeddings else None,
        logger=logger,
    )
    print(f"\n索引已生成: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
