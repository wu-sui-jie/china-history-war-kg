"""F02 规则层：问题类型判定、朝代过滤器识别、指代消解（词典部分）。

问题类型规则（见 docs/features.md 第二节与 docs/data-contract.md）：
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
_COREF_WITH_NOUN = {"这场战争", "该战争", "此战", "这次战争", "这个事件", "这一仗", "这个战役"}


def classify(question: str, entity_types: list[str], has_history: bool) -> QuestionType:
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


def extract_dynasty_mentions(question: str, dynasty_terms, entity_mentions=()) -> list[str]:
    """识别问句中提到的朝代（标准名列表），**仅供排序加权，不作为硬过滤**。

    语义：
    - 返回值进 F02Output.dynasty_bias（F03/F04 用于排序优先），不进 filters；
      显式筛选（F01 下拉）仍走 filters.dynasty 硬过滤。原因：硬过滤会把"被问到的
      朝代"连同事件本身剔除——实测问"鸣条之战与商朝的建立有什么关系"时，事件朝代
      为"夏"，一旦把"商朝"当硬筛选，图谱/文本/融合全为 0、直接拒答。
    - 识别两类表面形式：
      ① 词典多字别名（如"战国""三国""东汉"）直接命中；
      ② 单字朝代键 + "朝/国/代/王朝"后缀（如"商朝"→商、"秦朝"→秦、"楚国"→楚）；
      ② 与 ① 跨度重叠时不重复计（如"清朝末年"只出"清朝"，不再追加"清"）；
    - 跳过落在已识别实体 mention 内部的词（属实体的一部分而非朝代指称）；
    - 并列长度按词本身排序，保证跨进程确定（PYTHONHASHSEED 无关）。

    dynasty_terms 可为 {别名: 标准名} 映射（推荐）或字符串集合。
    """
    if hasattr(dynasty_terms, "items"):
        items = list(dynasty_terms.items())
    else:
        items = [(t, t) for t in (dynasty_terms or [])]
    mentions = [m for m in (entity_mentions or []) if m]
    single_char = {alias: std for alias, std in items
                   if len(alias) == 1 and alias not in ("不详", "未知", "无")}

    def _in_mention(term: str, start: int, end: int) -> bool:
        for m in mentions:
            i = question.find(m)
            if i >= 0 and i <= start and end <= i + len(m) and len(m) > len(term):
                return True
        return False

    found: list[str] = []
    covered: list[tuple[int, int]] = []   # ① 已命中的表面跨度，供 ② 去重
    # ① 多字别名（长度≥2）
    for alias, standard in sorted(items, key=lambda kv: (-len(kv[0]), kv[0])):
        t = alias or ""
        if len(t) < 2 or t in ("不详", "未知", "无") or t not in question:
            continue
        start = question.find(t)
        if _in_mention(t, start, start + len(t)):
            continue
        covered.append((start, start + len(t)))
        val = standard or t
        if val not in found:
            found.append(val)
    # ② 单字朝代 + 朝/国/代/王朝 后缀
    if single_char:
        chars = "".join(sorted(single_char.keys()))
        # 后缀含"代"（唐代/宋代/清代等正当写法）；"朝代/时代/近代"不会命中，
        # 因为要求前缀字符本身是单字朝代键（"朝/时/近"都不是）
        pattern = re.compile(f"([{chars}])(王朝|朝|国|代)")
        for m in pattern.finditer(question):
            # 与 ① 的多字别名重叠（如"清朝"）→ 同一朝代概念，不重复计
            if any(s < m.end() and m.start() < e for s, e in covered):
                continue
            ch = m.group(1)
            std = single_char.get(ch, ch)
            if _in_mention(ch, m.start(), m.end()):
                continue
            if std not in found:
                found.append(std)
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
