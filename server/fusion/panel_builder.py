"""F05 panel 装配（server/fusion/panel_builder.py）——panel 数据唯一装配方。

输入：融合后证据 + F03 命中的实体/邻居 + 快照实体字典。
输出：PanelData{entity_cards, subgraph, timeline, map_points}。
F07 只消费本模块产物，不自行拼第二套 subgraph。

字段与降级：
- entity_cards：命中实体转卡片（事件用 event_cards 结构化字段增强）。
- subgraph：命中实体 + 其 1 跳邻居（≤ 上限），边带关系名。
- timeline：相关事件按 dynasty 分组（groups）；start_date 缺失/不详 → "时间不详/仅知朝代"。
- map_points：地点需坐标；坐标缺失不进 map_points（降级为地点列表由前端处理）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from contracts.panel import (
    EntityCard,
    GraphEdge,
    GraphNode,
    MapPoint,
    PanelData,
    SubGraph,
    TimelineGroup,
    TimelineItem,
)

MAX_SUBGRAPH_NODES = 24
UNKNOWN_TIME_LABEL = "时间不详/仅知朝代"


class PanelBuilder:
    def __init__(self, snapshot_dir: Path):
        self.snapshot_dir = Path(snapshot_dir)
        self.entities: dict[str, dict] = {}   # entity_id → EntityNode dict
        self.event_cards: dict[str, dict] = {}  # event_id → card dict
        self._name_to_id: dict[str, str] = {}  # 实体名/别名 → entity_id（subgraph 边反查用）
        self._place_event_ids: dict[str, list[str]] = {}  # 地点实体 id → 相关事件 id（map_points，键用 entity_id 防同名地点串挂）
        self._load()

    def _load(self) -> None:
        ents = json.loads((self.snapshot_dir / "entities.json").read_text(encoding="utf-8"))
        for e in ents:
            self.entities[e["entity_id"]] = e
            nm = e.get("name")
            if nm:
                self._name_to_id.setdefault(nm, e["entity_id"])
                for a in e.get("aliases") or []:
                    self._name_to_id.setdefault(a, e["entity_id"])
        cards = json.loads((self.snapshot_dir / "event_cards.json").read_text(encoding="utf-8"))
        for c in cards:
            self.event_cards[c["event_id"]] = c
        # 地点 ↔ 事件关联（map_points[].events 只挂与该地点相关的事件，见 data-contract）。
        # 键用 entity_id：知识库存在大量同名地点（如洛阳×40、涿鹿×2），
        # 若按名称建键会把不同地点的关联事件串到一起。
        rels_path = self.snapshot_dir / "relations.json"
        if rels_path.exists():
            rels = json.loads(rels_path.read_text(encoding="utf-8"))
            place_events: dict[str, set[str]] = {}
            for r in rels:
                if r.get("pending_review"):
                    continue
                se_id, te_id = r.get("source_entity_id"), r.get("target_entity_id")
                se, te = self.entities.get(se_id), self.entities.get(te_id)
                if not se or not te:
                    continue
                if se["type"] == "地点" and te["type"] == "事件":
                    place_events.setdefault(se_id, set()).add(te_id)
                elif se["type"] == "事件" and te["type"] == "地点":
                    place_events.setdefault(te_id, set()).add(se_id)
            self._place_event_ids = {k: sorted(v) for k, v in place_events.items()}

    # ---- 单卡 ----
    def _entity_card(self, e: dict) -> EntityCard:
        card = self.event_cards.get(e.get("entity_id"))
        if card:
            return EntityCard(
                entity_id=card["event_id"],
                type="事件",
                name=card.get("name") or e.get("name"),
                event_type=card.get("event_type") or e.get("event_type"),
                dynasty=card.get("dynasty") or e.get("dynasty"),
                start_date=card.get("start_date") or e.get("start_date"),
                description=card.get("description") or e.get("description"),
                aliases=e.get("aliases") or [],
                source=card.get("source") or e.get("source"),
            )
        return EntityCard(
            entity_id=e.get("entity_id", ""),
            type=e.get("type", ""),
            name=e.get("name", ""),
            event_type=e.get("event_type"),
            dynasty=e.get("dynasty"),
            start_date=e.get("start_date"),
            end_date=e.get("end_date"),
            description=e.get("description"),
            aliases=e.get("aliases") or [],
            source=e.get("source"),
            role=e.get("role"),
            org=e.get("org"),
            org_type=e.get("org_type"),
            modern_name=e.get("modern_name"),
            longitude=e.get("longitude"),
            latitude=e.get("latitude"),
            province=e.get("province"),
            city=e.get("city"),
        )

    # ---- 装配 ----
    def build(self, hit_entities: list[dict],
              graph_evidence: list,
              related_event_ids: Optional[list[str]] = None,
              neighbors: Optional[list[dict]] = None) -> PanelData:
        # 1) entity_cards：命中实体
        entity_cards = []
        node_ids = set()
        for he in hit_entities:
            eid = he.get("entity_id")
            if not eid or eid in node_ids:
                continue
            node_ids.add(eid)
            entity_cards.append(self._entity_card(self.entities.get(eid, he)))

        # 2) subgraph：中心实体 + 融合证据两端的实体（优先），再补 1 跳邻居
        #    保证"进回答的证据"都有对应节点可画边，避免被邻居上限挤出。
        nodes: dict[str, dict] = {}
        center_ids = set()
        for eid in node_ids:
            ent = self.entities.get(eid)
            if not ent:
                continue
            center_ids.add(eid)
            nodes.setdefault(eid, {"id": eid, "type": ent.get("type", ""),
                                   "name": ent.get("name", eid),
                                   "dynasty": ent.get("dynasty")})

        def _add_node(eid: str) -> None:
            if eid in nodes or len(nodes) >= MAX_SUBGRAPH_NODES:
                return
            ent = self.entities.get(eid)
            if not ent:
                return
            nodes[eid] = {"id": eid, "type": ent.get("type", ""),
                          "name": ent.get("name", eid), "dynasty": ent.get("dynasty")}

        # 先加融合证据两端的实体（按 citation 顺序，保证边能画出来）
        for ev in graph_evidence:
            c = ev.content or {}
            for nm in (c.get("subject"), c.get("object")):
                eid = self._name_to_id.get(nm)
                if eid:
                    _add_node(eid)
        # 邻居补充（中心 1 跳，未超过上限时尽量加）
        if neighbors and len(nodes) < MAX_SUBGRAPH_NODES:
            for nb in neighbors:
                _add_node(nb.get("id"))

        # 边：中心 ↔ 邻居 或 证据两端（均在 nodes 内）
        edges: list[dict] = []
        for ev in graph_evidence:
            c = ev.content or {}
            s_id = self._name_to_id.get(c.get("subject"))
            o_id = self._name_to_id.get(c.get("object"))
            if s_id and o_id and s_id in nodes and o_id in nodes:
                edges.append({"source": s_id, "target": o_id,
                              "relation": c.get("relation", "")})
        # 去重边
        seen_e = set()
        dedup_edges = []
        for ed in edges:
            k = (ed["source"], ed["target"], ed["relation"])
            if k in seen_e:
                continue
            seen_e.add(k)
            dedup_edges.append(ed)

        subgraph = SubGraph(
            nodes=[GraphNode(**nodes[k]) for k in nodes],
            edges=[GraphEdge(**ed) for ed in dedup_edges],
        )

        # 3) timeline：相关事件（related_event_ids 或实体卡片中事件）按朝代分组
        timeline = self._build_timeline(related_event_ids or self._event_ids(entity_cards))

        # 4) map_points：graph evidence 中地点实体（含坐标）
        map_points = self._build_map_points(graph_evidence)

        return PanelData(entity_cards=entity_cards, subgraph=subgraph,
                         timeline=timeline, map_points=map_points)

    @staticmethod
    def _event_ids(cards: list[EntityCard]) -> list[str]:
        return [c.entity_id for c in cards if c.type == "事件"]

    def _build_timeline(self, event_ids: list[str]) -> dict:
        groups: dict[str, list[TimelineItem]] = {}
        for eid in event_ids:
            card = self.event_cards.get(eid)
            if not card:
                continue
            item = TimelineItem(
                event_id=eid,
                name=card.get("name") or "",
                start_date=card.get("start_date"),
                dynasty=card.get("dynasty"),
            )
            if not item.start_date or str(item.start_date).strip() in ("", "不详", "无"):
                groups.setdefault(UNKNOWN_TIME_LABEL, []).append(item)
                continue
            label = str(item.dynasty or UNKNOWN_TIME_LABEL)
            groups.setdefault(label, []).append(item)
        ordered = []
        # 有准确时间的朝代放前，时间不详放最后
        for label in sorted([k for k in groups if k != UNKNOWN_TIME_LABEL]):
            ordered.append(TimelineGroup(label=label, items=groups[label]).to_dict())
        if groups.get(UNKNOWN_TIME_LABEL):
            ordered.append(TimelineGroup(label=UNKNOWN_TIME_LABEL,
                                         items=groups[UNKNOWN_TIME_LABEL]).to_dict())
        return {"groups": ordered}

    def _build_map_points(self, graph_evidence: list) -> list[MapPoint]:
        place_names = set()
        for ev in graph_evidence:
            c = ev.content or {}
            if c.get("object_type") == "地点":
                place_names.add(c.get("object"))
            if c.get("subject_type") == "地点":
                place_names.add(c.get("subject"))
        # 快照地点实体查坐标；同名不同地点（跨朝代）各自独立成点、挂各自事件
        points = []
        seen = set()
        for ent in self.entities.values():
            if ent.get("type") != "地点":
                continue
            nm = ent.get("name")
            if nm not in place_names:
                continue
            if ent.get("longitude") is None or ent.get("latitude") is None:
                continue  # 无坐标不进 map_points（降级为地点列表）
            eid = ent.get("entity_id")
            if eid in seen:
                continue
            seen.add(eid)
            points.append(MapPoint(
                place_id=eid,
                name=nm,
                modern_name=ent.get("modern_name"),
                longitude=ent.get("longitude"),
                latitude=ent.get("latitude"),
                events=list(self._place_event_ids.get(eid, [])),
            ))
        return points
