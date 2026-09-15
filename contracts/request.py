"""查询请求 / F02 内部输出 / 实体纠正（docs/data-contract.md）。

- QueryRequest：F01 前端发来的请求体。
- F02Output：F02 在后端生成、仅内部链路传递（rewritten_question / question_type /
  entities / candidates）。
- CorrectedEntity / CorrectionAction：用户手动纠正实体。
- EntityCandidate：候选实体（供前端实体卡与纠正）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from contracts.base import BaseModel
from contracts.question import QuestionType


class CorrectionAction(str, Enum):
    ADD = "add"
    REPLACE = "replace"
    REMOVE = "remove"


@dataclass
class HistoryTurn(BaseModel):
    role: str  # user | assistant
    content: str


@dataclass
class CorrectedEntity(BaseModel):
    action: CorrectionAction
    entity_type: Optional[str] = None   # add/replace 必填
    name: Optional[str] = None          # add 必填(standard_name)
    original: Optional[str] = None      # replace/remove 必填
    replacement: Optional[str] = None   # replace 必填(standard_name)


@dataclass
class EntityRef(BaseModel):
    """F02 entities 列表中的一项 / 前端识别结果展示。"""

    name: str
    type: Optional[str] = None            # 事件/人物/组织/地点/朝代
    standard_name: Optional[str] = None   # 归一后标准名
    confidence: Optional[str] = None      # high/medium/low
    entity_id: Optional[str] = None       # 快照中标准实体 id（F09 输出后填充）
    dynasty: Optional[str] = None


@dataclass
class CandidateOption(BaseModel):
    name: str
    standard_name: str
    confidence: Optional[str] = None
    dynasty: Optional[str] = None
    event_type: Optional[str] = None
    entity_id: Optional[str] = None


@dataclass
class EntityCandidate(BaseModel):
    mention: str
    entity_type: Optional[str] = None
    options: List[CandidateOption] = field(default_factory=list)


@dataclass
class Filters(BaseModel):
    dynasty: List[str] = field(default_factory=list)      # 空 = 不过滤
    event_type: List[str] = field(default_factory=list)


@dataclass
class QueryRequest(BaseModel):
    session_id: str
    question: str
    history: List[HistoryTurn] = field(default_factory=list)
    filters: Filters = field(default_factory=Filters)
    corrected_entities: List[CorrectedEntity] = field(default_factory=list)

    @staticmethod
    def from_dict(d: dict) -> "QueryRequest":
        """从请求 dict 构造（嵌套 dict → dataclass 转换，避免直接 ** 展开时
        filters/corrected_entities/history 仍是 dict）。"""
        return QueryRequest(
            session_id=d.get("session_id", ""),
            question=d.get("question", ""),
            history=[HistoryTurn(**h) for h in (d.get("history") or [])],
            filters=Filters(**(d.get("filters") or {})),
            corrected_entities=[
                CorrectedEntity(**c) for c in (d.get("corrected_entities") or [])
            ],
        )


@dataclass
class F02Output(BaseModel):
    rewritten_question: str = ""
    question_type: QuestionType = None  # type: ignore[assignment]  # 内部链路一定填充
    entities: List[EntityRef] = field(default_factory=list)
    candidates: List[EntityCandidate] = field(default_factory=list)
    filters: Filters = field(default_factory=Filters)
    # 问句中自动识别到的朝代（仅用于排序加权，**不作为硬过滤**）。
    # 显式筛选（F01 下拉）走 filters.dynasty，保持硬过滤；两者分开是 2026-09-13
    # 审核后修复：硬过滤会把"被问到的朝代"连同事件本身一起剔除（如问"商朝"时
    # 鸣条之战属夏，被整题清空而拒答）。详见 docs/changes/20260913-ragv4-review-summary.md。
    dynasty_bias: List[str] = field(default_factory=list)
    # 是否走了 F02 的 LLM 兜底（词典完全未命中 → 模型抽实体，RAGv5 §4.5）。
    # 仅作可观测性：默认关闭；开启后进入 entities 事件与评测 trace，便于核对是否误触发。
    llm_entity_used: bool = False
    # 同名多实体时，是否由"问句里提到的朝代"选定（偏好而非硬过滤，RAGv5 2026-09-14）。
    # 例：问"西汉的井陉之战"→ 候选含战国/西汉两条，命中西汉那条并前置。
    # 仅作可观测性：进入 entities 事件与评测 trace；候选集合不变，页面仍可纠正。
    dynasty_disambiguated: bool = False

    def to_dict(self, skip_none: bool = True) -> dict:
        d = super().to_dict(skip_none)
        if isinstance(d.get("question_type"), QuestionType):
            d["question_type"] = d["question_type"].value
        return d
