"""
关系数据模型
定义事件与实体、事件与事件之间关系的Pydantic模型
"""
from __future__ import annotations

from pydantic import BaseModel, Field
from typing import List, Optional


class EventPlaceRelation(BaseModel):
    """事件-地点关系模型"""
    EventName: str
    relation: str
    # Changed 2026-04-21 13:46:29 +08:00: Allow missing modern_name so
    # relation rows with only historical place names do not fail validation.
    modern_name: Optional[str] = None
    evidence: Optional[str] = None


class EventOrganizationRelation(BaseModel):
    """事件-组织关系模型"""
    EventName: str
    relation: str
    OrgName: str
    evidence: Optional[str] = None


class EventPersonRelation(BaseModel):
    """事件-人物关系模型"""
    EventName: str
    relation: str
    PersonName: str
    evidence: Optional[str] = None


class EventEventRelation(BaseModel):
    """事件-事件关系模型"""
    EventName_A: str
    relation: str
    EventName_B: str
    evidence: Optional[str] = None


class RelationExtractionResult(BaseModel):
    """关系抽取结果容器"""
    event_place_relations: List[EventPlaceRelation] = Field(default_factory=list)
    event_organization_relations: List[EventOrganizationRelation] = Field(default_factory=list)
    event_person_relations: List[EventPersonRelation] = Field(default_factory=list)
    event_event_relations: List[EventEventRelation] = Field(default_factory=list)

    def model_dump(self, **kwargs):
        """自定义序列化，确保空列表也输出"""
        data = super().model_dump(**kwargs)

        data.setdefault("event_place_relations", [])
        data.setdefault("event_organization_relations", [])
        data.setdefault("event_person_relations", [])
        data.setdefault("event_event_relations", [])

        for rel in data.get("event_place_relations", []):
            rel.setdefault("EventName", None)
            rel.setdefault("relation", None)
            rel.setdefault("modern_name", None)
            rel.setdefault("evidence", None)

        for rel in data.get("event_organization_relations", []):
            rel.setdefault("EventName", None)
            rel.setdefault("relation", None)
            rel.setdefault("OrgName", None)
            rel.setdefault("evidence", None)

        for rel in data.get("event_person_relations", []):
            rel.setdefault("EventName", None)
            rel.setdefault("relation", None)
            rel.setdefault("PersonName", None)
            rel.setdefault("evidence", None)

        for rel in data.get("event_event_relations", []):
            rel.setdefault("EventName_A", None)
            rel.setdefault("relation", None)
            rel.setdefault("EventName_B", None)
            rel.setdefault("evidence", None)

        return data
