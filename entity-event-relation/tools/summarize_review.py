#!/usr/bin/env python
"""
抽样核验（B 组）的汇总：把人工判定表算成**可引用的数字**。

**为什么要有这个工具。** `docs/抽样判定规范.md` §8 把流程写到最后一步是"按分层与整体算精确率、
'无法判断'的比例，并附 Wilson 置信区间"，然后如实记了一句"**汇总脚本还没有**：目前填完的 CSV
需要手工统计"。手工统计的问题不是慢，而是**口径会漂**：分母算不算"无法判断"、分层条数少时
要不要照样给区间、错误类型怎么归——每个人算一遍都可能不一样，而这份数字是阶段三 E1/E2/E3
三节的共同输入，口径漂了后面全跟着漂。

**这里的口径（与规范 §3/§6/§8 对齐）**：

- 三档只有对/错/无法判断，**没有"部分对"**：一条记录只要一个字段写错，整条就是"错"。
- 精确率 = 对 / (对 + 错)，**"无法判断"不进分母**（它不是"错"，只是核不清）；
  但它的比例要单独报出来——规范 §6 明确说"无法判断的比例本身就是个要报出去的数"。
- 置信区间用 **Wilson 区间**（不是正态近似）：小样本、比例接近 1 或 0 时正态近似会给出
  越界的区间（比如上限超过 100%），而分层只有 8 条这种量级。
- **分层的区间比整体宽得多**，规范 §8 的原话是"分层数字只能当相对比较"，所以输出里
  把条数一起印出来，别只看百分比。
- 错误类型按 `判据` 的**固定前缀**统计（规范 §3 要求判据以 `时间：`/`主动方：` 这类前缀开头，
  就是为了这里能机器汇总）。一条记录的判据可以带多个前缀（时间与结果都错），所以
  前缀计数之和**大于**错误条数是正常的。

**用法**

    python tools/summarize_review.py                      # 打印汇总
    python tools/summarize_review.py --json review.json    # 落盘，供两版对比
    python tools/summarize_review.py --csv <别的表>        # 换一份判定表

**跨版本对比**：改完重跑后，用 `tools/sample_for_review.py --compare` 对同一批定位重新定位，
重新判一遍，再用本工具算第二次的数字。**区间重叠就不要声称"变好了"**（整改方案定的口径）。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TABLE = MODULE_ROOT / "evaluation" / "review" / "sample_20260926_with_source.csv"

VERDICT_COLUMN = "判定（对/错/无法判断）"
REASON_COLUMN = "判据"
LAYER_COLUMN = "分层"

VERDICT_OK = "对"
VERDICT_BAD = "错"
VERDICT_UNKNOWN = "无法判断"
KNOWN_VERDICTS = (VERDICT_OK, VERDICT_BAD, VERDICT_UNKNOWN)

#: 规范 §3 规定的判据前缀。**按最长优先匹配**：`防守方` 与（不存在的）子串关系不大，
#: 但 `关系类型`/`关系方向` 这类同前缀的必须先匹配长的，否则会被短的吃掉。
REASON_PREFIXES = (
    "事件本身", "关系类型", "关系方向", "实体类型", "附属性字段", "上下文不足",
    "角色/组织", "主动方", "防守方", "时间", "地点", "结果",
)


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple:
    """
    比例的 Wilson 置信区间（含端点，均为 0~1 的闭区间）。

    为什么不用 `p ± z·sqrt(p(1-p)/n)`：分层只有 8 条时，正态近似给出的上限会超过 1
    （也出现过下限为负），而那种数字一旦写进文档就会被当成真的。Wilson 区间对
    "全部判对""一条没判对"这两种极端也仍然落在 [0, 1] 内。
    """
    if total <= 0:
        return 0.0, 0.0
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def extract_reason_prefixes(reason: str) -> list:
    """
    从判据里取出规范 §3 的固定前缀（按出现顺序，可重复）。

    只认"前缀 + 冒号"的形式（`时间：`/`地点:`），不认正文里出现的同名词——
    否则"结果"这两个字在解释里随便出现一次就会被当成一个错误类型。
    """
    text = reason or ""
    found = []
    for match in re.finditer(r"([^；;，,。\s]{2,6}?)[:：]", text):
        token = match.group(1)
        for prefix in sorted(REASON_PREFIXES, key=len, reverse=True):
            if token.endswith(prefix) and len(token) <= len(prefix) + 2:
                found.append(prefix)
                break
    return found


def categorize(layer: str) -> str:
    """
    分层名的"类"（冒号前那一级）：`关系:事件-人物` → `关系`。

    为什么要有这一级：事件层按朝代切成 8 条一层，单看每层都是"8 条里错 2 条"这种量级，
    区间宽到没法比较；而 `关系 / 事件 / 实体` 三类的合并数字才是"这类问题有多大"的读数，
    也正好对应阶段三的 E2 / E3 / E1 三节。没有冒号的分层名（历史表或手工表）原样返回。
    """
    return (layer or "").split(":")[0].split("：")[0]


def summarize(rows: list) -> dict:
    """把判定表算成整体 + 分类 + 分层的数字。"""
    counts = Counter()
    per_layer = {}
    per_category = {}
    reason_counts = Counter()
    unknown_reasons = Counter()
    for row in rows:
        verdict = (row.get(VERDICT_COLUMN) or "").strip()
        layer = (row.get(LAYER_COLUMN) or "").strip() or "（未分层）"
        counts[verdict] += 1
        per_layer.setdefault(layer, Counter())[verdict] += 1
        per_category.setdefault(categorize(layer), Counter())[verdict] += 1
        prefixes = extract_reason_prefixes(row.get(REASON_COLUMN))
        if verdict == VERDICT_BAD:
            for prefix in prefixes:
                reason_counts[prefix] += 1
        elif verdict == VERDICT_UNKNOWN:
            unknown_reasons[prefixes[0] if prefixes else "（未写前缀）"] += 1

    def bucket_stats(bucket: Counter) -> dict:
        ok, bad, unknown = bucket[VERDICT_OK], bucket[VERDICT_BAD], bucket[VERDICT_UNKNOWN]
        judged = ok + bad
        low, high = wilson_interval(ok, judged)
        return {
            "rows": sum(bucket.values()), "ok": ok, "bad": bad, "unknown": unknown,
            "precision": (ok / judged) if judged else None,
            "wilson_low": low, "wilson_high": high,
            "unknown_ratio": unknown / sum(bucket.values()) if bucket else 0.0,
        }

    ok, bad, unknown = counts[VERDICT_OK], counts[VERDICT_BAD], counts[VERDICT_UNKNOWN]
    judged = ok + bad
    low, high = wilson_interval(ok, judged)
    overall = {
        "table_rows": len(rows),
        "ok": ok, "bad": bad, "unknown": unknown, "judged": judged,
        "precision": (ok / judged) if judged else None,
        "wilson_low": low, "wilson_high": high,
        "unknown_ratio": (unknown / len(rows)) if rows else 0.0,
    }

    categories = [dict(category=name, **bucket_stats(bucket))
                  for name, bucket in sorted(per_category.items(), key=lambda item: -sum(item[1].values()))]
    layers = [dict(layer=name, **bucket_stats(bucket))
              for name, bucket in sorted(per_layer.items(), key=lambda item: -sum(item[1].values()))]

    return {
        **overall,
        "error_types": dict(reason_counts.most_common()),
        "unknown_reasons": dict(unknown_reasons.most_common()),
        "categories": categories,
        "layers": layers,
    }


def load_rows(path: Path) -> list:
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def print_summary(report: dict, table: Path) -> None:
    print("=" * 74)
    print(f"抽样核验汇总（B 组）: {table}")
    print("=" * 74)
    print(f"总行数 {report['table_rows']}：对 {report['ok']} / 错 {report['bad']}"
          f" / 无法判断 {report['unknown']}（占 {report['unknown_ratio']:.1%}）")
    if report["precision"] is None:
        print("可分层的判定一条都没有，算不出精确率")
        return
    print(f"\n【抽样精确率】{report['precision']:.1%}"
          f"（{report['ok']}/{report['judged']}，分母不含'无法判断'）")
    print(f"  Wilson 95% 区间 [ {report['wilson_low']:.1%} , {report['wilson_high']:.1%} ]")
    print("  口径：只测'抽出来的对不对'；'漏了什么'要有参考集才知道（规范 §2）")

    print("\n【分类】（冒号前那一级：关系 / 事件 / 实体，对应阶段三 E2 / E3 / E1）")
    for item in report["categories"]:
        rate = "—" if item["precision"] is None else f"{item['precision']:.1%}"
        print(f"  {item['category']}: 精确率 {rate}"
              f"（{item['ok']}/{item['ok'] + item['bad']}，行数 {item['rows']}，"
              f"无法判断 {item['unknown']}）  Wilson [ {item['wilson_low']:.0%} , {item['wilson_high']:.0%} ]")

    print("\n【分层】（条数少的层区间很宽，只能当相对比较，规范 §8）")
    print(f"  {'层':<22} {'行数':>4} {'对':>4} {'错':>4} {'无法判断':>6} {'精确率':>8}  {'Wilson 95%':>18}")
    for item in report["layers"]:
        rate = "—" if item["precision"] is None else f"{item['precision']:.1%}"
        interval = f"[{item['wilson_low']:.0%}, {item['wilson_high']:.0%}]"
        print(f"  {item['layer']:<22} {item['rows']:>4} {item['ok']:>4} {item['bad']:>4}"
              f" {item['unknown']:>6} {rate:>8}  {interval:>18}")

    if report["error_types"]:
        print("\n【错误类型】（按判据的固定前缀统计；一条记录可带多个前缀，故之和 > 错数）")
        for prefix, count in report["error_types"].items():
            print(f"  {prefix}: {count}")
    if report["unknown_reasons"]:
        print("\n【无法判断的原因】（这些行是参考集完整度 c 的输入之一，规范 §6）")
        for prefix, count in report["unknown_reasons"].items():
            print(f"  {prefix}: {count}")


def main() -> int:
    parser = argparse.ArgumentParser(description="抽样核验（B 组）判定表的汇总")
    parser.add_argument("--csv", default=str(DEFAULT_TABLE), help="填好判定的表（CSV）")
    parser.add_argument("--json", default=None, help="把汇总写到这个路径，供两版对比")
    args = parser.parse_args()

    table = Path(args.csv)
    if not table.is_file():
        print(f"判定表不存在: {table}")
        return 2
    rows = load_rows(table)
    if not rows:
        print(f"判定表是空的: {table}")
        return 2

    verdicts = [(row.get(VERDICT_COLUMN) or "").strip() for row in rows]
    missing = sum(1 for verdict in verdicts if verdict not in KNOWN_VERDICTS)
    if missing:
        # 不静默跳过：未填/写成别的一律当"没填"，直接报出来（否则分母会莫名其妙变小）
        print(f"警告：{missing} 行的判定列不是 对/错/无法判断 之一，它们不计入任何一档")

    report = summarize(rows)
    report["source"] = str(table)
    report["unrecognized_verdicts"] = missing
    print_summary(report, table)

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n汇总已写入: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
