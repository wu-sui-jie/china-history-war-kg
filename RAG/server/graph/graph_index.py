"""F03 内存图谱：加载快照 entities/relations 建邻接索引。

快照字段（见 contracts.governance EntityNode / RelationEdge）：
- entities: {entity_id, type(事件/人物/组织/地点), name, dynasty, ...}
- relations: {source_entity_id, source_name, relation, target_entity_id, target_name,
  confidence, evidence, pending_review}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class GraphIndex:
    def __init__(self, snapshot_dir: Path, source_version: str):
        self.snapshot_dir = Path(snapshot_dir)
        self.source_version = source_version
        self.entities: list[dict] = []
        self.relations: list[dict] = []
        self._by_id: dict[str, dict] = {}
        self._by_name: dict[str, list[dict]] = {}       # 标准名 → 实体
        self._alias_to_id: dict[str, str] = {}          # 别名 → 实体 id
        # 邻接：entity_id → [(relation, target_id, rel_row)]，出边
        self._adj_out: dict[str, list[tuple]] = {}
        # 入边：target_id → [(relation, source_id, rel_row)]
        self._adj_in: dict[str, list[tuple]] = {}
        self._load()

    # ---- 加载 ----
    def _load(self) -> None:
        ents = json.loads((self.snapshot_dir / "entities.json").read_text(encoding="utf-8"))
        rels = json.loads((self.snapshot_dir / "relations.json").read_text(encoding="utf-8"))
        self.entities = ents
        self.relations = rels
        for e in ents:
            self._by_id[e["entity_id"]] = e
            self._by_name.setdefault(e["name"], []).append(e)
        # pending_review 关系不进入可查询图谱（data-contract）
        for r in rels:
            if r.get("pending_review"):
                continue
            s, t = r.get("source_entity_id"), r.get("target_entity_id")
            if not s or not t:
                continue
            self._adj_out.setdefault(s, []).append((r["relation"], t, r))
            self._adj_in.setdefault(t, []).append((r["relation"], s, r))

    # ---- 查询辅助 ----
    def get_by_id(self, eid: str) -> Optional[dict]:
        return self._by_id.get(eid)

    def find_by_name(self, name: str) -> list[dict]:
        """标准名精确匹配（同名多实体全部返回）。"""
        return self._by_name.get(name, [])

    def type_of(self, eid: str) -> str:
        e = self._by_id.get(eid)
        return e["type"] if e else ""

    def name_of(self, eid: str) -> str:
        e = self._by_id.get(eid)
        return e["name"] if e else eid

    def neighbors(self, eid: str) -> list[dict]:
        """1 跳邻居行（含方向）：每条 = 标准关系行 + 我方角色 source/target。"""
        out = []
        for rel, tgt, row in self._adj_out.get(eid, []):
            out.append({**row, "_role": "source", "_other_id": tgt})
        for rel, src, row in self._adj_in.get(eid, []):
            out.append({**row, "_role": "target", "_other_id": src})
        return out

    def out_edges(self, eid: str) -> list[tuple]:
        return self._adj_out.get(eid, [])

    def in_edges(self, eid: str) -> list[tuple]:
        return self._adj_in.get(eid, [])

    def neighbors_of_type(self, eid: str, types: set[str]) -> list[dict]:
        """1 跳邻居中另一端类型 ∈ types 的行。"""
        rows = []
        for rel, tgt, row in self._adj_out.get(eid, []):
            if self.type_of(tgt) in types:
                rows.append({**row, "_role": "source", "_other_id": tgt})
        for rel, src, row in self._adj_in.get(eid, []):
            if self.type_of(src) in types:
                rows.append({**row, "_role": "target", "_other_id": src})
        return rows

    def neighbors_with_type(self, eid: str) -> list[dict]:
        """1 跳邻居，附带对方 id/name/type（供 subgraph 绘制）。"""
        seen = set()
        rows = []
        for rel, tgt, row in self._adj_out.get(eid, []):
            e = self._by_id.get(tgt)
            if not e:
                continue
            key = (tgt, rel)
            if key in seen:
                continue
            seen.add(key)
            rows.append({"id": tgt, "type": e["type"], "name": e["name"],
                         "relation": rel, "direction": "out", "dynasty": e.get("dynasty")})
        for rel, src, row in self._adj_in.get(eid, []):
            e = self._by_id.get(src)
            if not e:
                continue
            key = (src, rel)
            if key in seen:
                continue
            seen.add(key)
            rows.append({"id": src, "type": e["type"], "name": e["name"],
                         "relation": rel, "direction": "in", "dynasty": e.get("dynasty")})
        return rows

    def has_edge_between(self, a: str, b: str) -> list[dict]:
        """A→B 或 B→A 的直接边行。"""
        rows = []
        for rel, tgt, row in self._adj_out.get(a, []):
            if tgt == b:
                rows.append({**row, "_role": "source", "_other_id": b})
        for rel, src, row in self._adj_in.get(a, []):
            if src == b:
                rows.append({**row, "_role": "target", "_other_id": b})
        return rows

    def two_hop_paths(self, a: str, b: str, max_paths: int = 6) -> list[list[dict]]:
        """a→b 的两跳路径：返回中间实体路径列表，路径上含边行。"""
        paths: list[list[dict]] = []
        # 第一跳出/入
        first = [(rel1, mid, row1) for rel1, mid, row1 in self.out_edges(a)] + \
                [(rel1, mid, row1) for rel1, mid, row1 in self.in_edges(a)]
        for rel1, mid, row1 in first:
            if mid == b:
                continue
            for rel2, tgt, row2 in self._adj_out.get(mid, []):
                if tgt == b:
                    paths.append([{**row1, "_other_id": mid}, {**row2, "_other_id": b}])
                    if len(paths) >= max_paths:
                        return paths
            for rel2, src, row2 in self._adj_in.get(mid, []):
                if src == b:
                    paths.append([{**row1, "_other_id": mid}, {**row2, "_other_id": b}])
                    if len(paths) >= max_paths:
                        return paths
        return paths
