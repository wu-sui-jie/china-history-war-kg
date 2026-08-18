"""
Flask应用主入口

功能: 提供RESTful API接口
  - 用户认证: /api/login, /api/sign_in
  - 知识图谱查询: /search_name_kg
  - 节点管理: /create_node, /update_node, /delete_node
  - 智能问答: /api/ai/inference

数据流向: 前端 → SQLite(主存储) → Neo4j(可视化)
"""

import json
import os
import time
import uuid
import atexit
import re
import sys
from collections import Counter

# ================== Flask核心模块 ==================
from flask import Flask, request, jsonify, g, Response
from flask_cors import CORS
from sqlalchemy.pool import NullPool
from sqlalchemy import func, text

# ================== 自定义模块 ==================
from db_utils import DbUtil
from jwt_util import decode, encode
from model_search import neo4j_db
from inference.rule_llm_integration import DYNASTY_SCOPE_MAP
from models import (
    Event,
    Place,
    Organization,
    Person,
    EventEventRelation,
    EventOrganizationRel,
    EventPlaceRelation,
    EventPersonRelation,
)

# ================== 创建Flask应用 ==================
app = Flask(__name__)
CORS(app)
neo4j_db_handle = neo4j_db()
shared_entity_extractor = None
shared_rule_llm_integration = None
user_id = None

# ================== 数据库配置 ==================
APP_PATH = os.path.dirname(__file__)

# ================== entity-event-relation 模块路径注入 ==================
# src.* 包位于同级目录 entity-event-relation 下，运行时需先加入 sys.path
ENTITY_EVENT_RELATION_DIR = os.path.join(os.path.dirname(APP_PATH), 'entity-event-relation')
if ENTITY_EVENT_RELATION_DIR not in sys.path:
    sys.path.insert(0, ENTITY_EVENT_RELATION_DIR)

from src.models import (
    EntityExtractionResult,
    EventExtractionResult,
    RelationExtractionResult,
)
from src.extractors.entity_extractor import EntityExtractor
from src.extractors.event_extractor import EventExtractor
from src.extractors.relation_extractor import RelationExtractor
from src.core.text_splitter import TextSplitter

app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{APP_PATH}/database'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'poolclass': NullPool,
    'connect_args': {
        'check_same_thread': False,
        'timeout': 30
    }
}

# 初始化关系型数据库（包含同步管理器初始化）
DbUtil.init_app(app)


def ensure_event_place_relation_metadata_columns():
    """为旧 SQLite 库补充事件-地点关系证据字段。"""
    from models import db

    existing_columns = {
        row[1] for row in db.session.execute(text("PRAGMA table_info(event_place_relations)")).fetchall()
    }
    column_sql = {
        "evidence": "ALTER TABLE event_place_relations ADD COLUMN evidence TEXT",
        "source_type": "ALTER TABLE event_place_relations ADD COLUMN source_type VARCHAR(50)",
        "confidence": "ALTER TABLE event_place_relations ADD COLUMN confidence VARCHAR(50)",
    }
    changed = False
    for column, sql in column_sql.items():
        if column not in existing_columns:
            db.session.execute(text(sql))
            changed = True
    if changed:
        db.session.commit()


def ensure_place_coordinate_metadata_columns():
    """为旧 SQLite 库补充地点坐标治理字段。"""
    from models import db

    existing_columns = {
        row[1] for row in db.session.execute(text("PRAGMA table_info(places)")).fetchall()
    }
    column_sql = {
        "longitude": "ALTER TABLE places ADD COLUMN longitude FLOAT",
        "latitude": "ALTER TABLE places ADD COLUMN latitude FLOAT",
        "coord_source": "ALTER TABLE places ADD COLUMN coord_source VARCHAR(100)",
        "coord_confidence": "ALTER TABLE places ADD COLUMN coord_confidence VARCHAR(50)",
        "coord_note": "ALTER TABLE places ADD COLUMN coord_note TEXT",
    }
    changed = False
    for column, sql in column_sql.items():
        if column not in existing_columns:
            db.session.execute(text(sql))
            changed = True
    if changed:
        db.session.commit()


def backfill_event_place_relation_evidence():
    """从当前 processed JSON 快速回填关系证据，不覆盖已有人工值。"""
    from models import db

    def safe(value):
        if value is None:
            return ""
        return str(value).strip()

    rel_path = os.path.join(APP_PATH, "data", "processed", "事件-地点关系表_event_place_relations.json")
    if not os.path.exists(rel_path):
        return

    try:
        with open(rel_path, "r", encoding="utf-8") as handle:
            rows = json.load(handle)
    except Exception as exc:
        print(f"读取事件-地点关系证据失败: {exc}")
        return

    evidence_index = {}
    for item in rows:
        event_name = safe(item.get("EventName"))
        rel_type = safe(item.get("relation") or item.get("relations"))
        place_name = safe(item.get("modern_name") or item.get("geo_name") or item.get("PlaceName"))
        evidence = safe(item.get("evidence") or item.get("source_text"))
        if event_name and rel_type and place_name and evidence:
            evidence_index[(event_name, rel_type, place_name)] = evidence

    if not evidence_index:
        return

    updated = 0
    for relation in EventPlaceRelation.query.all():
        if safe(getattr(relation, "evidence", "")):
            continue
        candidates = [
            (relation.event_name, relation.relation_type, relation.modern_name),
            (relation.event_name, relation.relation_type, relation.place_name),
        ]
        evidence = next((evidence_index.get(tuple(safe(value) for value in key)) for key in candidates if evidence_index.get(tuple(safe(value) for value in key))), "")
        if evidence:
            relation.evidence = evidence
            relation.source_type = relation.source_type or "extraction"
            relation.confidence = relation.confidence or "medium"
            updated += 1

    if updated:
        db.session.commit()
        print(f"已回填事件-地点关系证据 {updated} 条")


# 启用 WAL 模式
with app.app_context():
    from models import db

    try:
        db.session.execute(text("PRAGMA journal_mode=WAL;"))
        db.session.execute(text("PRAGMA synchronous=NORMAL;"))
        ensure_event_place_relation_metadata_columns()
        ensure_place_coordinate_metadata_columns()
        backfill_event_place_relation_evidence()
        db.session.commit()
        print("✅ SQLite WAL 模式已启用")
    except Exception as e:
        print(f"⚠️ 设置 WAL 模式失败: {e}")

# 历史地名词典
historical_places = []

DYNASTY_DISPLAY_ORDER = [
    "夏",
    "商",
    "西周",
    "东周",
    "春秋",
    "战国",
    "秦朝",
    "汉朝",
    "西汉",
    "东汉",
    "三国",
    "魏国",
    "蜀国",
    "吴国",
    "晋朝",
    "西晋",
    "东晋",
    "南北朝",
    "隋朝",
    "唐朝",
    "五代十国",
    "宋朝",
    "北宋",
    "南宋",
    "辽朝",
    "金朝",
    "元朝",
    "明朝",
    "清朝",
    "民国",
]

DYNASTY_ALIAS_MAP = {
    "夏朝": "夏",
    "商朝": "商",
    "周朝": "东周",
    "秦": "秦朝",
    "汉": "汉朝",
    "隋": "隋朝",
    "唐": "唐朝",
    "宋": "宋朝",
    "辽": "辽朝",
    "金": "金朝",
    "元": "元朝",
    "明": "明朝",
    "清": "清朝",
}

TYPE_LABELS = {
    "Event": "战争事件",
    "Place": "战争地点",
    "Organization": "参战组织",
    "Person": "历史人物",
}

TYPE_ALIASES = {
    "Event": "Event",
    "战争事件": "Event",
    "事件": "Event",
    "Place": "Place",
    "战争地点": "Place",
    "地点": "Place",
    "Organization": "Organization",
    "参战组织": "Organization",
    "势力组织": "Organization",
    "组织": "Organization",
    "Person": "Person",
    "历史人物": "Person",
    "人物": "Person",
}

SOURCE_TYPE_LABELS = {
    "graph": "来自图谱",
    "rule": "规则推理",
    "llm": "大模型补充",
}

PROVINCE_CENTROIDS = {
    "北京": (116.40, 39.90),
    "天津": (117.20, 39.12),
    "河北": (114.48, 38.03),
    "山西": (112.55, 37.87),
    "内蒙古": (111.67, 40.82),
    "辽宁": (123.43, 41.80),
    "吉林": (125.32, 43.90),
    "黑龙江": (126.63, 45.75),
    "上海": (121.47, 31.23),
    "江苏": (118.78, 32.04),
    "浙江": (120.15, 30.28),
    "安徽": (117.27, 31.86),
    "福建": (119.30, 26.08),
    "江西": (115.89, 28.68),
    "山东": (117.00, 36.65),
    "河南": (113.62, 34.75),
    "湖北": (114.30, 30.60),
    "湖南": (112.98, 28.20),
    "广东": (113.27, 23.13),
    "广西": (108.32, 22.82),
    "海南": (110.35, 20.02),
    "重庆": (106.55, 29.57),
    "四川": (104.07, 30.67),
    "贵州": (106.71, 26.57),
    "云南": (102.71, 25.04),
    "西藏": (91.13, 29.65),
    "陕西": (108.95, 34.27),
    "甘肃": (103.82, 36.07),
    "青海": (101.78, 36.62),
    "宁夏": (106.27, 38.47),
    "新疆": (87.62, 43.82),
    "香港": (114.17, 22.28),
    "澳门": (113.54, 22.19),
    "台湾": (121.51, 25.04),
}

CITY_CENTROIDS = {
    "西安": (108.95, 34.27),
    "洛阳": (112.44, 34.66),
    "安阳": (114.39, 36.10),
    "新乡": (113.93, 35.31),
    "焦作": (113.24, 35.22),
    "运城": (111.01, 35.02),
    "渭南": (109.50, 34.50),
    "长治": (113.12, 36.20),
    "宝鸡": (107.23, 34.36),
    "咸阳": (108.70, 34.33),
    "郑州": (113.62, 34.75),
    "开封": (114.30, 34.80),
    "邯郸": (114.54, 36.63),
    "邢台": (114.49, 37.06),
    "濮阳": (115.03, 35.76),
    "商丘": (115.65, 34.44),
    "平顶山": (113.19, 33.77),
    "南阳": (112.53, 32.99),
    "十堰": (110.78, 32.65),
    "武汉": (114.31, 30.52),
    "成都": (104.07, 30.67),
    "太原": (112.55, 37.87),
    "北京": (116.40, 39.90),
    "南京": (118.78, 32.04),
    "杭州": (120.15, 30.28),
}

HISTORICAL_REGION_CENTROIDS = {
    "汉水": (112.15, 32.05),
    "汉水流域": (112.35, 31.75),
    "荆楚": (112.20, 30.35),
    "湖北长江流域": (112.30, 30.40),
    "淮河流域": (116.80, 32.90),
    "东夷": (118.40, 35.20),
    "东夷（山东": (117.00, 36.65),
    "江苏一带）": (118.78, 32.04),
    "西北地区": (108.95, 34.27),
    "徐夷地区": (117.20, 34.25),
    "淮夷地区": (116.80, 32.90),
    "徐戎": (118.20, 33.45),
    "犬丘": (107.25, 34.35),
    "骊山": (109.21, 34.37),
}

ROUTE_RELATION_PRIORITY = {
    "出发地": 1,
    "驻防地": 2,
    "途经地": 3,
    "补给地": 4,
    "指挥所": 5,
    "主战场": 6,
    "次要战场": 7,
    "战略要地": 8,
    "议和地点": 9,
    "目的地": 10,
}


# ================== 初始化函数 ==================

