"""F09 延伸离线入口：规则推理固化（P2，见 docs/RAG_v2/RAG规则推理移植-需求与设计.md）。

用法：

  python scripts/build_inferred_relations.py                       # 最新快照
  python scripts/build_inferred_relations.py --version 20260915_v1
  python scripts/build_inferred_relations.py --rules data/rules/rule_base.json
  python scripts/build_inferred_relations.py --dry-run             # 只统计不落盘

产物（写在快照目录内，与 relations.json 同级）：

- `inferred_relations.json`：推理关系行（与 relations.json 同构 + inferred/rule_id/rule_name/
  derived_from/derived_from_rows 标记），在线由 GraphIndex 与原始关系一起加载；
- `inference_report.json`：构建报告（规则文件哈希、逐规则产出、跳过原因、产物哈希）。

幂等：覆盖式重建，输出稳定排序 + UTF-8 + LF，重复构建字节一致。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_settings  # noqa: E402
from data.snapshot import inference  # noqa: E402
from lib.logging_util import get_logger  # noqa: E402
from lib.versions import list_versions  # noqa: E402


def _resolve_snapshot_dir(settings, version: str | None) -> Path:
    if version:
        path = Path(settings.snapshot_dir) / version
        if not path.exists():
            raise FileNotFoundError(f"快照目录不存在: {path}")
        return path
    snaps = list_versions(Path(settings.snapshot_dir))
    if not snaps:
        raise FileNotFoundError(f"无可用快照: {settings.snapshot_dir}")
    return Path(settings.snapshot_dir) / snaps[0]


def main() -> int:
    ap = argparse.ArgumentParser(description="F09 规则推理固化（推理结果离线化）")
    ap.add_argument("--version", default=None, help="快照版本号（默认最新）")
    ap.add_argument("--rules", default=None,
                    help="规则库路径（默认 data/rules/rule_base.json）")
    ap.add_argument("--max-per-rule", type=int, default=inference.MAX_PER_RULE_DEFAULT,
                    help=f"单规则产出上限（默认 {inference.MAX_PER_RULE_DEFAULT}，防路径爆炸）")
    ap.add_argument("--dry-run", action="store_true", help="只统计不落盘")
    args = ap.parse_args()

    logger = get_logger("build_inferred_relations")
    settings = get_settings()
    snap_dir = _resolve_snapshot_dir(settings, args.version)
    logger.info(f"规则推理：快照 {snap_dir.name}（dry_run={args.dry_run}）")

    report = inference.build_inferred_relations(
        snap_dir,
        rules_path=Path(args.rules) if args.rules else None,
        max_per_rule=args.max_per_rule,
        dry_run=args.dry_run,
    )

    logger.info(f"输入关系 {report.input_relation_count} 条"
                f"（跳过待审核 {report.skipped_pending_review} 条），"
                f"产出推理关系 {report.output_count} 条")
    for item in report.per_rule:
        note = f"  ← {item['note']}" if item.get("note") else ""
        logger.info(f"  {item['rule_id']} {item['name']}：{item['produced']} 条{note}")
    if report.truncated_rules:
        logger.warning(f"触发单规则上限被截断：{', '.join(report.truncated_rules)}")
    if not args.dry_run:
        logger.info(f"产物：{snap_dir / 'inferred_relations.json'}（sha256 {report.output_sha256[:16]}…）"
                    f"；报告：{snap_dir / 'inference_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
