#!/usr/bin/env python
"""
汇总**参考集核验表**的填写结果，并可做两人一致率（IAA）。

**为什么需要它。** `tools/build_draft_annotation_table.py` 出的表填完之后，如果不汇总，
下面三件事都答不出来：① 这批草稿有多少条被删（= 模型那批多余记录的真实规模）；
② 修正集中在哪些字段（= 该回头改提示词的地方）；③ 补漏补了多少条（= 参考集完整度 `c` 的输入之一）。
`tools/summarize_review.py` 汇总的是 **B 组判定表**（对/错/无法判断，口径不同），**不要混用**。

**口径**（与 `docs/参考集重建规范.md` §4 第 ③ 步对齐）：

- `核验结论` 四档：`保留` / `删除` / `修改` / `新增`；空着 = 还没处理（**单独报出来**，
  不然"没填"会被当成"保留"混进分母）。
- **草稿保留率 = 保留 /（保留 + 删除）**——它是"草稿有多少可用"的读数，
  与 B 组的抽样精确率不是一回事（那边判的是产物、三档是对/错/无法判断）。
- `修正内容` 按 `字段=新值` 解析（分号分隔），统计**哪些字段最常被改**。
- `--compare <另一份填好的表>`：按 `定位键` 配对算一致率，并列出不一致行——
  这就是 `docs/参考集重建规范.md` 第 ⑥ 步的 IAA。

用法：

    python tools/summarize_rebuild_table.py evaluation/review/draft_明_with_source.csv
    python tools/summarize_rebuild_table.py <我填的.csv> --compare <第二个人填的.csv> --json <留档>
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1]
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from tools.summarize_review import wilson_interval  # noqa: E402  # 区间口径只那一份

VERDICT_COLUMN = "核验结论"
FIX_COLUMN = "修正内容"
KEY_COLUMN = "定位键"
LAYER_COLUMN = "分层"
KNOWN_VERDICTS = ("保留", "删除", "修改", "新增")


def _field_names(text: str) -> list:
    """从 `修正内容` 里取出被改的字段名（`字段=新值`，多个用分号或逗号分隔）。"""
    names = []
    for part in (text or "").replace("；", ";").replace("，", ";").split(";"):
        part = part.strip()
        if "=" in part:
            names.append(part.split("=", 1)[0].strip())
    return names


def summarize(rows: list) -> dict:
    verdicts = Counter()
    per_layer = {}
    fields = Counter()
    for row in rows:
        verdict = (row.get(VERDICT_COLUMN) or "").strip()
        layer = (row.get(LAYER_COLUMN) or "").strip() or "（未分层）"
        verdicts[verdict] += 1
        per_layer.setdefault(layer, Counter())[verdict] += 1
        for name in _field_names(row.get(FIX_COLUMN)):
            fields[name] += 1

    kept, deleted = verdicts["保留"], verdicts["删除"]
    judged = kept + deleted
    low, high = wilson_interval(kept, judged)
    return {
        "rows": len(rows),
        "verdicts": dict(verdicts.most_common()),
        "unfilled": verdicts[""],
        "kept": kept, "deleted": deleted, "modified": verdicts["修改"],
        "added_by_human": verdicts["新增"],
        "draft_keep_rate": (kept / judged) if judged else None,
        "wilson_low": low, "wilson_high": high,
        "changed_fields": dict(fields.most_common(15)),
        "layers": {layer: dict(bucket.most_common())
                   for layer, bucket in sorted(per_layer.items(),
                                               key=lambda item: -sum(item[1].values()))},
    }


def agreement(rows_a: list, rows_b: list) -> dict:
    """两份填好的表按 `定位键` 配对算一致率（第 ⑥ 步 IAA）。"""
    by_key_a = {(row.get(KEY_COLUMN) or "").strip(): (row.get(VERDICT_COLUMN) or "").strip()
                for row in rows_a}
    by_key_b = {(row.get(KEY_COLUMN) or "").strip(): (row.get(VERDICT_COLUMN) or "").strip()
                for row in rows_b}
    shared = sorted(set(by_key_a) & set(by_key_b))
    both_filled = [key for key in shared if by_key_a[key] and by_key_b[key]]
    agree = [key for key in both_filled if by_key_a[key] == by_key_b[key]]
    disagreements = [{"定位键": key, "甲": by_key_a[key], "乙": by_key_b[key]} for key in both_filled
                     if by_key_a[key] != by_key_b[key]]
    return {
        "rows_a": len(by_key_a), "rows_b": len(by_key_b),
        "shared_keys": len(shared),
        "only_in_a": len(set(by_key_a) - set(by_key_b)),
        "only_in_b": len(set(by_key_b) - set(by_key_a)),
        "both_filled": len(both_filled),
        "agreed": len(agree),
        "agreement_rate": (len(agree) / len(both_filled)) if both_filled else None,
        "disagreements": disagreements[:50],
    }


def _load(path: Path) -> list:
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _load_dir(path: Path) -> list:
    rows = []
    for part in sorted(path.glob("*.csv")):
        rows.extend(_load(part))
    return rows


def print_summary(result: dict, path: Path) -> None:
    print("=" * 74)
    print(f"参考集核验表汇总: {path}")
    print("=" * 74)
    print(f"总行数 {result['rows']}：{result['verdicts']}")
    if result["unfilled"]:
        print(f"  **还有 {result['unfilled']} 行没填核验结论**——它们不进任何分母，"
              "别把「没填」读成「保留」")
    if result["draft_keep_rate"] is None:
        print("  没有一行填了结论，算不出保留率")
        return
    print(f"\n【草稿保留率】{result['draft_keep_rate']:.1%}"
          f"（保留 {result['kept']} / 保留+删除 {result['kept'] + result['deleted']}）")
    print(f"  Wilson 95% 区间 [ {result['wilson_low']:.1%} , {result['wilson_high']:.1%} ]")
    print(f"  另外：修改 {result['modified']} 条，人工**新增（补漏）** {result['added_by_human']} 条")
    print("\n【分层】")
    for layer, bucket in result["layers"].items():
        print(f"  {layer}: {bucket}")
    if result["changed_fields"]:
        print("\n【最常被改的字段】（= 该回头改提示词的地方）")
        for name, count in result["changed_fields"].items():
            print(f"  {name}: {count}")


def main() -> int:
    parser = argparse.ArgumentParser(description="参考集核验表的汇总与 IAA")
    parser.add_argument("table", help="填好的核验表（CSV，或按层拆分的目录）")
    parser.add_argument("--compare", default=None, help="第二个人填的表，用来算一致率")
    parser.add_argument("--json", default=None, help="把汇总写到这个路径")
    args = parser.parse_args()

    path = Path(args.table)
    rows = _load_dir(path) if path.is_dir() else (_load(path) if path.is_file() else None)
    if rows is None:
        print(f"表不存在: {path}")
        return 2
    result = {"source": str(path), **summarize(rows)}
    print_summary(result, path)

    if args.compare:
        other = Path(args.compare)
        if not other.is_file():
            print(f"第二份表不存在: {other}")
            return 2
        iaa = agreement(rows, _load(other))
        result["iaa"] = iaa
        print(f"\n【两人一致率（IAA）】共有定位键 {iaa['shared_keys']}，"
              f"两侧都填的 {iaa['both_filled']}，一致 {iaa['agreed']}"
              f"（{iaa['agreement_rate']:.0%}）"
              if iaa["agreement_rate"] is not None else "\n【两人一致率】两侧都没有可比的填写")
        print(f"  只在甲表有 {iaa['only_in_a']}，只在乙表有 {iaa['only_in_b']}"
              f"（新增行会造成差异，这是正常的）")
        for item in iaa["disagreements"][:10]:
            print(f"    不一致: {item['定位键']}  甲={item['甲']}  乙={item['乙']}")
        print("  做法：不一致的逐条仲裁，结论写回 data/annotations/README.md 的口径样例")

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n汇总已写入: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
