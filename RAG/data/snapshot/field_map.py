"""F09 关系-事件卡片字段映射表生成（data-contract.md L406-419）。

用途：F05 做 field_vs_triple 冲突判定时读取本表，不在各模块硬编码。

设计要点：
- 同一关系名在不同目标实体类型下语义不同，因此映射表按 (relation, target_type) 行细分。
  例如"发起方"目标为组织 → 归 aggressor 字段(exact)；目标为人物 → 归 persons 字段(contains)。
- 判定方式与 data-contract 一致：
  * exact：图谱 relation 值 与 卡片字段值 文本归一化后不一致 → 冲突；
  * contains：卡片字段值（列表）不含三元组另一端名称 → 冲突；
  * none：不参与 field_vs_triple（事件-事件关系）。
- 治理中出现的任何 (relation, target_type) 组合在表中都有一行；无法归组的落入 unknown，
  保证 F05 不会因缺行崩溃。
"""

from __future__ import annotations

# 目标类型：人物/组织/地点/事件（来自关系另一端实体类型）
T_PERSON = "人物"
T_ORG = "组织"
T_PLACE = "地点"
T_EVENT = "事件"

# 组织侧"发起/防守"类：图谱 object(组织) → 卡片 aggressor/defender 字段(exact)
_ORG_AGGRESSOR_RELS = {"发起方"}
_ORG_DEFENDER_RELS = {"防守方"}
# 人物侧发起/防守（极少）：归 persons 字段
_PERSON_SIDE_ROLE_RELS = {"发起方", "防守方"}
# 人物侧其他关系：归 persons 字段（contains，不做职务语义）
_PERSON_RELS = {
    "统帅", "将领", "参与者", "君主", "谋士", "阵亡", "投降", "俘虏", "使者",
    "叛变", "可汗", "被俘", "防守方统帅", "同盟方", "向导", "监督", "支援方",
}
# 组织侧其他关系（投降/支援/参战/同盟/被俘/议和/调停…）：归 organizations 字段（contains）
_ORG_OTHER_RELS = {
    "投降方", "支援方", "参战方", "同盟方", "被俘方", "议和方", "调停方",
}
# 地点类关系：归 place 字段（contains）
_PLACE_RELS = {
    "主战场", "次要战场", "战略要地", "目的地", "途经地", "驻防地", "出发地",
    "议和地点", "补给地", "指挥所", "退守地", "登陆地", "会师地", "撤退地",
    "出边地", "集结地", "会师地点", "逃亡地", "伏击地", "终点",
}
# 事件间关系：不参与 field_vs_triple
_EVENT_EVENT_RELS = {"顺承关系", "并列关系", "因果关系", "包含关系", "条件关系"}

_GROUPS_META = [
    {"group": "aggressor", "card_field": "aggressor", "method": "exact",
     "note": "图谱发起方(组织)与卡片发起方文本归一化后不一致即冲突"},
    {"group": "defender", "card_field": "defender", "method": "exact",
     "note": "图谱防守方(组织)与卡片防守方文本归一化后不一致即冲突"},
    {"group": "place", "card_field": "place", "method": "contains",
     "note": "卡片地点字段不包含三元组另一端地点名即冲突"},
    {"group": "person", "card_field": "persons", "method": "contains",
     "note": "人物名称不在卡片人物字段中出现即冲突（不做职务语义对比）"},
    {"group": "org_other", "card_field": "organizations", "method": "contains",
     "note": "组织类关系（投降/支援/参战/同盟等）比对卡片参战方字段"},
    {"group": "event_event", "card_field": None, "method": "none",
     "note": "事件间关系不参与 field_vs_triple"},
    {"group": "unknown", "card_field": None, "method": "none",
     "note": "未归组关系，暂不参与冲突判定"},
]
_GROUP_NOTES = {g["group"]: g["note"] for g in _GROUPS_META}


def _rules_for(relation: str, target_type: str) -> dict | None:
    """返回 (relation, target_type) 的归组规则；无法归组返回 None。"""
    if target_type == T_EVENT:
        if relation in _EVENT_EVENT_RELS:
            return {"group": "event_event", "card_field": None, "method": "none"}
        return None
    if target_type == T_ORG:
        if relation in _ORG_AGGRESSOR_RELS:
            return {"group": "aggressor", "card_field": "aggressor", "method": "exact"}
        if relation in _ORG_DEFENDER_RELS:
            return {"group": "defender", "card_field": "defender", "method": "exact"}
        if relation in _ORG_OTHER_RELS:
            return {"group": "org_other", "card_field": "organizations", "method": "contains"}
        if relation in _PERSON_SIDE_ROLE_RELS:  # 组织侧但不属于上述——罕见
            return {"group": "org_other", "card_field": "organizations", "method": "contains"}
        return None
    if target_type == T_PERSON:
        if relation in _PERSON_SIDE_ROLE_RELS or relation in _PERSON_RELS:
            return {"group": "person", "card_field": "persons", "method": "contains"}
        return None
    if target_type == T_PLACE:
        if relation in _PLACE_RELS:
            return {"group": "place", "card_field": "place", "method": "contains"}
        return None
    return None


def build_field_map(relation_type_pairs: set[tuple[str, str]] | None = None) -> dict:
    """生成随快照输出的映射表结构。

    relation_type_pairs: 治理中实际出现的 (relation, target_type) 组合。
    每个组合在表中都有一行（含 unknown 归组）。
    """
    rows = []
    pairs = relation_type_pairs or set()
    for relation, target_type in sorted(pairs):
        rule = _rules_for(relation, target_type)
        if rule is None:
            rule = {"group": "unknown", "card_field": None, "method": "none"}
            note = "治理阶段未归组的关系类型，暂不参与 field_vs_triple；请在 RAGv2 人工审核时补充归组"
        else:
            note = _GROUP_NOTES[rule["group"]]
        rows.append({
            "relation": relation,
            "target_type": target_type,
            "group": rule["group"],
            "card_field": rule["card_field"],
            "method": rule["method"],
            "note": note,
        })
    return {
        "version": "1",
        "description": "关系类型(×目标实体类型)→事件卡片字段映射表；F05 冲突判定读取本表，不在模块硬编码",
        "conflict_type": "field_vs_triple",
        "groups": _GROUPS_META,
        "mapping": rows,
    }
