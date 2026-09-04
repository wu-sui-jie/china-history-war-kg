"""冲突对象（docs/data-contract.md → conflicts 示例 / 冲突与证据合并）。

初版只做两类结构化冲突判定，范围严格限定：
- graph_triple × graph_triple：同 subject、同 relation、不同 object（different_object）。
- graph_triple × event_card：图谱关系字段与事件卡片字段不一致（field_vs_triple）。
raw_text / evidence 不参与自动冲突判定。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from contracts.base import BaseModel


class ConflictType(str, Enum):
    DIFFERENT_OBJECT = "different_object"
    FIELD_VS_TRIPLE = "field_vs_triple"


@dataclass
class Conflict(BaseModel):
    subject: str
    field: str                    # relation 或事件卡片字段名
    evidence_ids: List[str]
    conflict_type: ConflictType
    description: Optional[str] = None
