"""问题类型（docs/data-contract.md → 问题类型）。"""

from __future__ import annotations

from enum import Enum


class QuestionType(str, Enum):
    SINGLE_ENTITY = "single_entity"   # 介绍单个实体
    RELATION = "relation"             # 查询关系或参与方
    EVENT_EVENT = "event_event"       # 查询事件之间的关系
    COMPARISON = "comparison"         # 比较多个实体或事件
    TIMELINE = "timeline"             # 查询时间顺序或时间线
    BACKGROUND = "background"         # 查询过程、原因、背景
    UNKNOWN = "unknown"               # 无法确定