def load_current_dataset_meta():
    """读取当前数据集元信息，供工作台页面复用。"""
    meta_path = os.path.join(APP_PATH, 'data', 'current_dataset.json')
    if not os.path.exists(meta_path):
        return {}

    try:
        with open(meta_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as exc:
        print(f"读取当前数据集元信息失败: {exc}")
        return {}


def _safe_text(value):
    """将任意值转换为字符串，便于做空值判断。"""
    if value is None:
        return ''
    return str(value).strip()


def _normalize_dynasty_name(name):
    raw = _safe_text(name)
    if not raw:
        return ''
    return DYNASTY_ALIAS_MAP.get(raw, raw)


def _dynasty_filter_values(name):
    canonical = _normalize_dynasty_name(name)
    if not canonical:
        return []
    values = {canonical}
    for alias, mapped in DYNASTY_ALIAS_MAP.items():
        if mapped == canonical:
            values.add(alias)
    return list(values)


def _duplicate_name_rows(model, type_name, limit=20):
    """统计重名节点。"""
    rows = (
        model.query.with_entities(model.name, func.count(model.id).label('count'))
        .filter(model.name.isnot(None))
        .group_by(model.name)
        .having(func.count(model.id) > 1)
        .order_by(func.count(model.id).desc(), model.name.asc())
        .limit(limit)
        .all()
    )
    return [{"name": row[0], "count": row[1], "type": type_name} for row in rows]


def _missing_field_rows(model, type_name, required_fields, limit=20):
    """扫描缺少关键字段的节点。"""
    rows = []
    for record in model.query.all():
        missing_fields = []
        for field in required_fields:
            if _safe_text(getattr(record, field, None)) == '':
                missing_fields.append(field)

        if missing_fields:
            rows.append({
                "id": record.id,
                "name": getattr(record, 'name', f'ID-{record.id}'),
                "type": type_name,
                "missing_fields": missing_fields
            })

        if len(rows) >= limit:
            break
    return rows


def _isolated_node_rows(limit=20):
    """找出未参与任何结构化关系的孤立节点。"""
    related_event_ids = {
        row[0] for row in EventEventRelation.query.with_entities(EventEventRelation.event_a_id).all() if row[0] is not None
    } | {
        row[0] for row in EventEventRelation.query.with_entities(EventEventRelation.event_b_id).all() if row[0] is not None
    } | {
        row[0] for row in EventPlaceRelation.query.with_entities(EventPlaceRelation.event_id).all() if row[0] is not None
    } | {
        row[0] for row in EventPersonRelation.query.with_entities(EventPersonRelation.event_id).all() if row[0] is not None
    } | {
        row[0] for row in EventOrganizationRel.query.with_entities(EventOrganizationRel.event_id).all() if row[0] is not None
    }

    related_place_ids = {row[0] for row in EventPlaceRelation.query.with_entities(EventPlaceRelation.place_id).all() if row[0] is not None}
    related_person_ids = {row[0] for row in EventPersonRelation.query.with_entities(EventPersonRelation.person_id).all() if row[0] is not None}
    related_org_ids = {row[0] for row in EventOrganizationRel.query.with_entities(EventOrganizationRel.org_id).all() if row[0] is not None}

    isolated_rows = []

    for event in Event.query.all():
        if event.id not in related_event_ids:
            isolated_rows.append({"id": event.id, "name": event.name, "type": "Event"})
        if len(isolated_rows) >= limit:
            return isolated_rows

    for place in Place.query.all():
        if place.id not in related_place_ids:
            isolated_rows.append({"id": place.id, "name": place.name, "type": "Place"})
        if len(isolated_rows) >= limit:
            return isolated_rows

    for person in Person.query.all():
        if person.id not in related_person_ids:
            isolated_rows.append({"id": person.id, "name": person.name, "type": "Person"})
        if len(isolated_rows) >= limit:
            return isolated_rows

    for org in Organization.query.all():
        if org.id not in related_org_ids:
            isolated_rows.append({"id": org.id, "name": org.name, "type": "Organization"})
        if len(isolated_rows) >= limit:
            return isolated_rows

    return isolated_rows


def _count_all_isolated_nodes():
    """统计全部孤立节点数量。"""
    return len(_isolated_node_rows(limit=99999))


def _event_timeline_issues(limit=20):
    """识别时间字段缺失或顺序异常的事件。"""
    rows = []
    for event in Event.query.all():
        issue_types = []
        start_date = _safe_text(event.start_date)
        end_date = _safe_text(event.end_date)

        if not start_date:
            issue_types.append("缺少开始时间")
        if start_date and not end_date:
            issue_types.append("缺少结束时间")
        if start_date and end_date and start_date > end_date:
            issue_types.append("开始时间晚于结束时间")

        if issue_types:
            rows.append({
                "id": event.id,
                "name": event.name,
                "type": "Event",
                "start_date": event.start_date,
                "end_date": event.end_date,
                "problem_types": issue_types
            })

        if len(rows) >= limit:
            break
    return rows


def _place_related_event_count(place_id):
    return EventPlaceRelation.query.filter(EventPlaceRelation.place_id == place_id).count()


def _coordinate_issue_rows(issue_type, limit=20):
    rows = []
    for place in Place.query.order_by(Place.id.asc()).all():
        related_event_count = _place_related_event_count(place.id)
        if related_event_count == 0:
            continue

        coord_result = _resolve_place_coordinates(place)
        if issue_type == "missing" and coord_result:
            continue
        if issue_type == "low_confidence":
            if not coord_result or coord_result.get("coord_confidence") != "low":
                continue

        rows.append({
            "id": place.id,
            "name": place.name,
            "type": "Place",
            "modern_name": place.modern_name,
            "dynasty": place.dynasty,
            "coord_hint": " / ".join([item for item in [place.province, place.city, place.district] if _safe_text(item)]),
            "event_count": related_event_count,
            "coord_source": coord_result.get("coord_source") if coord_result else "unresolved",
            "coord_source_label": coord_result.get("coord_source_label") if coord_result else "未解析",
            "coord_confidence": coord_result.get("coord_confidence") if coord_result else "",
            "coord_note": coord_result.get("coord_note") if coord_result else "地点表无显式坐标，且规则无法解析",
        })

        if len(rows) >= limit:
            break
    return rows


def _count_coordinate_issues(issue_type):
    return len(_coordinate_issue_rows(issue_type, limit=99999))


def _count_all_timeline_issues():
    """统计全部时间异常事件数量。"""
    return len(_event_timeline_issues(limit=99999))


def _model_for_type(node_type):
    node_type = TYPE_ALIASES.get(node_type, node_type)
    return {
        "Event": Event,
        "Place": Place,
        "Organization": Organization,
        "Person": Person,
    }.get(node_type)


def _required_fields_by_type(node_type):
    return {
        "Event": ["name", "start_date", "dynasty"],
        "Place": ["name", "modern_name"],
        "Organization": ["name", "org_type"],
        "Person": ["name", "role"],
    }.get(node_type, ["name"])


def _quality_flags_for_record(node_type, record):
    missing_fields = []
    for field in _required_fields_by_type(node_type):
        if _safe_text(getattr(record, field, None)) == '':
            missing_fields.append(field)

    duplicate_count = 0
    model = _model_for_type(node_type)
    if model and _safe_text(getattr(record, 'name', None)):
        duplicate_count = model.query.filter(model.name == record.name).count()

    relation_count = len(_related_entities_for_node(node_type, record.id, limit=999))
    flags = {
        "missing_fields": missing_fields,
        "is_isolated": relation_count == 0,
        "duplicate_count": duplicate_count,
        "relation_count": relation_count,
        "timeline_problems": []
    }

    if node_type == "Event":
        start_date = _safe_text(record.start_date)
        end_date = _safe_text(record.end_date)
        if not start_date:
            flags["timeline_problems"].append("missing_start_date")
        if start_date and not end_date:
            flags["timeline_problems"].append("missing_end_date")
        if start_date and end_date and start_date > end_date:
            flags["timeline_problems"].append("end_before_start")

    if node_type == "Place":
        coord_result = _resolve_place_coordinates(record)
        flags["coordinate_quality"] = {
            "is_mappable": bool(coord_result),
            "source_label": coord_result.get("coord_source_label") if coord_result else "未解析",
            "confidence": coord_result.get("coord_confidence") if coord_result else "",
            "note": coord_result.get("coord_note") if coord_result else "该地点暂时无法在地图上显示",
        }

    return flags


def _parse_year_value(text):
    if not text:
        return None

    raw = str(text).strip()
    if not raw:
        return None

    match = re.search(r'(\d+)', raw)
    if not match:
        return None

    year = int(match.group(1))
    if '公元前' in raw or raw.startswith('前') or '前' in raw[:3] or 'BC' in raw.upper():
        return -year
    return year


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_region_name(value):
    text = _safe_text(value)
    if not text:
        return ''
    for suffix in ["省", "市", "地区", "盟", "自治区", "特别行政区", "县", "区"]:
        text = text.replace(suffix, '')
    return text


def _coordinate_result(coords, source, confidence, label, matched_text='', note=''):
    return {
        "coords": coords,
        "coord_source": source,
        "coord_source_label": label,
        "coord_confidence": confidence,
        "coord_matched_text": matched_text,
        "coord_note": note,
    }


_COORD_REGION_SORTED = None
_COORD_CACHE = {}


def _resolve_place_coordinates(place):
    global _COORD_REGION_SORTED
    if _COORD_REGION_SORTED is None:
        _COORD_REGION_SORTED = sorted(
            HISTORICAL_REGION_CENTROIDS.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        )
    name = _safe_text(getattr(place, "name", None))
    city_key = _normalize_region_name(getattr(place, "city", None))
    province_key = _normalize_region_name(getattr(place, "province", None))
    modern_name = _safe_text(getattr(place, "modern_name", None))
    specific_location = _safe_text(getattr(place, "specific_location", None))
    longitude = getattr(place, "longitude", None)
    latitude = getattr(place, "latitude", None)
    cache_key = (
        getattr(place, "id", None),
        name, city_key, province_key, modern_name, specific_location,
        longitude, latitude,
    )
    if cache_key in _COORD_CACHE:
        cached = _COORD_CACHE[cache_key]
        return dict(cached) if cached else None

    if longitude is not None and latitude is not None:
        result = _coordinate_result(
            (float(longitude), float(latitude)),
            _safe_text(getattr(place, "coord_source", None)) or "manual",
            _safe_text(getattr(place, "coord_confidence", None)) or "high",
            "显式坐标",
            "longitude/latitude",
            _safe_text(getattr(place, "coord_note", None)) or "地点表已提供经纬度",
        )
    else:
        result = None
        for key, coords in CITY_CENTROIDS.items():
            if key and (city_key == key or key in modern_name or key in name or key in specific_location):
                result = _coordinate_result(coords, "city_centroid", "medium", "城市中心点", key, "按城市或现代地名匹配到城市中心点")
                break
        if result is None:
            for key, coords in PROVINCE_CENTROIDS.items():
                if key and (province_key == key or key in modern_name or key in specific_location):
                    result = _coordinate_result(coords, "province_centroid", "low", "省级中心点", key, "仅能匹配到省级范围，坐标用于概览展示")
                    break
        if result is None:
            for key, coords in _COORD_REGION_SORTED:
                if key and (key == name or key == modern_name):
                    result = _coordinate_result(coords, "historical_region", "medium", "历史区域估算", key, "按内置历史地名映射表精确匹配")
                    break
        if result is None:
            for key, coords in _COORD_REGION_SORTED:
                if key and (key in name or key in modern_name or key in specific_location):
                    result = _coordinate_result(coords, "historical_region_fuzzy", "low", "历史区域模糊估算", key, "按内置历史地名映射表模糊匹配")
                    break

    _COORD_CACHE[cache_key] = result
    return dict(result) if result else None


def _event_participants(event_id):
    persons = []
    organizations = []
    related_events = []

    for row in EventPersonRelation.query.filter(EventPersonRelation.event_id == event_id).limit(12).all():
        persons.append({
            "id": row.person_id,
            "name": row.person_name,
            "relation_type": row.relation_type,
            "detail_route": f"/knowledge/entity-detail?type=Person&id={row.person_id}",
        })

    for row in EventOrganizationRel.query.filter(EventOrganizationRel.event_id == event_id).limit(12).all():
        organizations.append({
            "id": row.org_id,
            "name": row.org_name,
            "relation_type": row.relation_type,
            "detail_route": f"/knowledge/entity-detail?type=Organization&id={row.org_id}",
        })

    for row in EventEventRelation.query.filter(
        (EventEventRelation.event_a_id == event_id) | (EventEventRelation.event_b_id == event_id)
    ).limit(10).all():
        target_id = row.event_b_id if row.event_a_id == event_id else row.event_a_id
        target_name = row.event_b_name if row.event_a_id == event_id else row.event_a_name
        related_events.append({
            "id": target_id,
            "name": target_name,
            "relation_type": row.relation_type,
            "detail_route": f"/knowledge/entity-detail?type=Event&id={target_id}",
        })

    return {
        "persons": persons,
        "organizations": organizations,
        "related_events": related_events,
    }


def _extract_query_entities(question_text):
    matches = []
    for node_type, model in [("Event", Event), ("Place", Place), ("Organization", Organization), ("Person", Person)]:
        for record in model.query.limit(1500).all():
            name = _safe_text(getattr(record, "name", ""))
            if not name:
                continue
            if name in question_text or question_text in name:
                matches.append({
                    "id": record.id,
                    "name": name,
                    "type": node_type,
                    "type_label": TYPE_LABELS.get(node_type, node_type),
                    "record": record,
                })
            if len(matches) >= 8:
                return matches
    return matches


def _event_brief(event):
    return {
        "id": event.id,
        "name": event.name,
        "dynasty": event.dynasty,
        "event_type": event.event_type,
        "start_date": event.start_date,
        "end_date": event.end_date,
        "parsed_year": _parse_year_value(event.start_date) or _parse_year_value(event.end_date),
        "place": event.place,
        "aggressor": event.aggressor,
        "defender": event.defender,
        "detail_route": f"/knowledge/entity-detail?id={event.id}&type=Event",
        "quality_flags": _quality_flags_for_record("Event", event),
    }


def _triple_from_relation(source_name, source_type, relation):
    target_name = relation.get("target_name") or ""
    target_type = relation.get("target_type") or ""
    relation_type = relation.get("relation_type") or "关联"
    return {
        "subject": source_name,
        "subject_type": source_type,
        "predicate": relation_type,
        "object": target_name,
        "object_type": target_type,
        "source_type": "graph",
        "source_label": SOURCE_TYPE_LABELS["graph"],
        "detail_route": relation.get("detail_route", ""),
    }


def _timeline_events_by_participant(name):
    if not _safe_text(name):
        return []

    person = Person.query.filter(Person.name == name).first()
    org = Organization.query.filter(Organization.name == name).first()
    event_ids = set()

    if person:
        for row in EventPersonRelation.query.filter(EventPersonRelation.person_id == person.id).all():
            event_ids.add(row.event_id)
    if org:
        for row in EventOrganizationRel.query.filter(EventOrganizationRel.org_id == org.id).all():
            event_ids.add(row.event_id)

    rows = []
    for event_id in event_ids:
        event = Event.query.get(event_id)
        if event:
            rows.append(_event_brief(event))
    rows.sort(key=lambda item: (item["parsed_year"] is None, item["parsed_year"] if item["parsed_year"] is not None else 999999, item["name"]))
    return rows[:80]


def build_map_overview(keyword='', dynasty=''):
    place_query = Place.query
    if keyword:
        place_query = place_query.filter(
            (Place.name.contains(keyword)) |
            (Place.modern_name.contains(keyword)) |
            (Place.province.contains(keyword)) |
            (Place.city.contains(keyword)) |
            (Place.district.contains(keyword))
        )
    if dynasty:
        filter_values = _dynasty_filter_values(dynasty)
        if filter_values:
            place_query = place_query.filter(Place.dynasty.in_(filter_values))

    places = place_query.limit(300).all()
    allowed_place_ids = {place.id for place in places}
    points = []
    dynasty_counter = Counter()
    all_event_ids = set()
    mappable_event_ids = set()
    event_point_map = {}
    unmapped_places = []

    for place in places:
        coord_result = _resolve_place_coordinates(place)
        coords = coord_result["coords"] if coord_result else None
        related_rows = EventPlaceRelation.query.filter(EventPlaceRelation.place_id == place.id).all()
        event_ids = []
        route_segments = []
        related_events = []
        entity_counter = {
            "persons": {},
            "organizations": {},
            "related_events": {},
        }
        for row in related_rows:
            event = Event.query.get(row.event_id)
            if not event:
                continue
            if dynasty and _normalize_dynasty_name(event.dynasty) != _normalize_dynasty_name(dynasty):
                continue
            all_event_ids.add(event.id)
            event_ids.append(event.id)
            participant_bundle = _event_participants(event.id)
            event_brief = _event_brief(event)
            event_brief["entities"] = participant_bundle
            related_events.append(event_brief)
            dynasty_counter[_normalize_dynasty_name(event.dynasty) or "未标注"] += 1
            route_segments.append({
                "event_id": event.id,
                "event_name": event.name,
                "relation_type": row.relation_type,
                "from": event.aggressor or "未知发起方",
                "to": place.modern_name or place.name,
                "evidence": row.evidence,
                "source_type": row.source_type or "extraction",
                "confidence": row.confidence or ("medium" if _safe_text(row.evidence) else ""),
            })
            if coords:
                mappable_event_ids.add(event.id)
                event_point_map.setdefault(event.id, {
                    "event": event_brief,
                    "nodes": [],
                })
                event_point_map[event.id]["nodes"].append({
                    "place_id": place.id,
                    "name": place.modern_name or place.name,
                    "place_name": place.name,
                    "modern_name": place.modern_name,
                    "coord_hint": " / ".join([item for item in [place.province, place.city, place.district] if _safe_text(item)]),
                    "coords": [coords[0], coords[1]],
                    "coord_source": coord_result["coord_source"],
                    "coord_source_label": coord_result["coord_source_label"],
                    "coord_confidence": coord_result["coord_confidence"],
                    "coord_matched_text": coord_result["coord_matched_text"],
                    "coord_note": coord_result["coord_note"],
                    "relation_type": row.relation_type,
                    "evidence": row.evidence,
                    "source_type": row.source_type or "extraction",
                    "confidence": row.confidence or ("medium" if _safe_text(row.evidence) else ""),
                    "priority": ROUTE_RELATION_PRIORITY.get(row.relation_type, 99),
                })
            for person in participant_bundle["persons"]:
                entity_counter["persons"][person["id"]] = person
            for org in participant_bundle["organizations"]:
                entity_counter["organizations"][org["id"]] = org
            for related_event in participant_bundle["related_events"]:
                entity_counter["related_events"][related_event["id"]] = related_event

        if not event_ids:
            continue

        if not coords:
            unmapped_places.append({
                "place_id": place.id,
                "place_name": place.name,
                "modern_name": place.modern_name,
                "dynasty": place.dynasty,
                "coord_hint": " / ".join([item for item in [place.province, place.city, place.district] if _safe_text(item)]),
                "event_count": len(set(event_ids)),
                "events": related_events[:6],
                "reason": "缺少可解析坐标",
                "coord_source": "unresolved",
                "coord_source_label": "未解析",
                "coord_confidence": "",
                "coord_note": "地点表无显式坐标，且现代地名、行政区和内置历史区域表均未命中",
                "detail_route": f"/knowledge/entity-detail?type=Place&id={place.id}",
            })

        persons = list(entity_counter["persons"].values())[:10]
        organizations = list(entity_counter["organizations"].values())[:10]
        related_event_entities = list(entity_counter["related_events"].values())[:10]
        points.append({
            "place_id": place.id,
            "place_name": place.name,
            "modern_name": place.modern_name,
            "dynasty": place.dynasty,
            "province": place.province,
            "city": place.city,
            "district": place.district,
            "specific_location": place.specific_location,
            "event_count": len(set(event_ids)),
            "label_name": place.modern_name or place.name,
            "coord_hint": " / ".join([item for item in [place.province, place.city, place.district] if _safe_text(item)]),
            "compare_name": f"{place.name} / {place.modern_name}" if _safe_text(place.modern_name) and place.modern_name != place.name else place.name,
            "longitude": coords[0] if coords else None,
            "latitude": coords[1] if coords else None,
            "has_real_coord": bool(coords),
            "coord_source": coord_result["coord_source"] if coord_result else "unresolved",
            "coord_source_label": coord_result["coord_source_label"] if coord_result else "未解析",
            "coord_confidence": coord_result["coord_confidence"] if coord_result else "",
            "coord_matched_text": coord_result["coord_matched_text"] if coord_result else "",
            "coord_note": coord_result["coord_note"] if coord_result else "",
            "event_ids": sorted(set(event_ids)),
            "events": related_events[:8],
            "entities": {
                "persons": persons,
                "organizations": organizations,
                "related_events": related_event_entities,
            },
            "routes": route_segments[:6],
            "detail_route": f"/knowledge/entity-detail?type=Place&id={place.id}",
        })

    points.sort(key=lambda item: (-item["event_count"], item["label_name"]))
    unmapped_places.sort(key=lambda item: (-item["event_count"], item["place_name"]))

    event_points = []
    for event_id, item in event_point_map.items():
        nodes = sorted(item["nodes"], key=lambda node: (node["priority"], node["name"]))
        if not nodes:
            continue
        primary_node = nodes[0]
        event = item["event"]
        event_points.append({
            "event_id": event_id,
            "event_name": event["name"],
            "dynasty": event.get("dynasty"),
            "event_type": event.get("event_type"),
            "place_id": primary_node["place_id"],
            "place_name": primary_node["place_name"],
            "modern_name": primary_node["modern_name"],
            "label_name": event["name"],
            "longitude": primary_node["coords"][0],
            "latitude": primary_node["coords"][1],
            "coord_hint": primary_node["coord_hint"],
            "coord_source": primary_node.get("coord_source"),
            "coord_source_label": primary_node.get("coord_source_label"),
            "coord_confidence": primary_node.get("coord_confidence"),
            "coord_matched_text": primary_node.get("coord_matched_text"),
            "coord_note": primary_node.get("coord_note"),
            "relation_type": primary_node["relation_type"],
            "evidence": primary_node.get("evidence"),
            "source_type": primary_node.get("source_type"),
            "confidence": primary_node.get("confidence"),
            "related_place_count": len(nodes),
            "has_route": len({f"{node['coords'][0]}-{node['coords'][1]}" for node in nodes}) >= 2,
            "detail_route": event.get("detail_route"),
        })
    event_points.sort(key=lambda item: (not item["has_route"], item["event_name"]))

    hot_routes = []
    for item in points[:30]:
        for route in item.get("routes", []):
            hot_routes.append({
                "event_id": route["event_id"],
                "event_name": route["event_name"],
                "relation_type": route["relation_type"],
                "from_label": route["from"],
                "to_label": route["to"],
                "place_name": item["place_name"],
            })

    route_lines = []
    event_route_source = {}
    event_query = Event.query
    if dynasty:
        filter_values = _dynasty_filter_values(dynasty)
        if filter_values:
            event_query = event_query.filter(Event.dynasty.in_(filter_values))

    for event in event_query.limit(500).all():
        route_rows = EventPlaceRelation.query.filter(EventPlaceRelation.event_id == event.id).all()
        for row in route_rows:
            if allowed_place_ids and row.place_id not in allowed_place_ids:
                continue
            place = Place.query.get(row.place_id)
            if not place:
                continue
            coord_result = _resolve_place_coordinates(place)
            coords = coord_result["coords"] if coord_result else None
            if not coords:
                continue
            event_route_source.setdefault(event.id, {
                "event_id": event.id,
                "event_name": event.name,
                "nodes": [],
            })
            event_route_source[event.id]["nodes"].append({
                "name": place.modern_name or row.modern_name or place.name or row.place_name,
                "place_name": place.name or row.place_name,
                "coords": [coords[0], coords[1]],
                "coord_source": coord_result["coord_source"],
                "coord_source_label": coord_result["coord_source_label"],
                "coord_confidence": coord_result["coord_confidence"],
                "coord_matched_text": coord_result["coord_matched_text"],
                "coord_note": coord_result["coord_note"],
                "relation_type": row.relation_type,
                "evidence": row.evidence,
                "source_type": row.source_type or "extraction",
                "confidence": row.confidence or ("medium" if _safe_text(row.evidence) else ""),
                "priority": ROUTE_RELATION_PRIORITY.get(row.relation_type, 99),
            })

    for route_item in event_route_source.values():
        nodes = sorted(route_item["nodes"], key=lambda item: (item["priority"], item["name"]))
        seen_names = set()
        deduped = []
        for node in nodes:
            key = f"{node['coords'][0]}-{node['coords'][1]}"
            if key in seen_names:
                continue
            seen_names.add(key)
            deduped.append(node)
        if len(deduped) < 2:
            continue
        for index in range(len(deduped) - 1):
            route_lines.append({
                "event_id": route_item["event_id"],
                "event_name": route_item["event_name"],
                "from_name": deduped[index]["name"],
                "to_name": deduped[index + 1]["name"],
                "coords": [deduped[index]["coords"], deduped[index + 1]["coords"]],
                "relation_chain": f"{deduped[index]['relation_type']} → {deduped[index + 1]['relation_type']}",
                "relation_steps": [
                    {
                        "place_name": deduped[index]["place_name"],
                        "name": deduped[index]["name"],
                        "relation_type": deduped[index]["relation_type"],
                        "evidence": deduped[index].get("evidence"),
                        "source_type": deduped[index].get("source_type"),
                        "confidence": deduped[index].get("confidence"),
                        "coord_source_label": deduped[index].get("coord_source_label"),
                        "coord_confidence": deduped[index].get("coord_confidence"),
                        "coord_note": deduped[index].get("coord_note"),
                    },
                    {
                        "place_name": deduped[index + 1]["place_name"],
                        "name": deduped[index + 1]["name"],
                        "relation_type": deduped[index + 1]["relation_type"],
                        "evidence": deduped[index + 1].get("evidence"),
                        "source_type": deduped[index + 1].get("source_type"),
                        "confidence": deduped[index + 1].get("confidence"),
                        "coord_source_label": deduped[index + 1].get("coord_source_label"),
                        "coord_confidence": deduped[index + 1].get("coord_confidence"),
                        "coord_note": deduped[index + 1].get("coord_note"),
                    },
                ],
                "route_source": "inferred_by_relation_order",
                "route_source_label": "按关系类型推断",
                "route_confidence": "medium",
            })

    route_event_ids = {item["event_id"] for item in route_lines}
    single_point_event_ids = mappable_event_ids - route_event_ids
    coord_source_counter = Counter(item.get("coord_source_label") or "未解析" for item in points if item.get("has_real_coord"))
    low_confidence_places = [
        item for item in points
        if item.get("has_real_coord") and item.get("coord_confidence") in {"low", "低"}
    ]

    return {
        "summary": {
            "place_count": len(points),
            "total_events": len(all_event_ids),
            "covered_events": len(all_event_ids),
            "mappable_events": len(mappable_event_ids),
            "route_events": len(route_event_ids),
            "single_point_events": len(single_point_event_ids),
            "unmapped_events": len(all_event_ids - mappable_event_ids),
            "dynasty_count": len([key for key in dynasty_counter.keys() if key]),
            "real_coord_points": len([item for item in points if item.get("has_real_coord")]),
            "mappable_places": len([item for item in points if item.get("has_real_coord")]),
            "unmapped_places": len(unmapped_places),
            "low_confidence_places": len(low_confidence_places),
        },
        "dynasties": sorted([key for key in dynasty_counter.keys() if key]),
        "points": points[:120],
        "place_points": points[:120],
        "event_points": event_points[:160],
        "unmapped_places": unmapped_places[:120],
        "dynasty_distribution": [{"name": key, "value": value} for key, value in dynasty_counter.most_common(12)],
        "coord_source_distribution": [{"name": key, "value": value} for key, value in coord_source_counter.most_common()],
        "routes": hot_routes[:80],
        "route_lines": route_lines[:120],
    }


def _related_entities_for_node(node_type, node_id, limit=50):
    rows = []

    def append_row(relation_type, target_id, target_name, target_type, direction, source_id=None, source_name=None):
        rows.append({
            "relation_type": relation_type,
            "target_id": target_id,
            "target_name": target_name,
            "target_type": target_type,
            "target_type_label": TYPE_LABELS.get(target_type, target_type),
            "direction": direction,
            "source_id": source_id,
            "source_name": source_name,
            "detail_route": f"/knowledge/entity-detail?id={target_id}&type={target_type}" if target_id is not None else "",
        })

    if node_type == "Event":
        for row in EventEventRelation.query.filter(
            (EventEventRelation.event_a_id == node_id) | (EventEventRelation.event_b_id == node_id)
        ).limit(limit).all():
            if row.event_a_id == node_id:
                append_row(row.relation_type, row.event_b_id, row.event_b_name, "Event", "outgoing", node_id, row.event_a_name)
            else:
                append_row(row.relation_type, row.event_a_id, row.event_a_name, "Event", "incoming", node_id, row.event_b_name)

        for row in EventPlaceRelation.query.filter(EventPlaceRelation.event_id == node_id).limit(limit).all():
            append_row(row.relation_type, row.place_id, row.place_name or row.modern_name, "Place", "outgoing", node_id, row.event_name)

        for row in EventPersonRelation.query.filter(EventPersonRelation.event_id == node_id).limit(limit).all():
            append_row(row.relation_type, row.person_id, row.person_name, "Person", "outgoing", node_id, row.event_name)

        for row in EventOrganizationRel.query.filter(EventOrganizationRel.event_id == node_id).limit(limit).all():
            append_row(row.relation_type, row.org_id, row.org_name, "Organization", "outgoing", node_id, row.event_name)

    elif node_type == "Place":
        for row in EventPlaceRelation.query.filter(EventPlaceRelation.place_id == node_id).limit(limit).all():
            append_row(row.relation_type, row.event_id, row.event_name, "Event", "incoming", node_id, row.place_name)

    elif node_type == "Person":
        for row in EventPersonRelation.query.filter(EventPersonRelation.person_id == node_id).limit(limit).all():
            append_row(row.relation_type, row.event_id, row.event_name, "Event", "incoming", node_id, row.person_name)

    elif node_type == "Organization":
        for row in EventOrganizationRel.query.filter(EventOrganizationRel.org_id == node_id).limit(limit).all():
            append_row(row.relation_type, row.event_id, row.event_name, "Event", "incoming", node_id, row.org_name)

    return rows[:limit]


def build_entity_detail(node_type, node_id):
    node_type = TYPE_ALIASES.get(node_type, node_type)
    model = _model_for_type(node_type)
    if not model:
        return None

    record = model.query.get(node_id)
    if not record:
        record = model.query.filter(model.neo4j_id == node_id).first()
    if not record:
        return None

    detail = record.to_dict()
    detail["type"] = node_type
    relations = _related_entities_for_node(node_type, node_id, limit=100)
    quality_flags = _quality_flags_for_record(node_type, record)

    timeline_context = []
    if node_type == "Event":
        parsed_year = _parse_year_value(record.start_date) or _parse_year_value(record.end_date)
        siblings = Event.query.filter(Event.id != record.id).all()
        event_rows = []
        for item in siblings:
            year = _parse_year_value(item.start_date) or _parse_year_value(item.end_date)
            if year is None:
                continue
            event_rows.append({
                "id": item.id,
                "name": item.name,
                "year": year,
                "start_date": item.start_date,
                "end_date": item.end_date,
                "dynasty": item.dynasty,
            })
        event_rows.sort(key=lambda item: abs((item["year"] or 0) - (parsed_year or 0)))
        timeline_context = event_rows[:6]

    return {
        "node": detail,
        "type_label": TYPE_LABELS.get(node_type, node_type),
        "quality_flags": quality_flags,
        "relations": relations,
        "timeline_context": timeline_context,
        "next_actions": [
            {
                "label": "数据修复",
                "route": f"/workspace/repair?type={node_type}&id={node_id}",
                "kind": "repair",
            },
        ]
    }


def build_quality_workbench():
    report = build_quality_report()
    issue_queue = []

    for item in report.get("missing_required_fields", []):
        issue_queue.append({
            "issue_key": f"missing-{item['type']}-{item['id']}",
            "issue_type": "missing_fields",
            "severity": "high",
            "title": f"{item['name']} 缺少关键字段",
            "description": f"需要补齐字段：{', '.join(item.get('missing_fields', []))}",
            "node_id": item["id"],
            "node_type": item["type"],
            "node_name": item["name"],
            "fields": item.get("missing_fields", []),
            "editable": True,
        })

    for item in report.get("timeline_issues", []):
        issue_queue.append({
            "issue_key": f"timeline-{item['id']}",
            "issue_type": "timeline_issue",
            "severity": "high",
            "title": f"{item['name']} 存在时间异常",
            "description": f"时间问题：{', '.join(item.get('problem_types', []))}",
            "node_id": item["id"],
            "node_type": "Event",
            "node_name": item["name"],
            "fields": ["StartDate", "EndDate"],
            "editable": True,
        })

    for item in report.get("coordinate_missing", []):
        issue_queue.append({
            "issue_key": f"coordinate-missing-{item['id']}",
            "issue_type": "coordinate_missing",
            "severity": "high",
            "title": f"{item['name']} 缺少坐标",
            "description": f"无法在地图上显示，已关联 {item.get('event_count', 0)} 场事件，建议补充经纬度和坐标说明。",
            "node_id": item["id"],
            "node_type": "Place",
            "node_name": item["name"],
            "fields": ["modern_name", "Province", "City", "District_County", "Specific_location", "longitude", "latitude", "coord_source", "coord_confidence", "coord_note"],
            "editable": True,
        })

    for item in report.get("coordinate_low_confidence", []):
        issue_queue.append({
            "issue_key": f"coordinate-low-{item['id']}",
            "issue_type": "coordinate_low_confidence",
            "severity": "medium",
            "title": f"{item['name']} 坐标置信度较低",
            "description": f"当前使用{item.get('coord_source_label') or '估算坐标'}，已关联 {item.get('event_count', 0)} 场事件，建议人工确认。",
            "node_id": item["id"],
            "node_type": "Place",
            "node_name": item["name"],
            "fields": ["modern_name", "Province", "City", "District_County", "Specific_location", "longitude", "latitude", "coord_source", "coord_confidence", "coord_note"],
            "editable": True,
        })

    for item in report.get("isolated_nodes", []):
        issue_queue.append({
            "issue_key": f"isolated-{item['type']}-{item['id']}",
            "issue_type": "isolated_node",
            "severity": "medium",
            "title": f"{item['name']} 是孤立节点",
            "description": "建议补充关系，或确认该节点是否应保留。",
            "node_id": item["id"],
            "node_type": item["type"],
            "node_name": item["name"],
            "fields": [],
            "editable": True,
        })

    for item in report.get("duplicate_names", []):
        issue_queue.append({
            "issue_key": f"duplicate-{item['type']}-{item['name']}",
            "issue_type": "duplicate_name",
            "severity": "medium",
            "title": f"{item['name']} 可能重复",
            "description": f"{TYPE_LABELS.get(item['type'], item['type'])} 中出现 {item['count']} 次，建议人工核对别名或合并策略。",
            "node_id": None,
            "node_type": item["type"],
            "node_name": item["name"],
            "fields": [],
            "editable": False,
        })

    severity_order = {"high": 0, "medium": 1, "low": 2}
    issue_queue.sort(key=lambda item: (severity_order.get(item["severity"], 9), item["title"]))

    return {
        "summary": report.get("summary", {}),
        "issue_queue": issue_queue,
        "report": report,
    }


def build_timeline_overview(keyword='', dynasty='', participant=''):
    events = Event.query
    if keyword:
        events = events.filter(Event.name.contains(keyword))
    if dynasty:
        filter_values = _dynasty_filter_values(dynasty)
        if filter_values:
            events = events.filter(Event.dynasty.in_(filter_values))
        else:
            events = events.filter(Event.dynasty == dynasty)

    participant_event_ids = set()
    if participant:
        participant_rows = _timeline_events_by_participant(participant)
        participant_event_ids = {item["id"] for item in participant_rows}

    rows = []
    dynasty_options = set()
    for event in events.all():
        if participant and event.id not in participant_event_ids:
            continue
        parsed_year = _parse_year_value(event.start_date) or _parse_year_value(event.end_date)
        item = _event_brief(event)
        item["parsed_year"] = parsed_year
        rows.append(item)
        if _safe_text(event.dynasty):
            dynasty_options.add(event.dynasty)

    rows.sort(key=lambda item: (item["parsed_year"] is None, item["parsed_year"] if item["parsed_year"] is not None else 999999, item["name"]))
    dynasty_groups = []
    for dynasty_name in sorted(dynasty_options):
        dynasty_items = [item for item in rows if item.get("dynasty") == dynasty_name]
        dynasty_groups.append({
            "dynasty": dynasty_name,
            "count": len(dynasty_items),
            "events": dynasty_items[:12],
        })

    comparison_groups = {}
    for item in rows:
        if item["parsed_year"] is None:
            continue
        comparison_groups.setdefault(item["parsed_year"], []).append(item)
    comparisons = []
    for year, group in comparison_groups.items():
        if len(group) < 2:
            continue
        comparisons.append({
            "year": year,
            "events": group[:4],
        })
    comparisons.sort(key=lambda item: (abs(item["year"]), -len(item["events"])))

    return {
        "summary": {
            "total_events": len(rows),
            "with_year": len([item for item in rows if item["parsed_year"] is not None]),
            "without_year": len([item for item in rows if item["parsed_year"] is None]),
            "timeline_issues": len([item for item in rows if item["quality_flags"]["timeline_problems"]]),
        },
        "dynasties": sorted(dynasty_options),
        "dynasty_groups": dynasty_groups,
        "comparisons": comparisons[:12],
        "participant": participant,
        "participant_timeline": _timeline_events_by_participant(participant) if participant else [],
        "events": rows[:300],
    }


def build_global_search(keyword, limit=12):
    keyword = _safe_text(keyword)
    if not keyword:
        return []

    results = []
    for node_type, model in [("Event", Event), ("Person", Person), ("Place", Place), ("Organization", Organization)]:
        for record in model.query.filter(model.name.contains(keyword)).limit(limit).all():
            results.append({
                "id": record.id,
                "type": node_type,
                "type_label": TYPE_LABELS.get(node_type, node_type),
                "name": record.name,
                "subtitle": getattr(record, "dynasty", None) or getattr(record, "role", None) or getattr(record, "modern_name", None) or "",
                "entity_route": f"/knowledge/entity/{node_type}/{record.id}",
                "detail_route": f"/knowledge/entity-detail?type={node_type}&id={record.id}",
                "graph_route": f"/knowledge/graph?focus=1&name={record.name}&type={node_type}",
                "timeline_route": f"/knowledge/timeline?keyword={record.name}",
            })
            if len(results) >= limit:
                return results
    return results


def build_dataset_versions():
    meta = load_current_dataset_meta()
    current_version = (
        meta.get("metadata", {}).get("extraction_version")
        or meta.get("quality_report", {}).get("extraction_version")
        or "current"
    )
    return [{
        "id": current_version,
        "version": current_version,
        "status": meta.get("metadata", {}).get("publish_stage", "已发布"),
        "source_path": meta.get("source_path", ""),
        "extracted_at": meta.get("metadata", {}).get("extracted_at", ""),
        "entity_delta": sum((meta.get("sqlite_counts") or {}).get(key, 0) for key in ["events", "places", "organizations", "persons"]),
        "relation_delta": (meta.get("sqlite_counts") or {}).get("relations", 0),
        "quality": meta.get("quality_report", {}),
    }]


def build_dashboard_overview():
    """构建首页仪表盘数据。"""
    dataset_meta = load_current_dataset_meta()
    common_stats = build_common_dataset_stats(dataset_meta)
    return {
        "cards": [
            {"title": "实体总量", "value": common_stats["counts"]["entities"], "subtitle": "SQLite 主库中可维护的节点总数"},
            {"title": "关系总量", "value": common_stats["counts"]["relations"], "subtitle": "已落库的结构化关系数量"},
            {"title": "孤立节点", "value": common_stats["quality_snapshot"]["isolated_nodes"], "subtitle": "尚未绑定任何关系的节点"},
            {"title": "时间异常", "value": common_stats["quality_snapshot"]["timeline_issues"], "subtitle": "开始时间或结束时间存在异常的事件"}
        ],
        "dynasty_distribution": common_stats["dynasty_distribution"],
        "dataset": common_stats["dataset"],
        "quality_snapshot": common_stats["quality_snapshot"],
    }


def build_common_dataset_stats(dataset_meta=None):
    """构建首页与数据集中心共用的统计数据。"""
    dataset_meta = dataset_meta or load_current_dataset_meta()
    node_counts = {
        "events": Event.query.count(),
        "places": Place.query.count(),
        "organizations": Organization.query.count(),
        "persons": Person.query.count()
    }
    relation_counts = {
        "event_event": EventEventRelation.query.count(),
        "event_place": EventPlaceRelation.query.count(),
        "event_person": EventPersonRelation.query.count(),
        "event_organization": EventOrganizationRel.query.count()
    }
    dynasty_counter = Counter()
    for raw_name, in Event.query.with_entities(Event.dynasty).all():
        dynasty_name = _normalize_dynasty_name(raw_name)
        if dynasty_name:
            dynasty_counter[dynasty_name] += 1

    extra_dynasties = sorted(
        [name for name in dynasty_counter.keys() if name not in DYNASTY_DISPLAY_ORDER]
    )
    dynasty_distribution = [
        {
            "name": dynasty_name,
            "value": dynasty_counter.get(dynasty_name, 0),
            "has_data": dynasty_counter.get(dynasty_name, 0) > 0,
        }
        for dynasty_name in DYNASTY_DISPLAY_ORDER + extra_dynasties
    ]
    quality_report = dataset_meta.get("quality_report", {})
    timeline_issue_count = _count_all_timeline_issues()
    isolated_node_count = _count_all_isolated_nodes()
    sqlite_counts = dataset_meta.get("sqlite_counts", {})
    counts = {
        "entities": sum(node_counts.values()),
        "events": sqlite_counts.get("events", node_counts["events"]),
        "places": sqlite_counts.get("places", node_counts["places"]),
        "organizations": sqlite_counts.get("organizations", node_counts["organizations"]),
        "persons": sqlite_counts.get("persons", node_counts["persons"]),
        "relations": sqlite_counts.get("relations", sum(relation_counts.values())),
    }
    quality_snapshot = {
        "missing_source_text": sum(quality_report.get("missing_source_text", {}).values()) if quality_report else 0,
        "missing_evidence": sum(quality_report.get("missing_evidence", {}).values()) if quality_report else 0,
        "isolated_nodes": isolated_node_count,
        "timeline_issues": timeline_issue_count,
    }

    return {
        "dataset": dataset_meta,
        "counts": counts,
        "node_counts": node_counts,
        "relation_counts": relation_counts,
        "dynasty_distribution": dynasty_distribution,
        "quality_snapshot": quality_snapshot,
    }


def build_dataset_overview():
    """构建数据集中心页面所需数据。"""
    dataset_meta = load_current_dataset_meta()
    common_stats = build_common_dataset_stats(dataset_meta)
    relation_counts = common_stats.get("relation_counts", {})
    return {
        "dataset": dataset_meta,
        "counts": common_stats["counts"],
        "relation_breakdown": [
            {"name": "事件-事件", "value": relation_counts.get("event_event", 0)},
            {"name": "事件-地点", "value": relation_counts.get("event_place", 0)},
            {"name": "事件-人物", "value": relation_counts.get("event_person", 0)},
            {"name": "事件-组织", "value": relation_counts.get("event_organization", 0)},
        ],
        "quality_report": dataset_meta.get("quality_report", {})
    }


def build_quality_report():
    """构建图谱质检数据。"""
    return {
        "summary": {
            "duplicate_names": sum([
                len(_duplicate_name_rows(Event, "Event", limit=99999)),
                len(_duplicate_name_rows(Place, "Place", limit=99999)),
                len(_duplicate_name_rows(Person, "Person", limit=99999)),
                len(_duplicate_name_rows(Organization, "Organization", limit=99999)),
            ]),
            "isolated_nodes": _count_all_isolated_nodes(),
            "missing_required_fields": len(_missing_field_rows(Event, "Event", ["name", "start_date", "dynasty"], limit=99999)),
            "timeline_issues": _count_all_timeline_issues(),
            "coordinate_missing": _count_coordinate_issues("missing"),
            "coordinate_low_confidence": _count_coordinate_issues("low_confidence"),
        },
        "duplicate_names": (
            _duplicate_name_rows(Event, "Event") +
            _duplicate_name_rows(Place, "Place") +
            _duplicate_name_rows(Person, "Person") +
            _duplicate_name_rows(Organization, "Organization")
        )[:20],
        "isolated_nodes": _isolated_node_rows(),
        "missing_required_fields": _missing_field_rows(Event, "Event", ["name", "start_date", "dynasty"]),
        "timeline_issues": _event_timeline_issues(),
        "coordinate_missing": _coordinate_issue_rows("missing", limit=99999),
        "coordinate_low_confidence": _coordinate_issue_rows("low_confidence", limit=99999),
    }

@app.before_request
def initialize_entity_extractor():
    """初始化实体提取器和规则推理模块"""
    global shared_entity_extractor, shared_rule_llm_integration

    if shared_entity_extractor is None:
        try:
            from entity_extract.extractor import Extractor
            shared_entity_extractor = Extractor()
            # 加载已知实体到提取器，用于规则匹配快速提取
            try:
                shared_entity_extractor.load_known_entities(neo4j_db_handle)
                print(f"实体提取器已初始化，并加载了已知实体")
            except Exception as load_err:
                print(f"加载已知实体失败，将使用纯模型提取: {load_err}")
        except Exception as e:
            print(f"初始化实体提取器失败: {str(e)}")

    if shared_rule_llm_integration is None:
        try:
            from inference.rule_llm_integration import RuleLLMIntegration
            shared_rule_llm_integration = RuleLLMIntegration(
                rule_file_path='rules/rule_base.json',
                model_name='deepseek-r1:7b',
                max_depth=30
            )
            print("规则推理模块已初始化")
        except Exception as e:
            print(f"初始化规则推理模块失败: {str(e)}")

    if shared_entity_extractor is not None:
        g.entity_extractor = shared_entity_extractor
    if shared_rule_llm_integration is not None:
        g.rule_llm_integration = shared_rule_llm_integration


def init_user_dict():
    """从data.json提取地名实体并创建历史地名词典文件"""
    dict_path = os.path.join(APP_PATH, 'historical_places.txt')
    data_json_path = os.path.join(APP_PATH, 'data', 'data.json')

    try:
        if not os.path.exists(data_json_path):
            print(f"警告：data.json文件不存在: {data_json_path}")
            return

        with open(data_json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        print(f"从data.json中提取实体，共有{len(data)}条记录")

        entities = set()
        entity_types = set()
        relation_types = set()

        for item in data:
            if "实体名称" in item and item["实体名称"]:
                entities.add(item["实体名称"])

            if "关联实体" in item and item["关联实体"]:
                associated_entities = item["关联实体"].split("、")
                for entity in associated_entities:
                    entities.add(entity)

            if "实体类型" in item and item["实体类型"]:
                entity_types.add(item["实体类型"])

            if "关联实体类型" in item and item["关联实体类型"]:
                entity_types.add(item["关联实体类型"])

            if "实体关系" in item and item["实体关系"]:
                relation_types.add(item["实体关系"])

        dynasties = [
            "秦朝", "汉朝", "西汉", "东汉", "三国", "魏国", "蜀国", "吴国",
            "晋朝", "西晋", "东晋", "南北朝", "隋朝", "唐朝", "五代十国",
            "宋朝", "北宋", "南宋", "辽朝", "金朝", "元朝", "明朝", "清朝", "民国"
        ]

        for dynasty in dynasties:
            entities.add(dynasty)

        dict_content = []

        for entity in entities:
            dict_content.append(f"{entity} 10 ns")

        for entity_type in entity_types:
            dict_content.append(f"{entity_type} 10 n")

        for relation in relation_types:
            dict_content.append(f"{relation} 10 v")

        with open(dict_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(dict_content))

        print(f"已成功生成历史地名词典文件: {dict_path}")
        print(f"词典包含 {len(entities)} 个实体名称, {len(entity_types)} 个实体类型, {len(relation_types)} 个关系类型")

    except Exception as e:
        print(f"创建历史地名词典文件失败: {str(e)}")
        import traceback
        traceback.print_exc()


# 调用初始化函数
init_user_dict()

# 预加载jieba分词用户词典
try:
    import jieba

    dict_path = os.path.join(APP_PATH, 'historical_places.txt')
    if os.path.exists(dict_path):
        jieba.load_userdict(dict_path)
        print(f"成功加载历史地名词典: {dict_path}")
    else:
        print(f"警告：历史地名词典不存在: {dict_path}")
except ImportError:
    print("未找到jieba分词库，跳过用户词典加载")
except Exception as e:
    print(f"加载用户词典失败: {str(e)}")


# ================== 权限拦截器 ==================

@app.before_request
def before():
    """全局请求拦截器（权限校验）"""
    url = request.path
    print('url:' + url)

    pass_url = ["/", "/api/login", "/api/sign_in"]

    if url.startswith("/static") or url in pass_url:
        pass
    else:
        token = request.headers.get('Token')

        if not token:
            return jsonify({
                "code": 403,
                "msg": "您还未登录，请先登录"
            })
        else:
            global user_id
            user_id = decode(token)['user_id']


# ================== 用户相关接口 ==================

@app.route('/api/login', methods=['POST'])
def login():
    """用户登录接口
    
    请求参数(JSON):
        - account: 用户账号
        - password: 用户密码
    
    响应:
        - code: 200(成功) / 403(失败)
        - data: JWT Token(成功时返回)
        - msg: 错误信息(失败时返回)
    """
    global user_id
    params = request.get_json()
    handler = DbUtil()
    # 验证用户账号密码
    user = handler.authentication(params)
    if user:
        # 生成JWT Token
        token = encode(user.id)
        return jsonify({
            "code": 200,
            "data": token
        })
    else:
        return jsonify({
            "code": 403,
            "msg": "用户名或密码错误"
        })


@app.route('/api/userinfo', methods=['GET', 'POST'])
def userinfo():
    handler = DbUtil()
    global user_id
    result = handler.find_user(user_id)
    return jsonify({
        "code": 200,
        "data": result
    })


@app.route('/api/sign_in', methods=['POST'])
def sign_in():
    global user_id
    data = request.get_json()
    handler = DbUtil()
    result = handler.add_user(data)
    if result.get("code") == 200:
        user_id = result["data"]["user_id"]
        token = encode(user_id)
        return jsonify({
            "code": 200,
            "data": token,
            "msg": "注册成功"
        })
    return jsonify(result)


# ================== 知识图谱接口 - 节点管理操作SQLite，查询仍可用Neo4j ==================

@app.route('/search_name_kg', methods=['POST'])
def search_name():
    """
    知识图谱搜索 - 从Neo4j读取（可视化展示）
    保持原样，前端可视化仍从Neo4j读取
    """
    data = request.get_json()
    entity = data.get('name', '')
    node_type = data.get('node_type', '')
    rel_type = data.get('rel_type', '')
    load_all = data.get('load_all', True)

    try:
        if not entity and not node_type and not rel_type:
            json_data = neo4j_db_handle.get_default_graph(limit=50, load_all=load_all)
            print(f"使用默认图谱加载方式, {'加载全部' if load_all else '加载部分'}")
        else:
            if entity and node_type and not rel_type:
                json_data = neo4j_db_handle.search_by_name_and_type(entity, node_type)
                print("名称+类型组合查询")
            elif entity and rel_type:
                json_data = neo4j_db_handle.search_by_name_and_relation(entity, rel_type)
                print(f"组合查询：名称+关系 {entity} + {rel_type}")
            elif entity:
                json_data = neo4j_db_handle.search_nodes_by_name(entity)
                print(f"按实体名称'{entity}'搜索")
            elif node_type:
                json_data = neo4j_db_handle.get_nodes_by_type(node_type)
                print(f"按节点类型'{node_type}'筛选")
            elif rel_type:
                json_data = neo4j_db_handle.get_nodes_by_relationship(rel_type)
                print(f"按关系类型'{rel_type}'筛选")

        return jsonify({
            "code": 200,
            "msg": "success",
            "data": json_data
        })
    except Exception as e:
        print(f"搜索地名知识图谱异常: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "code": 500,
            "msg": str(e),
            "data": {"nodes": [], "lines": []}
        })


@app.route('/api/find_node_page', methods=['POST'])
def find_list():
    """
    节点列表查询 - 改为从SQLite查询
    支持分页、名称搜索和类型过滤
    """
    try:
        current = int(request.json.get('pageNum', 1))
        limit = int(request.json.get('pageSize', 10))
        name_query = request.json.get('name', '')
        node_type = request.json.get('node_type', '')

        # 使用DbUtil从SQLite查询
        result = DbUtil.find_node_page(current, limit, name_query, node_type if node_type else None)

        return jsonify({
            "code": 200,
            "data": result
        })
    except Exception as e:
        print(f"查询节点列表失败: {e}")
        return jsonify({
            "code": 500,
            "msg": str(e),
            "data": {"total": 0, "records": []}
        })


@app.route('/create_node', methods=['POST'])
def create_node():
    """创建节点接口
    
    请求参数(JSON):
        - type: 节点类型(Event/Place/Organization/Person)
        - name: 节点名称
        - 其他属性字段
    
    处理流程:
        1. 接收前端传来的节点数据
        2. 调用DbUtil.create_node创建SQLite记录
        3. 自动同步到Neo4j
    
    响应:
        - code: 200(成功) / 500(失败)
        - msg: 操作结果信息
    """
    try:
        data = request.json
        node_type = data.get("type")
        name = data.get("name")
        # 提取除type和name外的其他属性
        properties = {k: v for k, v in data.items() if k not in ['type', 'name']}

        result = DbUtil.create_node(node_type, name, properties)
        return jsonify(result)

    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": f"创建节点失败: {str(e)}"
        })


