"""检索通道输出与文本证据 content 装配（docs/data-contract.md）。

F03/F04 各自输出的证据已在 contracts.evidence.Evidence 定义。
本文件补充两类"跨通道信封"：
- F03 检索输出（triples + 邻居实体）：图谱证据列表 + 供 F05 装配 panel 的实体信息。
- F04 检索输出：文本证据列表 + 命中模式说明。
- F05 融合输出摘要：排序后证据 + citation_index 编号映射 + conflicts。

文本证据 content 需满足 data-contract L221-239：
raw_text / evidence 携带 text/doc_id/chunk_type（+related_entities）；
event_card 额外携带事件名/朝代/时间/参与方/结果等结构化字段。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from contracts.base import BaseModel
from contracts.conflict import Conflict
from contracts.evidence import Evidence


def build_text_content(chunk: dict) -> dict:
    """由 chunks.jsonl 一条片段生成文本证据 content（dict）。

    规则：
    - raw / evidence：text / doc_id / chunk_type。
    - event_card：额外携带事件结构化字段（name/dynasty/start_date/event_type/
      aggressor/defender/persons/place/result 等），缺失字段不写入。
    """
    ctype = chunk.get("chunk_type")
    content = {
        "text": chunk.get("text", ""),
        "doc_id": chunk.get("doc_id", ""),
        "chunk_type": ctype,
    }
    if ctype == "event_card":
        for key in (
            "event_name", "event_type", "dynasty", "start_date",
            "aggressor", "defender", "persons", "place", "result",
        ):
            val = chunk.get(key)
            if val is not None:
                content[key] = val
    elif ctype in ("raw", "evidence"):
        if chunk.get("event_name"):
            content["event_name"] = chunk["event_name"]
        if chunk.get("event_id"):
            content["event_id"] = chunk["event_id"]
    return content


@dataclass
class GraphResult(BaseModel):
    """F03 图谱检索输出。"""

    evidence: List[Evidence] = field(default_factory=list)
    # 命中的标准实体（供 F05 装配 entity_cards / subgraph）
    hit_entities: List[dict] = field(default_factory=list)   # EntityNode dict 子集
    related_event_ids: List[str] = field(default_factory=list)  # 命中的事件 id（timeline）
    # 1 跳邻居详情（含 relation/direction/type），供 F05 装配 subgraph
    neighbors: List[dict] = field(default_factory=list)


@dataclass
class TextResult(BaseModel):
    """F04 文本检索输出。"""

    evidence: List[Evidence] = field(default_factory=list)
    mode: str = "keyword"          # keyword / vector / hybrid / none
    vector_available: bool = False
    # ---- 向量降级是"被观测到的事实"，不靠调用方推断 ----
    # vector_available 只说明制品与客户端就绪；查询向量化是网络调用，运行期仍可能失败
    # 并静默降级关键词。这两个字段把"真的降级了"和原因显式带出来，供 health、日志与
    # 评测共用同一份判据，不必各自去猜 mode 与 vector_available 的组合含义。
    vector_degraded: bool = False
    vector_note: str = ""          # 降级原因（未降级时为空串）


@dataclass
class CitationAssign(BaseModel):
    """F05 分配的引用编号项：证据 id ↔ 编号。"""

    evidence_id: str
    citation_index: int


@dataclass
class FusionOutput(BaseModel):
    """F05 融合输出摘要（供 SSE fusion 事件与 F06 消费）。"""

    evidence: List[Evidence] = field(default_factory=list)
    citation_index: List[CitationAssign] = field(default_factory=list)
    conflicts: List[Conflict] = field(default_factory=list)
