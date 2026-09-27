#!/usr/bin/env python
"""
把**填好的核验表**落成参考集 JSON（`docs/参考集重建规范.md` §0.3 的第 ③ 步）。

**它补的是哪一环。** 核验表是 CSV（人填）、成稿是 JSON（评估器读），中间原先只能手工拷贝修改：
漏一条删除、少改一个字段都不会报错。这个工具把四档结论机械落下去：

- `保留`：照抄草稿记录；
- `删除`：丢掉；
- `修改`：按 `修正内容` 里的 `字段=新值` 覆盖（多个用分号）；
- `新增`：`修正内容` 当作**整条记录的字段集**（`字段=值` 分号分隔），拼成一条新记录；
- **没填的行按"保留"落，但会高声报出条数**——"没填"不等于"判过了"，把这个数摊在明面上。

**草稿记录来自 JSON 旁车**（`build_draft_annotation_table.py` 与 CSV 一起写出的
`draft_<子集>_with_source.json`）：它逐行带着**原始记录**（已做产物→标注的字段映射，
见 `data/annotations/README.md` 第二节）与关系的类别。**不按定位键回产物里猜**——
那种反查一旦键对不上就会静默丢行。

**为什么 `--sidecar` 可以给多份、并且要接着去重。** 三个子集是分表核验的，成稿要把它们
**合到一起**：跑一次得给全部三份旁车与九份核验表。合并会撞上一件事——**跨朝代的同名行同时
出现在多个子集的草稿里**（如「兰州｜唐」既在明子集的噪声里、也是唐子集的正当条目），
两个子集都判「保留」时同一条记录就会被写两遍。所以落成后必须按**记录身份**去重，
且把"同身份但内容不同"的**冲突高声报出**（那说明两处判得不一样，不是重复）。

**IAA 仲裁要单独喂进来。** 双人复核抽样的那部分分歧由仲裁逐条给了结论，它**覆盖**核验表里
甲的原始判定（`--arbitration`）。不给这个参数，抽样部分的仲裁结论就不会生效——
2026-09-27 那版成稿正是漏了这一步（见 `docs/数据提取模块分析与整改方案.md` §0.8）。

**为什么要把 `产物证据` 抄进记录的 `evidence_hint`。** 成稿里的 `evidence` 是
`backfill_annotation_evidence.py` 从原文**重新定位**出来的锚点，而它定位需要一条线索：
事件名多是模型合成的概括名（实测只有 26% 是原文串），拿它定位会大片失败；草稿表里的
`产物证据` 是书里的原句，定位可靠得多。所以落成时把这条线索带上（`evidence_hint`），
回填工具按"证据优先、名称兜底"用它。它**只是线索**，不是最终证据——最终证据由回填工具
独立定位后写入 `evidence` / `evidence_start` / `evidence_end` / `locate_status`。

用法：

    python tools/apply_rebuild_table.py \
        --sidecar evaluation/review/draft_明_with_source.json \
                  evaluation/review/draft_唐_with_source.json \
                  evaluation/review/draft_秦汉_with_source.json \
        --table evaluation/review/draft_*_with_source__*.csv \
        --arbitration evaluation/review/iaa_20260927/仲裁_20260927.csv \
        --output data/annotations/v2
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

from tools.annotation_io import GOLD_FILES, RELATION_CATEGORY_LABELS  # noqa: E402

#: 关系的四类 → JSON 的顶层键（连字符形式，评估器直接读它）。
RELATION_KEYS = {
    "event_place_relations": "event-place",
    "event_organization_relations": "event-org",
    "event_person_relations": "event-person",
    "event_event_relations": "event-event",
}
#: 人工表的分层名 → 关系的四类键。**新增**的关系行没有旁车记录、也就没有 `_attribute`，
#: 只能从分层名推回去；没有这张表，新增的关系行会被静默丢掉。
LAYER_TO_ATTRIBUTE = {layer: attribute
                      for attribute, (_label, layer) in RELATION_CATEGORY_LABELS.items()}
#: 分层前缀 → 记录类别。
LAYER_CATEGORY = {"事件": "events", "实体": "entities", "关系": "relations"}


def parse_fix(text: str) -> dict:
    """`字段=新值；字段=新值` → 字典（中英文分号/逗号都当分隔符）。"""
    fields = {}
    normalized = (text or "").replace("；", ";").replace("，", ";").replace(",", ";")
    for part in normalized.split(";"):
        part = part.strip()
        if "=" in part:
            key, _, value = part.partition("=")
            fields[key.strip()] = value.strip()
    return fields


def load_arbitration(path) -> dict:
    """
    读一份仲裁表 → `{定位键: (仲裁结论, 仲裁修正)}`。

    仲裁表是双人复核产生的那份分歧清单（`summarize_rebuild_table.py --compare` 之后由人逐条仲裁）。
    它的列名与核验表不同（`仲裁结论` / `仲裁修正`），所以在这里转成与核验表同构的两元组，
    好让 `apply()` 只有一条覆盖路径。
    """
    rows = {}
    with open(path, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            key = (row.get("定位键") or "").strip()
            verdict = (row.get("仲裁结论") or "").strip()
            if not key or not verdict:
                continue
            rows[key] = (verdict, row.get("仲裁修正") or "")
    return rows


def load_arbitrations(paths) -> dict:
    """
    读**多份**仲裁表并合并（后面的覆盖前面的同名键）；返回 (合并结果, 各文件的条数)。

    **为什么要多份**：`iaa_20260927/仲裁_20260927.csv` 是双人复核那一轮的原始记录，
    它是"那一次抽样"的证据，**不该被后来的人工判定改写**——否则事后没法回答
    "IAA 的一致率是怎么算出来的"。后续的人工判定（如"某个不在抽样内的行按口径回环事项删除"）
    另起一份 `人工判定补录_*.csv`，同列名，一起喂进来。
    """
    merged, counts = {}, []
    for raw in paths:
        part = load_arbitration(raw)
        counts.append((str(raw), len(part)))
        merged.update(part)
    return merged, counts


#: 记录身份 = 这些字段的组合。**不是**内容全等：同身份而内容不同叫冲突，不叫重复。
#: 事件的取法与 `参考集重建规范.md` §3.2 的身份口径一致（名称 + 朝代 + 起始时间 + 地点，
#: 同名不同年代各记一条）。
IDENTITY_FIELDS = {
    "events": ("EventName", "DynastyName", "StartDate", "Place"),
    "places": ("geo_name", "DynastyName", "modern_name"),
    "persons": ("PersonName", "DynastyName"),
    "organizations": ("OrgName", "DynastyName"),
}


def dedupe(records: dict) -> tuple:
    """
    按记录身份去重（跨子集合并的必然产物）；返回 (冲突清单, 统计)。

    **同身份 + 内容全等** → 只留一条，记账为「完全重复」（合并时同一条被写了两遍）。
    **同身份 + 内容不同** → 保留先出现的那条，但**记为冲突**并原样报出两条：
    那是"同一个键在两个子集里判得不一样"，要么回去对齐口径，要么其中一条该换身份字段，
    **不能悄悄留一条**。
    """
    conflicts = []
    stats = Counter()
    for kind in ("events", "places", "persons", "organizations"):
        fields = IDENTITY_FIELDS[kind]
        seen = {}
        kept = []
        for row in records[kind]:
            key = tuple(str(row.get(field) or "") for field in fields)
            if key in seen:
                if seen[key] == row:
                    stats[f"{kind} 完全重复（已去重）"] += 1
                else:
                    stats[f"{kind} 同身份内容冲突（保留首条）"] += 1
                    conflicts.append({"类别": kind, "身份": key,
                                      "保留": seen[key], "丢弃": row})
                continue
            seen[key] = row
            kept.append(row)
        records[kind] = kept
    for label, rows in records["relations"].items():
        seen = {}
        kept = []
        for row in rows:
            key = (str(row.get("head") or ""), str(row.get("relation") or ""),
                   str(row.get("tail") or ""))
            if key in seen:
                if seen[key] == row:
                    stats[f"relations[{label}] 完全重复（已去重）"] += 1
                else:
                    stats[f"relations[{label}] 同身份内容冲突（保留首条）"] += 1
                    conflicts.append({"类别": f"relations[{label}]", "身份": key,
                                      "保留": seen[key], "丢弃": row})
                continue
            seen[key] = row
            kept.append(row)
        records["relations"][label] = kept
    return conflicts, stats


def _records_of(sidecar) -> list:
    """
    旁车的两种给法都收：单份旁车（`{"note":…, "records":[…]}`，文件读出来的那个形状）
    或**多个子集合并后**的记录列表。合并要一次读多份旁车，就该能直接给列表。
    """
    if isinstance(sidecar, dict):
        return sidecar.get("records") or []
    return list(sidecar)


def apply(sidecar, table_rows: list, arbitration: dict = None) -> tuple:
    """
    按核验结论把旁车里的原始记录落成成稿；返回 (记录, 统计)。

    `arbitration`（可选）是 `load_arbitration()` 的结果：命中的定位键**以仲裁结论为准**，
    仲裁的 `仲裁修正` 与表里的 `修正内容` 合并（仲裁优先）。没命中的行按表里的判定走。
    """
    arbitration = arbitration or {}
    by_key = {(record.get("定位键") or "").strip(): record for record in _records_of(sidecar)}
    stats = Counter()
    records = {"events": [], "places": [], "persons": [], "organizations": [],
               "relations": {key: [] for key in RELATION_KEYS}}
    touched = set()

    for row in table_rows:
        key = (row.get("定位键") or "").strip()
        verdict = (row.get("核验结论") or "").strip()
        fixes = parse_fix(row.get("修正内容"))
        override = arbitration.get(key)
        if override:
            arb_verdict, arb_text = override
            if arb_verdict != verdict:
                stats["仲裁改判了表的结论"] += 1
            elif arb_text:
                stats["仲裁给表补了修正内容"] += 1
            verdict = arb_verdict
            # 合并顺序：表的修正在前、仲裁在后——同名字段以仲裁为准
            fixes = {**fixes, **parse_fix(arb_text)}
        layer = (row.get("分层") or "").strip()
        category = LAYER_CATEGORY.get(layer.split(":")[0].split("：")[0])
        stats[f"结论/{verdict or '（未填）'}"] += 1

        if verdict == "新增":
            # 新增行：整条由 `修正内容` 给（草稿里没有、表里也不该有旁车记录）
            if not fixes:
                stats["新增行但修正内容为空（跳过）"] += 1
                continue
            fixes["evidence_hint"] = (row.get("产物证据") or "").strip()
            _append(records, category, layer, fixes, {})
            stats["新增（人工补漏）"] += 1
            continue

        record = by_key.get(key)
        if record is None:
            stats["表里有、旁车里没有（跳过）"] += 1
            continue
        touched.add(key)
        if verdict == "删除":
            continue
        payload = dict(record.get("_payload") or {})
        if not payload:
            stats["草稿记录为空（跳过）"] += 1
            continue
        payload.update(fixes)
        payload["evidence_hint"] = (row.get("产物证据") or "").strip()
        _append(records, category, layer or record.get("分层", ""), payload, record)
        if fixes:
            stats["落盘时应用了修正内容"] += 1

    stats["旁车未被表覆盖（既没判也没删）"] = len(set(by_key) - touched)
    return records, stats


def _append(records: dict, category: str, layer: str, payload: dict, node: dict) -> None:
    if category == "events":
        records["events"].append(payload)
    elif category == "entities":
        kind = {"实体:地点": "places", "实体:人物": "persons",
                "实体:组织": "organizations"}.get(layer)
        if kind:
            records[kind].append(payload)
    elif category == "relations":
        # 旁车记录带 `_attribute`；**新增**行没有旁车，从分层名推回去
        attribute = node.get("_attribute") or LAYER_TO_ATTRIBUTE.get(layer)
        if attribute in records["relations"]:
            records["relations"][attribute].append(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="把核验表落成参考集 JSON")
    parser.add_argument("--sidecar", nargs="+", required=True,
                        help="草稿表的 JSON 旁车（**多个子集一起给**，合并成一份成稿）")
    parser.add_argument("--table", nargs="+", required=True,
                        help="填好的核验表 CSV（可给多份，按层拆分的三份一起给）")
    parser.add_argument("--arbitration", nargs="+", default=None,
                        help="双人复核的仲裁表 CSV（如 evaluation/review/iaa_20260927/仲裁_20260927.csv）"
                             "**可给多份**（后给的覆盖前面的同名键）；给了它，命中行的结论以仲裁为准。"
                             "后续的人工判定另存补录文件、一起喂进来——不要改那一轮 IAA 的原始记录")
    parser.add_argument("--conflicts-out", default=None,
                        help="把「同身份内容冲突」写成 JSON 报出来（默认写到成稿目录的**同级**"
                             "`<成稿目录>_conflicts.json`——不放进成稿目录里，那个目录只该有三份 gold）")
    parser.add_argument("--output", required=True, help="成稿目录（如 data/annotations/v2）")
    args = parser.parse_args()

    sidecar_records = []
    for raw in args.sidecar:
        path = Path(raw)
        if not path.is_file():
            print(f"旁车不存在: {path}（它由 build_draft_annotation_table.py 与 CSV 一起写出）")
            return 2
        sidecar_records.extend(json.loads(path.read_text(encoding="utf-8")).get("records") or [])

    table_rows = []
    for path in args.table:
        with open(path, encoding="utf-8-sig", newline="") as handle:
            table_rows.extend(csv.DictReader(handle))
    if not table_rows:
        print("核验表是空的")
        return 2

    arbitration, arbitration_counts = ({}, [])
    if args.arbitration:
        arbitration, arbitration_counts = load_arbitrations(args.arbitration)
        for path, count in arbitration_counts:
            print(f"  仲裁表 {path}: {count} 行")
    records, stats = apply(sidecar_records, table_rows, arbitration)
    conflicts, dedupe_stats = dedupe(records)
    stats.update(dedupe_stats)

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / GOLD_FILES["events"]).write_text(
        json.dumps({"events": records["events"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / GOLD_FILES["entities"]).write_text(json.dumps(
        {kind: records[kind] for kind in ("places", "persons", "organizations")},
        ensure_ascii=False, indent=2), encoding="utf-8")
    (output / GOLD_FILES["relations"]).write_text(json.dumps(
        {RELATION_KEYS[attribute]: rows for attribute, rows in records["relations"].items()},
        ensure_ascii=False, indent=2), encoding="utf-8")

    conflicts_path = (Path(args.conflicts_out) if args.conflicts_out
                      else output.parent / f"{output.name}_conflicts.json")
    conflicts_path.write_text(json.dumps(
        {"note": "同身份但内容不同的记录：保留先出现的那条。要么回去对齐两个子集的口径，"
                 "要么其中一条该换身份字段——不要留着当两条。",
         "count": len(conflicts), "conflicts": conflicts},
        ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"成稿已写入: {output}")
    print(f"  事件 {len(records['events'])} / 地点 {len(records['places'])}"
          f" / 人物 {len(records['persons'])} / 组织 {len(records['organizations'])}"
          f" / 关系 {sum(len(rows) for rows in records['relations'].values())}")
    print(f"  仲裁表命中并覆盖了 {sum(v for k, v in stats.items() if k.startswith('仲裁'))} 行")
    for key, count in stats.most_common():
        print(f"  {key}: {count}")
    if conflicts:
        print(f"\n**注意**：有 {len(conflicts)} 条「同身份内容冲突」——两条的键一样、内容不一样，"
              f"已保留先出现的那条。明细: {conflicts_path}")
    if stats["结论/（未填）"]:
        print(f"\n**注意**：有 {stats['结论/（未填）']} 行没填核验结论，它们被当作「保留」落盘了。"
              "\n  那不叫判过了——回去补齐再重跑本工具。")
    if not arbitration:
        print("\n**注意**：没有给 `--arbitration`，双人复核抽样的仲裁结论**不会**生效。"
              "\n  那部分（分歧行）仍按核验表里的原始判定落盘。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
