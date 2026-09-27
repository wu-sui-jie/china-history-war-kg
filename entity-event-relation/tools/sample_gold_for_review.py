#!/usr/bin/env python
"""
从**参考集**（不是产物）抽样，生成"旧标注判定实验"的核验表。

**这一步是 C 组的第 0 步**（`docs/参考集重建规范.md` §4）：抽 30~50 条旧标注回原文核验，
统计"正确 / 漏标 / 错标 / 无法判断"，**用一个数字决定旧标注是可修还是只能当词表**——
不要凭感觉选"修旧的"还是"重标"。

**为什么单独一个工具，而不是给 `sample_for_review.py` 加开关。** 那个工具抽的是**产物**
（定位键按产物的字段造、`--compare` 靠它跨版本重定位）；这里抽的是**标注**（对象不同、
没有"跨版本重定位"这回事）。混在一个工具里会让"抽谁"变成隐式行为，而这两件事实测经常
被人搞混（"抽样精确率"与"旧标注判定"是两个不同的问题）。

**产出的 JSON 直接喂给现成的定位器**（`tools/expand_review_context.py`），
由它把"原文上下文"补上并标出证据位置——那份上下文扩全的机器是 B 组判定时验证过的，直接复用：

    python tools/sample_gold_for_review.py --total 40 --output evaluation/review/gold_probe.json
    python tools/expand_review_context.py --sample evaluation/review/gold_probe.json

第二步会生成 `gold_probe_with_source.csv`（判定/判据/备注三列留空，口径见 `抽样判定规范.md`）。
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
    RELATION_CATEGORY_LABELS,
    load_annotations,
    event_key,
    organization_key,
    person_key,
    place_key,
    relation_key,
)
from tools.sample_for_review import stratified_sample  # noqa: E402  # 选层算法只那一份
from war_extraction.utils.normalizer import Normalizer  # noqa: E402

DEFAULT_DIR = MODULE_ROOT / "data" / "annotations"
DEFAULT_OUT = MODULE_ROOT / "evaluation" / "review" / "gold_probe.json"


def collect_strata(gold: dict, dynasty_top: int = 8) -> dict:
    """与 `sample_for_review.collect_strata` 同形的分层（层名一致，汇总时可对齐看）。"""
    normalizer = Normalizer()
    strata: dict = {}

    for row in gold["places"]:
        name = (row.get("geo_name") or "").strip()
        if not name:
            continue
        strata.setdefault("实体:地点", []).append((
            place_key(name, row.get("DynastyName"), row.get("modern_name")),
            f"地点 {name}（现代名 {row.get('modern_name') or '—'}，朝代 {row.get('DynastyName') or '—'}）",
            "",
        ))
    for row in gold["persons"]:
        name = (row.get("PersonName") or "").strip()
        if not name:
            continue
        strata.setdefault("实体:人物", []).append((
            person_key(name, row.get("DynastyName")),
            f"人物 {name}（角色 {row.get('Role') or '—'}，朝代 {row.get('DynastyName') or '—'}，"
            f"组织 {row.get('OrgName') or '—'}）",
            "",
        ))
    for row in gold["organizations"]:
        name = (row.get("OrgName") or "").strip()
        if not name:
            continue
        strata.setdefault("实体:组织", []).append((
            organization_key(name, row.get("DynastyName")),
            f"组织 {name}（类型 {row.get('OrgType') or '—'}，朝代 {row.get('DynastyName') or '—'}）",
            "",
        ))

    dynasty_counts = {}
    for row in gold["events"]:
        dynasty = (row.get("DynastyName") or "（不详）").strip()
        dynasty_counts[dynasty] = dynasty_counts.get(dynasty, 0) + 1
    top = {name for name, _count in sorted(dynasty_counts.items(),
                                           key=lambda item: (-item[1], item[0]))[:dynasty_top]}
    for row in gold["events"]:
        name = (row.get("EventName") or "").strip()
        if not name:
            continue
        dynasty = (row.get("DynastyName") or "（不详）").strip()
        layer = f"事件:{dynasty}" if dynasty in top else "事件:其他朝代"
        strata.setdefault(layer, []).append((
            event_key(normalizer, name, row.get("DynastyName"), row.get("StartDate"), row.get("Place")),
            f"事件 {name}（{row.get('DynastyName') or '—'} {row.get('StartDate') or '—'}，"
            f"地点 {row.get('Place') or '—'}，主动方 {row.get('Aggressor') or '—'}，"
            f"结果 {row.get('Result') or '—'}）",
            "",
        ))

    for attribute, (label, layer) in RELATION_CATEGORY_LABELS.items():
        for row in gold["relations"][label]:
            head = (row.get("head") or "").strip()
            tail = (row.get("tail") or "").strip()
            if not head or not tail:
                continue
            strata.setdefault(layer, []).append((
                relation_key(attribute, head, row.get("relation"), tail),
                f"{head} —[{row.get('relation') or '—'}]→ {tail}",
                "",
            ))
    return strata


def main() -> int:
    parser = argparse.ArgumentParser(description="旧标注判定实验：从参考集分层抽样")
    parser.add_argument("--annotations", default=str(DEFAULT_DIR))
    parser.add_argument("--total", type=int, default=40, help="抽样总数（规范建议 30~50）")
    parser.add_argument("--seed", type=int, default=20260927)
    parser.add_argument("--min-per-layer", type=int, default=4)
    parser.add_argument("--output", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    gold = load_annotations(Path(args.annotations))
    strata = collect_strata(gold)
    records = stratified_sample(strata, args.total, args.seed, args.min_per_layer)
    if not records:
        print("没有抽到任何记录（参考集为空？）")
        return 2

    payload = {
        "note": ("旧标注判定实验的抽样表（C 组第 0 步）。**对象是参考集，不是产物**——"
                 "用来决定旧标注是「可修」还是「只能当词表」。下一步用 "
                 "`tools/expand_review_context.py --sample <本文件>` 把原文上下文补上，"
                 "判定三档与判据写法见 `docs/抽样判定规范.md`。"),
        "source_annotations": str(args.annotations),
        "seed": args.seed,
        "records": records,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    from collections import Counter
    layers = Counter(record["分层"] for record in records)
    print(f"抽样 {len(records)} 条 → {out}")
    for layer, count in layers.most_common():
        print(f"  {layer}: {count}")
    print("\n下一步（补原文上下文，生成可判定的表）：")
    print(f"  python tools/expand_review_context.py --sample {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
