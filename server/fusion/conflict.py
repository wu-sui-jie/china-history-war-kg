"""F05 结构化冲突判定（server/fusion/conflict.py）。

范围（data-contract）：
1. graph_triple × graph_triple：同 subject、同 relation、不同 object → different_object。
2. graph_triple × event_card：图谱关系字段与事件卡片结构化字段不一致 → field_vs_triple。

约束（RAGv2 规划 + RAGv1 数据特征）：
- field_vs_triple 只读取 relation_card_field_map.json（不硬编码）与 event_cards 结构化字段；
- 卡片无对应字段/为空 → 跳过该条比对，不判冲突（"卡片没写"≠"不一致"）；
- event_card 字段里的组织多人字段未覆盖时跳过；event_event/unknown 组不参与。
- method：exact（文本归一化后不一致即冲突）/ contains（卡片字段不含另一端点名即冲突）。
"""

from __future__ import annotations

import re
from collections import defaultdict

from contracts.conflict import Conflict, ConflictType
from contracts.evidence import Evidence, EvidenceKind

_NORM_RE = re.compile(r"[\s，。、；：,.;:（）()「」『』\"'《》]+")


def _norm(s: str) -> str:
    return _NORM_RE.sub("", str(s or ""))


def _load_context(ctx: dict) -> tuple[dict, dict]:
    """返回 (field_map_by_rel_type, event_cards_by_event_id)。"""
    fm = ctx.get("field_map") or {}
    cards = ctx.get("event_cards") or {}
    return fm, cards


def _load_field_map(ctx: dict) -> list:
    field_map = (ctx.get("field_map") or {})
    if isinstance(field_map, list):
        return field_map
    return field_map.get("mapping") or []


def _single_value_relation_names(mapping: list) -> set:
    """exact 单值组（aggressor/defender）的关系名集合。"""
    names = set()
    for m in mapping:
        if m.get("group") in ("aggressor", "defender") and m.get("method") == "exact":
            names.add(m.get("relation"))
    return names


def detect_conflicts(evidence: list[Evidence],
                     ctx: dict) -> list[Conflict]:
    """对融合后证据列表做两类结构化冲突判定。"""
    graph_evs = [e for e in evidence if e.kind == EvidenceKind.GRAPH_TRIPLE]
    field_map, cards = _load_context(ctx)
    out: list[Conflict] = []

    # ---- 1) different_object：同 subject + 同 relation + 不同 object ----
    # 细化（依据 RAGv1 数据特征）：旧库人物/地点/组织类关系是"多值并列"
    # （主帅=白起/王龄/廉颇 等是多个事实行，不是矛盾）。只有 exact 单值组
    # （发起方/防守方）的 relation 出现多个 object 才判定 different_object；
    # 多值组只保留为多条证据、不报冲突（与 F05"冲突不误删、避免编耦"精神一致，
    # 待 F10 评测再调）。
    field_map = _load_field_map(ctx)
    single_value_rels = _single_value_relation_names(field_map)

    groups: dict[tuple, list[Evidence]] = defaultdict(list)
    for ev in graph_evs:
        c = ev.content or {}
        groups[(c.get("subject"), c.get("subject_type"), c.get("relation"))].append(ev)
    for (subj, stype, rel), evs in groups.items():
        if rel not in single_value_rels:
            continue
        objs = {}
        for ev in evs:
            c = ev.content or {}
            objs.setdefault(c.get("object"), ev)
        if len(objs) > 1:
            ev_ids = [evs[0].evidence_id, next(iter(objs.values())).evidence_id]
            out.append(Conflict(
                subject=subj,
                field=rel,
                evidence_ids=ev_ids[:2],
                conflict_type=ConflictType.DIFFERENT_OBJECT,
                description=f"图谱对「{subj}—{rel}」存在不同说法："
                            f"{', '.join(list(objs.keys())[:3])}",
            ))

    # ---- 2) field_vs_triple：graph triple 对 event_card 结构化字段 ----
    mapping = _load_field_map(ctx)
    rel_lookup = {}
    for m in mapping:
        rel_lookup[(m.get("relation"), m.get("target_type"))] = m

    # 预建卡片索引：event_id → card 与 name → card（仅首个同名卡）
    card_by_event = {}
    card_by_name = {}
    for c in cards:
        eid = c.get("event_id")
        if eid and eid not in card_by_event:
            card_by_event[eid] = c
        nm = c.get("name")
        if nm and nm not in card_by_name:
            card_by_name[nm] = c

    for ev in graph_evs:
        c = ev.content or {}
        relation = c.get("relation")
        subj = c.get("subject")
        obj_name = c.get("object")
        obj_type = c.get("object_type")
        # 找到卡片：subject 为事件 → card 取 subj；否则若 object 为事件用 object
        card = None
        card_event_name = None
        if c.get("subject_type") == "事件":
            card = card_by_name.get(subj)
            card_event_name = subj
        elif obj_type == "事件":
            card = card_by_name.get(obj_name)
            card_event_name = obj_name
        if not card:
            continue
        # 目标类型：事件侧的另一端类型
        target_type = obj_type if c.get("subject_type") == "事件" else c.get("subject_type")
        mrow = rel_lookup.get((relation, target_type))
        if not mrow:
            continue
        if mrow.get("method") in ("none", None) or mrow.get("group") in ("event_event", "unknown"):
            continue
        card_field = mrow.get("card_field")
        card_val = card.get(card_field) if card_field else None
        # 卡片无该字段或为空 → 跳过（"卡片没写"≠"不一致"）
        if not card_val or not str(card_val).strip():
            continue

        method = mrow.get("method")
        conflict = False
        if method == "exact":
            if _norm(card_val) != _norm(obj_name or ""):
                conflict = True
        elif method == "contains":
            # 卡片字段需包含对象名（支持多值文本"项羽、刘邦"类）
            if obj_name and _norm(obj_name) not in _norm(card_val):
                conflict = True
        if conflict:
            out.append(Conflict(
                subject=str(card.get("name") or card_event_name),
                field=f"{relation}(卡片{card_field})",
                evidence_ids=[ev.evidence_id],
                conflict_type=ConflictType.FIELD_VS_TRIPLE,
                description=f"图谱「{relation}:{obj_name}」与事件卡片「{card_field}:{card_val}」表述不一致",
            ))
    # 同一 (subject, 卡片字段) 只保留一条代表性冲突：本质是同一口径差异，
    # 避免"统帅/将领/君主"多条同质冲突刷屏前端冲突提示。
    dedup = {}
    for c in out:
        if c.conflict_type == ConflictType.FIELD_VS_TRIPLE:
            key = (c.subject, c.field.split("(")[1] if "(" in c.field else c.field)
            dedup.setdefault(key, c)
    final = []
    seen_keys = set()
    for c in out:
        if c.conflict_type == ConflictType.FIELD_VS_TRIPLE:
            key = (c.subject, c.field.split("(")[1] if "(" in c.field else c.field)
            if key in seen_keys:
                continue
            seen_keys.add(key)
        final.append(c)
    return final