@app.route('/update_node', methods=['POST'])
def update_node():
    """
    更新节点 - 更新SQLite，异步同步到Neo4j
    """
    try:
        data = request.json
        node_type = data.get("type")
        node_id = data.get("id")
        new_name = data.get("name")
        properties = {k: v for k, v in data.items() if k not in ['type', 'id', 'name']}

        # 确保id是整数
        if isinstance(node_id, str):
            node_id = int(node_id)

        result = DbUtil.update_node(node_type, node_id, new_name, properties)
        return jsonify(result)

    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": f"更新节点失败: {str(e)}"
        })


@app.route('/delete_node', methods=['POST'])
def delete_node():
    """
    删除节点 - 删除SQLite，异步同步到Neo4j
    """
    try:
        data = request.json
        node_type = data.get("type")
        node_id = data.get("id")

        # 确保id是整数
        if isinstance(node_id, str):
            node_id = int(node_id)

        result = DbUtil.delete_node(node_type, node_id)
        return jsonify(result)

    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": f"删除节点失败: {str(e)}"
        })


@app.route('/api/node/detail', methods=['GET'])
def get_node_detail():
    """
    获取节点详情 - 优先从SQLite读取，如不存在则从Neo4j读取
    """
    try:
        node_id = request.args.get('id')
        node_type = request.args.get('type')

        if not node_id:
            return jsonify({
                "code": 400,
                "msg": "节点ID不能为空"
            })

        # 尝试从SQLite获取
        if node_type:
            sqlite_detail = DbUtil.get_node_detail_sqlite(int(node_id), node_type)
            if sqlite_detail:
                return jsonify({
                    "code": 200,
                    "msg": "success",
                    "data": sqlite_detail
                })

        # 如SQLite不存在，从Neo4j获取（兼容历史数据）
        node_detail = neo4j_db_handle.get_node_detail(node_id)
        if not node_detail:
            return jsonify({
                "code": 404,
                "msg": "节点不存在"
            })

        return jsonify({
            "code": 200,
            "msg": "success",
            "data": node_detail
        })

    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str(e)
        })


