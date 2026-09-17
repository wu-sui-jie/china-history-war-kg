"""F03 问题类型 → 图谱查询策略（docs/features/03-graph-retrieval.md）。

策略仅决定"查什么/怎么查"，具体执行在 search.py。
"""

from __future__ import annotations

from contracts.question import QuestionType

# 关系型问题更关注"组织/人物/地点"这些参与关系
RELATION_TYPES = {"事件", "人物", "组织", "地点"}

# 事件-事件关系（event_event）参与的关系名集合
EVENT_EVENT_RELATIONS = {
    "因果关系", "顺承关系", "并列关系", "包含关系", "条件关系",
}


def strategy_for(qtype: QuestionType) -> str:
    """返回查询策略 key。unknown / 不在表内一律保守单实体一跳。"""
    table = {
        QuestionType.SINGLE_ENTITY: "single_entity",
        QuestionType.RELATION: "relation",
        QuestionType.EVENT_EVENT: "event_event",
        QuestionType.COMPARISON: "comparison",
        QuestionType.TIMELINE: "timeline",
        QuestionType.BACKGROUND: "background",
        QuestionType.UNKNOWN: "single_entity",
    }
    return table.get(qtype, "single_entity")
