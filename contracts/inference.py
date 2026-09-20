"""规则推理契约（docs/RAG_v2/RAG规则推理移植-需求与设计.md §5）。

离线构建期由 `scripts/build_inferred_relations.py` 应用规则，把结果固化成
`data/snapshot/<version>/inferred_relations.json`；在线只读，且与 `relations.json` 的
RelationEdge **同构**（同名字段 + 推理标记），使 `GraphIndex` 能用同一份代码加载两种行。

与事实层的边界（必须保持）：
- 原始关系是事实，推理关系是"由事实按规则推导出的关系"，两者靠
  `source_type`（kg_relation / inference）与 `inferred` 标记区分；
- 引用与证据必须能回溯到规则（rule_id/rule_name）与原始行（derived_from_rows）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from contracts.base import BaseModel

# 推理关系的 source_type / legacy_table 取值：让证据 ID 形如 graph_inference_h<哈希>，
# 与原始行的 `graph_<旧表>_<行号>` 命名空间天然隔离（不会出现同 ID 的不同证据）。
INFERENCE_SOURCE_TYPE = "inference"
INFERENCE_LEGACY_TABLE = "inference"
# 继承原始关系置信度时缺失/非法值的兜底档
DEFAULT_CONFIDENCE = "medium"
# 置信度排序（复合规则取路径上最低的一档，不做放大）
CONFIDENCE_ORDER = ("low", "medium", "high")


@dataclass
class InferenceCondition(BaseModel):
    """规则条件：命中的原始关系名；composite 为真时按 path_length 步链式匹配。"""

    relation: str
    composite: bool = False
    path_length: int = 1


@dataclass
class InferenceAction(BaseModel):
    """规则结论：产出的关系名与方向。"""

    relation: str
    direction: str = "forward"      # forward / reverse（reverse 交换两端）
    transitive: bool = False


@dataclass
class InferenceRule(BaseModel):
    """一条推理规则（结构对齐旧系统 data/rules 的 rule_base.json，不增删字段）。"""

    rule_id: str
    name: str
    condition: InferenceCondition
    inference: InferenceAction
    description: str = ""


@dataclass
class InferredRelation(BaseModel):
    """一条推理关系行（RelationEdge 的超集，可直接灌进 GraphIndex 的邻接表）。"""

    source_entity_id: str
    source_name: str
    relation: str
    target_entity_id: str
    target_name: str
    confidence: str = DEFAULT_CONFIDENCE
    source_type: str = INFERENCE_SOURCE_TYPE
    legacy_table: str = INFERENCE_LEGACY_TABLE
    inferred: bool = True
    rule_id: str = ""
    rule_name: str = ""
    # 反向规则 = 原始关系名；复合规则 = "<关系>链"（与旧系统口径一致）
    derived_from: str = ""
    # 溯源链：[[source_row_id, legacy_table], ...]，引用可回指到原始行
    derived_from_rows: List[list] = field(default_factory=list)
    composite: bool = False
    # 复合规则的中间节点：[{entity_id, name, type}, ...]；反向规则为空
    path: List[dict] = field(default_factory=list)
    source_version: Optional[str] = None


@dataclass
class InferenceReport(BaseModel):
    """构建报告（供治理与审计，不进在线链路）。"""

    version: str = ""
    generated_at: str = ""
    rules_file: str = ""
    rules_sha256: str = ""
    input_relation_count: int = 0
    skipped_pending_review: int = 0
    output_count: int = 0
    output_sha256: str = ""
    # [{rule_id, name, produced, note}]：note 记录"3 步链无命中"这类跳过原因
    per_rule: List[dict] = field(default_factory=list)
    truncated_rules: List[str] = field(default_factory=list)