@app.route('/api/node/update_properties', methods=['POST'])
def update_node_properties():
    """
    更新节点属性 - 更新SQLite，异步同步到Neo4j
    """
    try:
        data = request.json
        node_id = data.get("id")
        node_type = data.get("type")
        properties = data.get("properties", {})

        if not node_id:
            return jsonify({
                "code": 400,
                "msg": "节点ID不能为空"
            })

        if not properties or not isinstance(properties, dict):
            return jsonify({
                "code": 400,
                "msg": "节点属性格式不正确"
            })

        # 从properties中提取type
        if not node_type and 'type' in properties:
            node_type = properties['type']

        if not node_id:
            return jsonify({
                "code": 400,
                "msg": "无法确定节点类型"
            })

        # 确保id是整数
        if isinstance(node_id, str):
            node_id = int(node_id)

        result = DbUtil.update_node_properties(node_id, node_type, properties)
        return jsonify(result)

    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str(e)
        })


# ================== 同步状态监控接口 ==================

def get_sync_manager():
    """获取同步管理器（如果没有则返回None）"""
    # 如果你还没有实现同步管理器，暂时返回None
    # 或者返回一个模拟对象
    return None

@app.route('/api/sync/stats', methods=['GET'])
def get_sync_stats():
    """
    获取同步状态统计
    用于监控SQLite到Neo4j的同步队列状态
    """
    try:
        manager = get_sync_manager()
        if not manager:
            return jsonify({
                "code": 200,
                "data": {
                    "status": "未初始化",
                    "queue_size": 0,
                    "success_count": 0,
                    "failed_count": 0
                }
            })

        stats = manager.get_stats()
        return jsonify({
            "code": 200,
            "data": stats
        })

    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str(e)
        })


