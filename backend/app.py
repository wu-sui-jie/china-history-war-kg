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
import sqlite3
import time
import uuid
import atexit
import functools
import traceback

# ================== Flask核心模块 ==================
from flask import Flask, request, jsonify, g, Response
from flask_cors import CORS
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlalchemy import text

# ================== 自定义模块 ==================
import local_settings
# LLM 流水线：模型调用、抽取链、问答编排（P2-1 第二步）
import llm_pipeline
# 报表构建器与领域常量已拆到独立模块（P2-1 第一步）
from db_handle import neo4j_db_handle
from report_builders import (
    build_dashboard_overview,
    build_dataset_overview,
    build_dataset_versions,
    build_entity_detail,
    build_global_search,
    build_map_overview,
    build_quality_report,
    build_quality_workbench,
    build_timeline_overview,
)
from db_utils import DbUtil
from jwt_util import TokenError, decode, encode
# models 由 db_utils / report_builders 在模块级导入，ORM 类在建表前已注册
from models import EventPlaceRelation
from logging_util import get_logger

# ================== 创建Flask应用 ==================
app = Flask(__name__)

# CORS：只放行本机开发用的前端来源。
# 生产是 nginx 同源反代，浏览器根本不发跨域请求，因此不需要对任意站点开放——
# 原先的 CORS(app) 允许任何来源带 Token 调写接口。局域网联调时用
# CORS_ALLOW_ORIGINS 显式追加来源（逗号分隔，或 '*' 表示不限制）。
_cors_origins = [
    origin.strip()
    for origin in local_settings.get(
        "CORS_ALLOW_ORIGINS", "http://localhost:3001,http://127.0.0.1:3001"
    ).split(",")
    if origin.strip()
]
CORS(app, resources={r"/*": {"origins": _cors_origins}})
# 请求身份统一存放在 flask.g.user_id（按请求隔离）；
# 历史上用模块级全局变量承载，多线程下会串号，已废弃。

# ================== 数据库配置 ==================
APP_PATH = os.path.dirname(os.path.abspath(__file__))

logger = get_logger(__name__)

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
        logger.warning(f"读取事件-地点关系证据失败: {exc}")
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
        logger.info(f"已回填事件-地点关系证据 {updated} 条")


def ensure_user_table_schema():
    """为旧库补 UserInfo.role 列、回填存量角色并补 account 唯一索引。"""
    from models import db

    rows = db.session.execute(text("PRAGMA table_info(UserInfo)")).fetchall()
    if not rows:
        # 表尚未创建（create_all 还没跑到）时无需处理
        return
    existing_columns = {row[1] for row in rows}
    if "role" not in existing_columns:
        db.session.execute(text("ALTER TABLE UserInfo ADD COLUMN role VARCHAR(32)"))
    # 角色模型引入前建的账号都是本机管理员手工创建的，统一回填为 admin，
    # 避免升级后原有账号立刻变成只读。新注册账号一律 viewer。
    db.session.execute(text("UPDATE UserInfo SET role = 'admin' WHERE role IS NULL OR role = ''"))
    try:
        # 唯一约束的最终防线（并发注册）；原表没有该约束，用唯一索引补上
        db.session.execute(
            text("CREATE UNIQUE INDEX IF NOT EXISTS idx_userinfo_account ON UserInfo(account)")
        )
    except Exception as exc:
        # 已存在重复账号时索引建不上：保留告警，人工清理后重启即可自动重建
        logger.warning(f"⚠️ UserInfo.account 唯一索引创建失败（可能存在重复账号）: {exc}")
    db.session.commit()


# ================== SQLite PRAGMA（每连接生效） ==================
# PRAGMA 是 per-connection 的：只在启动时执行一次，NullPool 下新连接全部回到
# 默认值（journal_mode=delete / synchronous=FULL），README 宣称的 WAL 并发并不存在。
# 用 connect 事件钩子保证每个新连接都设置。
@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
    finally:
        cursor.close()


