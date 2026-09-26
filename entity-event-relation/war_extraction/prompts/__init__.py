"""
提示词模板模块
导出所有Jinja2提示词模板

提示词阶段有三个：实体抽取 → 事件识别 + 完整事件 → 关系抽取。
原先还有一个"事件类型判定"模板（`EVENT_TYPE_PROMPT`），全仓无调用者，已按
整改方案 10.1 第 3 项删除；`EventType` 由 `FULL_EVENT_PROMPT` 输出。
"""

from war_extraction.prompts.entity_prompts import ENTITY_EXTRACTION_PROMPT
from war_extraction.prompts.event_prompts import (
    EVENT_IDENTIFICATION_PROMPT,
    FULL_EVENT_PROMPT
)
from war_extraction.prompts.relation_prompts import RELATION_EXTRACTION_PROMPT

__all__ = [
    'ENTITY_EXTRACTION_PROMPT',
    'EVENT_IDENTIFICATION_PROMPT',
    'FULL_EVENT_PROMPT',
    'RELATION_EXTRACTION_PROMPT',
]