# ================== 其他Neo4j查询接口（保持原样）====================

@app.route('/api/node_types', methods=['GET'])
def get_node_types():
    try:
        types = neo4j_db_handle.get_node_types()
        return jsonify({
            "code": 200,
            "msg": "success",
            "data": types
        })
    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str(e)
        })


@app.route('/api/relationship_types', methods=['GET'])
def get_relationship_types():
    try:
        types = neo4j_db_handle.get_relationship_types()
        return jsonify({
            "code": 200,
            "msg": "success",
            "data": types
        })
    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str(e)
        })


@app.route('/api/relationship_types_by_Event', methods=['GET'])
def get_relationship_types_by_Event():
    try:
        types = neo4j_db_handle.get_relationship_types_by_Event()
        return jsonify({
            "code": 200,
            "msg": "success",
            "data": types
        })
    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str(e)
        })


@app.route('/api/node/relations', methods=['GET'])
def get_node_relations():
    try:
        node_id = request.args.get('id')
        if not node_id:
            return jsonify({
                "code": 400,
                "msg": "节点ID不能为空"
            })

        result = neo4j_db_handle.get_node_relations(node_id)
        return jsonify({
            "code": 200,
            "msg": "success",
            "data": result
        })
    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str(e)
        })


@app.route('/api/node/by_type', methods=['GET'])
def get_nodes_by_type():
    try:
        node_type = request.args.get('type')
        if not node_type:
            return jsonify({
                "code": 400,
                "msg": "节点类型不能为空"
            })

        result = neo4j_db_handle.get_nodes_by_type(node_type)
        return jsonify({
            "code": 200,
            "msg": "success",
            "data": result
        })
    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str(e)
        })


@app.route('/api/node/by_relationship', methods=['GET'])
def get_nodes_by_relationship():
    try:
        rel_type = request.args.get('type')
        if not rel_type:
            return jsonify({
                "code": 400,
                "msg": "关系类型不能为空"
            })

        result = neo4j_db_handle.get_nodes_by_relationship(rel_type)
        return jsonify({
            "code": 200,
            "msg": "success",
            "data": result
        })
    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str(e)
        })


@app.route('/api/node/search_by_name', methods=['GET'])
def search_nodes_by_name():
    try:
        search_text = request.args.get('name')
        limit = request.args.get('limit', 100, type=int)

        if not search_text:
            return jsonify({
                "code": 400,
                "msg": "搜索文本不能为空"
            })

        result = neo4j_db_handle.search_nodes_by_name(search_text, limit)
        return jsonify({
            "code": 200,
            "msg": "success",
            "data": result
        })
    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str(e)
        })


# ================== 子页面关系图谱接口 ==================

@app.route('/api/graph/event_event', methods=['GET'])
def get_event_event_graph():
    """
    获取事件-事件关系图（关联战争子页面）
    只展示战争事件之间的关联关系
    """
    try:
        name_filter = request.args.get('name', '')
        rel_type = request.args.get('rel_type', '')
        
        result = neo4j_db_handle.get_event_event_relations(name_filter, rel_type)
        return jsonify({
            "code": 200,
            "msg": "success",
            "data": result
        })
    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str(e)
        })


@app.route('/api/graph/event_organization', methods=['GET'])
def get_event_organization_graph():
    """
    获取事件-组织关系图（参战势力子页面）
    展示参战势力与战争事件之间的关系
    """
    try:
        name_filter = request.args.get('name', '')
        rel_type = request.args.get('rel_type', '')
        
        result = neo4j_db_handle.get_event_organization_relations(name_filter, rel_type)
        return jsonify({
            "code": 200,
            "msg": "success",
            "data": result
        })
    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str(e)
        })


@app.route('/api/graph/event_person', methods=['GET'])
def get_event_person_graph():
    """
    获取事件-人物关系图（相关人物子页面）
    展示相关人物与战争事件之间的关系
    """
    try:
        name_filter = request.args.get('name', '')
        rel_type = request.args.get('rel_type', '')
        
        result = neo4j_db_handle.get_event_person_relations(name_filter, rel_type)
        return jsonify({
            "code": 200,
            "msg": "success",
            "data": result
        })
    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str(e)
        })


@app.route('/api/graph/event_place', methods=['GET'])
def get_event_place_graph():
    """
    获取事件-地点关系图（发生地点子页面）
    展示发生地点与战争事件之间的关系
    """
    try:
        name_filter = request.args.get('name', '')
        rel_type = request.args.get('rel_type', '')
        
        result = neo4j_db_handle.get_event_place_relations(name_filter, rel_type)
        return jsonify({
            "code": 200,
            "msg": "success",
            "data": result
        })
    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str(e)
        })


@app.route('/api/graph/node_context', methods=['GET'])
def get_node_context_graph():
    """获取单个实体的一阶关系子图，用于从实体详情页跳转到图谱时聚焦。"""
    try:
        graph_id = request.args.get('graph_id', type=int)
        name = request.args.get('name', '')

        if graph_id is None and name:
            search_result = neo4j_db_handle.search_nodes_by_name(name, limit=1)
            nodes = search_result.get('nodes', []) if search_result else []
            if nodes:
                graph_id = nodes[0].get('id')

        if graph_id is None:
            return jsonify({"code": 400, "msg": "缺少可定位的图谱节点", "data": {"nodes": [], "lines": []}})

        result = neo4j_db_handle.get_node_relations(graph_id)
        return jsonify({"code": 200, "msg": "success", "data": result})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e), "data": {"nodes": [], "lines": []}})


# ================== 智能问答接口====================

