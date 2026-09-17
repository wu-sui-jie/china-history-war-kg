"""F09 人工审核回填入口：读取 audit_decisions.json → 生成新版本快照。

用法：
  python scripts/apply_audit.py --decisions audit_decisions.json [--source 20260904_v2]
                               [--out-version 20260904_v3]

说明：
  - decisions 文件结构见 data/snapshot/apply_audit.py（source_version/operator/decisions）。
  - 回填生成**新版本**快照（不覆盖源版本）；之后用同版本重建索引：
      python scripts/build_index.py --version <out_version>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_settings  # noqa: E402
from data.snapshot import apply_audit  # noqa: E402
from lib.json_io import read_json  # noqa: E402
from lib.logging_util import get_logger  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="F09 人工审核决定回填")
    ap.add_argument("--decisions", required=True, help="audit_decisions.json 路径")
    ap.add_argument("--source", default=None,
                    help="源快照版本（默认取 decisions.source_version）")
    ap.add_argument("--out-version", default=None,
                    help="输出新快照版本（默认自动取当天 vN）")
    args = ap.parse_args()

    settings = get_settings()
    logger = get_logger("rag.apply_audit", settings.log_dir)
    decisions = read_json(Path(args.decisions))
    source_version = args.source or decisions.get("source_version")
    if not source_version:
        print("错误: 未指定 --source 且 decisions 无 source_version")
        return 2
    out = apply_audit.run_apply_audit(
        settings, source_version, Path(args.decisions),
        out_version=args.out_version, logger=logger)
    print(f"\n回填快照已生成: {out}")
    print("提示: 使用新版本前需重建索引:")
    print(f"  python scripts/build_index.py --version {out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
