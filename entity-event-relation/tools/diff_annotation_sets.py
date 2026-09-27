#!/usr/bin/env python
"""
两套参考集的**逐条对比**（旧 vs 新，或改前 vs 改后）。

**为什么需要它。** 改参考集等于改指标分母，而"分母变了"必须能一眼看出来：否则下一轮看到
指标动了，会把它误读成"模型退化了"。这个项目**已经吃过一次学费**（`data/annotations/README.md`
§四记着"曾经把产物换代当成匹配抖动"），所以改标注的前后对比要有机械工具，不能靠人回忆。

**怎么对比。** 按**稳定 ID**（`tools/annotation_io.py`）配对——它只跟"名称 + 朝代"（关系是三元组）
有关，所以把某条的地点/时间改对了，仍然认得出是同一条，只会显示成"字段变更"而不是"删一条加一条"。
两边都没写 `annotation_id` 时，工具现算（口径一致，结果相同）。

**输出三档**：新增 / 删除 / 字段变更（逐字段列出旧→新）。默认只打印摘要与样例，
`--json` 落盘完整清单（给留档用）。

用法：

    python tools/diff_annotation_sets.py --old data/annotations --new /tmp/ann_v2
    python tools/diff_annotation_sets.py --old data/annotations --new /tmp/ann_v2 \
        --json /tmp/diff.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1]
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from tools.annotation_io import (  # noqa: E402
    ID_PREFIXES,
    RELATION_CATEGORY_LABELS,
    entity_core,
    event_core,
    load_annotations,
    relation_core,
    stable_id,
)
from war_extraction.utils.normalizer import Normalizer  # noqa: E402

#: 不参与"字段变更"比较的键：它们是**记账字段**（ID、证据锚点、定位线索），
#: 改了它们不算内容变更——否则每次重跑回填都会显示成"全表都改了"，
#: 把真正的标注改动埋掉（`data/annotations/README.md` §四 那类误读）。
_BOOKKEEPING = {"annotation_id", "evidence", "evidence_start", "evidence_end", "locate_status",
                "evidence_hint"}


def _index(gold: dict) -> tuple:
    """
    稳定 ID → (类别, 行)，外加"存量 ID 与现算 ID 不一致"的告警。

    **ID 一律现算，不信行里存的那个。** 存量的 `annotation_id` 可能是另一版口径生成的、
    或者这一行被改过名称后没重算——一旦直接拿它配对，就会出现"一侧有 ID、一侧没有"
    或"同一内容两个 ID"的假差异（diff 会报成"删一条加一条"，把真实的字段变更淹掉）。
    存量值只用来核对：**不一致就说明这条的名称/朝代被改过**，那是要人看一眼的事。
    """
    normalizer = Normalizer()
    index = {}
    mismatched = []

    def add(category, annotation_id, row):
        stored = row.get("annotation_id")
        if stored and stored != annotation_id:
            mismatched.append({"存": stored, "算": annotation_id, "摘要": _label(row)})
        index[annotation_id] = (category, row)

    for row in gold["events"]:
        add("events", stable_id(ID_PREFIXES["events"], event_core(row, normalizer)), row)
    for kind in ("places", "persons", "organizations"):
        for row in gold[kind]:
            add(kind, stable_id(ID_PREFIXES[kind], entity_core(kind, row, normalizer)), row)
    for attribute, (label, _layer) in RELATION_CATEGORY_LABELS.items():
        for row in gold["relations"][label]:
            add("relations", stable_id(ID_PREFIXES["relations"], relation_core(attribute, row, normalizer)), row)
    return index, mismatched


def _label(row: dict) -> str:
    for key in ("EventName", "geo_name", "PersonName", "OrgName"):
        if row.get(key):
            return str(row[key])
    return f"{row.get('head')} —[{row.get('relation')}]→ {row.get('tail')}"


def compare(old: dict, new: dict) -> dict:
    old_index, old_mismatch = _index(old)
    new_index, new_mismatch = _index(new)
    added, removed, changed = [], [], []
    for annotation_id in sorted(new_index.keys() - old_index.keys()):
        added.append({"ID": annotation_id, "类别": new_index[annotation_id][0],
                      "摘要": _label(new_index[annotation_id][1])})
    for annotation_id in sorted(old_index.keys() - new_index.keys()):
        removed.append({"ID": annotation_id, "类别": old_index[annotation_id][0],
                        "摘要": _label(old_index[annotation_id][1])})
    for annotation_id in sorted(old_index.keys() & new_index.keys()):
        old_row, new_row = old_index[annotation_id][1], new_index[annotation_id][1]
        fields = {}
        for key in sorted((set(old_row) | set(new_row)) - _BOOKKEEPING):
            if old_row.get(key) != new_row.get(key):
                fields[key] = [old_row.get(key), new_row.get(key)]
        if fields:
            changed.append({"ID": annotation_id, "类别": new_index[annotation_id][0],
                            "摘要": _label(new_row), "字段": fields})
    return {
        "old_rows": len(old_index), "new_rows": len(new_index),
        "added": added, "removed": removed, "changed": changed,
        "unchanged": len(old_index & new_index.keys()) - len(changed),
        "stored_id_mismatch": {"old": old_mismatch, "new": new_mismatch},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="两套参考集逐条对比（按稳定 ID 配对）")
    parser.add_argument("--old", required=True, help="旧参考集目录")
    parser.add_argument("--new", required=True, help="新参考集目录")
    parser.add_argument("--json", default=None, help="把完整清单写到这个路径")
    parser.add_argument("--samples", type=int, default=10, help="每档打印几条样例")
    args = parser.parse_args()

    for label, path in (("旧", args.old), ("新", args.new)):
        if not Path(path).is_dir():
            print(f"{label}参考集目录不存在: {path}")
            return 2

    result = compare(load_annotations(Path(args.old)), load_annotations(Path(args.new)))

    print("=" * 74)
    print(f"参考集对比: {args.old} → {args.new}")
    print("=" * 74)
    print(f"行数: 旧 {result['old_rows']} → 新 {result['new_rows']}")
    print(f"新增 {len(result['added'])} / 删除 {len(result['removed'])} / "
          f"字段变更 {len(result['changed'])} / 未变 {result['unchanged']}")
    mismatch = result["stored_id_mismatch"]
    for side, rows in mismatch.items():
        if rows:
            print(f"\n[{side} 集的存量 annotation_id 与现算不一致] {len(rows)} 条"
                  f"——说明那些记录的名称/朝代被改过（ID 是内容派生的），看一眼是否预期：")
            for item in rows[:5]:
                print(f"  存 {item['存']} / 算 {item['算']}：{item['摘要']}")
    for key, title in (("added", "新增（新集里有、旧集没有）"),
                       ("removed", "删除（旧集里有、新集没有）"),
                       ("changed", "字段变更（同一条改了字段）")):
        rows = result[key]
        if not rows:
            continue
        print(f"\n[{title}] 共 {len(rows)} 条")
        for item in rows[:args.samples]:
            print(f"  {item['ID']} [{item['类别']}] {item['摘要']}")
            for field, (before, after) in (item.get("字段") or {}).items():
                print(f"      {field}: {before!r} → {after!r}")

    print("\n提醒：改参考集的改前改后指标要逐字段对照（除 metadata.evaluated_at），"
          "并把结论记进 docs/项目审查与修复历史.md——这是 README §四的流程。")
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"完整清单已写入: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