# 启动时的结构迁移与数据回填（PRAGMA 已由上面的 connect 钩子负责）
with app.app_context():
    from models import db

    try:
        ensure_event_place_relation_metadata_columns()
        ensure_place_coordinate_metadata_columns()
        backfill_event_place_relation_evidence()
        ensure_user_table_schema()
        db.session.commit()
        logger.info("✅ SQLite schema 检查完成（WAL 模式由连接钩子设置）")
    except Exception as e:
        logger.warning(f"⚠️ SQLite schema 检查失败: {e}")


# ================== 初始化函数 ==================









# ================== 初始化函数 ==================











































@app.before_request
def initialize_entity_extractor():
    """把进程内单例（实体提取器 / 规则推理引擎）挂到 flask.g。

    单例的构建与加锁都搬到了 llm_pipeline.get_shared_extractors()；这里只做请求级绑定：
    flask.g 按请求隔离，多线程下不会串号。两个单例都可能为 None（初始化失败），
    此时路由自己会按请求临时建一份。
    """
    extractor, rule_llm = llm_pipeline.get_shared_extractors()
    if extractor is not None:
        g.entity_extractor = extractor
    if rule_llm is not None:
        g.rule_llm_integration = rule_llm


# 生成 jieba 用户词典（实现已随 LLM 流水线拆到 llm_pipeline）
llm_pipeline.init_user_dict()

# 预加载jieba分词用户词典
try:
    import jieba

    dict_path = os.path.join(APP_PATH, 'historical_places.txt')
    if os.path.exists(dict_path):
        jieba.load_userdict(dict_path)
        logger.info(f"成功加载历史地名词典: {dict_path}")
    else:
        logger.info(f"警告：历史地名词典不存在: {dict_path}")
except ImportError:
    logger.info("未找到jieba分词库，跳过用户词典加载")
except Exception as e:
    logger.warning(f"加载用户词典失败: {str(e)}")


# ================== 权限拦截器 ==================

# 放行路径：登录、注册与静态资源
PASS_URLS = {"/", "/api/login", "/api/sign_in"}

# 可写数据的角色；注册接口一律建 viewer（只读）
WRITE_ROLES = {"admin", "editor"}

# 允许「全图加载」的节点数上限：超过就退回限量加载。
# 全量分支没有分页，节点数上万时单次请求的响应体与前端渲染开销都会失控。
MAX_LOAD_ALL_NODES = 3000


def require_write_role(view):
    """写接口鉴权：只读角色（viewer）不允许改数据。"""
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        role = DbUtil.get_role(getattr(g, "user_id", None))
        if role not in WRITE_ROLES:
            return jsonify({
                "code": 403,
                "msg": "当前账号为只读权限，无法执行该操作"
            }), 403
        return view(*args, **kwargs)
    return wrapper


@app.before_request
def before():
    """全局请求拦截器（权限校验）"""
    url = request.path

    if url.startswith("/static") or url in PASS_URLS:
        return None

    token = request.headers.get('Token')

    if not token:
        return jsonify({
            "code": 401,
            "msg": "您还未登录，请先登录"
        }), 401

    try:
        payload = decode(token)
    except TokenError as exc:
        # 过期 / 伪造 token 按未认证处理（原先直接抛异常返回 500）
        return jsonify({"code": 401, "msg": str(exc)}), 401

    # 请求身份放 flask.g，按请求隔离；模块级全局变量在多线程下会串号
    g.user_id = payload.get('user_id')
    return None


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
    result = handler.find_user(getattr(g, "user_id", None))
    return jsonify({
        "code": 200,
        "data": result
    })


