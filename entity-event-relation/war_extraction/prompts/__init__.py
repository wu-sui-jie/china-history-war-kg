"""
提示词模板模块
导出所有Jinja2提示词模板
"""

from war_extraction.prompts.entity_prompts import ENTITY_EXTRACTION_PROMPT
from war_extraction.prompts.event_prompts import (
    EVENT_TYPE_PROMPT,
    EVENT_IDENTIFICATION_PROMPT,
    FULL_EVENT_PROMPT
)
from war_extraction.prompts.relation_prompts import RELATION_EXTRACTION_PROMPT

__all__ = [
    'ENTITY_EXTRACTION_PROMPT',
    'EVENT_TYPE_PROMPT',
    'EVENT_IDENTIFICATION_PROMPT',  # 新增：事件识别
    'FULL_EVENT_PROMPT',
    'RELATION_EXTRACTION_PROMPT',
]
