#!/usr/bin/env python3
"""
将 SQLite 中的数据同步到 Neo4j。
"""

import argparse
import os

import local_settings
from common_utils import safe_identifier
from graph_key import graph_key_for
from node_property_mapping import SYNC_FIXED_PROPS, neo4j_props, orm_row
from relation_types import normalize_event_relation_type
from flask import Flask
from py2neo import Graph

from models import (
    Event,
    EventEventRelation,
    EventOrganizationRel,
    EventPersonRelation,
    EventPlaceRelation,
    Organization,
    Person,
    Place,
    db,
)

app = Flask(__name__)
APP_PATH = os.path.dirname(os.path.abspath(__file__))
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{os.path.join(APP_PATH, 'database')}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db.init_app(app)

NEO4J_URI = local_settings.NEO4J_URI
NEO4J_USER = local_settings.NEO4J_USER
NEO4J_PASSWORD = local_settings.require_neo4j_password()
graph = Graph(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


class SqliteToNeo4jSync:
    def __init__(self, mode="increment"):
        self.mode = mode
        self.stats = {
            "nodes_created": 0,
            "nodes_updated": 0,
            "nodes_skipped": 0,
            "relations_created": 0,
            "relations_skipped": 0,
            "relations_failed": 0,
        }

    def clear_neo4j(self):
        """全量模式下清空 Neo4j。"""
        if self.mode != "full":
            return
        graph.run("MATCH (n) DETACH DELETE n")
        with app.app_context():
            for model in [Event, Place, Organization, Person]:
                db.session.execute(model.__table__.update().values(neo4j_id=None))
            db.session.commit()

    def _create_node(self, label, name, properties=None, graph_key=None):
        """按**稳定图谱键** upsert 节点，返回 (neo4j 节点 id, 状态)。

        不要用 `MERGE (n:Label {name: $name})` 去重，两个后果（文档第六节第 5 条）：
        同名节点被合成一个（"赤壁之战"在不同来源/朝代里确实有多个），
        而改名会被当成新建，留下旧节点加一个新节点。按 `<Type>:<SQLite 主键>` 定位后，
        改名就是一次属性更新，也不会再把两个不同对象合成一个。

        `graph_key` 为空时回退到按名字 MERGE：独立脚本（如早期的一次性工具）可能还没
        传入图谱键，直接报错会把它们一起打坏；回退路径会在日志里留一条提示。
        """
        try:
            label = safe_identifier(label, kind="节点标签")
            props = properties or {}
            props["name"] = name
            if not graph_key:
                print(f"⚠️ 未提供图谱键，按名字合并（同名节点可能被合并）：{label}/{name}")
                cypher = f"""
                MERGE (n:`{label}` {{name: $name}})
                ON CREATE SET n._sync_new = true, n.created = timestamp(), n += $props
                ON MATCH SET n._sync_new = false, n.updated = timestamp(), n += $props
                WITH n, n._sync_new AS is_new
                REMOVE n._sync_new
                RETURN id(n) as node_id, is_new
                """
                result = graph.run(cypher, name=name, props=props).data()
            else:
                # 与 model_search.upsert_node 同一套语义（含"认领同名无键历史节点"）：
                # 两条写路径必须一致，否则管理台改完、全量同步一跑又变回旧样子。
                graph.run(f"""
                MATCH (legacy:`{label}` {{name: $name}})
                WHERE legacy.graph_key IS NULL
                WITH legacy LIMIT 1
                SET legacy.graph_key = $graph_key, legacy.adopted = timestamp()
                """, graph_key=graph_key, name=name)
                cypher = f"""
                MERGE (n:`{label}` {{graph_key: $graph_key}})
                ON CREATE SET n._sync_new = true, n.created = timestamp()
                ON MATCH SET n._sync_new = false
                SET n += $props, n.name = $name, n.graph_key = $graph_key, n.updated = timestamp()
                WITH n, n._sync_new AS is_new
                REMOVE n._sync_new
                RETURN id(n) as node_id, is_new
                """
                result = graph.run(cypher, graph_key=graph_key, name=name, props=props).data()
            if result:
                node_id = result[0]["node_id"]
                return node_id, "created" if result[0].get("is_new") else "updated"
            return None, "failed"
        except Exception as exc:
            print(f"创建节点失败：标签={label}，名称={name}，错误={exc}")
            return None, "failed"

    def sync_entities(self, model, label, properties_builder):
        used_neo4j_ids = set()
        with app.app_context():
            if self.mode == "full":
                entities = model.query.all()
            else:
                entities = model.query.filter(model.neo4j_id.is_(None)).all()
            if not entities:
                return

            for entity in entities:
                try:
                    with db.session.no_autoflush:
                        name = str(entity.name).strip() if entity.name else ""
                        if not name:
                            self.stats["nodes_skipped"] += 1
                            continue
                        props = {k: v for k, v in properties_builder(entity).items() if v not in (None, "")}

                    neo4j_id, status = self._create_node(
                        label, name, props,
                        graph_key=graph_key_for(label, entity.id),
                    )
                    if not neo4j_id:
                        self.stats["nodes_skipped"] += 1
                        continue
                    if neo4j_id in used_neo4j_ids:
                        self.stats["nodes_skipped"] += 1
                        continue

                    used_neo4j_ids.add(neo4j_id)
                    entity.neo4j_id = neo4j_id
                    if status == "created":
                        self.stats["nodes_created"] += 1
                    else:
                        self.stats["nodes_updated"] += 1

                    if (self.stats["nodes_created"] + self.stats["nodes_updated"]) % 50 == 0:
                        db.session.commit()
                except Exception:
                    db.session.rollback()
                    self.stats["nodes_skipped"] += 1

            db.session.commit()

    def sync_events(self):
        def props(e):
            return neo4j_props("Event", orm_row(e, "Event"),
                               include_name=True, fixed=SYNC_FIXED_PROPS.get("Event"))

        self.sync_entities(Event, "Event", props)

    def sync_places(self):
        def props(p):
            return neo4j_props("Place", orm_row(p, "Place"), include_name=True)

        self.sync_entities(Place, "Place", props)

    def sync_organizations(self):
        def props(o):
            return neo4j_props("Organization", orm_row(o, "Organization"), include_name=True)

        self.sync_entities(Organization, "Organization", props)

    def sync_persons(self):
        def props(p):
            return neo4j_props("Person", orm_row(p, "Person"), include_name=True)

        self.sync_entities(Person, "Person", props)

    def _create_relationship(self, start_id, end_id, rel_type, props=None):
        try:
            rel_type = safe_identifier(rel_type, kind="关系类型")
            relation_props = dict(props or {})
            relation_props["relation_type"] = rel_type
            relation_props = {k: v for k, v in relation_props.items() if v is not None}
            # 用 MERGE 代替「先查后建」：重跑不会产生重复边，属性作为 map 参数传入
            # （不再把属性名拼进 Cypher）。临时标记的用法同 _create_node。
            cypher = f"""
            MATCH (a), (b)
            WHERE id(a)=$start_id AND id(b)=$end_id
            MERGE (a)-[r:`{rel_type}`]->(b)
            ON CREATE SET r._sync_new = true
            ON MATCH SET r._sync_new = false
            SET r += $props
            WITH r, r._sync_new AS is_new
            REMOVE r._sync_new
            RETURN id(r) as rid, is_new
            """
            result = graph.run(
                cypher, start_id=start_id, end_id=end_id, props=relation_props
            ).data()
            if not result:
                return "failed"
            return "created" if result[0].get("is_new") else "exists"
        except Exception as exc:
            print(f"创建关系失败：{start_id}-[{rel_type}]->{end_id}，错误={exc}")
            return "failed"

    def _sync_relations_general(self, relation_model, from_model, to_model, from_id_attr, to_id_attr, rel_type_attr, extra_props_func=None):
        with app.app_context():
            relations = relation_model.query.all()
            valid_relations = []
            for rel in relations:
                from_node = db.session.get(from_model, getattr(rel, from_id_attr))
                to_node = db.session.get(to_model, getattr(rel, to_id_attr))
                if from_node and to_node and from_node.neo4j_id and to_node.neo4j_id:
                    valid_relations.append((rel, from_node.neo4j_id, to_node.neo4j_id))

            for rel, from_nid, to_nid in valid_relations:
                relation_type = getattr(rel, rel_type_attr) or "相关"
                if relation_model is EventEventRelation:
                    relation_type = normalize_event_relation_type(relation_type) or "相关"
                extra_props = extra_props_func(rel) if extra_props_func else {}
                status = self._create_relationship(from_nid, to_nid, relation_type, extra_props)
                if status == "created":
                    self.stats["relations_created"] += 1
                elif status == "exists":
                    self.stats["relations_skipped"] += 1
                else:
                    self.stats["relations_failed"] += 1

    def sync_relations(self):
        self._sync_relations_general(EventEventRelation, Event, Event, "event_a_id", "event_b_id", "relation_type")
        self._sync_relations_general(
            EventPlaceRelation,
            Event,
            Place,
            "event_id",
            "place_id",
            "relation_type",
            lambda r: {"modern_name": r.modern_name or ""},
        )
        self._sync_relations_general(EventPersonRelation, Event, Person, "event_id", "person_id", "relation_type")
        self._sync_relations_general(EventOrganizationRel, Event, Organization, "event_id", "org_id", "relation_type")

    def _build_output_stats(self):
        return {
            "新建节点数": self.stats["nodes_created"],
            "更新节点数": self.stats["nodes_updated"],
            "跳过节点数": self.stats["nodes_skipped"],
            "新建关系数": self.stats["relations_created"],
            "跳过关系数": self.stats["relations_skipped"],
            "失败关系数": self.stats["relations_failed"],
        }

    # ---- 孤儿节点清理（反向以 SQLite 为准）----
    def collect_orphans(self):
        """找出 Neo4j 侧在 SQLite 里已无对应记录的节点。

        用途：delete_node 先删 SQLite 再删 Neo4j，Neo4j 侧失败会留下孤儿节点，
        而增量同步以 SQLite 为源、永远清不掉它（质检接口的对账能看见 diff，但不负责清除）。
        判定同时看 neo4j_id 与 (标签, 名称)：任一命中都不算孤儿——宁可漏报也不误删。
        """
        with app.app_context():
            sqlite_ids = set()
            sqlite_names = set()
            for model in [Event, Place, Organization, Person]:
                for node in model.query.all():
                    neo4j_id = getattr(node, "neo4j_id", None)
                    if neo4j_id is not None:
                        try:
                            sqlite_ids.add(int(neo4j_id))
                        except (TypeError, ValueError):
                            pass
                    name = (node.name or "").strip()
                    if name:
                        sqlite_names.add((model.__name__, name))

        rows = graph.run(
            """
            MATCH (n)
            RETURN id(n) AS nid, labels(n) AS labels, n.name AS name
            """
        ).data()

        orphans = []
        for row in rows:
            nid = row["nid"]
            labels = row.get("labels") or []
            label = labels[0] if labels else ""
            name = (row.get("name") or "").strip()
            if nid in sqlite_ids:
                continue
            if label and name and (label, name) in sqlite_names:
                continue
            orphans.append({"neo4j_id": nid, "label": label, "name": name})
        return orphans

    def prune_orphans(self, execute=False):
        """删除（或演练删除）孤儿节点。

        默认只打印清单：删除是不可逆的，先让人看清要删什么再决定。
        """
        orphans = self.collect_orphans()
        if not orphans:
            print("未发现孤儿节点：Neo4j 与 SQLite 的节点集合一致")
            return {"found": 0, "deleted": 0}

        print(f"发现 {len(orphans)} 个孤儿节点（Neo4j 有、SQLite 已无对应记录）：")
        for item in orphans[:50]:
            label = item["label"] or "无标签"
            name = item["name"] or "(无名称)"
            print(f"  - [{label}] {name} (neo4j id={item['neo4j_id']})")
        if len(orphans) > 50:
            print(f"  …… 其余 {len(orphans) - 50} 个省略")

        if not execute:
            print("\n这是演练（dry-run），未删除任何节点。确认清单无误后加 --yes 执行删除。")
            return {"found": len(orphans), "deleted": 0}

        deleted = 0
        for item in orphans:
            try:
                graph.run("MATCH (n) WHERE id(n) = $nid DETACH DELETE n", nid=item["neo4j_id"])
                deleted += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  删除失败：neo4j id={item['neo4j_id']}，错误={exc}")
        print(f"已删除 {deleted}/{len(orphans)} 个孤儿节点")
        return {"found": len(orphans), "deleted": deleted}

    def run(self):
        if self.mode == "full":
            self.clear_neo4j()
        self.sync_events()
        self.sync_places()
        self.sync_organizations()
        self.sync_persons()
        self.sync_relations()
        print(self._build_output_stats())
        print("提示：如需清理 Neo4j 侧已被删除节点留下的孤儿，跑 "
              "`python sync_sqlite_to_neo4j.py --prune-orphans`（先演练，确认后加 --yes）。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="将 SQLite 数据同步到 Neo4j")
    parser.add_argument("--mode", choices=["full", "increment"], default="increment", help="同步模式：full 为全量，increment 为增量")
    parser.add_argument(
        "--prune-orphans",
        action="store_true",
        help="清理 Neo4j 中 SQLite 已删除的孤儿节点（默认只打印清单演练，加 --yes 才真正删除）",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="配合 --prune-orphans：确认执行删除",
    )
    args = parser.parse_args()

    syncer = SqliteToNeo4jSync(mode=args.mode)
    if args.prune_orphans:
        syncer.prune_orphans(execute=args.yes)
    else:
        syncer.run()
