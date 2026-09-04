"""SSE 流式事件协议（docs/data-contract.md → 流式事件协议）。

事件按顺序推送，每条事件都带 session_id 与 stage。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional

from contracts.base import BaseModel


class SSEEventType(str, Enum):
    SESSION_START = "session_start"
    STATUS = "status"
    ENTITIES = "entities"
    GRAPH_RESULTS = "graph_results"
    TEXT_RESULTS = "text_results"
    FUSION = "fusion"
    THINKING = "thinking"
    ANSWER = "answer"
    CITATIONS = "citations"
    PANEL = "panel"
    ERROR = "error"
    DONE = "done"


class StatusStage(str, Enum):
    ENTITY_LINKING = "entity_linking"
    GRAPH_SEARCH = "graph_search"
    TEXT_SEARCH = "text_search"
    FUSION = "fusion"
    GENERATING = "generating"
    CACHE_HIT = "cache_hit"
    START = "start"


class FinishReason(str, Enum):
    NORMAL = "normal"
    REFUSED = "refused"
    DEGRADED = "degraded"
    CANCELLED = "cancelled"


class ErrorCode(str, Enum):
    RETRIEVAL_EMPTY = "retrieval_empty"
    LLM_TIMEOUT = "llm_timeout"
    LLM_UNAVAILABLE = "llm_unavailable"
    INVALID_REQUEST = "invalid_request"
    INTERNAL = "internal"


@dataclass
class SSECitation(BaseModel):
    """citations 事件中的引用项。"""

    index: int
    evidence_id: str
    kind: str
    title: str
    snippet: str


@dataclass
class SSEEvent(BaseModel):
    """通用事件信封：type + session_id + stage + data。"""

    type: SSEEventType
    session_id: str
    stage: Optional[str] = None
    data: Any = None  # 各事件 payload 不同，见 docs/data-contract.md SSE 事件 payload

    def to_dict(self, skip_none: bool = True) -> dict:
        d = super().to_dict(skip_none)
        d["type"] = d["type"].value if isinstance(d["type"], SSEEventType) else d["type"]
        return d
