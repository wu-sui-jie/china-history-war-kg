#!/usr/bin/env python
"""
事件配对语义约束的复核：把"开约束 / 关约束"两次配对的差额导出来，交人工判定。

**为什么需要它。** 事件配对是三层指标的**共同上游**（事件 TP、实体的事件中心过滤、
关系 gold 三元组），所以配对一收紧，三层指标同时变。第一轮给事件配对加了三条语义约束
（朝代 / 起始年份容差 / 首个地点）之后，配对从 283 对掉到 176 对，事件 F1 从 41.75% 掉到
25.98%——但这里面"**拦掉了错误的配对**"与"**拦掉了正确的配对**"混在同一个数字里，
只看 F1 分不开。

**怎么判。** 本工具把"关约束时会配上、开约束后没配上"的配对逐对导出来，附上相似度、
拒绝原因与两侧的朝代/起始时间/地点字段，按相似度从高到低排——**相似度越高的越可能是被误杀的
正确配对**，最该先判。人工在 CSV 的"判定"列里填 `该配 / 不该配 / 无法判断`，
统计一下就能决定是否放宽 `event_year_tolerance`（默认 30 年）或"地点互相包含"这一条。

用法：

    python tools/event_pairing_review.py                       # 导出到 evaluation/review/
    python tools/event_pairing_review.py --limit 100            # 只导前 100 对（默认 300）
    python tools/event_pairing_review.py --pred <其他产物.json>

**边界**：它只导出候选、不做判断。判定要回原文或按史实，工具给不了那个答案。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from war_extraction.config import current_time_tag  # noqa: E402
from war_extraction.evaluation import OptimalEvaluator  # noqa: E402

MODULE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRED = MODULE_ROOT / "output" / "中国历代战争简史" / "9_final_all.json"
DEFAULT_CONFIG = MODULE_ROOT / "config" / "eval_config.json"
DEFAULT_ANNOTATIONS = MODULE_ROOT / "data" / "annotations"

COLUMNS = [
    ("pred_event", "预测事件"),
    ("gold_event", "被配上的标注事件"),
    ("similarity", "名称相似度"),
    ("rejection_reason", "被哪条约束拒绝"),
    ("pred_dynasty", "预测朝代"),
    ("gold_dynasty", "标注朝代"),
    ("pred_start", "预测起始时间"),
    ("gold_start", "标注起始时间"),
    ("pred_place", "预测地点"),
    ("gold_place", "标注地点"),
    ("strict_mapped_to", "开约束后该预测事件配到谁"),
    ("判定（该配/不该配/无法判断）", ""),
    ("判据", ""),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="事件配对语义约束复核：导出被约束拆掉的配对")
    parser.add_argument("--pred", default=str(DEFAULT_PRED), help="产物 JSON 路径")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="评估配置 JSON 路径")
    parser.add_argument("--limit", type=int, default=300, help="最多导出多少对（按相似度降序）")
    parser.add_argument("--output", default=None, help="CSV 输出路径")
    parser.add_argument("--json", default=None, help="JSON 输出路径")
    args = parser.parse_args()

    pred_path = Path(args.pred)
    if not pred_path.is_file():
        print(f"产物不存在: {pred_path}")
        return 2
    config_path = Path(args.config)
    eval_config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}

    evaluator = OptimalEvaluator(
        annotation_dir=DEFAULT_ANNOTATIONS,
        relation_threshold=eval_config.get("relation_threshold", 40),
        event_sim_threshold=eval_config.get("event_sim_threshold", 0.35),
        entity_fuzzy_threshold=eval_config.get("entity_fuzzy_threshold", 70),
        event_event_sim_threshold=eval_config.get("event_event_sim_threshold", 0.35),
        event_year_tolerance=eval_config.get("event_year_tolerance", 30),
        enforce_semantic_constraints=eval_config.get("enforce_semantic_constraints", True),
    )

    pred_data = json.loads(pred_path.read_text(encoding="utf-8"))
    pred_events = pred_data.get("events", {}).get("events", [])
    if not pred_events:
        print("产物里没有事件，无法复核")
        return 2

    diff = evaluator.event_mapping_diff(pred_events, limit=args.limit)
    dropped = diff["dropped"]

    output = Path(args.output or (MODULE_ROOT / "evaluation" / "review"
                                 / f"pairing_diff_{current_time_tag()}.csv"))
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[label for _key, label in COLUMNS])
        writer.writeheader()
        for item in dropped:
            writer.writerow({
                label: (item.get(key) if key else "")
                for key, label in COLUMNS
            })

    json_path = Path(args.json) if args.json else output.with_suffix(".json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"source": str(pred_path), **diff}, f, ensure_ascii=False, indent=2)

    reasons = Counter(item["rejection_reason"] for item in dropped)
    print(f"\n关约束配对数: {diff['unconstrained_pairs']}")
    print(f"开约束配对数: {diff['constrained_pairs']}")
    print(f"被约束拆掉的配对: {diff['dropped_count']}（本次导出前 {len(dropped)} 对）")
    print("按拒绝原因分项:")
    for reason, count in reasons.most_common():
        print(f"  {reason}: {count}")
    print("\n相似度最高的 15 对（最可能是被误杀的正确配对，优先判）:")
    for item in dropped[:15]:
        print(f"  {item['similarity']:.2f} | {item['pred_event']} → {item['gold_event']}"
              f" | {item['rejection_reason']}"
              f" | 朝代 {item['pred_dynasty']}/{item['gold_dynasty']}"
              f" | 时间 {item['pred_start']}/{item['gold_start']}")
    print(f"\nCSV（填「判定」列）: {output}")
    print(f"JSON: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
