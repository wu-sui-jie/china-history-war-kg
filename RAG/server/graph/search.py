"""F03 图谱检索执行编排。

输入 F02 标准实体名（可多个）→ 在图谱匹配节点 → 按问题类型查边/路径 →
输出 GraphResult{evidence: graph_triple, hit_entities}。

核心约束：
- 孤立节点不返回虚构关系（只进 hit_entities，由 F05 做实体卡）；
- 同名多实体全部进入匹配（歧义交给 F02 candidates + filters 消歧）；
- pending_review 关系已由 GraphIndex 加载时排除；
- filters（朝代/事件类型）作为图谱节点过滤条件。
"""

from __future__ import annotations

from typing import Optional

from contracts.evidence import Confidence, Evidence, EvidenceKind, SourceType, triple_content
from contracts.question import QuestionType
from contracts.retrieval import GraphResult
from server.graph.graph_index import GraphIndex
from server.graph.query_strategies import EVENT_EVENT_RELATIONS, strategy_for


class GraphSearch:
    def __init__(self, graph: GraphIndex, top_k: int = 40):
        self.graph = graph
        self.top_k = top_k

    # ---- 证据装配 ----
    def _triple_evidence(self, row: dict, source_name: str, source_type: str,
                         obj_name: str, obj_type: str) -> Evidence:
        return Evidence(
            evidence_id=f"graph_{row.get('source_row_id') or abs(hash((source_name, row['relation'], obj_name))) % 10**6}",
            kind=EvidenceKind.GRAPH_TRIPLE,
            source_type=SourceType.KG_RELATION,
            source_version=self.graph.source_version,
            confidence=row.get("confidence") if row.get("confidence") in
                       ("high", "medium", "low") else Confidence.MEDIUM,
            content=triple_content(source_name, source_type, row["relation"],
                                   obj_name, obj_type),
            related_entities=[source_name, obj_name],
        )

    def _entity_dict(self, ent: dict) -> dict:
        # 只携带 panel 需要的字段
        keys = ["entity_id", "type", "name", "aliases", "dynasty", "event_type",
                "start_date", "end_date", "role", "org", "org_type", "modern_name",
                "longitude", "latitude", "province", "city", "description", "source",
                "is_isolated"]
        return {k: ent.get(k) for k in keys if ent.get(k) is not None}

    # ---- 各策略 ----
    def _collect_neighbors(self, node_ids: list[str], limit: int = 40) -> list[dict]:
        """收集命中节点 1 跳邻居详情（id/type/name/relation/direction/dynasty），
        供 F05 装配 subgraph。节点自身与邻居都收（去重）。"""
        out_nodes: dict[str, dict] = {}
        for nid in node_ids:
            ent = self.graph.get_by_id(nid)
            if not ent:
                continue
            out_nodes[nid] = {"id": nid, "type": ent["type"], "name": ent["name"],
                              "dynasty": ent.get("dynasty"), "relation": None,
                              "direction": None}
            for nb in self.graph.neighbors_with_type(nid):
                out_nodes.setdefault(nb["id"], {
                    "id": nb["id"], "type": nb["type"], "name": nb["name"],
                    "dynasty": nb.get("dynasty"), "relation": None, "direction": None,
                })
                if len(out_nodes) >= limit:
                    break
        return list(out_nodes.values())

    def _single_entity(self, node: dict) -> GraphResult:
        """实体属性 + 1 跳邻居。"""
        ev = []
        eid = node["entity_id"]
        seen = set()
        for row in self.graph.neighbors(eid):
            other = self.graph.get_by_id(row["_other_id"])
            if not other:
                continue
            key = (row["_role"], row["_other_id"], row["relation"])
            if key in seen:
                continue
            seen.add(key)
            sn, st = node["name"], node["type"]
            on, ot = other["name"], other["type"]
            ev.append(self._triple_evidence(row, sn, st, on, ot))
            if len(ev) >= self.top_k:
                break
        neighbors = self._collect_neighbors([node["entity_id"]])
        return GraphResult(evidence=ev, hit_entities=[self._entity_dict(node)],
                           related_event_ids=[node["entity_id"]] if node["type"] == "事件" else [],
                           neighbors=neighbors)

    def _relation_edges(self, nodes: list[dict]) -> GraphResult:
        """关系/参与方：对每个命中节点取 1 跳邻居（不限类型），过滤孤立。"""
        ev, hit = [], []
        rel_event_ids = []
        seen_rows = set()
        for node in nodes:
            hit.append(self._entity_dict(node))
            if node["type"] == "事件" and node["entity_id"] not in rel_event_ids:
                rel_event_ids.append(node["entity_id"])
            if node.get("is_isolated"):
                continue
            seen = set()
            for row in self.graph.neighbors(node["entity_id"]):
                other = self.graph.get_by_id(row["_other_id"])
                if not other:
                    continue
                key = (row["_role"], row["_other_id"], row["relation"])
                if key in seen:
                    continue
                seen.add(key)
                seen_rows.add((row["_other_id"], row["relation"]))
                ev.append(self._triple_evidence(row, node["name"], node["type"],
                                                other["name"], other["type"]))
                if len(ev) >= self.top_k:
                    return GraphResult(evidence=ev, hit_entities=hit,
                                       related_event_ids=rel_event_ids,
                                       neighbors=self._collect_neighbors(
                                           [n["entity_id"] for n in nodes]))
        neighbors = self._collect_neighbors([n["entity_id"] for n in nodes])
        return GraphResult(evidence=ev, hit_entities=hit, related_event_ids=rel_event_ids,
                           neighbors=neighbors)

    def _event_event(self, nodes: list[dict]) -> GraphResult:
        """事件-事件：直接 event_event 关系；两事件则优先路径。"""
        ev, hit = [], []
        event_nodes = [n for n in nodes if n["type"] == "事件"]
        rel_ids = [n["entity_id"] for n in event_nodes]
        for n in event_nodes:
            hit.append(self._entity_dict(n))
            if n.get("is_isolated"):
                continue
            for row in self.graph.neighbors(n["entity_id"]):
                other = self.graph.get_by_id(row["_other_id"])
                if not other or row["relation"] not in EVENT_EVENT_RELATIONS:
                    continue
                if row["relation"] in ("顺承关系", "因果关系", "包含关系", "并列关系", "条件关系"):
                    ev.append(self._triple_evidence(row, n["name"], "事件",
                                                    other["name"], other["type"]))
        # 双事件 → 两跳路径（缺直接边时）
        if len(event_nodes) == 2:
            a, b = event_nodes
            if not any(self.graph.has_edge_between(a["entity_id"], b["entity_id"])):
                paths = self.graph.two_hop_paths(a["entity_id"], b["entity_id"])
                for path in paths:
                    for row in path:
                        mid = self.graph.get_by_id(row["_other_id"])
                        if mid:
                            ev.append(self._triple_evidence(row, row["source_name"],
                                                            self.graph.type_of(row["source_entity_id"]),
                                                            mid["name"], mid["type"]))
        return GraphResult(evidence=ev, hit_entities=hit, related_event_ids=rel_ids,
                           neighbors=self._collect_neighbors([n["entity_id"] for n in event_nodes]))

    def _comparison(self, nodes: list[dict]) -> GraphResult:
        """比较：分别做一跳，不强合并。"""
        return self._relation_edges(nodes)

    def _timeline(self, nodes: list[dict]) -> GraphResult:
        """时间线：取事件节点一跳作锚点，事件详情由 F05 用快照属性补。"""
        return self._relation_edges(nodes)

    def _background(self, nodes: list[dict]) -> GraphResult:
        """背景：图谱只返回实体上下文（一跳）供实体卡，主体文本通道。"""
        ev, hit = [], []
        for node in nodes:
            hit.append(self._entity_dict(node))
            if node.get("is_isolated"):
                continue
            for row in self.graph.neighbors(node["entity_id"])[:8]:
                other = self.graph.get_by_id(row["_other_id"])
                if not other:
                    continue
                ev.append(self._triple_evidence(row, node["name"], node["type"],
                                                other["name"], other["type"]))
        return GraphResult(evidence=ev, hit_entities=hit,
                           related_event_ids=[n["entity_id"] for n in nodes if n["type"] == "事件"],
                           neighbors=self._collect_neighbors([n["entity_id"] for n in nodes]))

    # ---- 主入口 ----
    def search(self, entity_names: list[str], qtype: QuestionType,
               filters: Optional[dict] = None,
               top_k: Optional[int] = None) -> GraphResult:
        if top_k:
            self.top_k = top_k
        nodes: list[dict] = []
        matched = set()
        for name in entity_names:
            for ent in self.graph.find_by_name(name):
                # filters 朝代/事件类型 作为图谱节点过滤条件
                if filters:
                    fs = filters or {}
                    if fs.get("dynasty") and ent.get("dynasty") not in fs["dynasty"]:
                        continue
                    if fs.get("event_type") and ent.get("event_type") not in fs["event_type"]:
                        continue
                if ent["entity_id"] not in matched:
                    matched.add(ent["entity_id"])
                    nodes.append(ent)
        if not nodes:
            return GraphResult()

        strategy = strategy_for(qtype)
        if strategy == "single_entity":
            # 多实体单实体策略时取主实体（第一个事件优先）
            primary = nodes[0]
            for n in nodes:
                if n["type"] == "事件":
                    primary = n
                    break
            return self._single_entity(primary)
        if strategy == "event_event":
            return self._event_event(nodes)
        if strategy == "comparison":
            return self._comparison(nodes)
        if strategy == "background":
            return self._background(nodes)
        # relation / timeline / unknown → 邻接
        return self._relation_edges(nodes)
