"""知识面板数据（docs/data-contract.md → 知识面板数据结构）。

F07 展示用的 panel 数据，由 F05 唯一装配，随 SSE 的 panel 事件推送。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from contracts.base import BaseModel


@dataclass
class EntityCard(BaseModel):
    entity_id: str
    type: str
    name: str
    event_type: Optional[str] = None
    dynasty: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    description: Optional[str] = None
    aliases: List[str] = field(default_factory=list)
    source: Optional[str] = None
    # 事件的叙事字段（2026-09-20 起从 event_cards 快照装配，只对事件卡有值）
    aggressor: Optional[str] = None
    defender: Optional[str] = None
    action: Optional[str] = None
    impact: Optional[str] = None
    place: Optional[str] = None
    # 人物/组织/地点的附加信息
    role: Optional[str] = None
    org: Optional[str] = None
    org_type: Optional[str] = None
    modern_name: Optional[str] = None
    longitude: Optional[float] = None
    latitude: Optional[float] = None
    province: Optional[str] = None
    city: Optional[str] = None


@dataclass
class GraphNode(BaseModel):
    id: str
    type: str
    name: str
    dynasty: Optional[str] = None


@dataclass
class GraphEdge(BaseModel):
    source: str
    target: str
    relation: str


@dataclass
class SubGraph(BaseModel):
    nodes: List[GraphNode] = field(default_factory=list)
    edges: List[GraphEdge] = field(default_factory=list)


@dataclass
class TimelineItem(BaseModel):
    event_id: str
    name: str
    start_date: Optional[str] = None
    dynasty: Optional[str] = None


@dataclass
class TimelineGroup(BaseModel):
    label: str
    items: List[TimelineItem] = field(default_factory=list)


@dataclass
class MapPoint(BaseModel):
    place_id: str
    name: str
    modern_name: Optional[str] = None
    longitude: Optional[float] = None
    latitude: Optional[float] = None
    events: List[str] = field(default_factory=list)


@dataclass
class PanelData(BaseModel):
    entity_cards: List[EntityCard] = field(default_factory=list)
    subgraph: SubGraph = field(default_factory=SubGraph)
    timeline: Optional[dict] = field(default_factory=dict)  # {groups: [...]} 与契约一致
    map_points: List[MapPoint] = field(default_factory=list)
