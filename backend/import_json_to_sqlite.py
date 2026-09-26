#!/usr/bin/env python3
"""
将抽取结果导入 SQLite。

支持两种输入：
1. 当前单文件格式：entity-event-relation/output/.../9_final_all.json
2. 旧版分表目录：backend/data/processed
"""

import argparse
import json
import sys
from pathlib import Path

from flask import Flask
from sqlalchemy import text

from common_utils import repair_mojibake, safe_identifier
from logging_util import get_logger
from relation_types import normalize_event_relation_type
from models import db, Event, Place, Organization, Person
from models import EventEventRelation, EventPlaceRelation, EventPersonRelation, EventOrganizationRel

logger = get_logger(__name__)


app = Flask(__name__)
APP_PATH = Path(__file__).resolve().parent
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{APP_PATH / 'database'}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db.init_app(app)

DEFAULT_FINAL_JSON = (
    APP_PATH.parent
    / "entity-event-relation"
    / "output"
    / "中国历代战争简史"
    / "9_final_all.json"
)
CURRENT_DATASET_META = APP_PATH / "data" / "current_dataset.json"
LEGACY_PROCESSED_DIR = APP_PATH / "data" / "processed"


def _safe_text(value):
    """把 JSON 里的任意标量转成去空白的字符串；None 得空串。

    顺带做编码复原：有一批值是把 UTF-8 字节按 GBK 读出来的乱码
    （如"鎴樹簤浜嬩欢"），在前端映射表里认乱码 key 只是把问题藏在展示层。
    这里在源头修——正常文本在这步是恒等操作，见 common_utils.repair_mojibake。
    """
    if value is None:
        return ""
    return repair_mojibake(str(value).strip())


def _safe_float(value):
    """宽松转 float：空值或转不动一律 None（坐标字段经常是空串或"不详"）。"""
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def clear_migration_tables():
    """清空 4 类实体表与 4 类关系表。**不动 UserInfo 等非知识表。**

    删除顺序是"先关系、后实体"，不是随便排的：app.py 的连接钩子对每个连接执行
    `PRAGMA foreign_keys=ON`，被引用的实体行若先于引用它的关系行删除，SQLite 会直接
    报 FOREIGN KEY constraint failed。不能靠 `PRAGMA foreign_keys = OFF` 兜着：
    那个 PRAGMA 在 SQLAlchemy 已经开启的事务里是**静默无效**的（SQLite 明确要求它必须
    在事务外执行），所以顺序本身就是唯一的正确做法。
    """
    with app.app_context():
        try:
            tables = [
                # 关系表先删（它们引用实体表）
                "event_event_relations",
                "event_place_relations",
                "event_person_relations",
                "event_organization_rel",
                # 再删实体表
                "events",
                "places",
                "organizations",
                "persons",
            ]
            for table in tables:
                db.session.execute(text(f"DELETE FROM {safe_identifier(table, kind='表名')}"))

            result = db.session.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'")
            ).fetchone()
            if result:
                db.session.execute(
                    text(
                        "DELETE FROM sqlite_sequence "
                        "WHERE name IN "
                        "('events','places','organizations','persons',"
                        "'event_event_relations','event_place_relations',"
                        "'event_person_relations','event_organization_rel')"
                    )
                )

            db.session.commit()
            # 重导同时作废旧的待补偿任务（文档第六节第 3 条 F）：
            # 主键会从 sqlite_sequence 重置后重新分配，旧任务指向的是**上一批数据的 id**。
            # 不在这里作废，它们会被后台照常重放，把上一批数据重新写进图谱，
            # 与新数据混在一起——而且这种混合没有任何报错，只能在图里肉眼发现。
            try:
                from sync_compensation import bump_dataset_version

                bump_dataset_version()
            except Exception as exc:  # noqa: BLE001 - 表还没建时不该让重导失败
                logger.warning(f"⚠️ 作废旧待补偿任务失败（不影响本次重导）：{exc}")
            return True, "成功"
        except Exception as exc:
            db.session.rollback()
            return False, str(exc)


def _build_legacy_filename_map(importer):
    """生成旧版分表快照文件名映射。"""
    return {
        "事件表_Event.json": importer._iter_events(),
        "地点表_Place.json": importer._iter_places(),
        "组织表_Organization.json": importer._iter_organizations(),
        "人物表_Person.json": importer._iter_persons(),
        "事件-事件关系表_event_event_relations.json": importer._iter_event_event_relations(),
        "事件-地点关系表_event_place_relations.json": importer._iter_event_place_relations(),
        "事件-人物关系表_event_person_relations.json": importer._iter_event_person_relations(),
        "事件-组织关系表_event_organization_rel.json": importer._iter_event_org_relations(),
    }


