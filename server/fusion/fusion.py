"""F05 证据融合与重排（server/fusion/fusion.py）。

- 合并 F03 图谱证据 + F04 文本证据 → 统一 Evidence 列表；
- 按问题类型加权排序：关系问题高图谱权重、背景问题高文本权重；
- 去重（同类证据 content 摘要重复合并）；
- 分配 citation_index（1 起）；
- 输出 FusionOutput（含 citation_index 映射 + conflicts 由 conflict 模块注入）。
"""

from __future__ import annotations

from typing import Optional

from contracts.conflict import Conflict
from contracts.evidence import Evidence, EvidenceKind
from contracts.question import QuestionType
from contracts.retrieval import CitationAssign, FusionOutput
from server.fusion.conflict import detect_conflicts

# 问题类型 → 图谱证据权重系数（文本证据权重 = 1 - 系数）
_GRAPH_WEIGHT = {
    QuestionType.RELATION: 0.7,
    QuestionType.EVENT_EVENT: 0.75,
    QuestionType.SINGLE_ENTITY: 0.5,
    QuestionType.COMPARISON: 0.6,
    QuestionType.TIMELINE: 0.4,
    QuestionType.BACKGROUND: 0.25,
    QuestionType.UNKNOWN: 0.4,
}
_GRAPH_KINDS = {EvidenceKind.GRAPH_TRIPLE}

# 融合后送入 F06 的证据上限（F06 提示词长度预算）
FUSION_LIMIT = 18
# 图谱证据内去重后，每个"主体+关系"保留的对象条数上限
_GRAPH_OBJ_PER_REL = 3
# 文本证据保底条数（即使背景类问题也保留图谱少条）
_MIN_TEXT_KEEP = 4


def _content_key(ev: Evidence) -> str:
    """证据去重键：按 kind+content 主体字段。"""
    c = ev.content or {}
    if ev.kind == EvidenceKind.GRAPH_TRIPLE:
        return "g|%s|%s|%s|%s" % (c.get("subject"), c.get("relation"),
                                  c.get("object"), c.get("object_type"))
    return "t|%s|%s" % (ev.source_type, (c.get("text") or "")[:80])


def _ev_score(ev: Evidence) -> float:
    return ev.score if ev.score is not None else 0.0


def _dedup_graph(evidence: list[Evidence]) -> list[Evidence]:
    """图谱证据：同一 (subject, relation) 的对象超过 _GRAPH_OBJ_PER_REL 时裁剪，
    避免"主帅/将领"多值关系淹没其它证据。"""
    cnt: dict[tuple, int] = {}
    out = []
    for ev in evidence:
        if ev.kind != EvidenceKind.GRAPH_TRIPLE:
            out.append(ev)
            continue
        c = ev.content or {}
        key = (c.get("subject"), c.get("subject_type"), c.get("relation"))
        n = cnt.get(key, 0)
        if n >= _GRAPH_OBJ_PER_REL:
            continue
        cnt[key] = n + 1
        out.append(ev)
    return out


def fuse(graph_evidence: list[Evidence],
         text_evidence: list[Evidence],
         qtype: QuestionType,
         conflict_context: Optional[dict] = None,
         limit: int = FUSION_LIMIT) -> FusionOutput:
    """融合主函数。

    conflict_context 需含：
      - snapshot_dir / 或预加载的 event_cards + field_map（供 field_vs_triple）
      - source_version
    若缺省则跳过冲突判定（不阻塞融合）。
    """
    # 1) 合并 + 去重（优先保留图谱证据）
    all_ev: list[Evidence] = []
    seen: set[str] = set()
    for ev in graph_evidence + text_evidence:
        k = _content_key(ev)
        if k in seen:
            continue
        seen.add(k)
        all_ev.append(ev)

    if not all_ev:
        return FusionOutput()

    # 2) 加权排序（稳定性：同分保持图谱在前）
    gw = _GRAPH_WEIGHT.get(qtype, 0.5)
    tw = 1.0 - gw

    def sort_key(ev: Evidence):
        if ev.kind in _GRAPH_KINDS:
            return -(gw * (0.5 + _ev_score(ev) * 0.5))
        return -(tw * (0.5 + _ev_score(ev) * 0.5))

    # 图谱内做多值去重，避免单一主体+关系占满名额
    graph_dedup = _dedup_graph([e for e in all_ev if e.kind in _GRAPH_KINDS])
    text_evs = [e for e in all_ev if e.kind not in _GRAPH_KINDS]
    graph_dedup.sort(key=lambda e: sort_key(e))
    text_evs.sort(key=lambda e: sort_key(e))

    # 3) 组装 keep：按问题类型权重分配名额 + 保底钳制（两类通道都进回答）

    # 图谱分得名额与问题类型图谱权重成比例（relation 高图、background 高文）
    gw_slots = round(limit * gw)
    graph_n = min(len(graph_dedup), max(0, gw_slots))
    text_n = min(len(text_evs), limit - graph_n)
    # 文本保底：图谱过多时至少留 _MIN_TEXT_KEEP 给文本；图谱不足时空位给文本
    if graph_n > limit - _MIN_TEXT_KEEP:
        graph_n = max(0, limit - _MIN_TEXT_KEEP)
    text_n = min(len(text_evs), limit - graph_n)
    if len(graph_dedup) < graph_n:
        graph_n = len(graph_dedup)
        text_n = min(len(text_evs), limit - graph_n)
    kept = graph_dedup[:graph_n] + text_evs[:text_n]

    # 4) 冲突判定（图谱 triple × triple / triple × event_card）
    conflicts: list[Conflict] = []
    if conflict_context:
        conflicts = detect_conflicts(kept, conflict_context)

    # 5) 分配 citation_index
    assigns: list[CitationAssign] = []
    for i, ev in enumerate(kept, start=1):
        ev.citation_index = i
        assigns.append(CitationAssign(evidence_id=ev.evidence_id, citation_index=i))

    return FusionOutput(evidence=kept, citation_index=assigns, conflicts=conflicts)