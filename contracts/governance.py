"""F09 治理输出契约：实体/关系/词典/卡片/报告 的落盘结构。

data-contract.md 要求 F09 输出“标准化实体词典、别名表、朝代别名、现代地名映射、
标准化战争类型词典、事件类型映射表、关系-事件卡片字段映射表、治理报告”。
本文件把这些结构统一定义，snapshot/ 层写盘、server/ 后续读盘共用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from contracts.base import BaseModel


# ---------------- 实体 ----------------

@dataclass
class EntityNode(BaseModel):
    entity_id: str            # 快照内标准实体 id（如 event_0001）
    type: str                 # 事件/人物/组织/地点
    name: str                 # 标准名
    aliases: List[str] = field(default_factory=list)
    dynasty: Optional[str] = None
    # 事件专属
    event_type: Optional[str] = None      # 归一后标准战争类型
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    # 人物/组织
    role: Optional[str] = None
    org: Optional[str] = None
    org_type: Optional[str] = None
    # 地点
    modern_name: Optional[str] = None
    longitude: Optional[float] = None
    latitude: Optional[float] = None
    province: Optional[str] = None
    city: Optional[str] = None
    # 统一描述（事件卡片的 description / 人物组织地点的简介）
    description: Optional[str] = None
    source: Optional[str] = None          # 来源（旧表名/行）
    is_isolated: bool = False             # 孤立节点标记


@dataclass
class RelationEdge(BaseModel):
    source_id: str            # 源实体 id
    source_name: str
    relation: str             # 归一后关系名（发起方/防守方/统帅/主战场/顺承关系…）
    target_id: str
    target_name: str
    confidence: str = "medium"             # high/medium/low
    source_type: str = "kg_relation"
    evidence: Optional[str] = None         # 来源证据文本
    source_row_id: Optional[int] = None    # 旧表行 id（可追溯）
    pending_review: bool = False           # True 时不进入 F03 可查询图谱


# ---------------- 词典 ----------------

@dataclass
class DictItem(BaseModel):
    """词典项：标准名 + 别名。"""

    standard: str
    aliases: List[str] = field(default_factory=list)


# 战争类型标准词典：标准类型名 → 描述
# 事件类型映射：旧值 → 标准值（用于旧事件事件类型归一）
# 关系-事件卡片字段映射表（随 F09 快照输出，F05 field_vs_triple 判定读取）
@dataclass
class EntityDicts(BaseModel):
    entities: dict = field(default_factory=dict)        # type → List[DictItem]（或 EntityNode 快照 id 引用）
    event_type_standard: List[DictItem] = field(default_factory=list)   # 标准战争类型（别名=旧写法）
    event_type_map: dict = field(default_factory=dict)  # 旧event_type → 标准event_type
    dynasty_aliases: dict = field(default_factory=dict) # 朝代名归一（含"夏"等歧义说明见治理报告）
    place_modern_map: dict = field(default_factory=dict) # 旧地点名 → 现代地名（归一用）