class JsonToSqliteImporter:
    def __init__(self, source_path):
        self.source_path = Path(source_path).resolve()
        self.stats = {
            "events": {"inserted": 0, "error": 0},
            "places": {"inserted": 0, "error": 0},
            "orgs": {"inserted": 0, "error": 0},
            "persons": {"inserted": 0, "error": 0},
            "relations": {"inserted": 0, "error": 0},
        }
        self.dataset_meta = {
            "source_path": str(self.source_path),
            "source_kind": "",
            "metadata": {},
            "quality_report": {},
        }
        self.payload = self._load_source()

    def _load_json_file(self, path: Path):
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _load_source(self):
        if self.source_path.is_file():
            data = self._load_json_file(self.source_path)
            if isinstance(data, dict) and "entities" in data and "relations" in data:
                self.dataset_meta["source_kind"] = "final_json"
                self.dataset_meta["metadata"] = data.get("metadata", {})
                self.dataset_meta["quality_report"] = data.get("quality_report", {})
                return data
            raise ValueError(f"不支持的 JSON 文件格式：{self.source_path}")

        if self.source_path.is_dir():
            self.dataset_meta["source_kind"] = "legacy_dir"
            return None

        raise FileNotFoundError(f"未找到数据源路径：{self.source_path}")

    def _save_dataset_meta(self):
        CURRENT_DATASET_META.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            **self.dataset_meta,
            "sqlite_counts": {
                "events": self.stats["events"]["inserted"],
                "places": self.stats["places"]["inserted"],
                "organizations": self.stats["orgs"]["inserted"],
                "persons": self.stats["persons"]["inserted"],
                "relations": self.stats["relations"]["inserted"],
            },
        }
        with CURRENT_DATASET_META.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    def _save_legacy_processed_snapshot(self):
        if self.dataset_meta["source_kind"] != "final_json":
            return

        LEGACY_PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        for filename, data in _build_legacy_filename_map(self).items():
            target = LEGACY_PROCESSED_DIR / filename
            with target.open("w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)

    def _load_legacy_json(self, filename):
        path = self.source_path / filename
        if not path.exists():
            return []
        return self._load_json_file(path)

    def _iter_events(self):
        if self.dataset_meta["source_kind"] == "final_json":
            events_payload = self.payload.get("events", [])
            if isinstance(events_payload, dict):
                return events_payload.get("events", [])
            return events_payload
        return self._load_legacy_json("事件表_Event.json")

    def _iter_places(self):
        if self.dataset_meta["source_kind"] == "final_json":
            return self.payload.get("entities", {}).get("places", [])
        return self._load_legacy_json("地点表_Place.json")

    def _iter_organizations(self):
        if self.dataset_meta["source_kind"] == "final_json":
            return self.payload.get("entities", {}).get("organizations", [])
        return self._load_legacy_json("组织表_Organization.json")

    def _iter_persons(self):
        if self.dataset_meta["source_kind"] == "final_json":
            return self.payload.get("entities", {}).get("persons", [])
        return self._load_legacy_json("人物表_Person.json")

    def _iter_event_event_relations(self):
        if self.dataset_meta["source_kind"] == "final_json":
            return self.payload.get("relations", {}).get("event_event_relations", [])
        return self._load_legacy_json("事件-事件关系表_event_event_relations.json")

    def _iter_event_place_relations(self):
        if self.dataset_meta["source_kind"] == "final_json":
            return self.payload.get("relations", {}).get("event_place_relations", [])
        return self._load_legacy_json("事件-地点关系表_event_place_relations.json")

    def _iter_event_person_relations(self):
        if self.dataset_meta["source_kind"] == "final_json":
            return self.payload.get("relations", {}).get("event_person_relations", [])
        return self._load_legacy_json("事件-人物关系表_event_person_relations.json")

    def _iter_event_org_relations(self):
        if self.dataset_meta["source_kind"] == "final_json":
            return self.payload.get("relations", {}).get("event_organization_relations", [])
        return self._load_legacy_json("事件-组织关系表_event_organization_rel.json")

    def import_events(self):
        for item in self._iter_events():
            try:
                name = _safe_text(item.get("EventName"))
                if not name:
                    self.stats["events"]["error"] += 1
                    continue

                event = Event(
                    name=name,
                    event_type=_safe_text(item.get("EventType")) or "战争",
                    start_date=_safe_text(item.get("StartDate")),
                    end_date=_safe_text(item.get("EndDate")),
                    dynasty=_safe_text(item.get("DynastyName")),
                    place=_safe_text(item.get("Place")),
                    aggressor=_safe_text(item.get("Aggressor")),
                    defender=_safe_text(item.get("Defender")),
                    result=_safe_text(item.get("Result")),
                    person=_safe_text(item.get("KeyPersons") or item.get("Person")),
                    impact=_safe_text(item.get("Impact")),
                    source=_safe_text(item.get("source_text") or item.get("source")),
                    scale=_safe_text(item.get("TroopSize") or item.get("Scale")),
                    action=_safe_text(item.get("Action") or item.get("action")),
                    remark=_safe_text(item.get("Remark")),
                    relations=json.dumps(item.get("relations", []), ensure_ascii=False)
                    if item.get("relations")
                    else "",
                )
                db.session.add(event)
                self.stats["events"]["inserted"] += 1
                if self.stats["events"]["inserted"] % 50 == 0:
                    db.session.commit()
            except Exception as exc:
                self.stats["events"]["error"] += 1
                db.session.rollback()
                event_name = _safe_text(item.get("EventName")) if isinstance(item, dict) else ""
                print(f"事件导入失败：事件名={event_name}，错误={exc}")
        db.session.commit()

    def import_places(self):
        for item in self._iter_places():
            try:
                name = _safe_text(item.get("geo_name"))
                if not name:
                    self.stats["places"]["error"] += 1
                    continue
                place = Place(
                    name=name,
                    modern_name=_safe_text(item.get("modern_name")),
                    dynasty=_safe_text(item.get("DynastyName")),
                    province=_safe_text(item.get("Province")),
                    city=_safe_text(item.get("City")),
                    district=_safe_text(item.get("District_County") or item.get("District")),
                    specific_location=_safe_text(item.get("Specific_location") or item.get("Specific_Location")),
                    longitude=_safe_float(item.get("longitude") or item.get("Longitude")),
                    latitude=_safe_float(item.get("latitude") or item.get("Latitude")),
                    coord_source=_safe_text(item.get("coord_source") or item.get("CoordSource")),
                    coord_confidence=_safe_text(item.get("coord_confidence") or item.get("CoordConfidence")),
                    coord_note=_safe_text(item.get("coord_note") or item.get("CoordNote")),
                )
                db.session.add(place)
                self.stats["places"]["inserted"] += 1
                if self.stats["places"]["inserted"] % 50 == 0:
                    db.session.commit()
            except Exception:
                self.stats["places"]["error"] += 1
                db.session.rollback()
        db.session.commit()

    def import_organizations(self):
        valid_types = {"国家", "部落", "起义军", "联盟", "地方势力", "中央政权"}
        for item in self._iter_organizations():
            try:
                name = _safe_text(item.get("OrgName"))
                if not name:
                    self.stats["orgs"]["error"] += 1
                    continue
                org_type = _safe_text(item.get("OrgType")) or "地方势力"
                if org_type not in valid_types:
                    org_type = "地方势力"
                org = Organization(
                    name=name,
                    org_type=org_type,
                    dynasty=_safe_text(item.get("DynastyName")),
                    description=_safe_text(item.get("Description") or item.get("source_text")),
                    remark=_safe_text(item.get("Remark")),
                )
                db.session.add(org)
                self.stats["orgs"]["inserted"] += 1
                if self.stats["orgs"]["inserted"] % 50 == 0:
                    db.session.commit()
            except Exception:
                self.stats["orgs"]["error"] += 1
                db.session.rollback()
        db.session.commit()

    def import_persons(self):
        for item in self._iter_persons():
            try:
                name = _safe_text(item.get("PersonName"))
                if not name:
                    self.stats["persons"]["error"] += 1
                    continue
                person = Person(
                    name=name,
                    dynasty=_safe_text(item.get("DynastyName")),
                    org=_safe_text(item.get("OrgName")),
                    role=_safe_text(item.get("Role")),
                    remark=_safe_text(item.get("Remark") or item.get("source_text")),
                )
                db.session.add(person)
                self.stats["persons"]["inserted"] += 1
                if self.stats["persons"]["inserted"] % 50 == 0:
                    db.session.commit()
            except Exception:
                self.stats["persons"]["error"] += 1
                db.session.rollback()
        db.session.commit()

    def _find_event(self, name):
        return Event.query.filter_by(name=_safe_text(name)).first()

    def _find_place(self, name, modern_name=""):
        raw_name = _safe_text(name)
        raw_modern = _safe_text(modern_name)

        # 1. 精确匹配 geo_name (name 字段)
        if raw_name:
            place = Place.query.filter_by(name=raw_name).first()
            if place:
                return place

        # 2. 精确匹配 modern_name
        if raw_modern:
            place = Place.query.filter_by(modern_name=raw_modern).first()
            if place:
                return place

        # 3. 模糊匹配：name 包含查询值，或查询值包含 name
        if raw_name:
            place = Place.query.filter(Place.name.like(f"%{raw_name}%")).first()
            if place:
                return place
            place = Place.query.filter(Place.modern_name.like(f"%{raw_name}%")).first()
            if place:
                return place

        # 4. 模糊匹配：modern_name 包含查询值
        if raw_modern:
            place = Place.query.filter(Place.name.like(f"%{raw_modern}%")).first()
            if place:
                return place
            place = Place.query.filter(Place.modern_name.like(f"%{raw_modern}%")).first()
            if place:
                return place

        return None

    def _find_person(self, name):
        return Person.query.filter_by(name=_safe_text(name)).first()

    def _find_org(self, name):
        return Organization.query.filter_by(name=_safe_text(name)).first()

    def _import_event_event(self):
        inserted = 0
        for item in self._iter_event_event_relations():
            try:
                a_name = _safe_text(item.get("EventName_A"))
                b_name = _safe_text(item.get("EventName_B"))
                rel_type = normalize_event_relation_type(item.get("relation") or item.get("relations")) or "相关"
                if not a_name or not b_name:
                    continue
                event_a = self._find_event(a_name)
                event_b = self._find_event(b_name)
                if not event_a or not event_b:
                    continue
                db.session.add(
                    EventEventRelation(
                        event_a_id=event_a.id,
                        event_b_id=event_b.id,
                        event_a_name=a_name,
                        event_b_name=b_name,
                        relation_type=rel_type,
                    )
                )
                inserted += 1
                if inserted % 50 == 0:
                    db.session.commit()
            except Exception:
                self.stats["relations"]["error"] += 1
                db.session.rollback()
        db.session.commit()
        self.stats["relations"]["inserted"] += inserted

    def _import_event_place(self):
        inserted = 0
        for item in self._iter_event_place_relations():
            try:
                event_name = _safe_text(item.get("EventName"))
                place_name = _safe_text(item.get("geo_name") or item.get("PlaceName") or item.get("modern_name"))
                modern_name = _safe_text(item.get("modern_name"))
                rel_type = _safe_text(item.get("relation") or item.get("relations")) or "发生地"
                evidence = _safe_text(item.get("evidence") or item.get("source_text"))
                if not event_name or not (place_name or modern_name):
                    continue
                event = self._find_event(event_name)
                place = self._find_place(place_name, modern_name)
                if not event or not place:
                    continue
                db.session.add(
                    EventPlaceRelation(
                        event_id=event.id,
                        place_id=place.id,
                        event_name=event_name,
                        place_name=place.name,
                        modern_name=place.modern_name,
                        relation_type=rel_type,
                        evidence=evidence,
                        source_type=_safe_text(item.get("source_type")) or "extraction",
                        confidence=_safe_text(item.get("confidence")) or ("medium" if evidence else ""),
                    )
                )
                inserted += 1
                if inserted % 50 == 0:
                    db.session.commit()
            except Exception:
                self.stats["relations"]["error"] += 1
                db.session.rollback()
        db.session.commit()
        self.stats["relations"]["inserted"] += inserted

    def _import_event_person(self):
        inserted = 0
        for item in self._iter_event_person_relations():
            try:
                event_name = _safe_text(item.get("EventName"))
                person_name = _safe_text(item.get("PersonName"))
                rel_type = _safe_text(item.get("relation") or item.get("relations")) or "参与"
                if not event_name or not person_name:
                    continue
                event = self._find_event(event_name)
                person = self._find_person(person_name)
                if not event or not person:
                    continue
                db.session.add(
                    EventPersonRelation(
                        event_id=event.id,
                        person_id=person.id,
                        event_name=event_name,
                        person_name=person_name,
                        relation_type=rel_type,
                    )
                )
                inserted += 1
                if inserted % 50 == 0:
                    db.session.commit()
            except Exception:
                self.stats["relations"]["error"] += 1
                db.session.rollback()
        db.session.commit()
        self.stats["relations"]["inserted"] += inserted

    def _import_event_org(self):
        inserted = 0
        for item in self._iter_event_org_relations():
            try:
                event_name = _safe_text(item.get("EventName"))
                org_name = _safe_text(item.get("OrgName"))
                rel_type = _safe_text(item.get("relation") or item.get("relations")) or "参战"
                if not event_name or not org_name:
                    continue
                event = self._find_event(event_name)
                org = self._find_org(org_name)
                if not event or not org:
                    continue
                db.session.add(
                    EventOrganizationRel(
                        event_id=event.id,
                        org_id=org.id,
                        event_name=event_name,
                        org_name=org_name,
                        relation_type=rel_type,
                    )
                )
                inserted += 1
                if inserted % 50 == 0:
                    db.session.commit()
            except Exception:
                self.stats["relations"]["error"] += 1
                db.session.rollback()
        db.session.commit()
        self.stats["relations"]["inserted"] += inserted

    def import_relations(self):
        self._import_event_event()
        self._import_event_place()
        self._import_event_person()
        self._import_event_org()

    def _build_output_stats(self):
        return {
            "事件": self.stats["events"],
            "地点": self.stats["places"],
            "组织": self.stats["orgs"],
            "人物": self.stats["persons"],
            "关系": self.stats["relations"],
        }

    def _user_snapshot(self):
        """当前账号快照：(总数, {账号: 角色})。

        导入前后各取一次做对照——导入是"只清知识表"的，账号必须一条不少、角色一个不变。
        这不是多余的断言：脚本只清 8 张业务表，任何把 UserInfo 带进去的改动都会被这里挡下，
        并在响应里明写"账号数量对不上"，而不是让人事后才发现登录不了。
        """
        try:
            rows = db.session.execute(
                text("SELECT account, role FROM UserInfo")
            ).fetchall()
        except Exception:
            # 全新库还没建 UserInfo（create_all 之前）——视为空快照
            return 0, {}
        return len(rows), {row[0]: (row[1] or "viewer") for row in rows}

    def run(self, confirm: bool = False):
        if not confirm:
            raise SystemExit(
                "已中止：本命令会**清空全部知识数据**（4 类实体 + 4 类关系表）并重新导入，"
                "属于破坏性操作，请先备份数据库文件。"
                "确认要覆盖现有知识数据时加 --yes 重跑。"
                "（账户表 UserInfo 不在清理范围内，导入前后会核对账号数量与角色。）"
            )
        with app.app_context():
            # 只建缺失的表，**不 drop_all**。
            #
            # 不能调 db.drop_all()：UserInfo 与知识表共用同一个 SQLAlchemy metadata，
            # 一句 drop_all 就会把"换一份抽取数据集重导"变成删光所有账号、口令哈希与角色；
            # 管理员账号消失后系统只能重新注册 viewer，再手工改 SQLite 才能恢复。
            # create_all 对已存在的表是空操作，正好满足"补齐新表、不动老表"。
            db.create_all()

            users_before = self._user_snapshot()

            success, msg = clear_migration_tables()
            if not success:
                print(f"清空数据表失败：{msg}")
                sys.exit(1)

            self.import_events()
            self.import_places()
            self.import_organizations()
            self.import_persons()
            self.import_relations()
            self._save_dataset_meta()
            self._save_legacy_processed_snapshot()

            users_after = self._user_snapshot()
            if users_before != users_after:
                # 走到这里说明账号被动过：业务表清理的范围出了问题，必须显式失败，
                # 不能打印一行统计就当成功（这条路径本该不可达）。
                print(
                    f"❌ 账号数据被意外改动：导入前 {users_before[0]} 个账号 / "
                    f"导入后 {users_after[0]} 个账号。请立即从备份恢复并检查清理范围。"
                )
                sys.exit(1)

            print(json.dumps(self._build_output_stats(), ensure_ascii=False, indent=2))
            print(f"✅ 账号未受影响：{users_after[0]} 个账号，角色不变。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="将抽取结果导入 SQLite 数据库")
    parser.add_argument(
        "--source",
        default=str(DEFAULT_FINAL_JSON),
        help="抽取结果文件路径，默认使用完整 9_final_all.json，也支持 published/final.json 或旧版 processed 目录",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="确认清空知识表（4 类实体 + 4 类关系）并重新导入；不加该参数时脚本拒绝执行。"
             "账号表 UserInfo 不受影响。",
    )
    args = parser.parse_args()
    importer = JsonToSqliteImporter(source_path=args.source)
    importer.run(confirm=args.yes)
