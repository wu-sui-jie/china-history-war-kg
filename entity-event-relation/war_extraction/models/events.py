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
    # evidence 可为空：模型少给一条证据不该让整个事件校验失败
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

    # 事件名别名（归并前后用过的其它写法）。
    # **为什么单独一个字段。** 原先把 `alias:旧名` 直接追加进 `Remark`，而 `Remark` 是
    # 正文备注，有三个下游出口（SQLite 的 `events.remark` 列、前端属性面板、Excel 列），
    # 于是流程元信息混进内容被展示出来。新增字段对下游零影响（不认识的键会被忽略）。
    #
    # **结论（第二轮定）：不进 SQLite / Neo4j，仅产物保留，供跨库对齐时人工查。**
    # 理由与代价都写在这里，免得下次又要重新讨论：
    #   - 现状：SQLite `events` 表没有 aliases 列（`backend/models.py`）、
    #     `API_TO_COLUMN` 没有该键、Neo4j 与前端也都没有出口——即"没有任何下游在读它"；
    #   - 真要进库不是加一行映射的事：要新增列（含存量库迁移）、`to_dict` 与
    #     `import_json_to_sqlite` 同步、Neo4j 属性名与前端展示一起定；
    #   - 反悔成本很低：产物里字段一直在（`main.py` 的归并处写入），要进库随时能补，
    #     届时再按 `node_property_mapping.API_TO_COLUMN` 加 `AliasNames → aliases` 一行即可。
    AliasNames: List[str] = Field(default_factory=list)

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
            event.setdefault("AliasNames", [])
            event.setdefault("relations", [])
            event.setdefault("source_text", None)

        return data