@app.route('/api/ai/inference', methods=['POST', 'GET'])
def ai_inference():
    """智能问答推理接口
    
    请求参数:
        POST/GET: question(用户问题)
    
    处理流程:
        1. 实体提取: 从问题中识别历史实体
        2. 图谱查询: 从Neo4j查询实体关系
        3. 规则推理: 应用规则推导隐含关系
        4. 大模型生成: 生成自然语言回答
    
    响应:
        - success: true/false
        - answer: AI回答内容
        - kg_data: 知识图谱可视化数据
        - entities: 识别到的实体列表
        - process_time: 处理耗时
    """
    try:
        if request.method == 'POST':
            data = request.get_json()
            if not data:
                return jsonify({
                    'success': False,
                    'error': '请求参数不能为空'
                }), 400

            user_question = data.get('question', '')
        else:
            user_question = request.args.get('question', '')

        if not user_question or len(user_question.strip()) == 0:
            return jsonify({
                'success': False,
                'error': '问题不能为空'
            }), 400

        request_id = str(uuid.uuid4())[:8]
        print(f"[{request_id}] 收到大模型推理请求: '{user_question}'")

        if not hasattr(g, 'rule_llm_integration'):
            print(f"[{request_id}] 初始化规则推理模块")
            try:
                from inference.rule_llm_integration import RuleLLMIntegration
                g.rule_llm_integration = RuleLLMIntegration(
                    rule_file_path='rules/rule_base.json',
                    model_name='deepseek-r1:7b',
                    max_depth=3
                )
            except Exception as init_err:
                print(f"[{request_id}] 初始化规则推理模块失败: {str(init_err)}")
                return jsonify({
                    'success': False,
                    'error': '系统初始化失败，请稍后再试',
                    'answer': '抱歉，推理系统正在初始化中，请稍后再试。',
                    'kg_data': {'nodes': [], 'lines': []}
                }), 500

        if not hasattr(g, 'entity_extractor'):
            print(f"[{request_id}] 初始化实体提取器")
            try:
                from entity_extract.extractor import Extractor
                g.entity_extractor = Extractor()
            except Exception as init_err:
                print(f"[{request_id}] 初始化实体提取器失败: {str(init_err)}")
                return jsonify({
                    'success': False,
                    'error': '实体提取器初始化失败，请稍后再试',
                    'answer': '抱歉，地名识别系统正在初始化中，请稍后再试。',
                    'kg_data': {'nodes': [], 'lines': []}
                }), 500

        print(f"[{request_id}] 开始处理问题...")
        start_time = time.time()

        try:
            result = g.rule_llm_integration.process_question(
                question=user_question,
                entity_extractor=g.entity_extractor,
                neo4j_db=neo4j_db_handle
            )

            process_time = result.get('process_time', 0)
            entities = result.get('query_entities') or result.get('entities', [])

            print(f"[{request_id}] 问题处理完成，耗时: {process_time:.2f}秒, 识别到 {len(entities)} 个实体")

            kg_data = result.get('kg_data', {'nodes': [], 'lines': []})
            node_count = len(kg_data.get('nodes', []))
            line_count = len(kg_data.get('lines', []))
            print(f"[{request_id}] 生成的知识图谱数据: {node_count} 个节点, {line_count} 条关系")

            relations_text = result.get('context', '未找到相关关系数据')

            response = {
                'success': True,
                'answer': result.get('answer', '抱歉，无法回答这个问题'),
                'kg_data': kg_data,
                'entities': entities,
                'query_entities': result.get('query_entities', entities),
                'process_time': process_time,
                'relations_text': relations_text
            }

            return jsonify(response)

        except Exception as process_err:
            error_type = type(process_err).__name__
            error_msg = str(process_err)

            print(f"[{request_id}] 处理问题时出错: {error_type} - {error_msg}")
            import traceback
            traceback.print_exc()

            user_message = "抱歉，处理您的问题时遇到了技术问题。"

            if "ConnectionRefused" in error_type or "ConnectionError" in error_type:
                user_message = "抱歉，无法连接到知识库服务器，请检查数据库连接。"
            elif "TimeoutError" in error_type:
                user_message = "抱歉，查询超时，请尝试简化您的问题或稍后再试。"
            elif "ollama" in error_msg.lower():
                user_message = "抱歉，大模型服务暂时不可用，请稍后再试。"
            elif "memory" in error_msg.lower() or "cuda" in error_msg.lower():
                user_message = "抱歉，系统资源不足，请稍后再试。"
            elif "invalid" in error_msg.lower() or "syntax" in error_msg.lower():
                user_message = "抱歉，您的问题格式可能有误，请尝试用不同方式提问。"

            return jsonify({
                'success': False,
                'error': f'处理问题时出错: {error_type}',
                'error_detail': error_msg,
                'answer': user_message,
                'kg_data': {'nodes': [], 'lines': []},
                'process_time': time.time() - start_time
            }), 500

    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e)
        print(f"处理推理请求时出错: {error_type} - {error_msg}")
        import traceback
        traceback.print_exc()

        return jsonify({
            'success': False,
            'error': f'请求处理错误: {error_type}',
            'error_detail': error_msg,
            'answer': f"抱歉，系统无法处理您的请求。请检查输入格式是否正确，或稍后再试。",
            'kg_data': {'nodes': [], 'lines': []}
        }), 500


@app.route('/api/ai/inference/stream', methods=['POST'])
def ai_inference_stream():
    """智能问答推理接口 - SSE流式输出版本

    处理流程:
        1. 实体提取: 从问题中识别历史实体
        2. 图谱查询: 从Neo4j查询实体关系
        3. 规则推理: 应用规则推导隐含关系
        4. 大模型生成: 流式生成自然语言回答

    响应: SSE (Server-Sent Events) 流
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': '请求参数不能为空'}), 400

        user_question = data.get('question', '')
        if not user_question or len(user_question.strip()) == 0:
            return jsonify({'success': False, 'error': '问题不能为空'}), 400

        request_id = str(uuid.uuid4())[:8]
        print(f"[{request_id}] 收到SSE推理请求: '{user_question}'")

        # 获取推理引擎和实体提取器
        if not hasattr(g, 'rule_llm_integration') or not hasattr(g, 'entity_extractor'):
            try:
                from inference.rule_llm_integration import RuleLLMIntegration
                from entity_extract.extractor import Extractor
                g.rule_llm_integration = RuleLLMIntegration(
                    rule_file_path='rules/rule_base.json',
                    model_name='deepseek-r1:7b',
                    max_depth=30
                )
                g.entity_extractor = Extractor()
            except Exception as init_err:
                print(f"[{request_id}] 初始化失败: {str(init_err)}")
                return jsonify({'success': False, 'error': '系统初始化失败'}), 500

        # 在生成器外部捕获Flask上下文对象，避免在生成器内访问g
        rule_engine = g.rule_llm_integration
        entity_ext = g.entity_extractor

        def generate():
            """SSE生成器"""
            try:
                # 发送开始信号
                yield f"data: {json.dumps({'status': 'start', 'request_id': request_id})}\n\n"

                # ========== 步骤1: 实体提取 ==========
                yield f"data: {json.dumps({'status': 'extracting', 'message': '正在识别实体...'})}\n\n"

                start_time = time.time()

                # 检测问题类型
                dynasty_scope = rule_engine._detect_dynasty_event_scope(user_question)
                if dynasty_scope:
                    extracted_entities = []
                    query_entities = [dynasty_scope["display"]]
                    event_detail_query = False
                    participant_event_query = False
                else:
                    extracted_entities = entity_ext.extract_entities(user_question)
                    query_entities = rule_engine._extract_query_entities(user_question, extracted_entities)
                    event_detail_query = rule_engine._looks_like_event_detail_query(user_question, extracted_entities)
                    participant_event_query = rule_engine._looks_like_participant_event_query(user_question)

                # 过滤实体
                if dynasty_scope:
                    entities = query_entities.copy()
                else:
                    entities = rule_engine._filter_relevant_entities(extracted_entities, user_question, neo4j_db_handle)

                extract_time = time.time() - start_time
                print(f"[{request_id}] 实体提取完成，耗时: {extract_time:.2f}秒，实体: {entities}")

                # 发送实体信息
                yield f"data: {json.dumps({'status': 'entities', 'entities': entities, 'query_entities': query_entities, 'extract_time': round(extract_time, 2)})}\n\n"

                if not entities:
                    yield f"data: {json.dumps({'status': 'done', 'answer': '抱歉，无法从问题中识别出实体。', 'kg_data': {'nodes': [], 'lines': []}})}\n\n"
                    return

                # ========== 步骤2: 查询知识图谱 ==========
                yield f"data: {json.dumps({'status': 'querying', 'message': '正在查询知识图谱...'})}\n\n"

                query_start = time.time()

                # 查询实体信息
                all_entity_info = []
                entity_info_map = {}

                if dynasty_scope:
                    all_entity_info = rule_engine._get_dynasty_event_infos(neo4j_db_handle, dynasty_scope["dynasties"])
                    print(f"[{request_id}] 朝代查询: {dynasty_scope['dynasties']}, 找到 {len(all_entity_info)} 条事件")
                    for info in all_entity_info:
                        entity_info_map[info['id']] = info
                elif event_detail_query:
                    event_names = [entity for entity in entities if any(suffix in entity for suffix in ["之战", "战役", "起义", "叛乱"])]
                    all_entity_info = rule_engine._get_event_detail_infos(neo4j_db_handle, event_names or entities)
                    for info in all_entity_info:
                        entity_info_map[info['id']] = info
                elif participant_event_query:
                    participant_names = query_entities or entities
                    all_entity_info = rule_engine._get_participant_event_infos(neo4j_db_handle, participant_names)
                    for info in all_entity_info:
                        entity_info_map[info['id']] = info
                else:
                    for entity in entities:
                        query = f"""
                        MATCH (n)
                        WHERE n.name = '{entity}'
                        RETURN n
                        LIMIT 1
                        """
                        results = neo4j_db_handle.graph.run(query).data()

                        if results:
                            node = results[0]['n']
                            info = {
                                'id': node.identity,
                                'name': node['name'],
                                'type': list(node.labels)[0] if node.labels else '',
                                'properties': {k: v for k, v in node.items()}
                            }
                            all_entity_info.append(info)
                            entity_info_map[info['id']] = info
                        else:
                            # 朝代别名回退：如"周朝"→查DynastyName为"西周"/"东周"的事件
                            dynasty_values = DYNASTY_SCOPE_MAP.get(entity)
                            if dynasty_values:
                                dynasty_query = """
                                MATCH (n:Event)
                                WHERE n.dynasty IN $dynasties
                                RETURN n
                                ORDER BY coalesce(n.start_date, ''), n.name
                                LIMIT 20
                                """
                                dynasty_results = neo4j_db_handle.graph.run(dynasty_query, dynasties=dynasty_values).data()
                                for result in dynasty_results:
                                    node = result['n']
                                    info = {
                                        'id': node.identity,
                                        'name': node['name'],
                                        'type': list(node.labels)[0] if node.labels else 'Event',
                                        'properties': {k: v for k, v in node.items()}
                                    }
                                    all_entity_info.append(info)
                                    entity_info_map[info['id']] = info
                                if dynasty_results:
                                    print(f"[{request_id}] 朝代别名回退: '{entity}' → {dynasty_values}, 找到 {len(dynasty_results)} 条事件")
                                    continue

                            # 模糊查询
                            fuzzy_query = f"""
                            MATCH (n)
                            WHERE n.name CONTAINS '{entity}' OR '{entity}' CONTAINS n.name
                            RETURN n
                            LIMIT 5
                            """
                            fuzzy_results = neo4j_db_handle.graph.run(fuzzy_query).data()
                            for result in fuzzy_results:
                                node = result['n']
                                info = {
                                    'id': node.identity,
                                    'name': node['name'],
                                    'type': list(node.labels)[0] if node.labels else '',
                                    'properties': {k: v for k, v in node.items()}
                                }
                                all_entity_info.append(info)
                                entity_info_map[info['id']] = info

                if not all_entity_info:
                    yield f"data: {json.dumps({'status': 'done', 'answer': f'抱歉，在知识库中找不到与{entities}相关的实体信息。', 'kg_data': {'nodes': [], 'lines': []}})}\n\n"
                    return

                # 查询实体关系
                all_relationships = []
                for info in all_entity_info:
                    relationships = rule_engine.get_entity_relationships(info['id'], neo4j_db_handle)
                    all_relationships.extend(relationships)

                # 搜索实体间路径
                all_paths = []
                if len(all_entity_info) >= 2 and not (dynasty_scope or event_detail_query or participant_event_query):
                    processed_entity_pairs = set()
                    for i in range(len(all_entity_info)):
                        for j in range(i+1, len(all_entity_info)):
                            entity1_id = all_entity_info[i]['id']
                            entity2_id = all_entity_info[j]['id']
                            if entity1_id == entity2_id:
                                continue
                            entity_pair = tuple(sorted([entity1_id, entity2_id]))
                            if entity_pair in processed_entity_pairs:
                                continue
                            processed_entity_pairs.add(entity_pair)
                            paths = rule_engine.search_paths_between_entities(entity1_id, entity2_id, neo4j_db_handle)
                            all_paths.extend(paths)

                # 应用推理规则
                inferred_relationships = rule_engine.apply_inference_rules(all_relationships)
                original_relationships = all_relationships.copy()
                all_relationships.extend(inferred_relationships)

                query_time = time.time() - query_start
                print(f"[{request_id}] 图谱查询完成，耗时: {query_time:.2f}秒")

                # 发送查询结果
                yield f"data: {json.dumps({'status': 'queried', 'entity_count': len(all_entity_info), 'relation_count': len(all_relationships), 'query_time': round(query_time, 2)})}\n\n"

                # ========== 步骤3: 流式生成回答 ==========
                yield f"data: {json.dumps({'status': 'generating', 'message': '正在生成回答...'})}\n\n"

                generate_start = time.time()

                # 构建提示词
                answer_entities = entities
                question_type = "general"
                answer_metadata = {}
                if dynasty_scope:
                    question_type = "dynasty_event_list"
                    answer_metadata["dynasty_scope"] = dynasty_scope
                    answer_entities = [f"{dynasty_scope['display']}（仅限 DynastyName 属于 {', '.join(dynasty_scope['dynasties'])} 的战争事件）"]
                elif event_detail_query:
                    question_type = "event_detail"
                elif participant_event_query:
                    question_type = "participant_event_list"

                # 构建提示词
                if question_type == "dynasty_event_list":
                    user_prompt = rule_engine._build_dynasty_event_list_prompt(
                        question=user_question,
                        dynasty_scope=answer_metadata.get("dynasty_scope", {}),
                        event_info=all_entity_info
                    )
                elif question_type == "event_detail":
                    user_prompt = rule_engine._build_event_detail_prompt(user_question, all_entity_info)
                elif question_type == "participant_event_list":
                    user_prompt = rule_engine._build_participant_event_prompt(user_question, all_entity_info)
                else:
                    user_prompt = rule_engine._build_inference_prompt(
                        question=user_question,
                        entities=answer_entities,
                        entity_info=all_entity_info,
                        relationships=all_relationships,
                        paths=all_paths
                    )

                # 设置system提示词
                if question_type in {"dynasty_event_list", "participant_event_list"}:
                    system_prompt = """你是一位中国历史战争知识图谱问答助手。当前任务是根据后端提供的结构化事件清单回答枚举类问题。
必须完整覆盖清单中的每一条事件；不得自行删减、合并、补充清单外事件；不得把枚举题回答成单个"最准确答案"。"""
                elif question_type == "event_detail":
                    system_prompt = """你是一位中国历史战争知识图谱问答助手。当前任务是根据后端提供的结构化事件记录回答事件详情问题。
只能使用记录中的字段作答；字段缺失时说明图谱未提供；不得补充图谱外史实。"""
                else:
                    system_prompt = """你是一位专业的中国历史战争研究与事件关系分析专家，精通中国历代战争事件、参战势力、战役过程及其历史背景。
你的职责是：
1. 只回答用户提出的原始问题，不要重复或引用我给你的指令内容
2. 不要在回答中说"根据提供的信息"或"基于图谱数据"等引导语
3. 所有回答必须基于知识图谱提供的事实，不要编造不存在的关系
4. 答案应直接、简洁，不要添加不必要的解释

