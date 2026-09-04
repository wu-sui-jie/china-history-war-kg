"""F02 规则层：问题类型判定、朝代过滤器识别、指代消解（词典部分）。

问题类型规则（features/02 与 data-contract）：
single_entity / relation / event_event / comparison / timeline / background / unknown。

朝代过滤器识别：问题中出现 dicts.dynasty_aliases 中的朝代术语（"战国时期…"），
进入 F02Output.filters，不作为图谱实体。
"""

from __future__ import annotations

import re
from typing import Optional

from contracts.question import QuestionType

_RELATION_WORDS = {"谁", "哪些", "哪个", "什么关系", "有什么关系", "参与", "发起方",
                   "攻打", "进攻", "主帅", "将领", "统帅", "主将", "兵力", "由谁",
                   "是谁", "属于哪个", "站在哪边", "属于哪方", "进攻方", "防守方"}
_PLACE_Q = {"在哪里", "哪儿", "何地", "发生于何处", "战场在哪里", "地点是", "位置"}
_WHEN_Q = {"什么时候", "哪一年", "何时", "什么时间", "年代"}
_WHY_BG = {"为什么", "原因", "为何", "起因", "背景", "怎么打", "如何", "经过", "过程",
           "是怎么", "如何打", "怎样", "怎么回事", "目的"}
_COMPARE_Q = {"比较", "对比", "谁更", "谁强", "区别", "异同", "有什么不同", "和.*有什么"}
_EVENT_EVENT_HINT = {"和", "与", "跟"}     # 双事件名连接（配合事件实体数量判断）
_TIMELINE_HINT = {"先后", "时间线", "顺序", "哪个先", "哪个后", "时间轴", "什么时候发生"}
_SINGLE_HINT = {"介绍", "是什么", "简介", "了解", "说说", "讲讲"}

# 指代词集合（多轮追问用）
_COREF_PRONOUNS = {"它", "他", "她", "这", "该", "此", "这个", "这场", "那", "那个"}
_COREF_WITH_NOUN = {"这场战争", "该战争", "此战", "这次战争", "这个事件", "这一仗", "这个战役"}


def classify(question: str, entity_types: list[str], entity_names: list[str],
             has_history: bool) -> QuestionType:
    """基于词法 + 实体构成判定问题类型。entity_types 为 F02 已识别实体的类型列表。"""
    q = question
    # 代词开头 + 有历史 → 多半指代实体，按当前会话已有实体（由上层已解析）决定
    # 比较
    if any(w in q for w in _COMPARE_Q):
        return QuestionType.COMPARISON
    # 事件-事件：两个事件实体 + 连接
    event_count = entity_types.count("事件")
    if event_count >= 2:
        if any(w in q for w in _EVENT_EVENT_HINT):
            return QuestionType.EVENT_EVENT
    # 时间线
    if any(w in q for w in _TIMELINE_HINT):
        return QuestionType.TIMELINE
    if any(w in q for w in _WHEN_Q):
        return QuestionType.TIMELINE
    # 地点类问题
    if any(w in q for w in _PLACE_Q):
        return QuestionType.RELATION
    # 背景/过程
    if any(w in q for w in _WHY_BG):
        return QuestionType.BACKGROUND
    # 关系类
    if any(w in q for w in _RELATION_WORDS):
        return QuestionType.RELATION
    # 双事件无连接词 → 关系问题
    if event_count >= 2:
        return QuestionType.EVENT_EVENT
    # 单实体介绍
    if entity_types and all(t != "地点" for t in entity_types):
        if any(w in q for w in _SINGLE_HINT) or event_count == 1:
            return QuestionType.SINGLE_ENTITY
    # 地点为主 → 仍可判定为 relation/background
    if entity_types:
        if len(entity_types) == 1:
            return QuestionType.SINGLE_ENTITY
        return QuestionType.RELATION
    return QuestionType.UNKNOWN


def extract_dynasty_filter(question: str, dynasty_terms: set[str]) -> list[str]:
    """识别问题中出现的朝代过滤器（"战国时期的XX"）。按最长词优先匹配。"""
    found = []
    terms = sorted(dynasty_terms, key=len, reverse=True)
    for t in terms:
        if t and t != "不详" and t in question:
            found.append(t)
            # 朝代术语作为过滤器时，避免与实体名重叠的双重识别（如"秦"）
    return found


def detect_coref_mention(question: str) -> Optional[str]:
    """判断是否多轮指代追问（出现"它/这场战争"等），返回要消解的指代词。"""
    for c in _COREF_WITH_NOUN:
        if c in question:
            return c
    for c in ("它", "这场", "该战争", "此战", "这"):
        if c in question:
            return c
    return None


def last_event_name_from_history(history: list) -> Optional[str]:
    """从最近用户轮取最后一个事件名（供指代消解）。简单启发式。"""
    for turn in reversed(history or []):
        if turn.get("role") != "user":
            continue
        content = turn.get("content") or ""
        # 以"XX之战"为事件名锚点，取最后一个
        import re
        m = re.findall(r"[^，。？、\s]{2,10}?(?:之战|之役|之变|大战|会战|起义)", content)
        if m:
            return m[-1]
    return None
