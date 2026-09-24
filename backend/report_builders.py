"""报表构建器：把 SQLite/Neo4j 的原始数据装配成前端要用的结构（P2-1 拆分第一步）。

原先这些函数与路由、LLM 流水线一起挤在 `app.py`（3800+ 行）里。本模块只负责
"读数据 → 组装响应"，不含路由与写操作：

- 仪表盘 / 数据集概览 / 数据集版本
- 图谱质检报告与工作台（含 SQLite↔Neo4j 计数对账）
- 时间轴、地图总览、实体详情
- 全局搜索、质量标记与坐标补全等辅助

依赖方向：本模块 → models / common_utils / dynasty_data / db_handle。
**不得反向 import app**（否则形成循环）；`neo4j_db_handle` 从 `db_handle` 取。

拆分的边界是用 AST 算出来的：这 33 个函数（11 个 `build_*` + 22 个私有辅助）
只被彼此调用，没有任何一个被路由或 LLM 流水线引用——所以可以整块搬走而不改行为。
行为一致性用 `tools/snapshot_responses.py` 对照验证（37 个请求逐字段 diff）。
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter

from sqlalchemy import func, or_, text

from common_utils import safe_identifier, safe_text as _safe_text
from db_handle import neo4j_db_handle
from dynasty_data import DYNASTY_ALIAS_MAP, DYNASTY_DISPLAY_ORDER
from logging_util import get_logger
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

# 与 app.py 的同名常量语义一致：都指向 backend/ 目录
APP_PATH = os.path.dirname(os.path.abspath(__file__))

logger = get_logger(__name__)

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
def load_current_dataset_meta():
    """读取当前数据集元信息，供工作台页面复用。"""
    meta_path = os.path.join(APP_PATH, 'data', 'current_dataset.json')
    if not os.path.exists(meta_path):
        return {}

    try:
        with open(meta_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as exc:
        logger.warning(f"读取当前数据集元信息失败: {exc}")
        return {}
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
def _exists(relation_model, condition):
    """关系表里是否存在满足条件的行（供 NOT EXISTS 下推用）。"""
    return db.session.query(relation_model).filter(condition).exists()


def _isolated_filter(model):
    """「在该模型对应的关系表里没有任何引用」的 SQL 条件。

    Event 出现在事件-事件的两侧，以及事件-地点/人物/组织的 event_id 上；
    Place / Person / Organization 各自对应一张关系表。
    """
    if model is Event:
        return ~or_(
            _exists(EventEventRelation, EventEventRelation.event_a_id == Event.id),
            _exists(EventEventRelation, EventEventRelation.event_b_id == Event.id),
            _exists(EventPlaceRelation, EventPlaceRelation.event_id == Event.id),
            _exists(EventPersonRelation, EventPersonRelation.event_id == Event.id),
            _exists(EventOrganizationRel, EventOrganizationRel.event_id == Event.id),
        )
    if model is Place:
        return ~_exists(EventPlaceRelation, EventPlaceRelation.place_id == Place.id)
    if model is Person:
        return ~_exists(EventPersonRelation, EventPersonRelation.person_id == Person.id)
    if model is Organization:
        return ~_exists(EventOrganizationRel, EventOrganizationRel.org_id == Organization.id)
    raise ValueError(f"不支持的节点类型: {model}")


def _isolated_node_rows(limit=20):
    """找出未参与任何结构化关系的孤立节点。

    P2-5：原实现把 8 张关系表的全部外键列与四类节点全量拉进 Python 再做集合差
    （本库约 1.7 万关系行 + 9925 个节点对象，每次质检请求都要重来一遍），
    这里下推到数据库侧用 NOT EXISTS 判断，LIMIT 也提前生效。
    顺序显式按 id 升序，保证返回稳定（原实现依赖 rowid 顺序）。
    """
    isolated_rows = []
    for model, type_name in ((Event, "Event"), (Place, "Place"),
                             (Person, "Person"), (Organization, "Organization")):
        remaining = limit - len(isolated_rows)
        if remaining <= 0:
            break
        rows = (
            model.query
            .filter(_isolated_filter(model))
            .order_by(model.id.asc())
            .limit(remaining)
            .all()
        )
        isolated_rows.extend(
            {"id": node.id, "name": node.name, "type": type_name} for node in rows
        )
    return isolated_rows


def _count_all_isolated_nodes():
    """统计全部孤立节点数量（SQL 侧 COUNT，不再拉全量节点做差集）。"""
    return sum(
        model.query.filter(_isolated_filter(model)).count()
        for model in (Event, Place, Person, Organization)
    )


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
    """单个地点的关联事件数。**批量场景不要用它**（N+1），改用 `_place_event_counts()`。"""
    return EventPlaceRelation.query.filter(EventPlaceRelation.place_id == place_id).count()


def _place_event_counts():
    """一次查出所有地点的关联事件数：{place_id: 行数}。

    原先 `_coordinate_issue_rows` 在 5316 个地点的循环里逐个 COUNT（P2-5 的 N+1 热点），
    这里换成一次 GROUP BY。用 count(id) 而不是 count(distinct event_id)，与原实现
    的行数口径保持一致。
    """
    return dict(
        db.session.query(EventPlaceRelation.place_id, func.count(EventPlaceRelation.id))
        .group_by(EventPlaceRelation.place_id)
        .all()
    )
def _coordinate_issue_rows(issue_type, limit=20):
    rows = []
    # 一次预取所有地点的计数，避免在循环里逐个查询
    place_event_counts = _place_event_counts()
    for place in Place.query.order_by(Place.id.asc()).all():
        related_event_count = place_event_counts.get(place.id, 0)
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
def _quality_flags_for_record(node_type, record, duplicate_count=None, relation_count=None):
    """单条记录的质量标记。

    两个计数字段默认各查一次库，逐条调用就是 N+1。总览类构建器请先用
    `_bulk_event_quality_flags()` 预取，再把结果传进来（`None` 表示"没预取，自己查"）。
    """
    missing_fields = []
    for field in _required_fields_by_type(node_type):
        if _safe_text(getattr(record, field, None)) == '':
            missing_fields.append(field)

    if duplicate_count is None:
        duplicate_count = 0
        model = _model_for_type(node_type)
        if model and _safe_text(getattr(record, 'name', None)):
            duplicate_count = model.query.filter(model.name == record.name).count()

    if relation_count is None:
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
def _event_brief(event, quality_flags=None):
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
        "quality_flags": quality_flags if quality_flags is not None else _quality_flags_for_record("Event", event),
    }


# ================== 批量取数（总览类构建器专用）==================
# 总览接口要遍历几百个地点、上千个事件，逐条查询就是 N+1：改动前实测
# build_map_overview 无过滤时 6313 条 SQL / 2.68s、build_timeline_overview 5251 条 /
# 2.16s，而 SQL 真正执行的部分只占 0.35s —— 开销在每条语句的 ORM 往返上，不在数据库。
#
# 下面这些函数把"逐条查"换成"一次查完、在 Python 里分组"，口径与原单条函数逐字段对齐
# （含 limit 截断与关系两侧去重），因此输出不变；用 tools/profile_overviews.py 可复查
# 语句数与耗时。

_IN_CHUNK = 400  # SQLite 绑定变量有上限，in_() 分批，避免一次塞进上千个参数


def _chunks(values, size=_IN_CHUNK):
    values = list(values)
    for index in range(0, len(values), size):
        yield values[index:index + size]


def _count_by_column(model, key_column, ids, extra_filter=None):
    """{key: 行数}：对 model 按 key_column 分组计数，分批合并。"""
    counts = {}
    for chunk in _chunks(ids):
        query = db.session.query(key_column, func.count(model.id)).filter(key_column.in_(chunk))
        if extra_filter is not None:
            query = query.filter(extra_filter)
        for key, count in query.group_by(key_column).all():
            counts[key] = counts.get(key, 0) + count
    return counts


def _bulk_duplicate_counts(model, names):
    """{name: 同名条数}：一次 GROUP BY 取代逐条的 .count()。"""
    return _count_by_column(model, model.name, {name for name in names if _safe_text(name)})


def _bulk_event_relation_counts(event_ids):
    """{event_id: 关系条数}，口径同 `_related_entities_for_node('Event', id, limit=999)`。

    即：四张关系表各自先按 999 截断，求和后再整体截断一次 999。事件-事件表两侧都要算，
    a == b 的自环行按原实现只算一次。
    """
    ids = sorted(set(event_ids))
    counts = {event_id: 0 for event_id in ids}
    if not ids:
        return counts

    place_counts = _count_by_column(EventPlaceRelation, EventPlaceRelation.event_id, ids)
    person_counts = _count_by_column(EventPersonRelation, EventPersonRelation.event_id, ids)
    org_counts = _count_by_column(EventOrganizationRel, EventOrganizationRel.event_id, ids)
    a_counts = _count_by_column(EventEventRelation, EventEventRelation.event_a_id, ids)
    b_counts = _count_by_column(EventEventRelation, EventEventRelation.event_b_id, ids)
    self_counts = _count_by_column(
        EventEventRelation, EventEventRelation.event_a_id, ids,
        extra_filter=EventEventRelation.event_a_id == EventEventRelation.event_b_id,
    )

    for event_id in ids:
        event_event_rows = (a_counts.get(event_id, 0) + b_counts.get(event_id, 0)
                            - self_counts.get(event_id, 0))
        total = (min(place_counts.get(event_id, 0), 999)
                 + min(person_counts.get(event_id, 0), 999)
                 + min(org_counts.get(event_id, 0), 999)
                 + min(event_event_rows, 999))
        counts[event_id] = min(total, 999)
    return counts


def _bulk_event_quality_flags(events):
    """{event_id: quality_flags}：先把计数批量查好，再逐条套用同一套标记逻辑。"""
    events = [event for event in events if event is not None]
    if not events:
        return {}
    ids = [event.id for event in events]
    duplicate_counts = _bulk_duplicate_counts(Event, [event.name for event in events])
    relation_counts = _bulk_event_relation_counts(ids)
    return {
        event.id: _quality_flags_for_record(
            "Event", event,
            duplicate_count=duplicate_counts.get(event.name, 0),
            relation_count=relation_counts.get(event.id, 0),
        )
        for event in events
    }


def _events_by_ids(event_ids):
    """一次取回一批事件（排序保证跨批次的顺序稳定）。"""
    events = []
    for chunk in _chunks(sorted({event_id for event_id in event_ids if event_id is not None})):
        events.extend(Event.query.filter(Event.id.in_(chunk)).all())
    return events


def _bulk_event_participants(event_ids):
    """{event_id: {"persons": [], "organizations": [], "related_events": []}}，同 `_event_participants`。

    三类参与方各一次查询（原先每个事件三次），每个事件仍按原顺序截断 12 / 12 / 10 条。
    """
    ids = sorted({event_id for event_id in event_ids if event_id is not None})
    bundles = {
        event_id: {"persons": [], "organizations": [], "related_events": []}
        for event_id in ids
    }
    if not ids:
        return bundles

    for chunk in _chunks(ids):
        for row in (EventPersonRelation.query
                    .filter(EventPersonRelation.event_id.in_(chunk))
                    .order_by(EventPersonRelation.id.asc()).all()):
            persons = bundles[row.event_id]["persons"]
            if len(persons) >= 12:
                continue
            persons.append({
                "id": row.person_id,
                "name": row.person_name,
                "relation_type": row.relation_type,
                "detail_route": f"/knowledge/entity-detail?type=Person&id={row.person_id}",
            })

        for row in (EventOrganizationRel.query
                    .filter(EventOrganizationRel.event_id.in_(chunk))
                    .order_by(EventOrganizationRel.id.asc()).all()):
            organizations = bundles[row.event_id]["organizations"]
            if len(organizations) >= 12:
                continue
            organizations.append({
                "id": row.org_id,
                "name": row.org_name,
                "relation_type": row.relation_type,
                "detail_route": f"/knowledge/entity-detail?type=Organization&id={row.org_id}",
            })

        event_event_rows = (EventEventRelation.query
                            .filter((EventEventRelation.event_a_id.in_(chunk))
                                    | (EventEventRelation.event_b_id.in_(chunk)))
                            .order_by(EventEventRelation.id.asc()).all())
        # 两趟填：先 a 侧（出边）再 b 侧（入边）。单事件查询的计划是 MULTI-INDEX OR
        # （扫 idx_eea_event_a 再扫 idx_eea_event_b，各自 rowid 升序），顺序会影响 10 条
        # 截断的结果，因此这里按同一次序复刻；只在 a 侧跳过自环（a == b），让它在 b 侧
        # 落一次，与原实现"一行只出现一次"一致。
        for row in event_event_rows:
            if row.event_a_id == row.event_b_id:
                continue
            bundle = bundles.get(row.event_a_id)
            if bundle is None or len(bundle["related_events"]) >= 10:
                continue
            bundle["related_events"].append({
                "id": row.event_b_id,
                "name": row.event_b_name,
                "relation_type": row.relation_type,
                "detail_route": f"/knowledge/entity-detail?type=Event&id={row.event_b_id}",
            })
        for row in event_event_rows:
            bundle = bundles.get(row.event_b_id)
            if bundle is None or len(bundle["related_events"]) >= 10:
                continue
            bundle["related_events"].append({
                "id": row.event_a_id,
                "name": row.event_a_name,
                "relation_type": row.relation_type,
                "detail_route": f"/knowledge/entity-detail?type=Event&id={row.event_a_id}",
            })
    return bundles


def _event_place_relations_by_place(place_ids):
    """{place_id: [关系行]}：按 place_id 分组的关系行，行序与原逐地点查询一致（rowid 升序）。"""
    grouped = {}
    for chunk in _chunks(place_ids):
        for row in (EventPlaceRelation.query
                    .filter(EventPlaceRelation.place_id.in_(chunk))
                    .order_by(EventPlaceRelation.id.asc()).all()):
            grouped.setdefault(row.place_id, []).append(row)
    return grouped


def _event_place_relations_by_event(event_ids):
    """{event_id: [关系行]}：按 event_id 分组的关系行，行序与原逐事件查询一致（rowid 升序）。"""
    grouped = {}
    for chunk in _chunks(event_ids):
        for row in (EventPlaceRelation.query
                    .filter(EventPlaceRelation.event_id.in_(chunk))
                    .order_by(EventPlaceRelation.id.asc()).all()):
            grouped.setdefault(row.event_id, []).append(row)
    return grouped
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

    # 一次取回事件与质量标记：原先是逐个 Event.get + 每条 brief 各查一次
    events_by_id = {event.id: event for event in _events_by_ids(event_ids)}
    # event_ids 是集合，按它的迭代顺序取事件，排序稳定性与原实现一致
    ordered_events = [events_by_id[event_id] for event_id in event_ids if event_id in events_by_id]
    flags_by_event = _bulk_event_quality_flags(ordered_events)

    rows = [_event_brief(event, flags_by_event.get(event.id)) for event in ordered_events]
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
    # 批量预取本页地点要用的关系行、事件、参与方与质量标记（原先全是逐条查询）
    relations_by_place = _event_place_relations_by_place([place.id for place in places])
    related_event_ids = {row.event_id for rows in relations_by_place.values() for row in rows}
    events_by_id = {event.id: event for event in _events_by_ids(related_event_ids)}
    participants_by_event = _bulk_event_participants(related_event_ids)
    flags_by_event = _bulk_event_quality_flags(events_by_id.values())
    points = []
    dynasty_counter = Counter()
    all_event_ids = set()
    mappable_event_ids = set()
    event_point_map = {}
    unmapped_places = []

    for place in places:
        coord_result = _resolve_place_coordinates(place)
        coords = coord_result["coords"] if coord_result else None
        related_rows = relations_by_place.get(place.id, [])
        event_ids = []
        route_segments = []
        related_events = []
        entity_counter = {
            "persons": {},
            "organizations": {},
            "related_events": {},
        }
        for row in related_rows:
            event = events_by_id.get(row.event_id)
            if not event:
                continue
            if dynasty and _normalize_dynasty_name(event.dynasty) != _normalize_dynasty_name(dynasty):
                continue
            all_event_ids.add(event.id)
            event_ids.append(event.id)
            participant_bundle = participants_by_event.get(
                event.id, {"persons": [], "organizations": [], "related_events": []})
            event_brief = _event_brief(event, flags_by_event.get(event.id))
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

    # 批量预取路由段要用的关系行与地点（原先每个事件一条关系查询、每条关系一次 Place.get）
    route_events = event_query.limit(500).all()
    route_relations_by_event = _event_place_relations_by_event([event.id for event in route_events])
    route_place_ids = {
        row.place_id
        for rows in route_relations_by_event.values()
        for row in rows
        if not allowed_place_ids or row.place_id in allowed_place_ids
    }
    route_places_by_id = {}
    for chunk in _chunks(sorted(place_id for place_id in route_place_ids if place_id is not None)):
        for place in Place.query.filter(Place.id.in_(chunk)).all():
            route_places_by_id[place.id] = place

    for event in route_events:
        for row in route_relations_by_event.get(event.id, []):
            if allowed_place_ids and row.place_id not in allowed_place_ids:
                continue
            place = route_places_by_id.get(row.place_id)
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

    summary = dict(report.get("summary", {}))
    reconciliation = report.get("sync_reconciliation") or {}
    # 把「SQLite 与 Neo4j 计数不一致的类型数」提到 summary，前端一眼可见
    summary["sync_mismatch"] = sum(
        1 for item in reconciliation.get("counts", []) if item.get("status") == "mismatch"
    )

    return {
        "summary": summary,
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
    kept_events = []
    for event in events.all():
        if participant and event.id not in participant_event_ids:
            continue
        kept_events.append(event)

    # 质量标记一次批量算好再逐条套用（原先每条 brief 都要查重复名与关系数）
    flags_by_event = _bulk_event_quality_flags(kept_events)
    for event in kept_events:
        parsed_year = _parse_year_value(event.start_date) or _parse_year_value(event.end_date)
        item = _event_brief(event, flags_by_event.get(event.id))
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
def build_sync_reconciliation():
    """对比 SQLite（主存储）与 Neo4j（可视化副本）两侧的节点计数。

    双写没有自动补偿机制：SQLite 提交成功而 Neo4j 失败时，只在单次响应里
    返回 sync_status=failed，缺口无人发现会静默累积。这里把两侧计数差异
    暴露到质检接口，让不一致可见。
    """
    counts = []
    consistent = True
    for label, model in [("Event", Event), ("Place", Place), ("Organization", Organization), ("Person", Person)]:
        sqlite_count = db.session.query(func.count(model.id)).scalar() or 0
        neo4j_count = None
        error = None
        try:
            result = neo4j_db_handle.graph.run(
                f"MATCH (n:`{safe_identifier(label, kind='节点标签')}`) RETURN count(n) AS c"
            ).data()
            neo4j_count = result[0]["c"] if result else 0
        except Exception as exc:
            error = str(exc)

        diff = None if neo4j_count is None else sqlite_count - neo4j_count
        # None（Neo4j 不可达，判不了）与非零差异一样不能算"已对齐"，
        # 否则对账在最需要报信的失败时刻反而显示一致。
        if diff != 0:
            consistent = False
        counts.append({
            "type": label,
            "sqlite": sqlite_count,
            "neo4j": neo4j_count,
            "diff": diff,
            "status": "ok" if diff == 0 else ("unknown" if error else "mismatch"),
            "error": error,
        })

    return {
        "consistent": consistent,
        "counts": counts,
        "note": "diff = SQLite - Neo4j。不一致时用 python sync_sqlite_to_neo4j.py（必要时 --mode full）重建副本。",
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
        # 双侧计数对账：把 SQLite 与 Neo4j 的差异显式暴露出来
        "sync_reconciliation": build_sync_reconciliation(),
    }