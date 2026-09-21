"""F09 孤立节点统计与标记。

孤立 = 实体没有任何关系边（不作为源或目标出现在关系列表中）。
F09 策略：孤立节点全部保留（不删除），标记 is_isolated=True；
能由结构化字段推导的关系不在此补（本层不做自动补边，见 README 设计决策）。
"""

from __future__ import annotations

from collections import Counter


def compute_isolated(entities: list[dict], relations: list[dict]) -> tuple[set, dict]:
    """返回 (孤立快照内id集合, 统计)。实体需已分配快照 entity_id。

    关系按 source_id / target_id 关联（id 已映射为快照 id 时生效）。
    """
    linked: set[str] = set()
    for rel in relations:
        s = rel.get("source_entity_id")
        t = rel.get("target_entity_id")
        if s:
            linked.add(s)
        if t:
            linked.add(t)
    isolated = set()
    by_type: Counter = Counter()
    for ent in entities:
        eid = ent.get("entity_id")
        if not eid:
            continue
        if eid not in linked:
            isolated.add(eid)
            by_type[ent.get("type")] += 1
    stats = {
        "isolated_total": len(isolated),
        "isolated_by_type": dict(by_type),
        "total_entities": len(entities),
        "isolation_rate": round(len(isolated) / len(entities), 4) if entities else 0.0,
    }
    return isolated, stats
