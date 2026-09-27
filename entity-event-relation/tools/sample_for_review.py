#!/usr/bin/env python
"""
分层抽样导出：把产物里的一批记录导成人工核验用的表格。

**它解决的是"参考集不可信时怎么判断改好没改好"。** 现行 gold 是多模型分片汇总、
未经人工处理的结果（整改方案 2.4.1），拿它当准确性分母测的其实是"两套流程之间的分歧"。
而抽样人工精确率不依赖参考集：从产物里分层随机抽一批记录，回原文逐条判"对/错/无法判断"，
得到的是精确率的无偏估计——这既是"改前改后怎么比"的基准（阶段 1 附二），
也是"错在哪一类"的优先级依据。

**稳定定位（跨版本可比的关键）**：每条记录的定位键不用数组下标（换版本就错位），
用"记录身份"——事件用「归一名称 + 朝代 + 起始时间 + 首个地点」，
实体用「类型 + 名称 + 朝代」，关系用「事件名 + 关系名 + 端名」。
所以 `--compare` 能把**同一批定位**在新产物里重新找出来，逐个核对"还成不成立"。

用法：

    # 抽 350 条，导出 CSV（utf-8-sig，Excel 直接打开）+ JSON
    python tools/sample_for_review.py --output review/sample.csv

    # 改完代码、重新抽取之后：对同一批定位做复核
    python tools/sample_for_review.py --compare review/sample.json --pred <新产物.json>

**局限**：只测精确率，不测召回率——"漏了什么"必须有参考集才知道（见阶段 1 附的说明）。
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from war_extraction.utils.normalizer import Normalizer  # noqa: E402
from tools.annotation_io import (
    event_key,
    organization_key,
    person_key,
    place_key,
    relation_key,
)  # noqa: E402

MODULE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRED = MODULE_ROOT / "output" / "中国历代战争简史" / "9_final_all.json"

#: 人工核验表的列。判定/判据/备注三列留给核验人填写；定位键与上下文是脚本填的。
_REVIEW_COLUMNS = [
    "分层", "定位键", "记录摘要", "原文上下文", "判定（对/错/无法判断）", "判据", "备注",
]

_RELATION_CATEGORIES = {
    "event_place_relations": "关系:事件-地点",
    "event_organization_relations": "关系:事件-组织",
    "event_person_relations": "关系:事件-人物",
    "event_event_relations": "关系:事件-事件",
}


def _events_of(payload: dict) -> list:
    block = payload.get("events") or {}
    return block if isinstance(block, list) else (block.get("events") or [])


def collect_strata(payload: dict, by_dynasty: bool = True, dynasty_top: int = 8) -> dict:
    """
    按分层收集**候选记录**：每层一个列表，元素是 (定位键, 记录摘要, 原文上下文)。

    分层是"按实体类型、事件、关系分别抽"的落地，事件再按朝代细分——纯随机抽样很容易
    全抽到明清这类体量最大的数据，上古/先秦那些真正难的部分抽样不到。
    """
    normalizer = Normalizer()
    entities = payload.get("entities") or {}
    events = _events_of(payload)
    relations = payload.get("relations") or {}

    strata: dict = {}

    for row in entities.get("places") or []:
        name = (row.get("geo_name") or "").strip()
        if not name:
            continue
        key = place_key(name, row.get("DynastyName"), row.get("modern_name"))
        summary = f"地点 {name}（现代名 {row.get('modern_name') or '—'}，朝代 {row.get('DynastyName') or '—'}）"
        strata.setdefault("实体:地点", []).append((key, summary, (row.get("source_text") or "")[:400]))

    for row in entities.get("persons") or []:
        name = (row.get("PersonName") or "").strip()
        if not name:
            continue
        key = person_key(name, row.get("DynastyName"))
        summary = (f"人物 {name}（角色 {row.get('Role') or '—'}，朝代 {row.get('DynastyName') or '—'}，"
                   f"组织 {row.get('OrgName') or '—'}）")
        strata.setdefault("实体:人物", []).append((key, summary, (row.get("source_text") or "")[:400]))

    for row in entities.get("organizations") or []:
        name = (row.get("OrgName") or "").strip()
        if not name:
            continue
        key = organization_key(name, row.get("DynastyName"))
        summary = f"组织 {name}（类型 {row.get('OrgType') or '—'}，朝代 {row.get('DynastyName') or '—'}）"
        strata.setdefault("实体:组织", []).append((key, summary, (row.get("source_text") or "")[:400]))

    dynasty_counts = {}
    for row in events:
        dynasty = (row.get("DynastyName") or "（不详）").strip()
        dynasty_counts[dynasty] = dynasty_counts.get(dynasty, 0) + 1
    top_dynasties = {name for name, _count in
                     sorted(dynasty_counts.items(), key=lambda item: (-item[1], item[0]))[:dynasty_top]}

    for row in events:
        name = (row.get("EventName") or "").strip()
        if not name:
            continue
        key = event_key(normalizer, name, row.get("DynastyName"),
                        row.get("StartDate"), row.get("Place"))
        summary = (f"事件 {name}（{row.get('DynastyName') or '—'} {row.get('StartDate') or '—'}，"
                   f"地点 {row.get('Place') or '—'}，主动方 {row.get('Aggressor') or '—'}，"
                   f"结果 {row.get('Result') or '—'}）")
        dynasty = (row.get("DynastyName") or "（不详）").strip()
        layer = f"事件:{dynasty}" if (by_dynasty and dynasty in top_dynasties) else "事件:其他朝代"
        strata.setdefault(layer, []).append((key, summary, (row.get("source_text") or "")[:400]))

    for attribute, layer in _RELATION_CATEGORIES.items():
        for row in relations.get(attribute) or []:
            if attribute == "event_event_relations":
                event_name = row.get("EventName_A") or ""
                target = row.get("EventName_B") or ""
                end_key = "EventName_B"
            elif attribute == "event_place_relations":
                event_name = row.get("EventName") or ""
                target = row.get("modern_name") or ""
                end_key = "modern_name"
            elif attribute == "event_organization_relations":
                event_name = row.get("EventName") or ""
                target = row.get("OrgName") or ""
                end_key = "OrgName"
            else:
                event_name = row.get("EventName") or ""
                target = row.get("PersonName") or ""
                end_key = "PersonName"
            if not event_name or not target:
                continue
            key = relation_key(attribute, event_name, row.get("relation"), target)
            summary = f"{event_name} —[{row.get('relation') or '—'}]→ {target}"
            strata.setdefault(layer, []).append((key, summary, (row.get("evidence") or "")[:400]))

    _ = end_key  # 端点字段名只用于上面的分支，保留可读性
    return strata


def stratified_sample(strata: dict, total: int, seed: int, min_per_layer: int = 8) -> list:
    """
    分层随机抽样：按层大小比例分配名额，每层至少 `min_per_layer` 条（层内用固定种子洗牌）。

    比例分配保证"大层抽得多"（估计量才有代表性），下限保证小层不会一条都抽不到
    （否则"哪一类最不可信"永远答不上来）。名额不足时从其它层按剩余大小再补。
    """
    rng = random.Random(seed)
    layers = {name: sorted(items) for name, items in strata.items() if items}
    sizes = {name: len(items) for name, items in layers.items()}
    pool_total = sum(sizes.values())
    if pool_total == 0:
        return []

    quota = {}
    for name, size in sizes.items():
        quota[name] = min(size, max(min_per_layer, int(round(total * size / pool_total))))

    # 超出总量时按"超出比例"回缩；不足时补给最大的层
    while sum(quota.values()) > total:
        name = max((n for n in quota if quota[n] > min_per_layer), key=lambda n: quota[n], default=None)
        if name is None:
            name = max(quota, key=lambda n: quota[n])
        quota[name] = max(1, quota[name] - 1)
    while sum(quota.values()) < total:
        candidates = [n for n in quota if quota[n] < sizes[n]]
        if not candidates:
            break
        name = max(candidates, key=lambda n: sizes[n] - quota[n])
        quota[name] += 1

    sampled = []
    for name in sorted(layers):
        items = list(layers[name])
        rng.shuffle(items)
        for key, summary, context in items[:quota[name]]:
            sampled.append({"分层": name, "定位键": key, "记录摘要": summary, "原文上下文": context,
                            "判定（对/错/无法判断）": "", "判据": "", "备注": ""})
    sampled.sort(key=lambda row: (row["分层"], row["定位键"]))
    return sampled


def write_csv(rows: list, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig：Excel 打开中文 CSV 不乱码
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_REVIEW_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def compare_against_new_product(sample_path: Path, new_pred: Path, output: Path) -> dict:
    """
    对同一批**定位键**在新产物里重新定位，输出复核表。

    这是固定评估抽样集的复用方式：被判的对象是"原文里的位置 + 记录身份"，
    不是某个版本的数组下标，所以跨版本可比；参考集怎么换都不影响这条对比线。
    """
    frozen = json.loads(Path(sample_path).read_text(encoding="utf-8"))
    payload = json.loads(Path(new_pred).read_text(encoding="utf-8"))
    strata = collect_strata(payload, by_dynasty=False)
    available = {}
    for layer, items in strata.items():
        for key, summary, context in items:
            available[key] = (layer, summary, context)

    rows = []
    still_present = 0
    for record in frozen.get("records", frozen if isinstance(frozen, list) else []):
        key = record["定位键"]
        hit = available.get(key)
        if hit:
            still_present += 1
        rows.append({
            "分层": record.get("分层", ""),
            "定位键": key,
            "记录摘要": record.get("记录摘要", ""),
            "原文上下文": record.get("原文上下文", ""),
            "新产物是否仍有该定位": "是" if hit else "否",
            "新产物摘要": hit[1] if hit else "",
            "判定（对/错/无法判断）": record.get("判定（对/错/无法判断）", ""),
            "判据": record.get("判据", ""),
            "备注": record.get("备注", ""),
        })

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else _REVIEW_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    summary = {
        "sample": str(sample_path),
        "new_product": str(new_pred),
        "frozen_records": len(rows),
        "still_present": still_present,
        "missing": len(rows) - still_present,
        "note": ("'仍然存在'不等于'仍然正确'——判定列要用新产物重新核一遍；"
                 "缺失的定位要逐条判断是'改对了所以不再产生'还是'漏掉了'"),
        "output": str(output),
    }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="分层抽样导出人工核验表（固定评估抽样集）")
    parser.add_argument("--pred", default=str(DEFAULT_PRED), help="产物 JSON 路径")
    parser.add_argument("--output", default=None, help="CSV 输出路径（默认 evaluation/review/sample_<seed>.csv）")
    parser.add_argument("--json", default=None, help="JSON 输出路径（--compare 时作为基准输入）")
    parser.add_argument("--total", type=int, default=350, help="抽样总数（默认 350）")
    parser.add_argument("--seed", type=int, default=20260926, help="随机种子（固定种子保证抽样可复现）")
    parser.add_argument("--min-per-layer", type=int, default=8, help="每层至少抽多少条")
    parser.add_argument("--no-dynasty-strata", action="store_true", help="事件不再按朝代细分层")
    parser.add_argument("--compare", default=None, help="已有的抽样 JSON；对这些定位在新产物上做复核")
    args = parser.parse_args()

    if args.compare:
        if not args.pred:
            print("--compare 需要同时给出 --pred（新产物）")
            return 2
        output = Path(args.output or (MODULE_ROOT / "evaluation" / "review" / "recheck.csv"))
        summary = compare_against_new_product(Path(args.compare), Path(args.pred), output)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    pred_path = Path(args.pred)
    if not pred_path.is_file():
        print(f"产物不存在: {pred_path}")
        return 2

    payload = json.loads(pred_path.read_text(encoding="utf-8"))
    strata = collect_strata(payload, by_dynasty=not args.no_dynasty_strata)
    rows = stratified_sample(strata, args.total, args.seed, args.min_per_layer)
    if not rows:
        print("没有可抽样的记录")
        return 2

    output = Path(args.output or (MODULE_ROOT / "evaluation" / "review" / f"sample_{args.seed}.csv"))
    write_csv(rows, output)
    json_path = Path(args.json) if args.json else output.with_suffix(".json")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "source": str(pred_path), "seed": args.seed, "total": len(rows),
            "layer_sizes": {name: len(items) for name, items in strata.items()},
            "records": rows,
        }, f, ensure_ascii=False, indent=2)

    print(f"抽样 {len(rows)} 条（种子 {args.seed}）")
    for name in sorted(strata):
        picked = sum(1 for row in rows if row["分层"] == name)
        print(f"  {name}: 候选 {len(strata[name])} → 抽 {picked}")
    print(f"CSV: {output}")
    print(f"JSON（--compare 的基准）: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
