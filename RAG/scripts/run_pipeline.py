"""F09 + F11 一键离线流水线：治理快照 → 规则推理固化 → 文本/向量索引。

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
from data.snapshot import inference as inference_build  # noqa: E402
from data.index import build as index_build  # noqa: E402
from lib.logging_util import get_logger  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="RAG 离线流水线 F09→推理固化→F11")
    ap.add_argument("--snapshot-version", default=None, help="治理版本（默认自动生成新版本）")
    ap.add_argument("--no-embeddings", action="store_true", help="跳过向量索引")
    ap.add_argument("--no-inference", action="store_true",
                    help="跳过规则推理固化（只产快照与索引，在线侧将退回纯原始图谱）")
    args = ap.parse_args()

    settings = get_settings()
    logger = get_logger("rag.pipeline", settings.log_dir)

    snap = governance.run_governance(settings, version=args.snapshot_version, logger=logger)
    # 规则推理固化（P2，docs/RAG_v2/RAG规则推理移植-需求与设计.md §4）：
    # 在快照之后、索引之前，产物落在快照目录内（inferred_relations.json + inference_report.json）。
    # 失败不阻断索引构建（在线侧缺文件即降级为纯原始图谱），但日志会明确报错。
    if args.no_inference:
        logger.info("按 --no-inference 跳过规则推理固化")
    else:
        try:
            report = inference_build.build_inferred_relations(snap)
            logger.info(f"规则推理固化完成：产出 {report.output_count} 条推理关系"
                        f"（规则库 {report.rules_sha256[:12]}…）")
        except Exception as exc:  # noqa: BLE001 —— 明确记录并继续，索引不依赖推理产物
            logger.error(f"规则推理固化失败（不影响索引构建）：{exc}")
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