重要: 不要将指令或提示词本身视为用户问题的一部分。用户的原始问题已在提示词开头明确标出。"""

                # 使用ollama流式调用
                import ollama

                full_answer = ""
                try:
                    stream = ollama.chat(
                        model='deepseek-r1:7b',
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        stream=True,
                        keep_alive="10m",
                        options={"temperature": 0.1}
                    )

                    for chunk in stream:
                        if chunk['message']['content']:
                            content = chunk['message']['content']
                            full_answer += content
                            # 发送内容块
                            yield f"data: {json.dumps({'status': 'content', 'content': content})}\n\n"

                except Exception as llm_err:
                    print(f"[{request_id}] LLM调用失败: {str(llm_err)}")
                    yield f"data: {json.dumps({'status': 'error', 'message': f'大模型调用失败: {str(llm_err)}'})}\n\n"
                    return

                generate_time = time.time() - generate_start
                total_time = time.time() - start_time
                print(f"[{request_id}] 回答生成完成，耗时: {generate_time:.2f}秒，总耗时: {total_time:.2f}秒")

                # ========== 步骤4: 构建可视化数据 ==========
                kg_data = rule_engine._convert_to_visual_data(all_entity_info, original_relationships, all_paths)

                # 格式化关系上下文
                context = rule_engine._format_relations_for_context(original_relationships, inferred_relationships)

                # 发送完成信号
                yield f"data: {json.dumps({'status': 'done', 'kg_data': kg_data, 'relations_text': context, 'entities': entities, 'query_entities': query_entities, 'process_time': round(total_time, 2)})}\n\n"

            except Exception as e:
                error_type = type(e).__name__
                error_msg = str(e)
                print(f"[{request_id}] SSE处理出错: {error_type} - {error_msg}")
                import traceback
                traceback.print_exc()
                yield f"data: {json.dumps({'status': 'error', 'message': f'处理出错: {error_msg}'})}\n\n"

        return Response(generate(), mimetype='text/event-stream',
                       headers={'Cache-Control': 'no-cache',
                               'Connection': 'keep-alive',
                               'X-Accel-Buffering': 'no'})

    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e)
        print(f"SSE请求处理错误: {error_type} - {error_msg}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': f'请求处理错误: {error_msg}'}), 500


@app.route('/api/dashboard/overview', methods=['GET'])
def get_dashboard_overview():
    """首页仪表盘接口。"""
    try:
        return jsonify({"code": 200, "data": build_dashboard_overview()})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e), "data": {}})


@app.route('/api/dataset/overview', methods=['GET'])
def get_dataset_overview():
    """数据集中心接口。"""
    try:
        return jsonify({"code": 200, "data": build_dataset_overview()})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e), "data": {}})


@app.route('/api/quality/report', methods=['GET'])
def get_quality_report():
    """图谱质检接口。"""
    try:
        return jsonify({"code": 200, "data": build_quality_report()})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e), "data": {}})


@app.route('/api/quality/workbench', methods=['GET'])
def get_quality_workbench():
    """数据修复工作台接口。"""
    try:
        return jsonify({"code": 200, "data": build_quality_workbench()})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e), "data": {}})


@app.route('/api/entity/detail', methods=['GET'])
def get_entity_detail():
    """实体详情页聚合接口。"""
    try:
        node_type = request.args.get('type', '')
        node_id = request.args.get('id', type=int)
        if not node_type or not node_id:
            return jsonify({"code": 400, "msg": "type 和 id 不能为空", "data": {}})

        data = build_entity_detail(node_type, node_id)
        if not data:
            return jsonify({"code": 404, "msg": "实体不存在", "data": {}})

        return jsonify({"code": 200, "data": data})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e), "data": {}})


@app.route('/api/timeline/overview', methods=['GET'])
def get_timeline_overview():
    """时间轴页面接口。"""
    try:
        keyword = request.args.get('keyword', '')
        dynasty = request.args.get('dynasty', '')
        participant = request.args.get('participant', '')
        return jsonify({"code": 200, "data": build_timeline_overview(keyword, dynasty, participant)})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e), "data": {}})


@app.route('/api/repair/issues', methods=['GET'])
def get_repair_issues():
    """修复工作台问题列表。"""
    try:
        workbench = build_quality_workbench()
        issue_type = request.args.get('type', '')
        issues = workbench.get("issue_queue", [])
        if issue_type:
            issues = [item for item in issues if item.get("issue_type") == issue_type]
        return jsonify({"code": 200, "data": {"summary": workbench.get("summary", {}), "issues": issues}})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e), "data": {"issues": []}})


@app.route('/api/repair/update_entity', methods=['POST'])
def repair_update_entity():
    """修复工作台快速更新实体字段。"""
    try:
        data = request.get_json() or {}
        node_id = data.get("id")
        node_type = data.get("type")
        properties = data.get("properties", {})
        if not node_id or not node_type:
            return jsonify({"code": 400, "msg": "id 和 type 不能为空"})
        return jsonify(DbUtil.update_node_properties(int(node_id), node_type, properties))
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e)})


@app.route('/api/repair/merge_nodes', methods=['POST'])
def repair_merge_nodes():
    """重复节点合并建议接口。默认不执行删除，避免误合并。"""
    try:
        data = request.get_json() or {}
        return jsonify({
            "code": 200,
            "msg": "已生成合并建议，未自动合并节点",
            "data": {
                "primary_id": data.get("primary_id"),
                "duplicate_ids": data.get("duplicate_ids", []),
                "suggestion": "建议先核对名称、朝代、来源文本和关联关系，再人工确认合并。"
            }
        })
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e)})


@app.route('/api/timeline/events', methods=['GET'])
def get_timeline_events():
    """时间轴事件列表。"""
    try:
        keyword = request.args.get('keyword', '')
        dynasty = request.args.get('dynasty', '')
        participant = request.args.get('participant', '')
        event_type = request.args.get('event_type', '')
        only_issues = request.args.get('only_issues', '0') == '1'
        data = build_timeline_overview(keyword, dynasty, participant)
        events = data.get("events", [])
        if event_type:
            events = [item for item in events if item.get("event_type") == event_type]
        if only_issues:
            events = [item for item in events if item.get("quality_flags", {}).get("timeline_problems")]
        data["events"] = events
        data["event_types"] = sorted({item.get("event_type") for item in events if item.get("event_type")})
        return jsonify({"code": 200, "data": data})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e), "data": {"events": []}})


@app.route('/api/map/events', methods=['GET'])
def get_event_map():
    """历史地图视图数据。"""
    try:
        keyword = request.args.get('keyword', '')
        dynasty = request.args.get('dynasty', '')
        return jsonify({"code": 200, "data": build_map_overview(keyword, dynasty)})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e), "data": {"points": []}})


@app.route('/api/relation-analysis/query', methods=['GET', 'POST'])
def relation_analysis_query():
    """关系分析查询：支持实体名称、类型和一跳/二跳深度。"""
    try:
        data = request.get_json() if request.method == 'POST' else request.args
        name = data.get('name', '')
        node_type = data.get('type', '')
        depth = int(data.get('depth', 1) or 1)
        rel_type = data.get('rel_type', '')

        if rel_type:
            graph_data = neo4j_db_handle.search_by_name_and_relation(name, rel_type, limit=120)
        elif name and node_type:
            graph_data = neo4j_db_handle.search_by_name_and_type(name, node_type, limit=120)
        elif name:
            search_result = neo4j_db_handle.search_nodes_by_name(name, limit=1)
            nodes = search_result.get("nodes", [])
            graph_data = neo4j_db_handle.get_node_relations(nodes[0]["id"]) if nodes else {"nodes": [], "lines": []}
        else:
            graph_data = {"nodes": [], "lines": []}

        if depth >= 2 and graph_data.get("nodes"):
            merged_nodes = {item["id"]: item for item in graph_data["nodes"]}
            merged_lines = {f"{item['from']}-{item['to']}-{item.get('text','')}": item for item in graph_data.get("lines", [])}
            for node in list(graph_data["nodes"])[:8]:
                sub_graph = neo4j_db_handle.get_node_relations(node["id"])
                for sub_node in sub_graph.get("nodes", []):
                    merged_nodes[sub_node["id"]] = sub_node
                for line in sub_graph.get("lines", []):
                    merged_lines[f"{line['from']}-{line['to']}-{line.get('text','')}"] = line
            graph_data = {"nodes": list(merged_nodes.values()), "lines": list(merged_lines.values())}

        return jsonify({"code": 200, "data": graph_data})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e), "data": {"nodes": [], "lines": []}})


@app.route('/api/search/global', methods=['GET'])
def global_search():
    try:
        keyword = request.args.get("keyword", "")
        return jsonify({"code": 200, "data": build_global_search(keyword)})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e), "data": []})


def _to_str(value):
    """将LLM返回的list/其他类型统一转为字符串，避免Pydantic类型校验失败"""
    if value is None:
        return None
    if isinstance(value, list):
        return "、".join(str(v) for v in value if v)
    return str(value)


# 合法朝代列表（来自entity-event-relation提示词模板）
VALID_DYNASTIES = [
    "夏", "商", "西周", "春秋", "战国", "秦", "西汉", "东汉",
    "三国", "魏", "蜀", "吴", "西晋", "东晋", "南北朝",
    "隋", "唐", "五代十国", "北宋", "南宋", "辽", "西夏", "金", "元", "明", "清",
    "上古", "原始社会", "父系氏族社会",
]

# 常见错误朝代名 → 正确朝代名 映射
_DYNASTY_CORRECTIONS = {
    "商汤": "商", "商朝": "商", "夏朝": "夏", "周朝": "西周",
    "秦朝": "秦", "汉朝": "西汉", "隋朝": "隋", "唐朝": "唐",
    "宋朝": "北宋", "辽朝": "辽", "金朝": "金", "元朝": "元",
    "明朝": "明", "清朝": "清",
}


def _normalize_dynasty(name):
    """校验并纠正朝代名称，将LLM输出的错误朝代名（如人名"商汤"）纠正为正确朝代"""
    if not name:
        return name
    name = str(name).strip()
    if not name:
        return name

    # 1. 精确匹配合法朝代
    if name in VALID_DYNASTIES:
        return name

    # 2. 查找已知错误映射
    if name in _DYNASTY_CORRECTIONS:
        return _DYNASTY_CORRECTIONS[name]

    # 3. 模糊匹配：如果名称包含某个合法朝代，提取该朝代
    #    例如 "商汤" 包含 "商"，"汉武帝" 包含 "汉"（但汉需要特殊处理）
    for dynasty in VALID_DYNASTIES:
        if dynasty in name and len(dynasty) >= 1:
            return dynasty

    return name


# 合法角色列表（来自entity-event-relation提示词模板）
VALID_ROLES = ["统帅", "将领", "谋士", "君主", "使者", "参战者"]

# 常见错误角色名 → 正确角色名 映射
_ROLE_CORRECTIONS = {
    "king": "君主", "emperor": "君主", "ruler": "君主", "monarch": "君主", "sovereign": "君主",
    "queen": "君主", "prince": "君主", "lord": "君主",
    "general": "将领", "commander": "统帅", "marshal": "统帅",
    "strategist": "谋士", "advisor": "谋士", "counselor": "谋士",
    "envoy": "使者", "emissary": "使者", "messenger": "使者",
    "soldier": "参战者", "warrior": "参战者", "fighter": "参战者",
    "首领": "统帅", "头领": "统帅", "主帅": "统帅", "主将": "将领",
    "将军": "将领", "军师": "谋士", "谋臣": "谋士", "大臣": "参战者",
    "将领": "将领", "统帅": "统帅", "谋士": "谋士", "君主": "君主",
}


def _normalize_role(name):
    """校验并纠正角色名称，将LLM输出的错误角色名（如英文"king"）纠正为中文"""
    if not name:
        return name
    name = str(name).strip()
    if not name:
        return name

    # 1. 精确匹配合法角色
    if name in VALID_ROLES:
        return name

    # 2. 查找已知错误映射（不区分大小写）
    lower_name = name.lower()
    if lower_name in _ROLE_CORRECTIONS:
        return _ROLE_CORRECTIONS[lower_name]

    # 3. 模糊匹配：如果名称包含某个合法角色
    for role in VALID_ROLES:
        if role in name:
            return role

    # 4. 默认返回"参战者"
    return "参战者"


def _normalize_event_name(event_name, source_text=""):
    """校验事件名称，确保名称出现在原文中或符合命名规范"""
    if not event_name:
        return event_name
    event_name = str(event_name).strip()
    if not event_name:
        return event_name

    # 如果事件名称直接出现在原文中，认为是正确的
    if source_text and event_name in source_text:
        return event_name

    # 检查是否符合"XX之战"、"XX之战"等规范命名
    import re
    if re.search(r'[一-龥]+之战', event_name):
        return event_name

    # 如果不符合规范，尝试从原文中提取正确的事件名称
    # 常见模式：XX之战、XX之役、XX大战
    if source_text:
        patterns = [
            r'([一-龥]{2,6}之战)',
            r'([一-龥]{2,6}之役)',
            r'([一-龥]{2,6}大战)',
            r'([一-龥]{2,6}会战)',
        ]
        for pattern in patterns:
            match = re.search(pattern, source_text)
            if match:
                return match.group(1)

    return event_name


def extract_all_optimized(llm, text: str):
    """
    使用entity-event-relation模块的原始抽取器和模板
    - EntityExtractor: 实体抽取（ENTITY_EXTRACTION_PROMPT）
    - EventExtractor: 事件抽取（EVENT_IDENTIFICATION_PROMPT + FULL_EVENT_PROMPT）
    - RelationExtractor: 关系抽取（RELATION_EXTRACTION_PROMPT）
    支持长文本分段处理，自动合并去重
    """

    # 文本分段处理（减小分段，减少LLM调用次数）
    try:
        splitter = TextSplitter(chunk_size=1200, overlap=150)
        chunks = splitter.split(text)
    except Exception:
        chunks = [(0, len(text), text)]

    # 初始化抽取器
    entity_extractor = EntityExtractor(llm)
    event_extractor = EventExtractor(llm)
    relation_extractor = RelationExtractor(llm)

    # 累积结果容器
    all_places = []
    all_orgs = []
    all_persons = []
    all_events = []
    all_event_place_rels = []
    all_event_person_rels = []
    all_event_org_rels = []
    all_event_event_rels = []

    seen_places = set()
    seen_orgs = set()
    seen_persons = set()
    seen_events = set()

    for chunk_start, chunk_end, chunk_text in chunks:
        if not chunk_text.strip():
            continue

        print(f"[提取] 处理文本段: {chunk_start}-{chunk_end} ({len(chunk_text)}字)")

        # ========== 第1阶段：实体抽取 ==========
        try:
            chunk_entities = entity_extractor.extract(chunk_text)
            print(f"[提取] 实体抽取完成: {len(chunk_entities.places)}地点, {len(chunk_entities.organizations)}组织, {len(chunk_entities.persons)}人物")
        except Exception as e:
            print(f"[提取] 实体抽取失败: {e}")
            chunk_entities = EntityExtractionResult()

        # 收集实体（去重）
        chunk_place_names = []
        for p in chunk_entities.places:
            name = _to_str(p.geo_name) or ""
            name = name.strip()
            if name and name not in seen_places:
                seen_places.add(name)
                # 类型安全处理 + 朝代校验
                p.geo_name = name
                p.modern_name = _to_str(p.modern_name)
                p.DynastyName = _normalize_dynasty(_to_str(p.DynastyName))
                p.Province = _to_str(p.Province)
                p.City = _to_str(p.City)
                p.District_County = _to_str(p.District_County)
                p.Specific_location = _to_str(p.Specific_location)
                p.source_text = _to_str(p.source_text)
                all_places.append(p)
            if name:
                chunk_place_names.append(name)

        chunk_org_names = []
        for o in chunk_entities.organizations:
            name = _to_str(o.OrgName) or ""
            name = name.strip()
            if name and name not in seen_orgs:
                seen_orgs.add(name)
                o.OrgName = name
                o.OrgType = _to_str(o.OrgType)
                o.DynastyName = _normalize_dynasty(_to_str(o.DynastyName))
                o.source_text = _to_str(o.source_text)
                all_orgs.append(o)
            if name:
                chunk_org_names.append(name)

        chunk_person_names = []
        for p in chunk_entities.persons:
            name = _to_str(p.PersonName) or ""
            name = name.strip()
            if name and name not in seen_persons:
                seen_persons.add(name)
                p.PersonName = name
                p.DynastyName = _normalize_dynasty(_to_str(p.DynastyName))
                p.OrgName = _to_str(p.OrgName)
                p.Role = _normalize_role(_to_str(p.Role))
                p.Note = _to_str(p.Note)
                p.source_text = _to_str(p.source_text)
                all_persons.append(p)
            if name:
                chunk_person_names.append(name)

        # ========== 第2阶段：事件抽取 ==========
        try:
            chunk_event_result = event_extractor.extract(chunk_text, chunk_entities)
            print(f"[提取] 事件抽取完成: {len(chunk_event_result.events)}事件")
        except Exception as e:
            print(f"[提取] 事件抽取失败: {e}")
            chunk_event_result = EventExtractionResult()

        # 收集事件（去重，类型安全处理）
        chunk_event_names = []
        for ev in chunk_event_result.events:
            event_name = _to_str(ev.EventName) or ""
            event_name = event_name.strip()
            if not event_name or event_name in seen_events:
                continue
            seen_events.add(event_name)
            # 类型安全处理所有字段 + 朝代校验 + 事件名称校验
            ev.EventName = _normalize_event_name(event_name, _to_str(ev.source_text) or chunk_text)
            ev.EventType = _to_str(ev.EventType)
            ev.StartDate = _to_str(ev.StartDate)
            ev.EndDate = _to_str(ev.EndDate)
            ev.DynastyName = _normalize_dynasty(_to_str(ev.DynastyName))
            ev.Place = _to_str(ev.Place)
            ev.Aggressor = _to_str(ev.Aggressor)
            ev.Defender = _to_str(ev.Defender)
            ev.Allies = _to_str(ev.Allies)
            ev.Commanders = _to_str(ev.Commanders)
            ev.KeyPersons = _to_str(ev.KeyPersons)
            ev.Result = _to_str(ev.Result)
            ev.TroopSize = _to_str(ev.TroopSize)
            ev.Duration = _to_str(ev.Duration)
            ev.GeographicScope = _to_str(ev.GeographicScope)
            ev.Casualties = _to_str(ev.Casualties)
            ev.Impact = _to_str(ev.Impact)
            ev.source = _to_str(ev.source)
            ev.source_text = _to_str(ev.source_text)
            all_events.append(ev)
            chunk_event_names.append(event_name)

        if not chunk_event_names:
            print(f"[提取] 该段未识别到事件，跳过关系抽取")
            continue

        # ========== 第3阶段：关系抽取 ==========
        try:
            chunk_relations = relation_extractor.extract(
                chunk_text,
                chunk_event_result.events,
                place_list="、".join(chunk_place_names),
                org_list="、".join(chunk_org_names),
                person_list="、".join(chunk_person_names),
            )
            print(f"[提取] 关系抽取完成: {len(chunk_relations.event_place_relations)}事件-地点, "
                  f"{len(chunk_relations.event_person_relations)}事件-人物, "
                  f"{len(chunk_relations.event_organization_relations)}事件-组织, "
                  f"{len(chunk_relations.event_event_relations)}事件-事件")
        except Exception as e:
            print(f"[提取] 关系抽取失败: {e}")
            chunk_relations = RelationExtractionResult()

        # 收集关系（类型安全处理）
        for r in chunk_relations.event_place_relations:
            ename = _to_str(r.EventName) or ""
            pname = _to_str(r.modern_name) or ""
            if ename and pname:
                r.EventName = ename
                r.modern_name = pname
                r.relation = _to_str(r.relation) or "发生地"
                r.evidence = _to_str(r.evidence) or ""
                all_event_place_rels.append(r)

        for r in chunk_relations.event_person_relations:
            ename = _to_str(r.EventName) or ""
            pname = _to_str(r.PersonName) or ""
            if ename and pname:
                r.EventName = ename
                r.PersonName = pname
                r.relation = _to_str(r.relation) or "参与者"
                r.evidence = _to_str(r.evidence) or ""
                all_event_person_rels.append(r)

        for r in chunk_relations.event_organization_relations:
            ename = _to_str(r.EventName) or ""
            oname = _to_str(r.OrgName) or ""
            if ename and oname:
                r.EventName = ename
                r.OrgName = oname
                r.relation = _to_str(r.relation) or "参战方"
                r.evidence = _to_str(r.evidence) or ""
                all_event_org_rels.append(r)

        for r in chunk_relations.event_event_relations:
            ea = _to_str(r.EventName_A) or ""
            eb = _to_str(r.EventName_B) or ""
            if ea and eb:
                r.EventName_A = ea
                r.EventName_B = eb
                r.relation = _to_str(r.relation) or "关联"
                r.evidence = _to_str(r.evidence) or ""
                all_event_event_rels.append(r)

    entities = EntityExtractionResult(
        places=all_places,
        organizations=all_orgs,
        persons=all_persons
    )
    event_result = EventExtractionResult(events=all_events)
    relations = RelationExtractionResult(
        event_place_relations=all_event_place_rels,
        event_person_relations=all_event_person_rels,
        event_organization_relations=all_event_org_rels,
        event_event_relations=all_event_event_rels
    )

    return entities, event_result, relations


@app.route('/api/extract/entities-events', methods=['POST'])
def extract_entities_events():
    """
    文本实体与事件识别接口 - 完整版

    请求参数(JSON):
        - text: 用户输入的文本内容

    响应:
        - code: 200(成功) / 400(参数错误) / 500(服务器错误)
        - data:
            - entities: {places, organizations, persons} 识别到的实体及其属性
            - events: 识别到的事件及其属性
            - relations: {event_place, event_person, event_organization, event_event} 关系
            - process_time: 处理耗时
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"code": 400, "msg": "请求参数不能为空", "data": {}})

        text = data.get('text', '').strip()
        if not text:
            return jsonify({"code": 400, "msg": "文本内容不能为空", "data": {}})

        if len(text) > 1000:
            return jsonify({"code": 400, "msg": "文本内容过长，请限制在1000字符以内", "data": {}})

        start_time = time.time()

        try:
            from src.extractors.entity_extractor import EntityExtractor
            from src.extractors.event_extractor import EventExtractor
            from src.extractors.relation_extractor import RelationExtractor
            from src.utils import EntityClassifier, Normalizer
        except ImportError as import_err:
            print(f"导入提取器模块失败: {import_err}")
            return jsonify({
                "code": 500,
                "msg": f"提取器模块导入失败: {str(import_err)}",
                "data": {}
            })

        # 创建Ollama适配器，替代DeepSeekClient
        class OllamaAdapter:
            """使用本地Ollama模型的适配器"""
            def __init__(self, model_name="deepseek-r1:7b"):
                import ollama
                self.model = model_name
                self.ollama = ollama

            def call(self, prompt: str, temperature: float = 0.1, max_retries: int = 3, json_mode: bool = False) -> str:
                """调用Ollama模型"""
                import json
                for attempt in range(max_retries):
                    try:
                        response = self.ollama.chat(
                            model=self.model,
                            messages=[{"role": "user", "content": prompt}],
                            options={"temperature": temperature},
                            stream=False,
                            keep_alive="10m"
                        )
                        content = response['message']['content']

                        # 清理可能的Markdown代码块
                        if "```json" in content:
                            start = content.find("```json") + 7
                            end = content.find("```", start)
                            if end > start:
                                content = content[start:end].strip()
                        elif "```" in content:
                            start = content.find("```") + 3
                            end = content.find("```", start)
                            if end > start:
                                content = content[start:end].strip()

                        return content
                    except Exception as e:
                        print(f"Ollama调用失败，重试 {attempt + 1}/{max_retries}: {e}")
                        if attempt < max_retries - 1:
                            import time
                            time.sleep(1)
                        else:
                            raise

        # 初始化LLM客户端
        try:
            llm = OllamaAdapter("deepseek-r1:7b")
            print("[提取] 使用本地Ollama模型: deepseek-r1:7b")
        except Exception as llm_err:
            print(f"LLM客户端初始化失败: {llm_err}")
            return jsonify({
                "code": 500,
                "msg": f"LLM客户端初始化失败: {str(llm_err)}",
                "data": {}
            })

        # 使用优化的单次抽取方案
        print(f"[提取] 开始单次综合抽取，文本长度: {len(text)}")

        entities, event_result, relations = extract_all_optimized(llm, text)

        print(f"[提取] 抽取完成: {len(entities.places)}地点, {len(entities.organizations)}组织, {len(entities.persons)}人物, {len(event_result.events)}事件")
        print(f"[提取] 关系: {len(relations.event_place_relations)}事件-地点, {len(relations.event_person_relations)}事件-人物, {len(relations.event_organization_relations)}事件-组织, {len(relations.event_event_relations)}事件-事件")

        process_time = time.time() - start_time

        # 构建响应数据
        def serialize_place(place):
            return {
                "geo_name": place.geo_name,
                "modern_name": place.modern_name,
                "DynastyName": place.DynastyName,
                "Province": place.Province,
                "City": place.City,
                "District_County": place.District_County,
                "Specific_location": place.Specific_location,
                "source_text": place.source_text
            }

        def serialize_org(org):
            return {
                "OrgName": org.OrgName,
                "OrgType": org.OrgType,
                "DynastyName": org.DynastyName,
                "source_text": org.source_text
            }

        def serialize_person(person):
            return {
                "PersonName": person.PersonName,
                "DynastyName": person.DynastyName,
                "OrgName": person.OrgName,
                "Role": person.Role,
                "Note": person.Note,
                "source_text": person.source_text
            }

        def serialize_event(event):
            return {
                "EventName": event.EventName,
                "EventType": event.EventType,
                "StartDate": event.StartDate,
                "EndDate": event.EndDate,
                "DynastyName": event.DynastyName,
                "Place": event.Place,
                "Aggressor": event.Aggressor,
                "Defender": event.Defender,
                "Allies": event.Allies,
                "Result": event.Result,
                "Commanders": event.Commanders,
                "KeyPersons": event.KeyPersons,
                "Action": event.Action,
                "TroopSize": event.TroopSize,
                "Duration": event.Duration,
                "GeographicScope": event.GeographicScope,
                "Casualties": event.Casualties,
                "source": event.source,
                "Impact": event.Impact,
                "Remark": event.Remark,
                "relations": [{"type": r.type, "to": r.to, "evidence": r.evidence} for r in event.relations],
                "source_text": event.source_text
            }

        def serialize_relation(rel, rel_type):
            base = {
                "relation_type": rel_type,
                "evidence": getattr(rel, "evidence", None),
            }
            if rel_type == "event_place":
                base.update({
                    "EventName": rel.EventName,
                    "PlaceName": getattr(rel, "PlaceName", None),
                    "modern_name": getattr(rel, "modern_name", None),
                    "relation": rel.relation,
                })
            elif rel_type == "event_person":
                base.update({
                    "EventName": rel.EventName,
                    "PersonName": rel.PersonName,
                    "relation": rel.relation,
                })
            elif rel_type == "event_organization":
                base.update({
                    "EventName": rel.EventName,
                    "OrgName": rel.OrgName,
                    "relation": rel.relation,
                })
            elif rel_type == "event_event":
                base.update({
                    "EventName_A": rel.EventName_A,
                    "EventName_B": rel.EventName_B,
                    "relation": rel.relation,
                })
            return base

        response_data = {
            "entities": {
                "places": [serialize_place(p) for p in entities.places],
                "organizations": [serialize_org(o) for o in entities.organizations],
                "persons": [serialize_person(p) for p in entities.persons],
            },
            "events": [serialize_event(e) for e in event_result.events],
            "relations": {
                "event_place": [serialize_relation(r, "event_place") for r in relations.event_place_relations],
                "event_person": [serialize_relation(r, "event_person") for r in relations.event_person_relations],
                "event_organization": [serialize_relation(r, "event_organization") for r in relations.event_organization_relations],
                "event_event": [serialize_relation(r, "event_event") for r in relations.event_event_relations],
            },
            "summary": {
                "place_count": len(entities.places),
                "organization_count": len(entities.organizations),
                "person_count": len(entities.persons),
                "event_count": len(event_result.events),
                "relation_count": (
                    len(relations.event_place_relations) +
                    len(relations.event_person_relations) +
                    len(relations.event_organization_relations) +
                    len(relations.event_event_relations)
                ),
            },
            "process_time": round(process_time, 2)
        }

        return jsonify({
            "code": 200,
            "msg": "识别完成",
            "data": response_data
        })

    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e)
        print(f"文本实体识别失败: {error_type} - {error_msg}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "code": 500,
            "msg": f"识别失败: {error_msg}",
            "data": {}
        })


