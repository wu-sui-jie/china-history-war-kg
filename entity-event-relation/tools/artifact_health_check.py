#!/usr/bin/env python
"""
产物体检：不依赖参考集的一致性检查，用途是**防回归**而不是打分。

**为什么要单独一个脚本。** 参考集本身不可信（见整改方案 2.4.1：它是多模型分片汇总、
未经人工处理），所以"指标降了"分不清是模型差了还是口径变了。但下面这十项是**绝对数字**，
与参考集无关：悬空边、枚举外取值、重复行、残缺年份、方向与时间矛盾、把"不详"当有效值、
证据复用……它们变差就一定是变差，没有解释空间，所以可以当 CI 门禁用。

**用法**

    python tools/artifact_health_check.py                       # 打印报告
    python tools/artifact_health_check.py --json health.json    # 存 JSON，供逐版比对
    python tools/artifact_health_check.py --baseline old.json   # 与上一版比，任何一项增加就退出码 1

**口径**：所有"枚举"取值都取自 `war_extraction/utils/vocabulary.py` 那份权威表。
枚举扩散（决策 5）之后这些计数应当为 0；变成非 0 说明"提示词枚举 / 权威表 / 产物"
三者漂移了，要先决定扩表还是改数据。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from war_extraction.utils.normalizer import Normalizer  # noqa: E402
from war_extraction.utils.value_parsing import (  # noqa: E402
    event_identity_key,
    parse_year_for_order,
)
from war_extraction.utils.vocabulary import (  # noqa: E402
    normalize_event_type,
    normalize_org_type,
    normalize_role,
    relation_type_allowed,
)

MODULE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRED = MODULE_ROOT / "output" / "中国历代战争简史" / "9_final_all.json"

RELATION_CATEGORIES = {
    "event_place_relations": "event-place",
    "event_organization_relations": "event-organization",
    "event_person_relations": "event-person",
    "event_event_relations": "event-event",
}
#: 已知的、**暂不打算修**的枚举外取值（键是"类别/取值"，值是说明）。
#:
#: 为什么要有这张表：门禁项是"绝对数字、改动后应不增加"，而一个**永远非零**的门禁项
#: 会让下一个人反复去查同一件事。这里登记已确认的例外；`--baseline` 比对照旧生效
#: （它比增减、不比零），报告里则能一眼看出"这个 1 是什么、为什么留着"。
KNOWN_ENUM_EXCEPTIONS = {
    "event_organization_relations/指挥所": (
        "LLM 把地点关系名写进了事件-组织关系：实测 1 条 `辽东半岛战役 —[指挥所]→ 征清大总督府`。"
        "组织不可能是「指挥所」，属模型误分类；**不把 `指挥所` 加进组织关系枚举**"
        "（那等于承认一个错的语义），它与另外三类一样留在 raw 产物、由 "
        "`split_publishable_outputs` 路由进候选区。重跑后若条数明显上涨，"
        "说明提示词对组织关系的约束在退化。"
    ),
}



def _load(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _events_of(payload: dict) -> list:
    block = payload.get("events") or {}
    return block if isinstance(block, list) else (block.get("events") or [])


def check_dangling_relations(payload: dict, normalizer) -> dict:
    """
    悬空边：关系的事件端在事件表里找不到。

    这是**导入时会被静默丢掉**的一类（`import_json_to_sqlite` 按精确事件名单过滤），
    所以 raw 产物有、库里的数字却是 0——差异在这里显形。精确与归一两种口径都给：
    只做名称标准化、不与最终事件名单对齐时是"归一悬空"；两者都算才算干净。
    """
    events = _events_of(payload)
    exact_names = {(e.get("EventName") or "").strip() for e in events if e.get("EventName")}
    normalized_names = {normalizer.normalize_event_name(name) for name in exact_names}
    relations = payload.get("relations") or {}

    report = {}
    for attribute, category in RELATION_CATEGORIES.items():
        exact_missing = 0
        normalized_missing = 0
        samples = []
        for rel in relations.get(attribute) or []:
            for endpoint in (("EventName",) if category != "event-event"
                             else ("EventName_A", "EventName_B")):
                name = (rel.get(endpoint) or "").strip()
                if not name:
                    continue
                if name not in exact_names:
                    exact_missing += 1
                    if len(samples) < 5:
                        samples.append(name)
                if normalizer.normalize_event_name(name) not in normalized_names:
                    normalized_missing += 1
        report[category] = {
            "exact_dangling": exact_missing,
            "normalized_dangling": normalized_missing,
            "samples": samples,
        }
    return report


def check_enum_values(payload: dict, normalizer) -> dict:
    """枚举外取值：各类取值的分布与"不在权威表里"的条数。"""
    entities = payload.get("entities") or {}
    events = _events_of(payload)
    relations = payload.get("relations") or {}

    def distribution(values):
        counter = Counter(values)
        outside = {value: count for value, count in counter.items() if value not in _legal}
        return dict(counter.most_common()), outside

    org_types = [(o.get("OrgType") or "").strip() for o in entities.get("organizations") or []]
    roles = [(p.get("Role") or "").strip() for p in entities.get("persons") or []]
    event_types = [(e.get("EventType") or "").strip() for e in events]

    report = {}

    _legal = {value for value in org_types if normalize_org_type(value)[1]}
    distribution_of, outside = distribution([v for v in org_types if v])
    report["OrgType"] = {"total": len([v for v in org_types if v]),
                         "outside_enum": outside, "distribution": distribution_of}

    _legal = {value for value in roles if normalize_role(value)[1]}
    distribution_of, outside = distribution([v for v in roles if v])
    report["Role"] = {"total": len([v for v in roles if v]),
                      "outside_enum": outside, "distribution": distribution_of}

    _legal = {value for value in event_types if normalize_event_type(value)[1]}
    distribution_of, outside = distribution(event_types)
    # 空值单独报：RAG 侧会用 `or "战争"` 兜底，于是"不确定"混进了确定值里
    report["EventType"] = {"total": len(event_types),
                           "empty": sum(1 for v in event_types if not v),
                           "outside_enum": outside, "distribution": distribution_of}

    for attribute, category in RELATION_CATEGORIES.items():
        values = [normalizer.normalize_relation((rel.get("relation") or "")) for rel in relations.get(attribute) or []]
        _legal = {value for value in values if relation_type_allowed(value, category)}
        distribution_of, outside = distribution(values)
        report[attribute] = {"total": len(values), "outside_enum": outside, "distribution": distribution_of}

    return report


def check_duplicates(payload: dict, normalizer) -> dict:
    """重复行：实体按（归一名称 + 朝代）与事件按统一身份键统计多余行数。"""
    entities = payload.get("entities") or {}
    events = _events_of(payload)

    def duplicates(rows, key_func):
        keys = [key_func(row) for row in rows]
        keys = [key for key in keys if key]
        return len(keys) - len(set(keys))

    return {
        "places": duplicates(
            entities.get("places") or [],
            lambda p: (normalizer.normalize_entity_name(p.get("geo_name")),
                       (p.get("DynastyName") or ""), (p.get("modern_name") or ""))),
        "persons": duplicates(
            entities.get("persons") or [],
            lambda p: (normalizer.normalize_entity_name(p.get("PersonName")),
                       (p.get("DynastyName") or ""))),
        "organizations": duplicates(
            entities.get("organizations") or [],
            lambda p: (normalizer.normalize_entity_name(p.get("OrgName")),
                       (p.get("DynastyName") or ""))),
        "events": duplicates(
            events,
            lambda e: event_identity_key(normalizer, e.get("EventName"), e.get("DynastyName"),
                                         e.get("StartDate"), e.get("Place"))),
    }


def check_residual_years(payload: dict) -> dict:
    """
    残缺年份：`200`、`214` 这类 1–3 位纯数字年份。

    实测标注里有 14 处（`天津陷落` 的 `200`、`北仓杨村阻击战` 的 `214`），
    它们是"年份被截断"而不是"公元 200 年"，会被 `parse_year_for_order` 当成真年份
    参与方向判定与排序。
    """
    samples = []
    for event in _events_of(payload):
        for field in ("StartDate", "EndDate"):
            value = event.get(field)
            text = str(value).strip() if value is not None else ""
            if text.isdigit() and len(text) <= 3:
                if len(samples) < 10:
                    samples.append({"EventName": event.get("EventName"), "field": field, "value": text})
    return {"count": len(samples) if len(samples) < 10 else _count_residual(payload), "samples": samples}


def _count_residual(payload: dict) -> int:
    total = 0
    for event in _events_of(payload):
        for field in ("StartDate", "EndDate"):
            value = event.get(field)
            text = str(value).strip() if value is not None else ""
            if text.isdigit() and len(text) <= 3:
                total += 1
    return total


def check_placeholder_as_value(payload: dict, normalizer) -> dict:
    """
    把"不详"当有效值：统计各字段里占位词的行数。

    发布过滤与 `is_placeholder_value` 早就是"不详 = 没有值"这个口径，只有字段填充率
    这一个指标例外（把它算成已填，于是报告里的 99.6% 是假象）。这里给出真实数字。
    """
    fields = ["TroopSize", "Duration", "Casualties", "GeographicScope", "Result", "Agree", "Place"]
    events = _events_of(payload)
    counter = {}
    for field in fields:
        if field == "Agree":
            continue
        count = 0
        for event in events:
            value = event.get(field)
            if value and normalizer.is_placeholder_value(value):
                count += 1
        counter[field] = count
    return {"total_events": len(events), "placeholder_rows": counter}


def check_direction_vs_time(payload: dict, normalizer) -> dict:
    """
    事件-事件关系方向与时间字段矛盾：A 的起始年份晚于 B，却标"顺承 A→B"。

    这是"方向被字典序改写"留下的痕迹——`顺承关系` 的语义是"A 发生在 B 之前"，
    与时间字段矛盾就说明方向错了。
    """
    years = {}
    for event in _events_of(payload):
        name = normalizer.normalize_event_name(event.get("EventName"))
        year = parse_year_for_order(event.get("StartDate"))
        if name and year is not None and (name not in years or year < years[name]):
            years[name] = year

    conflicts = []
    for rel in (payload.get("relations") or {}).get("event_event_relations") or []:
        if normalizer.normalize_relation(rel.get("relation")) != "顺承关系":
            continue
        name_a = normalizer.normalize_event_name(rel.get("EventName_A"))
        name_b = normalizer.normalize_event_name(rel.get("EventName_B"))
        year_a, year_b = years.get(name_a), years.get(name_b)
        if year_a is None or year_b is None or year_a == year_b:
            continue
        if year_a > year_b:
            if len(conflicts) < 10:
                conflicts.append({"EventName_A": rel.get("EventName_A"), "year_a": year_a,
                                  "EventName_B": rel.get("EventName_B"), "year_b": year_b})
    return {"count": _count_direction_conflicts(payload, normalizer, years), "samples": conflicts}


def _count_direction_conflicts(payload: dict, normalizer, years: dict) -> int:
    total = 0
    for rel in (payload.get("relations") or {}).get("event_event_relations") or []:
        if normalizer.normalize_relation(rel.get("relation")) != "顺承关系":
            continue
        name_a = normalizer.normalize_event_name(rel.get("EventName_A"))
        name_b = normalizer.normalize_event_name(rel.get("EventName_B"))
        year_a, year_b = years.get(name_a), years.get(name_b)
        if year_a is not None and year_b is not None and year_a > year_b:
            total += 1
    return total


def check_evidence_reuse(payload: dict, normalizer) -> dict:
    """
    证据复用：有多少关系与同事件 `source_text` 逐字符相同（= 共用同一段证据）。

    整改前实测 62%~72%，直接解释了"evidence 非空率 100%"与"能在原文找到只有 46%~82%"
    的落差：证据不是"这一条关系的依据"，而是整段文本。
    """
    source_text = {}
    for event in _events_of(payload):
        source_text[(event.get("EventName") or "").strip()] = (event.get("source_text") or "").strip()

    relations = payload.get("relations") or {}
    total = reused = empty = 0
    worst = Counter()
    for attribute, category in RELATION_CATEGORIES.items():
        for rel in relations.get(attribute) or []:
            total += 1
            evidence = (rel.get("evidence") or "").strip()
            if not evidence:
                empty += 1
                continue
            event_name = (rel.get("EventName") or rel.get("EventName_A") or "").strip()
            if source_text.get(event_name) and evidence == source_text[event_name]:
                reused += 1
                worst[event_name] += 1
    return {
        "total_relations": total,
        "evidence_empty": empty,
        "evidence_equals_event_source_text": reused,
        "reuse_ratio": (reused / total) if total else 0.0,
        "top_reused_events": worst.most_common(5),
    }


def check_relation_volume(payload: dict) -> dict:
    """关系量级：有关系的事件平均/最多有几条关系（整改前平均 17.5 条、极值 188 条）。"""
    per_event = Counter()
    relations = payload.get("relations") or {}
    for attribute, category in RELATION_CATEGORIES.items():
        for rel in relations.get(attribute) or []:
            event_name = (rel.get("EventName") or rel.get("EventName_A") or "").strip()
            if event_name:
                per_event[event_name] += 1
    if not per_event:
        return {"events_with_relations": 0, "average": 0.0, "max": 0, "top": []}
    return {
        "events_with_relations": len(per_event),
        "average": round(sum(per_event.values()) / len(per_event), 1),
        "max": per_event.most_common(1)[0][1],
        "top": per_event.most_common(5),
    }


def _count_placeholder(payload: dict, normalizer) -> dict:
    return check_placeholder_as_value(payload, normalizer)


def check_annotations(annotation_dir: Path) -> dict:
    """
    参考标注的**结构性问题**（阶段 1 第 2 项的清理目标）。

    这批数字是"为什么关系召回率的分母是浮动的"的答案：标注文档自身要求
    `head` 必须在事件标注里、`tail` 必须在对应实体标注里，而实测大量标注违反规范，
    这些关系在 `build_gold_triples` 阶段就被丢弃、**根本不进关系指标的分母**。

    注意：这里只报结构问题，**不判断标注内容对不对**——那需要回原文核验（阶段 1 第 7 项）。
    """
    if not annotation_dir.is_dir():
        return {}
    normalizer = Normalizer()
    entities = _load(annotation_dir / "sample_entities.json")
    events_payload = _load(annotation_dir / "sample_events.json")
    relations = _load(annotation_dir / "sample_relations.json")

    event_names = {(e.get("EventName") or "").strip()
                   for e in events_payload.get("events", []) if e.get("EventName")}
    place_names = {(p.get("geo_name") or "").strip()
                   for p in entities.get("places", []) if p.get("geo_name")}
    person_names = {(p.get("PersonName") or "").strip()
                    for p in entities.get("persons", []) if p.get("PersonName")}
    org_names = {(o.get("OrgName") or "").strip()
                 for o in entities.get("organizations", []) if o.get("OrgName")}
    tail_names = {
        "event-place": place_names,
        "event-person": person_names,
        "event-org": org_names,
        "event-organization": org_names,
        "event-event": event_names,
    }

    relation_stats = {}
    for category, rels in relations.items():
        if not isinstance(rels, list):
            continue
        head_missing = sum(1 for rel in rels
                           if (rel.get("head") or "").strip() not in event_names)
        allowed_tails = tail_names.get(category, set())
        tail_missing = sum(1 for rel in rels
                           if (rel.get("tail") or "").strip() not in allowed_tails)
        triples = [(rel.get("head"), normalizer.normalize_relation(rel.get("relation")), rel.get("tail"))
                   for rel in rels]
        relation_stats[category] = {
            "rows": len(rels),
            "duplicate_rows": len(triples) - len(set(triples)),
            "head_not_in_events": head_missing,
            "tail_not_in_scope": tail_missing,
        }

    residual_years = []
    for event in events_payload.get("events", []):
        for field in ("StartDate", "EndDate"):
            value = event.get(field)
            text = str(value).strip() if value is not None else ""
            if text.isdigit() and len(text) <= 3:
                residual_years.append({"EventName": event.get("EventName"), "field": field, "value": text})

    def duplicate_rows(rows, key_func):
        keys = [key_func(row) for row in rows]
        return len(keys) - len(set(keys))

    return {
        "dir": str(annotation_dir),
        "event_names": len(event_names),
        "entity_names": {"places": len(place_names), "persons": len(person_names),
                         "organizations": len(org_names)},
        # 重名行按**名称**统计（与标注 README 的实测口径一致：地点 28 / 组织 37 / 人物 0 /
        # 事件 3）。评估侧用的是名称集合，所以"同名多行"才是会被压成一个的那一类。
        "entity_duplicate_rows": {
            "places": duplicate_rows(entities.get("places", []),
                                     lambda p: (p.get("geo_name") or "")),
            "persons": duplicate_rows(entities.get("persons", []),
                                      lambda p: (p.get("PersonName") or "")),
            "organizations": duplicate_rows(entities.get("organizations", []),
                                            lambda o: (o.get("OrgName") or "")),
            "events": duplicate_rows(events_payload.get("events", []),
                                     lambda e: (e.get("EventName") or "")),
        },
        "relations": relation_stats,
        "residual_years": {"count": len(residual_years), "samples": residual_years[:10]},
    }


def build_report(pred_path: Path) -> dict:
    payload = _load(pred_path)
    normalizer = Normalizer()
    return {
        "source": str(pred_path),
        "counts": {
            "events": len(_events_of(payload)),
            "places": len((payload.get("entities") or {}).get("places") or []),
            "persons": len((payload.get("entities") or {}).get("persons") or []),
            "organizations": len((payload.get("entities") or {}).get("organizations") or []),
            "relations": sum(len((payload.get("relations") or {}).get(key) or [])
                             for key in RELATION_CATEGORIES),
        },
        "dangling_relations": check_dangling_relations(payload, normalizer),
        "enum_values": check_enum_values(payload, normalizer),
        "duplicates": check_duplicates(payload, normalizer),
        "residual_years": check_residual_years(payload),
        "placeholder_as_value": check_placeholder_as_value(payload, normalizer),
        "direction_vs_time": check_direction_vs_time(payload, normalizer),
        "evidence_reuse": check_evidence_reuse(payload, normalizer),
        "relation_volume": check_relation_volume(payload),
        "annotations": check_annotations(MODULE_ROOT / "data" / "annotations"),
        # 已知例外：让"永远非零的门禁项"有解释，免得下一个人反复查同一件事
        "known_enum_exceptions": dict(KNOWN_ENUM_EXCEPTIONS),
    }


#: 可以当门禁的绝对数字（越小越好）。键是取自报告的取值路径。
_GATE_PATHS = [
    ("悬空边（归一）", lambda r: sum(v["normalized_dangling"] for v in r["dangling_relations"].values())),
    ("枚举外 OrgType", lambda r: len(r["enum_values"]["OrgType"]["outside_enum"])),
    ("枚举外 Role", lambda r: len(r["enum_values"]["Role"]["outside_enum"])),
    ("枚举外 EventType", lambda r: len(r["enum_values"]["EventType"]["outside_enum"]) + r["enum_values"]["EventType"]["empty"]),
    ("枚举外关系名", lambda r: sum(len(r["enum_values"][key]["outside_enum"]) for key in RELATION_CATEGORIES)),
    ("重复行合计", lambda r: sum(r["duplicates"].values())),
    ("残缺年份", lambda r: r["residual_years"]["count"]),
    ("方向与时间矛盾", lambda r: r["direction_vs_time"]["count"]),
    ("证据为空的关系", lambda r: r["evidence_reuse"]["evidence_empty"]),
    ("标注 head 不在事件表", lambda r: sum(v["head_not_in_events"] for v in (r.get("annotations") or {}).get("relations", {}).values())),
    ("标注 tail 不在对应名单", lambda r: sum(v["tail_not_in_scope"] for v in (r.get("annotations") or {}).get("relations", {}).values())),
    ("标注重复关系行", lambda r: sum(v["duplicate_rows"] for v in (r.get("annotations") or {}).get("relations", {}).values())),
    ("标注残缺年份", lambda r: ((r.get("annotations") or {}).get("residual_years") or {}).get("count", 0)),
]


def print_report(report: dict) -> None:
    print("=" * 74)
    print(f"产物体检: {report['source']}")
    print("=" * 74)
    counts = report["counts"]
    print(f"条数: 事件 {counts['events']} / 地点 {counts['places']} / 人物 {counts['persons']}"
          f" / 组织 {counts['organizations']} / 关系 {counts['relations']}")

    print("\n[门禁项]（绝对数字，改动后应不增加）")
    for label, getter in _GATE_PATHS:
        print(f"  {label}: {getter(report)}")

    print("\n[明细]")
    for category, item in report["dangling_relations"].items():
        print(f"  悬空边 {category}: 精确 {item['exact_dangling']} / 归一 {item['normalized_dangling']}"
              f"{'  例: ' + '、'.join(item['samples'][:3]) if item['samples'] else ''}")
    for kind in ("OrgType", "Role", "EventType"):
        item = report["enum_values"][kind]
        extra = f"，空值 {item['empty']}" if "empty" in item else ""
        print(f"  {kind}: {item['total']} 条{extra}，枚举外 {len(item['outside_enum'])} 种 {item['outside_enum']}")
    for key in RELATION_CATEGORIES:
        item = report["enum_values"][key]
        print(f"  {key}: {item['total']} 条，枚举外 {item['outside_enum']}")
    # 把"已知例外"指出来：报告里出现某个固定数字时，先看它是不是已登记的那个
    current_exceptions = {
        f"{key}/{value}"
        for key in RELATION_CATEGORIES
        for value in report["enum_values"][key]["outside_enum"]
    }
    known = set(report.get("known_enum_exceptions") or {})
    if current_exceptions:
        print(f"  其中已知例外（不打算修，见 KNOWN_ENUM_EXCEPTIONS）: {sorted(current_exceptions & known) or '无'}")
        unknown = sorted(current_exceptions - known)
        if unknown:
            print(f"  未登记的枚举外取值（需要决定「扩表」还是「改数据」）: {unknown}")
    print(f"  重复行: {report['duplicates']}")
    print(f"  残缺年份: {report['residual_years']['count']}")
    print(f"  占位词当有效值: {report['placeholder_as_value']['placeholder_rows']}")
    print(f"  证据复用率: {report['evidence_reuse']['reuse_ratio']:.1%}"
          f"（{report['evidence_reuse']['evidence_equals_event_source_text']}/{report['evidence_reuse']['total_relations']}）"
          f"，证据为空 {report['evidence_reuse']['evidence_empty']}")
    print(f"  关系量级: 有关系的事件 {report['relation_volume']['events_with_relations']} 个，"
          f"平均 {report['relation_volume']['average']} 条，最多 {report['relation_volume']['max']} 条")

    annotations = report.get("annotations") or {}
    if annotations:
        print(f"\n[参考标注的结构问题] {annotations['dir']}")
        print(f"  唯一名称: 事件 {annotations['event_names']}，实体 {annotations['entity_names']}")
        print(f"  实体重复行: {annotations['entity_duplicate_rows']}")
        for category, item in annotations["relations"].items():
            print(f"  {category}: {item['rows']} 条，head 不在事件表 {item['head_not_in_events']}，"
                  f"tail 不在对应名单 {item['tail_not_in_scope']}，完全重复 {item['duplicate_rows']}")
        print(f"  残缺年份: {annotations['residual_years']['count']} 处"
              f"{'  例: ' + str(annotations['residual_years']['samples'][:3]) if annotations['residual_years']['samples'] else ''}")


def compare_with_baseline(report: dict, baseline: dict) -> list:
    """返回"变差"的项（数量增加），供门禁判定。"""
    regressions = []
    for label, getter in _GATE_PATHS:
        now, before = getter(report), getter(baseline)
        if now > before:
            regressions.append((label, before, now))
    return regressions


def main() -> int:
    parser = argparse.ArgumentParser(description="产物机械体检（不依赖参考集）")
    parser.add_argument("--pred", default=str(DEFAULT_PRED), help="产物 JSON 路径")
    parser.add_argument("--json", default=None, help="把报告写到这个路径，供逐版比对")
    parser.add_argument("--baseline", default=None, help="上一版体检报告；任何一项变差就返回退出码 1")
    args = parser.parse_args()

    pred_path = Path(args.pred)
    if not pred_path.is_file():
        print(f"产物不存在: {pred_path}")
        return 2

    report = build_report(pred_path)
    print_report(report)

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n报告已写入: {out}")

    if args.baseline:
        baseline = _load(Path(args.baseline))
        regressions = compare_with_baseline(report, baseline)
        if regressions:
            print("\n[回归] 以下门禁项比基线变差：")
            for label, before, now in regressions:
                print(f"  {label}: {before} → {now}")
            return 1
        print("\n[回归] 门禁项均未变差。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
