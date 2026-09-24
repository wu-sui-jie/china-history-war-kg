"""
事件数据模型
简化版：删除子事件，增加关系字段，拆分规模字段
"""
from __future__ import annotations

from pydantic import BaseModel, Field
from typing import List, Optional, Dict


class EventRelation(BaseModel):
    """事件之间的关系"""
    type: str  # 因果关系/顺承关系/并列关系/包含关系/条件关系
    to: str    # 目标事件名称或ID
    # Changed 2026-04-21 12:42:17 +08:00: Allow missing evidence so one
    # incomplete relation item from LLM will not invalidate the full event.
    evidence: Optional[str] = ""  # 原文证据


class Event(BaseModel):
    """完整事件模型"""
    EventName: str
    EventType: Optional[str] = None
    StartDate: Optional[str] = None
    EndDate: Optional[str] = None
    DynastyName: Optional[str] = None
    Place: Optional[str] = None
    
    # 参战方优化
    Aggressor: Optional[str] = None      # 主动发起方
    Defender: Optional[str] = None       # 防守方
    Allies: Optional[str] = None  # 盟友/支援方
    
    Result: Optional[str] = None
    
    # 人物拆分
    Commanders: Optional[str] = None     # 主要指挥官
    KeyPersons: Optional[str] = None  # 关键人物（谋士、使者等）
    
    Action: Optional[str] = None
    
    # 规模拆分
    TroopSize: Optional[str] = None      # 兵力规模
    Duration: Optional[str] = None       # 时间跨度
    GeographicScope: Optional[str] = None  # 地理范围
    Casualties: Optional[str] = None     # 伤亡人数
    
    source: Optional[str] = None
    Impact: Optional[str] = None
    Remark: Optional[str] = ""
    
    # 事件关系
    relations: List[EventRelation] = Field(default_factory=list)
    
    # 原文片段
    source_text: Optional[str] = ""


class EventExtractionResult(BaseModel):
    """事件抽取结果容器 - 简化版，删除子事件"""
    events: List[Event] = Field(default_factory=list)
    metadata: Dict = Field(default_factory=dict)

    def model_dump(self, **kwargs):
        """自定义序列化，确保 None 或空值输出为 null"""
        data = super().model_dump(**kwargs)
        data.setdefault("metadata", {})

        for event in data.get("events", []):
            event.setdefault("EventName", None)
            event.setdefault("EventType", None)
            event.setdefault("StartDate", None)
            event.setdefault("EndDate", None)
            event.setdefault("DynastyName", None)
            event.setdefault("Place", None)
            event.setdefault("Aggressor", None)
            event.setdefault("Defender", None)
            event.setdefault("Allies", None)
            event.setdefault("Result", None)
            event.setdefault("Commanders", None)
            event.setdefault("KeyPersons", None)
            event.setdefault("Action", None)
            event.setdefault("TroopSize", None)
            event.setdefault("Duration", None)
            event.setdefault("GeographicScope", None)
            event.setdefault("Casualties", None)
            event.setdefault("source", None)
            event.setdefault("Impact", None)
            event.setdefault("Remark", None)
            event.setdefault("relations", [])
            event.setdefault("source_text", None)

        return data
