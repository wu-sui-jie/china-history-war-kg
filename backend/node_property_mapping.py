"""SQLite ↔ Neo4j 字段映射的单一来源。

背景：同一套映射若在多个地方各维护一份——`db_utils._field_mapping_by_type`（API 键 → 列）、
`db_utils._neo4j_props`（列 → Neo4j 属性）、`sync_sqlite_to_neo4j` 的四个 `props()`（列 → Neo4j 属性）。
任一处新增字段都要改三遍，且两侧对不上时不会报错，只会让图谱属性悄悄缺字段。

本模块只描述映射关系，不含任何业务逻辑；改字段请只改这里。
"""

from __future__ import annotations

from common_utils import repair_mojibake_props

# ---- API 键 → SQLite 列名（查询接口的入参用 API 键，落库用列名）----
# 未命中的键按 `key.lower()` 兜底（见 db_utils._normalize_payload）。
API_TO_COLUMN = {
    "Event": {
        "EventName": "name",
        "EventType": "event_type",
        "StartDate": "start_date",
        "EndDate": "end_date",
        "DynastyName": "dynasty",
        "Place": "place",
        "Aggressor": "aggressor",
        "Defender": "defender",
        "KeyPersons": "person",
        "Action": "action",
        "Result": "result",
        "TroopSize": "scale",
        "Impact": "impact",
        "source_text": "source",
        "Remark": "remark",
        "relations": "relations",
    },
    "Place": {
        "geo_name": "name",
        "DynastyName": "dynasty",
        "Province": "province",
        "City": "city",
        "District_County": "district",
        "Specific_location": "specific_location",
        "Specific_Location": "specific_location",
        "modern_name": "modern_name",
        "ModernName": "modern_name",
        "longitude": "longitude",
        "latitude": "latitude",
        "coord_source": "coord_source",
        "coord_confidence": "coord_confidence",
        "coord_note": "coord_note",
    },
    "Organization": {
        "OrgName": "name",
        "OrgType": "org_type",
        "DynastyName": "dynasty",
        "Description": "description",
        "Remark": "remark",
    },
    "Person": {
        "PersonName": "name",
        "DynastyName": "dynasty",
        "OrgName": "org",
        "Role": "role",
        "Remark": "remark",
    },
}

# ---- SQLite 列名 → Neo4j 属性名 ----
# 注意名称字段的历史不一致（**刻意按原样保留**，见 backend/README 的字段说明）：
#   Place / Organization / Person 的名称随属性一起写（geo_name / OrgName / PersonName），
#   Event 的映射里**没有**名称项——创建路径靠 create_node(label, name) 单独写小写的 `name`，
#   同步脚本另写 `EventName`。统一属性名要连着前端读取与已入库的数据一起改，不在映射收敛范围内。
# NAME_TO_NEO4J 用于「映射里缺名称项」的类型（当前只有 Event）在同步链路上补写。
COLUMN_TO_NEO4J = {
    "Event": {
        "event_type": "EventType",
        "start_date": "StartDate",
        "end_date": "EndDate",
        "dynasty": "DynastyName",
        "place": "Place",
        "aggressor": "Aggressor",
        "defender": "Defender",
        "person": "KeyPersons",
        "action": "Action",
        "result": "Result",
        "scale": "TroopSize",
        "impact": "Impact",
        "source": "source_text",
        "relations": "relations",
        "remark": "Remark",
    },
    "Place": {
        "name": "geo_name",
        "modern_name": "modern_name",
        "dynasty": "DynastyName",
        "province": "Province",
        "city": "City",
        "district": "District_County",
        "specific_location": "Specific_location",
        "longitude": "longitude",
        "latitude": "latitude",
        "coord_source": "coord_source",
        "coord_confidence": "coord_confidence",
        "coord_note": "coord_note",
    },
    "Organization": {
        "name": "OrgName",
        "org_type": "OrgType",
        "dynasty": "DynastyName",
        "description": "Description",
        "remark": "Remark",
    },
    "Person": {
        "name": "PersonName",
        "dynasty": "DynastyName",
        "org": "OrgName",
        "role": "Role",
        "remark": "Remark",
    },
}

# 名称字段（SQLite 的 name 列）在各类型下写入 Neo4j 的属性名
NAME_TO_NEO4J = {
    "Event": "EventName",
    "Place": "geo_name",
    "Organization": "OrgName",
    "Person": "PersonName",
}

# 同步脚本额外写入的固定属性：图谱侧的展示用标签（create/update 路径不写）
SYNC_FIXED_PROPS = {
    "Event": {"type": "战争事件"},
}


def orm_row(obj, node_type: str) -> dict:
    """从 ORM 对象取出映射需要的列值（{列名: 值}）。"""
    columns = list(COLUMN_TO_NEO4J.get(node_type, {}).keys()) + ["name"]
    return {column: getattr(obj, column, None) for column in columns}


def neo4j_props(node_type: str, column_values: dict, include_name: bool = False,
                fixed: dict | None = None) -> dict:
    """把 {SQLite 列名: 值} 转成 {Neo4j 属性名: 值}。

    include_name：是否把名称字段一起写进去。create/update 路径的名称由单独参数写，
    同步脚本要随属性一起写。
    fixed：额外固定属性（如 Event 的展示标签 `type`）。

    写图谱前统一做编码复原：本函数是同步/建图路径上唯一的属性出口，
    乱码在这里修掉，前端就不必再维护"乱码 key → 正常 key"的兼容映射。
    """
    props = dict(fixed or {})
    if include_name:
        name_key = NAME_TO_NEO4J.get(node_type)
        if name_key:
            props[name_key] = (column_values or {}).get("name")
    for column, neo4j_key in COLUMN_TO_NEO4J.get(node_type, {}).items():
        props[neo4j_key] = (column_values or {}).get(column)
    return repair_mojibake_props(props)