@app.route('/api/dataset/versions', methods=['GET'])
def dataset_versions():
    try:
        return jsonify({"code": 200, "data": build_dataset_versions()})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e), "data": []})


@app.route('/api/dataset/version_detail', methods=['GET'])
def dataset_version_detail():
    try:
        version_id = request.args.get("id", "")
        versions = build_dataset_versions()
        detail = next((item for item in versions if item["id"] == version_id), versions[0] if versions else {})
        return jsonify({"code": 200, "data": detail})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e), "data": {}})


@app.route('/user/menu', methods=['GET'])
def get_menu():
    """获取系统菜单。"""
    menu_data = [
        {
            "id": "/workspace/dashboard",
            "icon": "layui-icon-home",
            "title": "首页仪表盘"
        },
        {
            "id": "/knowledge/timeline",
            "icon": "layui-icon-date",
            "title": "历史时间轴"
        },
        {
            "id": "/knowledge/map",
            "icon": "layui-icon-location",
            "title": "历史地图视图"
        },
        {
            "id": "/knowledge",
            "icon": "layui-icon-set",
            "title": "知识图谱",
            "children": [
                {
                    "id": "/knowledge/graph",
                    "icon": "layui-icon-find-fill",
                    "title": "战争关系图"
                },
                {
                    "id": "/knowledge/graph/event",
                    "icon": "layui-icon-flag",
                    "title": "历史战争"
                },
                {
                    "id": "/knowledge/graph/organization",
                    "icon": "layui-icon-group",
                    "title": "参战势力"
                },
                {
                    "id": "/knowledge/graph/place",
                    "icon": "layui-icon-location",
                    "title": "战争地点"
                },
                {
                    "id": "/knowledge/graph/person",
                    "icon": "layui-icon-user",
                    "title": "历史人物"
                },
                {
                    "id": "/knowledge/inference",
                    "icon": "layui-icon-engine",
                    "title": "历史问答助手"
                },
                {
                    "id": "/knowledge/text-extract",
                    "icon": "layui-icon-read",
                    "title": "文本实体识别"
                },
                {
                    "id": "/knowledge/relation-analysis",
                    "icon": "layui-icon-chart",
                    "title": "关系分析"
                },
                {
                    "id": "/knowledge/search",
                    "icon": "layui-icon-search",
                    "title": "全局搜索"
                },
                {
                    "id": "/knowledge/entity-detail",
                    "icon": "layui-icon-read",
                    "title": "实体详情页"
                }
            ]
        },
        {
            "id": "/workspace/manage",
            "icon": "layui-icon-console",
            "title": "数据运营",
            "children": [
                {
                    "id": "/workspace/dataset",
                    "icon": "layui-icon-template-1",
                    "title": "数据集中心"
                },
                {
                    "id": "/knowledge-list",
                    "icon": "layui-icon-fonts-code",
                    "title": "数据维护"
                },
                {
                    "id": "/workspace/quality",
                    "icon": "layui-icon-vercode",
                    "title": "图谱质检"
                },
                {
                    "id": "/workspace/repair",
                    "icon": "layui-icon-vercode",
                    "title": "数据修复工作台"
                },
                {
                    "id": "/workspace/dataset-versions",
                    "icon": "layui-icon-list",
                    "title": "数据版本管理"
                }
            ]
        }
    ]

    return jsonify({
        "code": 200,
        "data": menu_data
    })


@app.route('/user/permission', methods=['GET'])
def get_permission():
    """返回前端菜单权限占位数据。"""
    return jsonify({
        "code": 200,
        "data": []
    })


# ================== 应用生命周期管理 ==================

def graceful_shutdown():
    """优雅关闭"""
    print("\n🛑 正在关闭应用...")
    print("✅ 应用已安全关闭")


atexit.register(graceful_shutdown)

# ================== 启动应用 ==================

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("🌐 启动历史地名知识图谱系统")
    print("=" * 60)
    print("📍 访问地址: http://localhost:5000")
    print("💾 主存储: SQLite 关系型数据库")
    print("🔄 可视化: Neo4j 图数据库")
    print("=" * 60 + "\n")

    app.run(debug=True, port=5000, host='0.0.0.0')