@app.route('/api/sign_in', methods=['POST'])
def sign_in():
    data = request.get_json()
    handler = DbUtil()
    result = handler.add_user(data)
    if result.get("code") == 200:
        token = encode(result["data"]["user_id"])
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
    # 是否全图加载：默认关。全量分支会 `MATCH (n) RETURN n` 拉全部节点与关系且没有上限，
    # 大图上单次请求就能吃掉大量内存与带宽（BE-8）。显式要求时也要先看规模。
    load_all = bool(data.get('load_all', False))

    try:
        if not entity and not node_type and not rel_type:
            if load_all:
                node_total = neo4j_db_handle.count_nodes()
                if node_total > MAX_LOAD_ALL_NODES:
                    logger.warning(
                        "拒绝全图加载：节点数 %s 超过上限 %s，改为限量加载（如需全图请用图形库前端的聚焦/分页）",
                        node_total, MAX_LOAD_ALL_NODES,
                    )
                    load_all = False
            json_data = neo4j_db_handle.get_default_graph(limit=50, load_all=load_all)
            logger.info(f"使用默认图谱加载方式, {'加载全部' if load_all else '加载部分'}")
        else:
            if entity and node_type and not rel_type:
                json_data = neo4j_db_handle.search_by_name_and_type(entity, node_type)
                logger.info("名称+类型组合查询")
            elif entity and rel_type:
                json_data = neo4j_db_handle.search_by_name_and_relation(entity, rel_type)
                logger.info(f"组合查询：名称+关系 {entity} + {rel_type}")
            elif entity:
                json_data = neo4j_db_handle.search_nodes_by_name(entity)
                logger.info(f"按实体名称'{entity}'搜索")
            elif node_type:
                json_data = neo4j_db_handle.get_nodes_by_type(node_type)
                logger.info(f"按节点类型'{node_type}'筛选")
            elif rel_type:
                json_data = neo4j_db_handle.get_nodes_by_relationship(rel_type)
                logger.info(f"按关系类型'{rel_type}'筛选")

        return jsonify({
            "code": 200,
            "msg": "success",
            "data": json_data,
            # 本次实际用的加载方式（full / limited / focused）：前端据此提示"被降级了"，
            # 否则用户只会看到一张不完整的图、不知道原因
            "graph_mode": ("full" if (load_all and not entity and not node_type and not rel_type)
                           else "focused" if (entity or node_type or rel_type)
                           else "limited"),
        })
    except Exception as e:
        logger.warning(f"搜索地名知识图谱异常: {str(e)}")
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
        logger.warning(f"查询节点列表失败: {e}")
        return jsonify({
            "code": 500,
            "msg": str(e),
            "data": {"total": 0, "records": []}
        })


