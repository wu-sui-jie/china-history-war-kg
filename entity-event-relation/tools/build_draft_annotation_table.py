#!/usr/bin/env python
"""
按**朝代子集**从产物切一份"参考集草稿核验表"——C 组重建的第 2 步。

**它在流程里的位置**：先出草稿（本文），人工只做核验与修正（第 3 步），
最后交付带 `annotation_id` 与原文证据的参考集（`docs/参考集重建规范.md` §2）。

**为什么草稿要按朝代子集切，而不是全书一次出。** 重建是**按子集增量**推进的：
做透一个子集就能冻结一个 development 集、就能解锁阶段三那次"攒批重跑"，
不必等三个朝代都完。切片范围与 `data/dynasty_subsets/` 的原文子集对齐（明 / 唐 / 秦汉），
所以"表里的记录"与"你要读的那段原文"是同一块。

**为什么核验表的列与 B 组那张不一样。** B 组判的是**产物对不对**（三档：对/错/无法判断）；
这里判的是**草稿怎么改**（保留 / 删除 / 修改），还要能**补漏**——所以多了
`核验结论`、`修正内容` 两列，另有 `annotation_id` 便于成稿后引用与 diff。
补漏的做法：草稿里没有的记录，**直接在文件末尾追加一行**，`核验结论` 填 `新增`。

**成本**：本工具不调模型——它从**已有产物**切片。所以草稿反映的是**生成那份产物时的规则**；
若要用"当前规则"的草稿，先跑一次该子集（`python main.py data/dynasty_subsets/明.txt`，
明子集约 11 段 × 3 阶段，很便宜）。

用法：

    python tools/build_draft_annotation_table.py --dynasty 明
    python tools/build_draft_annotation_table.py --dynasty 唐 --pred <产物.json> --output <仓库外路径>
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

from tools.annotation_io import (  # noqa: E402
    ID_PREFIXES,
    event_core,
    event_key,
    organization_key,
    person_key,
    place_key,
    relation_key,
    stable_id,
)
from tools.backfill_annotation_evidence import Locator  # noqa: E402  # 定位只有这一份实现
from war_extraction.utils.normalizer import Normalizer  # noqa: E402

DEFAULT_PRED = MODULE_ROOT / "output" / "中国历代战争简史" / "9_final_all.json"
DEFAULT_BOOK = MODULE_ROOT / "data" / "中国历代战争简史.txt"

#: 人工核验表的列。`核验结论` / `修正内容` / `判据` / `备注` 四列**留空**给填表人——
#: 这条不变量有机械意义：填表人一眼能看出哪些行还没处理。
REBUILD_COLUMNS = [
    "分层", "定位键", "annotation_id", "记录摘要", "原文上下文", "产物证据",
    "定位方式", "原文出处", "核验结论", "修正内容", "判据", "备注",
]

#: 子集名 → 产物里的朝代取值（与 `data/dynasty_subsets/README.md` 的切分对齐）。
SUBSET_DYNASTIES = {
    "秦汉": ("秦", "西汉", "东汉"),
    "唐": ("唐",),
    "明": ("明",),
}


def _events_of(payload: dict) -> list:
    block = payload.get("events") or {}
    return block if isinstance(block, list) else (block.get("events") or [])


def _row_hash(row: dict) -> str:
    """按内容给草稿行算一个键——只为去重（同一实体可能被多个事件引用）。"""
    return json.dumps(row, ensure_ascii=False, sort_keys=True)


#: 产物 → 标注的字段映射（同名不列）。历史遗留，见 data/annotations/README.md 第二节：
#: 产物用 `Commanders`/`KeyPersons`/`TroopSize`，标注用 `Person`/`Scale`——评估器只比对事件名与
#: 几个共名字段，所以映射不影响指标，但**人工做错误分析时要记得它们不是同一字段**。
_EVENT_FIELD_MAP = {"Person": ("Commanders", "KeyPersons"), "Scale": ("TroopSize",)}


def _join(row: dict, names) -> str:
    parts = [str(row.get(name) or "").strip() for name in names]
    return "、".join(part for part in parts if part)


def event_to_gold(row: dict) -> dict:
    """产物事件 → 标注事件（含历史字段名映射）。"""
    gold = {key: row.get(key) for key in (
        "EventName", "EventType", "StartDate", "EndDate", "DynastyName", "Place",
        "Aggressor", "Defender", "Result", "Action", "source", "Impact", "Remark")}
    for target, sources in _EVENT_FIELD_MAP.items():
        gold[target] = _join(row, sources) or None
    relations = row.get("relations") or []
    if relations:
        gold["Relations"] = "；".join(f"{rel.get('type')}：{rel.get('to')}" for rel in relations
                                      if rel.get("type") or rel.get("to"))
    return {key: value for key, value in gold.items() if value not in (None, "")}


def entity_to_gold(kind: str, row: dict) -> dict:
    keys = {"places": ("geo_name", "modern_name", "DynastyName"),
            "persons": ("PersonName", "DynastyName", "OrgName", "Role", "Note"),
            "organizations": ("OrgName", "OrgType", "DynastyName")}[kind]
    return {key: row.get(key) for key in keys if row.get(key) not in (None, "")}


def relation_to_gold(attribute: str, row: dict) -> dict:
    tail_key = {"event_place_relations": "modern_name",
                "event_organization_relations": "OrgName",
                "event_person_relations": "PersonName",
                "event_event_relations": "EventName_B"}[attribute]
    gold = {"head": row.get("EventName") or row.get("EventName_A"),
            "relation": row.get("relation"), "tail": row.get(tail_key)}
    return {key: value for key, value in gold.items() if value not in (None, "")}


def _public(row: dict) -> dict:
    """只取公开列——`_payload` / `_attribute` 是内部字段，只进 JSON 旁车，不进 CSV。"""
    return {key: row.get(key, "") for key in REBUILD_COLUMNS}


def build_rows(payload: dict, dynasties: tuple, locator: Locator) -> list:
    """切出该子集的事件、被引用／同朝代的实体、以及挂在这些事件上的关系。"""
    normalizer = Normalizer()
    events = [event for event in _events_of(payload)
              if (event.get("DynastyName") or "").strip() in dynasties]
    event_names = {(event.get("EventName") or "").strip() for event in events if event.get("EventName")}
    entities = payload.get("entities") or {}
    relations = payload.get("relations") or {}
    rows = []

    def add(layer, key, annotation_id, summary, evidence_text, context, way, source_span,
            payload=None, attribute=None):
        rows.append({
            "分层": layer, "定位键": key, "annotation_id": annotation_id, "记录摘要": summary,
            "原文上下文": context, "产物证据": evidence_text, "定位方式": way,
            "原文出处": source_span, "核验结论": "", "修正内容": "", "判据": "", "备注": "",
            # 下面两个是**内部字段**（不进 CSV，只进同名的 `.json` 旁车）：
            # 落盘工具（`apply_rebuild_table.py`）靠它们把"保留/修改"的原始记录写回 JSON，
            # 不用按定位键回产物里猜。
            "_payload": payload, "_attribute": attribute,
        })

    # --- 事件（判：该不该算一个事件、字段对不对）
    #: 事件名 → 已定位到的**清洗后区间**；关系定位复用它当锚点（见下）。
    event_anchors = {}
    for event in events:
        name = (event.get("EventName") or "").strip()
        # **证据优先**：产物的 `source_text` 是书里的原句，比合成的事件名可靠得多
        found = locator.locate_best(name, event.get("source_text") or "")
        if found.get("cleaned_span"):
            event_anchors[name] = found["cleaned_span"]
        add(
            f"事件:{event.get('DynastyName') or '（不详）'}",
            event_key(normalizer, name, event.get("DynastyName"), event.get("StartDate"), event.get("Place")),
            stable_id(ID_PREFIXES["events"], event_core(event, normalizer)),
            (f"事件 {name}（{event.get('DynastyName') or '—'} {event.get('StartDate') or '—'}，"
             f"地点 {event.get('Place') or '—'}，主动方 {event.get('Aggressor') or '—'}，"
             f"防守方 {event.get('Defender') or '—'}，指挥官 {event.get('Commanders') or '—'}，"
             f"结果 {event.get('Result') or '—'}）"),
            (event.get("source_text") or "")[:400],
            found["evidence"], found["status"], f"[{found['start']}, {found['end']}]",
            payload=event_to_gold(event),
        )

    # --- 实体：同朝代的 + 被这些事件引用到的（跨朝代的实体在子集里也要核）
    referenced = set()
    for event in events:
        for field in ("Place", "Aggressor", "Defender", "Allies", "Commanders", "KeyPersons"):
            for value in (event.get(field) or "").replace("，", "、").replace(",", "、").split("、"):
                value = value.strip()
                if value:
                    referenced.add(value)

    def entity_rows(kind, key_name, layer, label, extra):
        seen = set()
        for row in entities.get(kind) or []:
            name = (row.get(key_name) or "").strip()
            if not name or _row_hash(row) in seen:
                continue
            same_dynasty = (row.get("DynastyName") or "").strip() in dynasties
            if not same_dynasty and name not in referenced:
                continue
            seen.add(_row_hash(row))
            found = locator.locate_best(name, row.get("source_text") or "")
            core_kind = {"places": "places", "persons": "persons", "organizations": "organizations"}[kind]
            add(
                layer,
                {"places": place_key, "persons": person_key,
                 "organizations": organization_key}[kind](name, row.get("DynastyName")) if kind != "places"
                else place_key(name, row.get("DynastyName"), row.get("modern_name")),
                stable_id(ID_PREFIXES[core_kind],
                          f"{core_kind}|{normalizer.normalize_entity_name(name)}|{row.get('DynastyName') or ''}"),
                f"{label} {name}（{extra(row)}）",
                (row.get("source_text") or "")[:400],
                found["evidence"], found["status"], f"[{found['start']}, {found['end']}]",
                payload=entity_to_gold(kind, row),
            )

    entity_rows("places", "geo_name", "实体:地点", "地点",
                lambda row: f"现代名 {row.get('modern_name') or '—'}，朝代 {row.get('DynastyName') or '—'}"
                            f"{'（被事件引用）' if (row.get('DynastyName') or '').strip() not in dynasties else ''}")
    entity_rows("persons", "PersonName", "实体:人物", "人物",
                lambda row: f"角色 {row.get('Role') or '—'}，朝代 {row.get('DynastyName') or '—'}，"
                            f"组织 {row.get('OrgName') or '—'}"
                            f"{'（被事件引用）' if (row.get('DynastyName') or '').strip() not in dynasties else ''}")
    entity_rows("organizations", "OrgName", "实体:组织", "组织",
                lambda row: f"类型 {row.get('OrgType') or '—'}，朝代 {row.get('DynastyName') or '—'}"
                            f"{'（被事件引用）' if (row.get('DynastyName') or '').strip() not in dynasties else ''}")

    # --- 关系（只取挂在本子集事件上的）
    layer_of = {
        "event_place_relations": ("关系:事件-地点", "modern_name"),
        "event_organization_relations": ("关系:事件-组织", "OrgName"),
        "event_person_relations": ("关系:事件-人物", "PersonName"),
        "event_event_relations": ("关系:事件-事件", "EventName_B"),
    }
    for attribute, (layer, tail_key) in layer_of.items():
        for row in relations.get(attribute) or []:
            head = (row.get("EventName") or row.get("EventName_A") or "").strip()
            tail = (row.get(tail_key) or "").strip()
            if head not in event_names or not tail:
                continue
            if attribute == "event_event_relations":
                # 事件-事件的"目标"也是事件名（合成的），按名称找不到——改用它自己的证据句；
                # 再退一步用**两个事件各自的区间并集**（这段叙述里同时提到两端）。
                found = locator.locate_text(row.get("evidence") or "")
                if found is None:
                    spans = [event_anchors[name] for name in (head, tail) if name in event_anchors]
                    found = (locator.locate_near(tail, (min(s[0] for s in spans), max(s[1] for s in spans)))
                             if spans else None)
                if found is None:
                    found = {"status": "未命中(两端事件都未定位)", "evidence": "",
                             "start": None, "end": None}
            else:
                anchor = event_anchors.get(head)
                found = (locator.locate_near(tail, anchor) if anchor
                         else {"status": "未命中(头事件)", "evidence": "", "start": None, "end": None})
            add(
                layer,
                relation_key(attribute, head, row.get("relation"), tail),
                stable_id(ID_PREFIXES["relations"],
                          f"{attribute}|{normalizer.normalize_event_name(head)}"
                          f"|{normalizer.normalize_relation(row.get('relation'))}"
                          f"|{normalizer.normalize_entity_name(tail)}"),
                f"{head} —[{row.get('relation') or '—'}]→ {tail}",
                (row.get("evidence") or "")[:400],
                found["evidence"], found["status"], f"[{found['start']}, {found['end']}]",
                payload=relation_to_gold(attribute, row), attribute=attribute,
            )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="按朝代子集切一份参考集草稿核验表")
    parser.add_argument("--dynasty", required=True, choices=sorted(SUBSET_DYNASTIES),
                        help="子集名（与 data/dynasty_subsets/ 对齐）")
    parser.add_argument("--pred", default=str(DEFAULT_PRED), help="产物 JSON（草稿的来源）")
    parser.add_argument("--book", default=str(DEFAULT_BOOK))
    parser.add_argument("--output", default=None, help="CSV 输出路径（合并表）")
    parser.add_argument("--split-by-layer", action="store_true",
                        help="另外按类拆成 事件 / 实体 / 关系 三份——判的人**先判事件、再实体、最后关系**"
                             "（关系依赖前两者的名单，混在一起判会反复来回翻）")
    args = parser.parse_args()

    pred_path = Path(args.pred)
    book = Path(args.book)
    if not pred_path.is_file():
        print(f"产物不存在: {pred_path}")
        return 2
    if not book.is_file():
        print(f"原文不存在: {book}")
        return 2

    payload = json.loads(pred_path.read_text(encoding="utf-8"))
    locator = Locator(book)
    rows = build_rows(payload, SUBSET_DYNASTIES[args.dynasty], locator)

    output = Path(args.output) if args.output else (
        MODULE_ROOT / "evaluation" / "review" / f"draft_{args.dynasty}_with_source.csv")
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REBUILD_COLUMNS)
        writer.writeheader()
        writer.writerows(_public(row) for row in rows)

    if args.split_by_layer:
        # 按"冒号前那一级"拆（事件 / 实体 / 关系），文件名带类别后缀，与合并表并存
        groups = {}
        for row in rows:
            groups.setdefault(row["分层"].split(":")[0], []).append(row)
        for group, group_rows in sorted(groups.items()):
            part = output.with_name(f"{output.stem}__{group}.csv")
            with open(part, "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=REBUILD_COLUMNS)
                writer.writeheader()
                writer.writerows(_public(row) for row in group_rows)
            print(f"  拆分: {part.name}（{len(group_rows)} 行）")

    sidecar = output.with_suffix(".json")
    sidecar.write_text(json.dumps({
        "note": ("草稿核验表的机器可读旁车：`_payload` 是该行对应的**原始记录**（已做产物→标注的"
                 "字段映射），`_attribute` 是关系的类别。落盘工具 `tools/apply_rebuild_table.py` 读它，"
                 "不按定位键回产物里猜。CSV 是给人填的，本文件不用改。"),
        "dynasty": args.dynasty, "pred": str(pred_path),
        "records": [{"分层": row["分层"], "定位键": row["定位键"], "记录摘要": row["记录摘要"],
                     "_payload": row.get("_payload"), "_attribute": row.get("_attribute")}
                    for row in rows],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  JSON 旁车: {sidecar.name}")

    layers = Counter(row["分层"] for row in rows)
    located = sum(1 for row in rows if "未命中" not in row["定位方式"])
    print(f"草稿 {args.dynasty} 子集 → {output}")
    print(f"  共 {len(rows)} 行，定位到原文 {located} 行（{located / len(rows):.0%}）")
    for layer, count in layers.most_common():
        print(f"  {layer}: {count}")
    print("\n填表说明（详见 docs/参考集重建规范.md §4 第 3 步）：")
    print("  核验结论：保留 / 删除 / 修改 / 新增（草稿里没有的，追加到文件末尾并填『新增』）")
    print("  修正内容：写成 `字段=新值`，多个用分号；补漏的写在新增行里")
    print("  判据：用 docs/抽样判定规范.md §3 的固定前缀（时间：地点：主动方：结果：关系类型：…）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
