"""统一证据对象（docs/data-contract.md → 统一证据对象）。

kind / source_type / confidence 取值与文档一致。
score：0~1。F04 必须输出；F03 可选。只用于排序与融合，不作拒答阈值。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from contracts.base import BaseModel


class EvidenceKind(str, Enum):
    GRAPH_TRIPLE = "graph_triple"
    RAW_TEXT = "raw_text"
    EVENT_CARD = "event_card"
    EVIDENCE = "evidence"


class SourceType(str, Enum):
    KG_RELATION = "kg_relation"
    KG_ENTITY = "kg_entity"
    ORIGINAL_TEXT = "original_text"
    EVENT_CARD_JSON = "event_card_json"
    RELATION_EVIDENCE = "relation_evidence"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    PENDING_REVIEW = "pending_review"


# 事件卡片 / 文本证据的 chunk_type 取值
class ChunkType(str, Enum):
    RAW = "raw"
    EVENT_CARD = "event_card"
    EVIDENCE = "evidence"


@dataclass
class Evidence(BaseModel):
    evidence_id: str
    kind: EvidenceKind
    source_type: SourceType
    source_version: str
    confidence: Confidence
    content: dict  # kind 不同结构不同
    related_entities: list = field(default_factory=list)
    score: Optional[float] = None
    citation_index: Optional[int] = None

    def to_dict(self, skip_none: bool = True) -> dict:
        d = super().to_dict(skip_none)
        d["kind"] = d["kind"].value if isinstance(d["kind"], EvidenceKind) else d["kind"]
        d["source_type"] = (
            d["source_type"].value if isinstance(d["source_type"], SourceType) else d["source_type"]
        )
        d["confidence"] = (
            d["confidence"].value if isinstance(d["confidence"], Confidence) else d["confidence"]
        )
        return d


def triple_content(subject: str, subject_type: str, relation: str, obj: str, object_type: str) -> dict:
    return {
        "subject": subject,
        "subject_type": subject_type,
        "relation": relation,
        "object": obj,
        "object_type": object_type,
    }
