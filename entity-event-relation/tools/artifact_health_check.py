#!/usr/bin/env python
"""
产物体检：不依赖参考集的一致性检查，用途是**防回归**而不是打分。

**为什么要单独一个脚本。** 参考集本身不可信（见整改方案 2.4.1：它是多模型分片汇总、
未经人工处理），所以"指标降了"分不清是模型差了还是口径变了。但下面这十几项是**绝对数字**，
与参考集无关：悬空边、枚举外取值、重复行、残缺年份、方向与时间矛盾、把"不详"当有效值、
证据复用、事件字段的形态……它们变差就一定是变差，没有解释空间，所以可以当 CI 门禁用。

**用法**

    python tools/artifact_health_check.py                       # 打印报告
    python tools/artifact_health_check.py --json health.json    # 存 JSON，供逐版比对
    python tools/artifact_health_check.py --baseline old.json   # 与上一版比，任何一项增加就退出码 1
    python tools/artifact_health_check.py --json b.json --note "这份基线对应哪份产物"

报告里会带**产物指纹** `source_sha256`（附录 B 第 26 条：数字与指纹必须一起记），
入库的基线另用 `--note` 记一句来历。

**口径**：所有"枚举"取值都取自 `war_extraction/utils/vocabulary.py` 那份权威表。
枚举扩散（决策 5）之后这些计数应当为 0；变成非 0 说明"提示词枚举 / 权威表 / 产物"
三者漂移了，要先决定扩表还是改数据。

**事件字段级门禁（第三阶段 D3）。** 在此之前体检只有关系与实体侧的检查，而阶段三要改的
恰恰是 `Result` / `Aggressor` / `Defender` 这些**事件字段**——改完只能靠"看报告"判断。
D3 补了三项绝对数字：字段占位词、攻守方里的非组织值、`Result` 的弱值。

**这三项都是"基线值"而不是"应当为 0"**（旧产物上就有大量命中）：门禁的语义是
"不应比上一版更多"，所以第一次纳入时必须拿旧产物取基线。**其中「事件字段写占位词」还有一层
特殊性**：如实写"不详"是**正确**行为（整改方案 2.6.4：数值/结果类字段宁可留空），
而阶段三 E1 的改动方向正是"不确定就写不详"——那一项**上升是预期的**，
届时更新基线并在此处写清理由，不要把它当成回归。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from war_extraction.utils.normalizer import Normalizer  # noqa: E402
from war_extraction.utils.provenance import file_sha256  # noqa: E402
from war_extraction.utils.publish_rules import (  # noqa: E402
    is_overbroad_event,
    load_publish_rules,
)
from war_extraction.utils.relation_rules import (  # noqa: E402
    build_event_start_years,
    evidence_order,
)
from war_extraction.utils.value_parsing import (  # noqa: E402
    event_identity_key,
    parse_year_for_order,
    split_multi_value,
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
    # —— 2026-09-27 第三阶段整本重跑后新登记的一批（判定：不加入枚举，保留 raw、由候选区挡住）——
    "event_place_relations/同盟方": (
        "模型把**组织**关系名写进了事件-地点关系：`商汤灭夏之战 —[同盟方]→ 薛`、`→ 有莘氏` 共 2 条。"
        "根子在**目标实体类型**：`薛`/`有莘氏` 是方国/部落（组织）却被记成了地点，于是组织关系名"
        "只能挂到地点类别下（同一段历史里 `同盟方` 用在事件-组织上是对的）。**不加进地点关系枚举**"
        "——那等于承认一个错的语义。实测发布子集里 0 条。"
    ),
    "event_place_relations/支援地": (
        "新造的关系名：`郑成功收复台湾之战 —[支援地]→ 巴达维亚` 1 条。地点关系里已有"
        "`补给地`/`驻防地` 一类语义，再收 `支援地` 就是在枚举边上开新口子；留 raw、不进发布子集。"
    ),
    "event_organization_relations/驻防地": (
        "`辽东之战 —[驻防地]→ 征清大总督府` 1 条：`驻防地` 是**地点**关系名，却挂到了事件-组织上"
        "（组织不可能是「驻防地」）。与 `指挥所` 同一形态：模型误分类。不加进组织关系枚举。"
    ),
    "event_person_relations/自杀": (
        "`北仓杨村之战 —[自杀]→ 裕禄`、`河西务之战 —[自杀]→ 李秉衡` 共 2 条。人物关系枚举里有"
        "`阵亡`；`自杀` 是**死法、不是关系**。不加进枚举（那会开一个没有边界的新枚举口子）。"
    ),
    "Role/地方势力": (
        "`刘琨`、`曹疑`（西晋）的 `Role` 填了 `地方势力`——那是 **OrgType 的取值**，属字段混用。"
        "人物角色枚举里没有它，也不该有（角色不是势力类型）。实测发布子集里 0 条。"
    ),
    "EventType/交战": (
        "`赤眉军击败景尚、王党之战`、`赤眉军全歼王匡、廉丹之战` 的 `EventType` 填了 `交战`："
        "这两条的 `Action` 也都是「交战」，即模型把**动作值抄进了类型字段**。判定（2026-09-27）："
        "不把 `交战` 加进枚举与 RAG 词典（它是动作词，不是事件类型）。实测发布子集里 0 条。"
    ),
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

    # **目标端**（2026-09-27 补）：原先只查事件端，于是"目标实体不在实体表里"的边
    # 看不见——而导入时 `_find_person/_find_org/_find_place` 都是精确匹配，对不上就**静默丢掉**
    # （实测新产物 167 条没进库：人物 82 / 组织 82 / 地点 2 / 事件-事件 1）。口径与导入器一致：
    # 精确匹配；地点两侧（`geo_name` / `modern_name`）都算。
    entities = payload.get("entities") or {}
    tail_pools = {
        "event-place": {(p.get("geo_name") or "").strip() for p in entities.get("places") or []}
                      | {(p.get("modern_name") or "").strip() for p in entities.get("places") or []},
        "event-organization": {(o.get("OrgName") or "").strip() for o in entities.get("organizations") or []},
        "event-person": {(p.get("PersonName") or "").strip() for p in entities.get("persons") or []},
    }
    tail_fields = {"event-place": ("modern_name", "geo_name"),
                   "event-organization": ("OrgName",),
                   "event-person": ("PersonName",)}

    report = {}
    for attribute, category in RELATION_CATEGORIES.items():
        exact_missing = 0
        normalized_missing = 0
        tail_missing = 0
        samples = []
        tail_samples = []
        pool = tail_pools.get(category)
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
            if pool:
                target = next(((rel.get(f) or "").strip() for f in tail_fields[category]
                               if (rel.get(f) or "").strip()), "")
                if target and target not in pool:
                    tail_missing += 1
                    if len(tail_samples) < 5:
                        tail_samples.append(target)
        report[category] = {
            "exact_dangling": exact_missing,
            "normalized_dangling": normalized_missing,
            "tail_missing": tail_missing,
            "samples": samples,
            "tail_samples": tail_samples,
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


def _normalized_name_set(payload: dict, kind: str, key: str, normalizer) -> set:
    """产物自带实体名单的归一集合（`kind` 取 places/persons/organizations）。"""
    rows = (payload.get("entities") or {}).get(kind) or []
    return {
        normalizer.normalize_entity_name(row.get(key))
        for row in rows
        if row.get(key)
    }


def _event_text(event: dict) -> str:
    """事件的判定文本（与发布期 `main._is_summary_only_event` 同一口径）。"""
    name = event.get("EventName") or ""
    return f"{name} {event.get('source_text') or ''} {event.get('Remark') or ''}"


def _as_year_source(event: dict):
    """
    把产物里的事件字典包成 `build_event_start_years` 要的形状（只要有那两个属性）。

    为什么不直接传字典：那个函数按**属性**取名（生产链路上传的是 pydantic 事件对象），
    字典传进去会静默得到空索引——"年份能判 0 条"这种数看起来像结论，其实是适配错了。
    """
    return SimpleNamespace(EventName=event.get("EventName"), StartDate=event.get("StartDate"))


def check_party_not_org(payload: dict, normalizer) -> dict:
    """
    攻守方里的"非组织值"：`Aggressor` / `Defender` 填了人物名或地点名。

    **这是"形态计数"，不是"错误判定"。** 字段的语义是"方"（组织/势力），填人名说明模型
    把"人"当成了"方"（实测 `曹操`、`铁木真`、`郑成功` 这类），但是否算错需要人工抽样
    （B 组抽样 → E1/E3）。这里只给出**绝对数字**，让"改完有没有变少"可验证。

    口径：拿产物**自己的实体名单**交叉比对（`各字段词表`），不是另建一张词表——

    - 值落在 `persons` 名单里 → **人名混入**；
    - 值落在 `places` 名单里、且**不在** `organizations` 名单里 → **地点名混入**。
      两国都在的（`唐`/`魏`/`秦`/`契丹` 这类朝代与国名）按**组织**算，不算混入——
      否则这一项会被朝代名灌满，那既不是噪声也不是"填错"，只是同一个名字在两张表里都有。

    `EntityClassifier.is_valid_org_name` 在这里排不上用场：它对短名一律返回 True
    （只拦显式低质量名单与"…集团/…势力"），所以"像不像组织"判不出混入，名单交叉才判得出。
    """
    persons = _normalized_name_set(payload, "persons", "PersonName", normalizer)
    places = _normalized_name_set(payload, "places", "geo_name", normalizer)
    orgs = _normalized_name_set(payload, "organizations", "OrgName", normalizer)

    per_field = {"Aggressor": {"person": 0, "place": 0}, "Defender": {"person": 0, "place": 0}}
    samples = []
    total_values = 0
    rows_with_hit = set()
    for index, event in enumerate(_events_of(payload)):
        for field in ("Aggressor", "Defender"):
            for value in split_multi_value(event.get(field)):
                total_values += 1
                key = normalizer.normalize_entity_name(value)
                kind = None
                if key in persons:
                    kind = "person"
                elif key in places and key not in orgs:
                    kind = "place"
                if kind is None:
                    continue
                per_field[field][kind] += 1
                rows_with_hit.add(index)
                if len(samples) < 10:
                    samples.append({"EventName": event.get("EventName"), "field": field,
                                    "value": value, "kind": kind})
    person_total = sum(item["person"] for item in per_field.values())
    place_total = sum(item["place"] for item in per_field.values())
    return {
        "total_party_values": total_values,
        "person_as_party": person_total,
        "place_as_party": place_total,
        "total": person_total + place_total,
        "ratio": ((person_total + place_total) / total_values) if total_values else 0.0,
        "events_affected": len(rows_with_hit),
        "per_field": per_field,
        "samples": samples,
    }


def check_weak_result(payload: dict, normalizer) -> dict:
    """
    `Result` 的"看起来有值、其实无法判定胜负"：占位词（`不详`/`未知`）或弱值词。

    弱值词表复用 `config/publish_rules.json` 的 `weak_result_tokens`（发布过滤
    `has_strong_result` 用的就是这一份），所以"发布侧认定的弱结果"与"体检报出来的弱结果"
    是同一个口径——否则会出现"发布过滤剔掉了、体检却说没问题"。

    分两栏报：`placeholder`（如实写"不详"，**正确行为**）与 `weak_token`
    （写了"周军获胜，暂时控制局势"这类句子——有值、但判不出胜负）。门禁看两者之和。
    """
    rules = load_publish_rules()
    weak_tokens = rules["weak_result_tokens"]
    placeholder = weak_token = 0
    samples = []
    for event in _events_of(payload):
        value = (event.get("Result") or "").strip()
        if not value:
            continue
        if normalizer.is_placeholder_value(value):
            placeholder += 1
        elif any(token in value for token in weak_tokens):
            weak_token += 1
        else:
            continue
        if len(samples) < 10:
            samples.append({"EventName": event.get("EventName"), "Result": value})
    total_events = len(_events_of(payload))
    return {
        "total_events": total_events,
        "placeholder": placeholder,
        "weak_token": weak_token,
        "undecidable": placeholder + weak_token,
        "ratio": ((placeholder + weak_token) / total_events) if total_events else 0.0,
        "samples": samples,
    }


def check_overbroad_candidates(payload: dict) -> dict:
    """
    「过宽概括」的**形态式候选**：只看事件名的形态，产一份清单给 C 组补标用。

    **它与 `overbroad_events`（标记表）用途不同，不要合并。**
    `config/publish_rules.json` 的 `overbroad_event_markers` 是**过滤输入**——发布过滤与
    抽取器的识别过滤都用它，改它等于**预测侧单方面收紧**（口径要求预测侧与 gold 补标
    **两侧同时改**）。而这一项只是"哪些名字看起来像战役级以上的概括"的搜索式，
    **永远不参与过滤**，所以它可以现在就动、且可以在 gold 补标之前先给人工一份工作清单。

    形态取自本产物的实测（`data/annotations` 的口径见 README）：
    名字含「战争」24 条、结尾「之役」1 条、含「统一」6 条，**并集 29 条**。
    **刻意不含「战役」与「会战」**：实测那两类（`霍邑战役`、`邙山会战`、`旅顺战役`…）
    都是具体战役，拿它们当信号只会往清单里灌噪声。
    """
    hits = []
    for event in _events_of(payload):
        name = event.get("EventName") or ""
        matched = [pattern for pattern in ("战争", "统一") if pattern in name]
        if name.endswith("之役"):
            matched.append("结尾「之役」")
        if not matched:
            continue
        hits.append({
            "EventName": name,
            "DynastyName": event.get("DynastyName"),
            "patterns": matched,
            "source_text": (event.get("source_text") or "")[:60],
        })
    return {
        "total_events": len(_events_of(payload)),
        "candidates": len(hits),
        "ratio": (len(hits) / len(_events_of(payload))) if _events_of(payload) else 0.0,
        "samples": hits[:20],
    }


def check_containment_vs_time(payload: dict, normalizer) -> dict:
    """
    包含关系的父子区间**与时间字段矛盾**：子事件的时间区间没被父事件的区间包含。

    语义上 `包含关系` 是"A 包含 B"（父含子，见 `relation_prompts.py`：原文有"分兵"、
    "一路…一路…"），所以有一条不依赖参考集的机械检查：父事件的起止年份应覆盖子事件的。
    实测旧产物 28 条 `包含关系`、两侧年份都能解析的 28 条里 **10 条**不覆盖。

    **命名要读准：这是"与时间字段矛盾"，不是"方向错了"。** 父/子方向本身就是模型给的，
    所以这里的矛盾有两种成因——方向标反了，或时间字段填错了，两者都无法从这个检查区分
    （实测样本里成对出现的正反两条即前一种）。**先只报数，不进门禁**：存量摸清之后再决定
    是当门禁还是当候选区标记。
    """
    spans = {}
    for event in _events_of(payload):
        name = normalizer.normalize_event_name(event.get("EventName"))
        if not name or name in spans:
            # 同名多行只取第一处：身份键（名称+朝代+时间+地点）不同的事件会同名，
            # 取并集会让区间无端变宽、把真实矛盾掩盖掉。
            continue
        start = parse_year_for_order(event.get("StartDate"))
        end = parse_year_for_order(event.get("EndDate"))
        if start is None and end is None:
            continue
        if start is None:
            start = end
        if end is None:
            end = start
        spans[name] = (min(start, end), max(start, end))

    relations = (payload.get("relations") or {}).get("event_event_relations") or []
    containment = [rel for rel in relations
                   if normalizer.normalize_relation(rel.get("relation")) == "包含关系"]
    comparable = contradictions = 0
    samples = []
    for rel in containment:
        name_a = normalizer.normalize_event_name(rel.get("EventName_A"))
        name_b = normalizer.normalize_event_name(rel.get("EventName_B"))
        if name_a not in spans or name_b not in spans:
            continue
        comparable += 1
        parent, child = spans[name_a], spans[name_b]
        if parent[0] <= child[0] and child[1] <= parent[1]:
            continue
        contradictions += 1
        if len(samples) < 10:
            samples.append({
                "EventName_A": rel.get("EventName_A"), "span_a": list(parent),
                "EventName_B": rel.get("EventName_B"), "span_b": list(child),
            })
    return {
        "relations_total": len(containment),
        "comparable": comparable,
        "contradictions": contradictions,
        "samples": samples,
    }


def check_overbroad_events(payload: dict, normalizer) -> dict:
    """
    疑似"过宽概括"事件（阶段三 E4 与 C 组补标的输入）：用**合并后**的特征表统计。

    特征表在 `config/publish_rules.json` 的 `overbroad_event_markers`，判定函数是
    `publish_rules.is_overbroad_event`——与 `main` 的发布过滤、抽取器的识别过滤**同一份**。
    这一项不改产物，只是把"哪些条疑似过宽"列出来给人工补标用。

    **口径比抽取期宽，所以是上界**：这里用事件名 + `source_text` + `Remark`（发布期口径），
    而识别期只有证据句（`evidence`）。同一事件在两处的命中结果可能不同——
    要精确复核某一条，得回抽取期的证据句。
    """
    rules = load_publish_rules()
    hits = []
    marker_hits = Counter()
    for event in _events_of(payload):
        name = event.get("EventName") or ""
        text = _event_text(event)
        if not is_overbroad_event(name, text, rules):
            continue
        markers = rules["overbroad_event_markers"]
        matched = [marker for marker in markers["event_name_markers"] if marker in name]
        matched += [marker for marker in markers["text_markers"] if marker in text]
        if name.strip() in set(markers["event_names"]):
            matched.append("事件名名单")
        marker_hits.update(matched)
        hits.append({
            "EventName": name,
            "DynastyName": event.get("DynastyName"),
            "markers": matched,
            "source_text": (event.get("source_text") or "")[:60],
        })
    return {
        "total_events": len(_events_of(payload)),
        "suspected": len(hits),
        "ratio": (len(hits) / len(_events_of(payload))) if _events_of(payload) else 0.0,
        "marker_hits": dict(marker_hits.most_common()),
        # 前 20 条样本：C 组补标要的是"逐条看名字"，给多了没人看
        "samples": hits[:20],
    }


def check_supreme_commander_claims(payload: dict, normalizer) -> dict:
    """
    同一事件挂 ≥2 条 `统帅` 关系的事件数——**"指挥官列表"被当成"最高指挥官"的机械痕迹**。

    **为什么值得门禁。** 事件的 `Commanders` 字段定义是"双方主要军事指挥官"（一个列表），
    而关系枚举里 `统帅` 是"最高指挥官"（单数语义）。规则原先把列表里的**每一个人**
    都派生成 `统帅`（阶段三 E2 已改为 `将领`），于是"同一场战事有 16 个最高指挥官"
    这种自相矛盾在产物里成规模出现。实测旧产物：有 `统帅` 关系的 977 个事件里
    **698 个（71.4%）挂着 ≥2 条**，最多 17 条（`平定三藩之乱`、`安庆保卫战`）。

    **目标不是 0**：双方各有一位主帅是合理的（`赤壁之战` 的曹操与周瑜）。
    这一项要看的量级——改完（规则不再批量制造、只剩关系阶段按原文抽的）应当**显著低于 698**。
    所以它记的是"基线值 + 应当大幅下降"，与 D3 那三项同一读法。
    """
    events = _events_of(payload)
    known = {(event.get("EventName") or "").strip() for event in events if event.get("EventName")}
    counter = Counter()
    for rel in (payload.get("relations") or {}).get("event_person_relations") or []:
        if normalizer.normalize_relation(rel.get("relation")) != "统帅":
            continue
        name = (rel.get("EventName") or "").strip()
        if name in known:
            counter[name] += 1
    multiple = {name: count for name, count in counter.items() if count >= 2}
    return {
        "events_with_claim": len(counter),
        "events_with_multiple": len(multiple),
        "ratio": (len(multiple) / len(counter)) if counter else 0.0,
        "max": max(counter.values()) if counter else 0,
        "top": Counter(multiple).most_common(5),
    }


def check_undecidable_direction(payload: dict, normalizer) -> dict:
    """
    `顺承关系` 的方向**判不出来**的条数——方向规则修不到的那一块，只看数不改产物。

    **为什么要报。** 方向判定（`relation_rules.resolve_event_event_direction`）按"证据里两个
    事件名的先后 → 两个事件的起始年份"两档来定；两档都判不出时它**保持原样**并返回 `False`
    （设计就是"不猜"）。问题在于"保持原样"事后完全看不出来——`check_direction_vs_time` 只报
    "方向与时间矛盾"那一类，而"根本没有依据可判"的这一类没有出口。

    实测旧产物 914 条事件-事件关系里 **322 条**（35%）落在这一档，B 组抽样里那 7 条方向错例中
    有 2 条正是它（`周文王剪商羽翼之战` 一类两个事件都没有绝对年份；`汉军再攻洛阳` 两条年份相同）。
    重跑后这个数就是"方向仍然只能沿用模型给的顺序"的规模——**残余风险，不是错误数**。

    逐条判定用与产品同一条规则，所以它随规则一起变；这也让它成了 E2 方向改动的对照数字。
    """
    events = _events_of(payload)
    # `build_event_start_years` 读的是**对象属性**（生产链路上传的是 pydantic 事件），
    # 而这里的产物是字典——直接传进去会得到空索引，于是"年份能判"恒为 0（实测踩过）。
    # 用一个最小适配层保持"规则只有一份"，而不是在这里重写一遍取最早的逻辑。
    year_index = build_event_start_years(normalizer, [_as_year_source(e) for e in events])
    by_evidence = by_year = undecidable = 0
    samples = []
    for rel in (payload.get("relations") or {}).get("event_event_relations") or []:
        if normalizer.normalize_relation(rel.get("relation")) != "顺承关系":
            continue
        name_a = rel.get("EventName_A") or ""
        name_b = rel.get("EventName_B") or ""
        if evidence_order(rel.get("evidence") or "", name_a, name_b) in ("a_first", "b_first"):
            by_evidence += 1
            continue
        year_a = year_index.get(normalizer.normalize_event_name(name_a))
        year_b = year_index.get(normalizer.normalize_event_name(name_b))
        if year_a is not None and year_b is not None and year_a != year_b:
            by_year += 1
            continue
        undecidable += 1
        if len(samples) < 10:
            samples.append({"EventName_A": name_a, "EventName_B": name_b,
                            "year_a": year_a, "year_b": year_b})
    total = by_evidence + by_year + undecidable
    return {
        "relations_total": total,
        "decided_by_evidence": by_evidence,
        "decided_by_year": by_year,
        "undecidable": undecidable,
        "undecidable_ratio": (undecidable / total) if total else 0.0,
        "samples": samples,
    }


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


def build_report(pred_path: Path, annotation_dir: Path = None) -> dict:
    payload = _load(pred_path)
    normalizer = Normalizer()
    return {
        "source": str(pred_path),
        # **产物指纹必须与数字一起记**（整改方案附录 B 第 26 条）：只记路径的话，
        # "这份报告读的是哪一版产物"事后无从证明——两份基线看着一样、
        # 实际一份来自改前产物、一份来自改后产物，这种混淆这个项目已经付过一次学费。
        "source_sha256": file_sha256(pred_path),
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
        # 事件字段级形态（第三阶段 D3）：阶段三要改的就是这几个字段，没有这几项的话
        # "改完有没有变好"只能靠看报告
        "party_not_org": check_party_not_org(payload, normalizer),
        "weak_result": check_weak_result(payload, normalizer),
        # 包含关系的父子区间与时间字段矛盾（第三阶段 D4，先只报数、不进门禁）
        "containment_vs_time": check_containment_vs_time(payload, normalizer),
        # 阶段三 E2：「指挥官列表」被当成「最高指挥官」的机械痕迹（进门禁，见下）
        "supreme_commander_claims": check_supreme_commander_claims(payload, normalizer),
        # 阶段三 E2：顺承关系里方向**判不出来**的条数（只报数——它是残余风险，不是错误数）
        "undecidable_direction": check_undecidable_direction(payload, normalizer),
        # 疑似"过宽概括"清单（第三阶段 D5，给 C 组补标与 E4 用，不进任何门禁）
        "overbroad_events": check_overbroad_events(payload, normalizer),
        # 过宽概括的**形态式**候选（阶段三 E4-a：标记表在本产物命中 0，这一项才给得出清单）
        "overbroad_candidates": check_overbroad_candidates(payload),
        "direction_vs_time": check_direction_vs_time(payload, normalizer),
        "evidence_reuse": check_evidence_reuse(payload, normalizer),
        "relation_volume": check_relation_volume(payload),
        # 默认查仓库里那份旧的；成稿的 v2 用 `--annotations <目录>` 指过来做结构自检
        "annotations": check_annotations(annotation_dir or (MODULE_ROOT / "data" / "annotations")),
        # 已知例外：让"永远非零的门禁项"有解释，免得下一个人反复查同一件事
        "known_enum_exceptions": dict(KNOWN_ENUM_EXCEPTIONS),
    }


def _known_exception_count(report: dict, categories=None) -> int:
    """
    报告里**已登记**的枚举外例外条数（按 `类别/取值` 键逐条匹配）。

    为什么要按键匹配而不是 `len(KNOWN_ENUM_EXCEPTIONS)` 硬减：硬减会在将来新增真实问题时
    把不该扣的也扣掉（新增的枚举外取值没登记，却被减法一并吞了），门禁就再也拦不住它。
    同时要求**该取值确实出现在这份报告里**——已消失的例外不再扣，避免"旧例外消失了、
    新问题冒出来"时两者互相抵消。
    """
    total = 0
    for key in KNOWN_ENUM_EXCEPTIONS:
        category, _, value = key.partition("/")
        if categories is not None and category not in categories:
            continue
        outside = ((report.get("enum_values") or {}).get(category) or {}).get("outside_enum") or {}
        if value in outside:
            total += 1
    return total


def _placeholder_gate(report: dict) -> int:
    """
    门禁「事件字段写占位词」的取值：各字段占位词行数之和。

    **这一项上升可能是对的**：如实写"不详"是正确行为（整改方案 2.6.4），阶段三 E1 的
    改动方向也正是"不确定就写不详"。所以它被当门禁用是为了"发现形态突变"（比如某次改完
    占位词从 1897 跳到 2500，说明提示词在乱写不详），而不是"越低越好"。
    真要压低它，方向是"补全可判定的值"，不是让模型猜。

    **取值故意写成严格下标**（不是 `.get` 兜 0）：报告里缺这一节说明检查坏了，该报错；
    而"基线里缺这一节"（旧报告）由 `compare_with_baseline` 捕获 `KeyError` 判为无法比较。
    写成 `.get(...) or 0` 会让旧基线看起来"这一项是 0"，于是新项第一次对照就报变差。
    """
    rows = report["placeholder_as_value"]["placeholder_rows"]
    return sum(rows.values())


#: 可以当门禁的绝对数字（越小越好）。键是取自报告的取值路径。
_GATE_PATHS = [    ("悬空边（归一）", lambda r: sum(v["normalized_dangling"] for v in r["dangling_relations"].values())),
    ("枚举外 OrgType", lambda r: len(r["enum_values"]["OrgType"]["outside_enum"])
                             - _known_exception_count(r, {"OrgType"})),
    ("枚举外 Role", lambda r: len(r["enum_values"]["Role"]["outside_enum"])
                           - _known_exception_count(r, {"Role"})),
    ("枚举外 EventType", lambda r: len(r["enum_values"]["EventType"]["outside_enum"])
                                - _known_exception_count(r, {"EventType"})
                                + r["enum_values"]["EventType"]["empty"]),
    # 扣掉已登记的例外（`KNOWN_ENUM_EXCEPTIONS`，逐键匹配）。不扣的话这项**恒 ≥ 1**
    # ——`指挥所` 是已确认不打算修的那一条——而一个永远非零的门禁项只会让下一个人
    # 反复去查同一件事。扣减只影响门禁值与比对照，报告里仍会把例外点名列出。
    ("枚举外关系名", lambda r: sum(len(r["enum_values"][key]["outside_enum"]) for key in RELATION_CATEGORIES)
                             - _known_exception_count(r, set(RELATION_CATEGORIES))),
    ("重复行合计", lambda r: sum(r["duplicates"].values())),
    ("残缺年份", lambda r: r["residual_years"]["count"]),
    ("方向与时间矛盾", lambda r: r["direction_vs_time"]["count"]),
    ("证据为空的关系", lambda r: r["evidence_reuse"]["evidence_empty"]),
    ("标注 head 不在事件表", lambda r: sum(v["head_not_in_events"] for v in (r.get("annotations") or {}).get("relations", {}).values())),
    ("标注 tail 不在对应名单", lambda r: sum(v["tail_not_in_scope"] for v in (r.get("annotations") or {}).get("relations", {}).values())),
    ("标注重复关系行", lambda r: sum(v["duplicate_rows"] for v in (r.get("annotations") or {}).get("relations", {}).values())),
    ("标注残缺年份", lambda r: ((r.get("annotations") or {}).get("residual_years") or {}).get("count", 0)),
    # --- 事件字段级（第三阶段 D3）。这三项**都是基线值，不是"应当为 0"**：旧产物上
    # 就分别有 1897 / 255 / 26 条命中，门禁的语义是"不应比上一版更多"。第一次纳入
    # （对着 `health_check_before.json` 这种旧报告）时基线里没有这几节，取值会抛 KeyError，
    # `compare_with_baseline` 据此判为"无法比较"并跳过——不然门禁一建就是红的（A2 踩过）。
    ("事件字段写占位词", lambda r: _placeholder_gate(r)),
    ("攻守方非组织值", lambda r: (r["party_not_org"]["person_as_party"]
                             + r["party_not_org"]["place_as_party"])),
    ("Result 弱值或占位", lambda r: r["weak_result"]["undecidable"]),
    # --- 阶段三 E2：规则不再把「指挥官列表」逐人派生成「统帅」之后，这一项应当**大幅下降**
    # （基线 698，见 check_supreme_commander_claims 的 docstring：目标不是 0，
    # 双方各一位主帅是合理的）。它落在旧产物上，所以是"改了抽取侧 + 重跑"才看得到变化的门禁。
    ("同一事件多条统帅", lambda r: r["supreme_commander_claims"]["events_with_multiple"]),
]


def print_report(report: dict) -> None:
    print("=" * 74)
    print(f"产物体检: {report['source']}")
    # 指纹进表头：报告尾巴经常被单独贴进文档或聊天里，没有指纹的片段事后无法确认读的是哪份产物
    sha = report.get("source_sha256")
    if sha:
        print(f"产物指纹: sha256 {sha[:16]}…")
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
    dangling_tail = sum(v.get("tail_missing") or 0 for v in report["dangling_relations"].values())
    if dangling_tail:
        print(f"  悬空边（目标端，导入时会被静默丢掉）: {dangling_tail}")
        for key, item in report["dangling_relations"].items():
            if item.get("tail_missing"):
                print(f"    {key}: {item['tail_missing']} 条，例 {item['tail_samples']}")
    for key in RELATION_CATEGORIES:
        item = report["enum_values"][key]
        print(f"  {key}: {item['total']} 条，枚举外 {item['outside_enum']}")
    # 把"已知例外"指出来：报告里出现某个固定数字时，先看它是不是已登记的那个
    # 四类都要点：只列关系类的话，登记在 `Role`/`EventType`/`OrgType` 上的例外就成了
    # "门禁扣了、报告不说"——那正是这张表要避免的事（门禁扣减的意义是"别让人反复查同一件事"）。
    current_exceptions = {
        f"{key}/{value}"
        for key in list(RELATION_CATEGORIES) + ["OrgType", "Role", "EventType"]
        for value in (report["enum_values"].get(key) or {}).get("outside_enum") or {}
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

    print("\n[事件字段级形态]（阶段三要改的就是这几个字段）")
    party = report["party_not_org"]
    print(f"  攻守方填了人名/地点名: {party['total']} 处"
          f"（人名 {party['person_as_party']} / 地点名 {party['place_as_party']}），"
          f"占 {party['total_party_values']} 个攻守方取值中的 {party['ratio']:.1%}，"
          f"涉及 {party['events_affected']} 个事件")
    print(f"    Aggressor: 人名 {party['per_field']['Aggressor']['person']}"
          f" / 地点名 {party['per_field']['Aggressor']['place']}；"
          f"Defender: 人名 {party['per_field']['Defender']['person']}"
          f" / 地点名 {party['per_field']['Defender']['place']}")
    for sample in party["samples"][:3]:
        print(f"    例: {sample['EventName']} 的 {sample['field']} = {sample['value']}"
              f"（{sample['kind']}）")
    weak = report["weak_result"]
    print(f"  Result 无法判定胜负: {weak['undecidable']} 条={weak['ratio']:.1%}"
          f"（占位词 {weak['placeholder']} / 弱值词 {weak['weak_token']}）")
    for sample in weak["samples"][:3]:
        print(f"    例: {sample['EventName']} 的 Result = {sample['Result']}")

    supreme = report["supreme_commander_claims"]
    print(f"  同一事件多条统帅: {supreme['events_with_multiple']} 个事件"
          f"（有统帅关系的 {supreme['events_with_claim']} 个里占 {supreme['ratio']:.1%}，"
          f"最多 {supreme['max']} 条）——`Commanders` 是列表而 `统帅` 是单数语义；"
          f"目标不是 0（双方各一位主帅是合理的）")
    if supreme["top"]:
        print(f"    最多的: {supreme['top'][:3]}")

    direction = report["undecidable_direction"]
    print(f"  顺承方向判不出来: {direction['undecidable']} 条"
          f"（共 {direction['relations_total']} 条顺承关系，占 {direction['undecidable_ratio']:.1%}；"
          f"证据能判 {direction['decided_by_evidence']} / 年份能判 {direction['decided_by_year']}）"
          f"——方向规则两档都判不出时保持原样，这一档是**残余风险**不是错误数")

    containment = report["containment_vs_time"]
    print(f"  包含关系与时间字段矛盾: {containment['contradictions']} 条"
          f"（共 {containment['relations_total']} 条包含关系，"
          f"两侧年份都可解析 {containment['comparable']} 条）"
          f"——**方向或时间字段至少一个有问题**，本检查分不出是哪个")
    for sample in containment["samples"][:3]:
        print(f"    例: 父 {sample['EventName_A']}{sample['span_a']} ⊅ 子 "
              f"{sample['EventName_B']}{sample['span_b']}")

    overbroad = report["overbroad_events"]
    print(f"  疑似「过宽概括」事件: {overbroad['suspected']} 条（{overbroad['ratio']:.1%}）"
          f"，命中特征 {overbroad['marker_hits']}")
    print("    （清单给 C 组补标与 E4 用；此处口径比抽取期宽，是上界）")
    for sample in overbroad["samples"][:5]:
        print(f"    例: {sample['EventName']}（{sample['DynastyName']}）"
              f"[{'/'.join(sample['markers'])}] {sample['source_text']}")

    candidates = report["overbroad_candidates"]
    print(f"  过宽概括**形态式候选**（E4 给 C 组的工作清单）: {candidates['candidates']} 条"
          f"（{candidates['ratio']:.1%}）——名字含「战争」/「统一」或结尾「之役」")
    print("    （与上一项的差别：上一项是**过滤输入**（标记表，改它等于预测侧单方面收紧、"
          "要与 gold 补标同时动），这一项只产清单、永不参与过滤）")
    for sample in candidates["samples"][:5]:
        print(f"    例: {sample['EventName']}（{sample['DynastyName']}）"
              f"[{'/'.join(sample['patterns'])}]")

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


def compare_with_baseline(report: dict, baseline: dict) -> tuple:
    """
    返回 `(变差项, 无法比较项)`，供门禁判定。

    **无法比较**：基线里没有这一项要读的那节（旧报告生成于该项加入之前）。这时**不能**
    当成"基线是 0"——那会让每个新门禁项在第一次对照时都报变差（门禁一建就是红的）。
    取不到值就跳过，并在报告里点名，让人知道"这一项从此版才开始比"。
    """
    regressions = []
    skipped = []
    for label, getter in _GATE_PATHS:
        try:
            now = getter(report)
        except (KeyError, TypeError):
            # 新报告自己取不到值＝这项检查坏了，不是基线的问题：照旧报错
            raise
        try:
            before = getter(baseline)
        except (KeyError, TypeError):
            skipped.append((label, now))
            continue
        if now > before:
            regressions.append((label, before, now))
    return regressions, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description="产物机械体检（不依赖参考集）")
    parser.add_argument("--pred", default=str(DEFAULT_PRED), help="产物 JSON 路径")
    parser.add_argument("--json", default=None, help="把报告写到这个路径，供逐版比对")
    parser.add_argument("--baseline", default=None, help="上一版体检报告；任何一项变差就返回退出码 1")
    parser.add_argument("--annotations", default=None,
                        help="参考集目录（默认查 data/annotations；成稿的 v2 用它做结构自检，"
                             "看 head/tail 违规、重复行、残缺年份是否归零）")
    parser.add_argument("--note", default=None,
                        help="写进报告的说明。**入库的基线报告都该带一句**：这份报告对应哪份产物、"
                             "为什么与上一份基线并排放着（否则它就是一份没有来历的裸 JSON）")
    args = parser.parse_args()

    pred_path = Path(args.pred)
    if not pred_path.is_file():
        print(f"产物不存在: {pred_path}")
        return 2

    report = build_report(pred_path, Path(args.annotations) if args.annotations else None)
    if args.note:
        report["note"] = args.note
    print_report(report)

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n报告已写入: {out}")
        print(f"  产物指纹: {report['source_sha256']}")

    if args.baseline:
        baseline = _load(Path(args.baseline))
        regressions, skipped = compare_with_baseline(report, baseline)
        if skipped:
            print("\n[对照] 基线里没有这些项（该项在此之后才加入），本次不比较：")
            for label, now in skipped:
                print(f"  {label}: 本次 {now}")
            print("  这不是漏检。日常对照请用**当前基线** "
                  "evaluation/baseline/health_check_after.json（它含全部门禁项）；"
                  "本次报告若要当基线，重新生成时记得带 --note 说明来历。")
        if regressions:
            print("\n[回归] 以下门禁项比基线变差：")
            for label, before, now in regressions:
                print(f"  {label}: {before} → {now}")
            return 1
        print("\n[回归] 门禁项均未变差。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