@app.route('/create_node', methods=['POST'])
@require_write_role
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
@require_write_role
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
@require_write_role
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
@require_write_role
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

        if not node_type:
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
        logger.info(f"[{request_id}] 收到大模型推理请求: '{user_question}'")

        # 推理引擎与实体提取器优先用 before_request 建好的进程内单例；
        # 单例缺失（如启动时初始化失败）时按请求临时建一份，与原先一致。
        if not hasattr(g, 'rule_llm_integration'):
            logger.info(f"[{request_id}] 初始化规则推理模块")
            try:
                from inference.rule_llm_integration import RuleLLMIntegration
                g.rule_llm_integration = RuleLLMIntegration(
                    rule_file_path='rules/rule_base.json',
                    model_name='deepseek-r1:7b',
                    max_depth=3
                )
            except Exception as init_err:
                logger.warning(f"[{request_id}] 初始化规则推理模块失败: {str(init_err)}")
                return jsonify({
                    'success': False,
                    'error': '系统初始化失败，请稍后再试',
                    'answer': '抱歉，推理系统正在初始化中，请稍后再试。',
                    'kg_data': {'nodes': [], 'lines': []}
                }), 500

        if not hasattr(g, 'entity_extractor'):
            logger.info(f"[{request_id}] 初始化实体提取器")
            try:
                from entity_extract.extractor import Extractor
                g.entity_extractor = Extractor()
            except Exception as init_err:
                logger.warning(f"[{request_id}] 初始化实体提取器失败: {str(init_err)}")
                return jsonify({
                    'success': False,
                    'error': '实体提取器初始化失败，请稍后再试',
                    'answer': '抱歉，地名识别系统正在初始化中，请稍后再试。',
                    'kg_data': {'nodes': [], 'lines': []}
                }), 500

        payload, status = llm_pipeline.run_inference(
            g.rule_llm_integration, g.entity_extractor, user_question, request_id
        )
        return jsonify(payload), status

    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e)
        logger.info(f"处理推理请求时出错: {error_type} - {error_msg}")
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
        logger.info(f"[{request_id}] 收到SSE推理请求: '{user_question}'")

        # 获取推理引擎和实体提取器（同上：单例优先，缺失时按请求临时建一份）
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
                logger.warning(f"[{request_id}] 初始化失败: {str(init_err)}")
                return jsonify({'success': False, 'error': '系统初始化失败'}), 500

        # 在生成器外部捕获Flask上下文对象，避免在生成器内访问g
        rule_engine = g.rule_llm_integration
        entity_ext = g.entity_extractor

        return Response(
            llm_pipeline.stream_inference(rule_engine, entity_ext, user_question, request_id),
            mimetype='text/event-stream',
            headers={'Cache-Control': 'no-cache',
                     'Connection': 'keep-alive',
                     'X-Accel-Buffering': 'no'}
        )

    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e)
        logger.warning(f"SSE请求处理错误: {error_type} - {error_msg}")
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
            logger.warning(f"导入提取器模块失败: {import_err}")
            return jsonify({
                "code": 500,
                "msg": f"提取器模块导入失败: {str(import_err)}",
                "data": {}
            })

        # 初始化LLM客户端
        try:
            llm = llm_pipeline.OllamaAdapter("deepseek-r1:7b")
            logger.info("[提取] 使用本地Ollama模型: deepseek-r1:7b")
        except Exception as llm_err:
            logger.warning(f"LLM客户端初始化失败: {llm_err}")
            return jsonify({
                "code": 500,
                "msg": f"LLM客户端初始化失败: {str(llm_err)}",
                "data": {}
            })

        # 使用优化的单次抽取方案
        logger.info(f"[提取] 开始单次综合抽取，文本长度: {len(text)}")

        entities, event_result, relations = llm_pipeline.extract_all_optimized(llm, text)

        logger.info(f"[提取] 抽取完成: {len(entities.places)}地点, {len(entities.organizations)}组织, {len(entities.persons)}人物, {len(event_result.events)}事件")
        logger.info(f"[提取] 关系: {len(relations.event_place_relations)}事件-地点, {len(relations.event_person_relations)}事件-人物, {len(relations.event_organization_relations)}事件-组织, {len(relations.event_event_relations)}事件-事件")

        response_data = llm_pipeline.serialize_extraction_result(
            entities, event_result, relations, time.time() - start_time
        )

        return jsonify({
            "code": 200,
            "msg": "识别完成",
            "data": response_data
        })

    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e)
        logger.warning(f"文本实体识别失败: {error_type} - {error_msg}")
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
                    "id": "/knowledge/rag",
                    "icon": "layui-icon-chat",
                    "title": "RAG 智能问答"
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
                    "id": "/workspace/dataset-versions",
                    "icon": "layui-icon-list",
                    "title": "数据版本管理"
                }
            ]
        }
    ]

    # 角色裁剪：viewer（只读）不下发数据运营组。写权限的真正防线在
    # require_write_role 的 403，这里不下发菜单是第二层——让只读使用者
    # 界面上就看不到管理入口，而不是点了才被拒。
    role = DbUtil.get_role(getattr(g, "user_id", None))
    if role == "viewer":
        menu_data = [m for m in menu_data if m.get("id") != "/workspace/manage"]

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
    logger.info("🛑 正在关闭应用...")
    logger.info("✅ 应用已安全关闭")


atexit.register(graceful_shutdown)

# ================== 启动应用 ==================

if __name__ == "__main__":
    logger.info("\n" + "=" * 60)
    logger.info("🌐 启动历史地名知识图谱系统")
    logger.info("=" * 60)

    # 监听地址与调试开关都由环境变量控制：默认关闭 debug、只监听本机。
    # 不要用 debug=True + 0.0.0.0 对外提供服务：Werkzeug 调试器可执行任意代码。
    debug_enabled = local_settings.get("FLASK_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
    host = local_settings.get("BACKEND_HOST", "127.0.0.1").strip() or "127.0.0.1"
    try:
        port = int(local_settings.get("BACKEND_PORT", "5000") or 5000)
    except ValueError:
        port = 5000

    logger.info(f"📍 监听地址: http://{host}:{port}（调试模式: {'开' if debug_enabled else '关'}）")
    logger.info("💾 主存储: SQLite 关系型数据库")
    logger.info("🔄 可视化: Neo4j 图数据库")
    logger.info("=" * 60 + "\n")

    app.run(debug=debug_enabled, port=port, host=host)
