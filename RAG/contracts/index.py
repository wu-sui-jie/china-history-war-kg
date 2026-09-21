"""F11 索引输出契约：切分片段结构。

docs/data-contract.md 文本证据 content 包含 text/doc_id/chunk_type；
事件卡片证据额外含事件名/朝代/时间/参与方/结果等字段。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from contracts.base import BaseModel
from contracts.evidence import ChunkType


@dataclass
class TextChunk(BaseModel):
    chunk_id: str
    doc_id: str                 # 来源文档 id（原文/事件卡片/证据标识）
    source_version: str
    chunk_type: ChunkType       # raw | event_card | evidence
    text: str
    # 元信息（可空字段缺失时该条不写）
    event_id: Optional[str] = None
    event_name: Optional[str] = None
    event_type: Optional[str] = None
    dynasty: Optional[str] = None
    start_date: Optional[str] = None
    source: Optional[str] = None
    related_entities: List[str] = field(default_factory=list)
    # 切分回溯
    parent_chunk_id: Optional[str] = None    # 二次切分时的父片段
    seq_in_doc: int = 0                      # 文档内顺序
