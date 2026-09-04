"""F09 离线入口：数据快照与治理（export + normalize + alias + isolate + report）。

用法：
  python scripts/export_snapshot.py                 # 版本自动取当天 vN
  python scripts/export_snapshot.py --version 20260904_v2
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# 使 RAG 根可 import（脚本从 RAG/ 根运行）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_settings  # noqa: E402
from data.snapshot import governance  # noqa: E402
from lib.logging_util import get_logger  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="F09 数据快照与治理")
    ap.add_argument("--version", default=None, help="快照版本号，如 20260904_v2（默认取当天 vN）")
    args = ap.parse_args()

    settings = get_settings()
    logger = get_logger("rag.export", settings.log_dir)
    out = governance.run_governance(settings, version=args.version, logger=logger)
    print(f"\n快照已生成: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
