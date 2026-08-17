#!/usr/bin/env python3
"""
将 SQLite 中的数据同步到 Neo4j。
"""

import argparse
import os

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

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "REDACTED-USE-NEO4J_PASSWORD-ENV"
graph = Graph(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

EVENT_RELATION_TYPE_ALIASES = {
    "因果": "因果关系",
    "因果关系": "因果关系",
    "顺承": "顺承关系",
    "顺承关系": "顺承关系",
    "并列": "并列关系",
    "并发": "并列关系",
    "并列关系": "并列关系",
    "并发关系": "并列关系",
    "包含": "包含关系",
    "包含关系": "包含关系",
    "条件": "条件关系",
    "条件关系": "条件关系",
}


def normalize_event_relation_type(value):
    value = (value or "").strip()
    return EVENT_RELATION_TYPE_ALIASES.get(value, value)


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

    def _create_node(self, label, name, properties=None):
        try:
            props = properties or {}
            props["name"] = name
            cypher = f"""
            MERGE (n:{label} {{name: $name}})
            ON CREATE SET n.created = timestamp(), n += $props
            ON MATCH SET n.updated = timestamp(), n += $props
            RETURN id(n) as node_id, n.created as created_time
            """
            result = graph.run(cypher, name=name, props=props).data()
            if result:
                node_id = result[0]["node_id"]
                is_new = result[0].get("created_time") is not None
                return node_id, "created" if is_new else "updated"
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

                    neo4j_id, status = self._create_node(label, name, props)
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
            return {
                "type": "战争事件",
                "EventName": e.name,
                "EventType": e.event_type,
                "StartDate": e.start_date,
                "EndDate": e.end_date,
                "DynastyName": e.dynasty,
                "Place": e.place,
                "Aggressor": e.aggressor,
                "Defender": e.defender,
                "KeyPersons": e.person,
                "Action": e.action,
                "Result": e.result,
                "TroopSize": e.scale,
                "Impact": e.impact,
                "source_text": e.source,
                "relations": e.relations,
                "Remark": e.remark,
            }

        self.sync_entities(Event, "Event", props)

    def sync_places(self):
        def props(p):
            return {
                "geo_name": p.name,
                "modern_name": p.modern_name,
                "DynastyName": p.dynasty,
                "Province": p.province,
                "City": p.city,
                "District_County": p.district,
                "Specific_location": p.specific_location,
                "longitude": p.longitude,
                "latitude": p.latitude,
                "coord_source": p.coord_source,
                "coord_confidence": p.coord_confidence,
                "coord_note": p.coord_note,
            }

        self.sync_entities(Place, "Place", props)

    def sync_organizations(self):
        def props(o):
            return {
                "OrgName": o.name,
                "OrgType": o.org_type,
                "DynastyName": o.dynasty,
                "Description": o.description,
                "Remark": o.remark,
            }

        self.sync_entities(Organization, "Organization", props)

    def sync_persons(self):
        def props(p):
            return {
                "PersonName": p.name,
                "DynastyName": p.dynasty,
                "OrgName": p.org,
                "Role": p.role,
                "Remark": p.remark,
            }

        self.sync_entities(Person, "Person", props)

    def _create_relationship(self, start_id, end_id, rel_type, props=None):
        try:
            check = graph.run(
                """
                MATCH (a)-[r]->(b)
                WHERE id(a)=$start_id AND id(b)=$end_id AND type(r)=$type
                RETURN count(r) as cnt
                """,
                start_id=start_id,
                end_id=end_id,
                type=rel_type,
            ).data()
            if check and check[0]["cnt"] > 0:
                return "exists"

            relation_props = props or {}
            relation_props["relation_type"] = rel_type
            relation_props = {k: v for k, v in relation_props.items() if v is not None}
            set_clause = ", ".join([f"r.{k}=${k}" for k in relation_props.keys()]) if relation_props else ""
            cypher = f"""
            MATCH (a), (b)
            WHERE id(a)=$start_id AND id(b)=$end_id
            CREATE (a)-[r:`{rel_type}`]->(b)
            {f"SET {set_clause}" if set_clause else ""}
            RETURN r
            """
            params = {"start_id": start_id, "end_id": end_id}
            params.update(relation_props)
            graph.run(cypher, **params)
            return "created"
        except Exception:
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

    def run(self):
        if self.mode == "full":
            self.clear_neo4j()
        self.sync_events()
        self.sync_places()
        self.sync_organizations()
        self.sync_persons()
        self.sync_relations()
        print(self._build_output_stats())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="将 SQLite 数据同步到 Neo4j")
    parser.add_argument("--mode", choices=["full", "increment"], default="increment", help="同步模式：full 为全量，increment 为增量")
    args = parser.parse_args()
    SqliteToNeo4jSync(mode=args.mode).run()
