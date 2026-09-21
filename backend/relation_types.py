"""事件-事件关系类型的唯一权威表。

原先导入（import_json_to_sqlite）、同步（sync_sqlite_to_neo4j）、
图谱查询（model_search）三处各维护一份别名表，任一处新增关系类型都要改三遍。
此处收敛为一份，其余模块一律 import 这里。
"""

# 标准名 -> 该标准名下的全部写法（含标准名自身），供按别名查询时展开
EVENT_RELATION_TYPE_ALIASES = {
    "因果关系": ["因果关系", "因果"],
    "顺承关系": ["顺承关系", "顺承"],
    "并列关系": ["并列关系", "并列", "并发", "并发关系"],
    "包含关系": ["包含关系", "包含"],
    "条件关系": ["条件关系", "条件"],
}

# 别名 -> 标准名，供写入端归一
EVENT_RELATION_TYPE_CANONICAL = {
    alias: standard
    for standard, aliases in EVENT_RELATION_TYPE_ALIASES.items()
    for alias in aliases
}


def relationship_type_aliases(rel_type):
    """把任意写法展开成同义写法列表；未知类型原样返回单元素列表。"""
    rel_type = str(rel_type).strip() if rel_type is not None else ""
    standard = EVENT_RELATION_TYPE_CANONICAL.get(rel_type)
    if not standard:
        return [rel_type] if rel_type else []
    return list(EVENT_RELATION_TYPE_ALIASES[standard])


def normalize_event_relation_type(value):
    """统一事件-事件关系类型，保持与前端筛选枚举一致。"""
    value = str(value).strip() if value is not None else ""
    return EVENT_RELATION_TYPE_CANONICAL.get(value, value)
