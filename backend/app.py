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
import atexit

# ================== Flask核心模块 ==================
from flask import Flask, request, jsonify, g
from flask_cors import CORS
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlalchemy import text

# ================== 自定义模块 ==================
import local_settings

# 统一错误响应层（框架层错误 → JSON，见 api_errors 的模块文档）
import api_errors
# LLM 流水线：模型调用、抽取链、问答编排（P2-1 第二步）——这里只用于请求级单例的准备
import llm_pipeline
# 报表构建器、Neo4j 句柄、鉴权装饰器与领域常量都已拆到独立模块（P2-1 与蓝图拆分）：
# 路由侧在 blueprints/ 里各自 import，本文件只剩应用装配与启停。
from db_utils import DbUtil
from jwt_util import TokenError, decode
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

# 统一错误响应（第 13 轮整改，文档第九节）：框架层错误（404/405/413/未捕获异常）
# 原先回的是 HTML 页面，前端读不到任何文案，只能显示通用的"操作失败"。
# 必须在**注册任何路由之前**装好——否则早注册的路由一旦出错就没有处理器可用。
api_errors.install(app)
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
    """为旧库补 UserInfo 的新列、回填存量取值并补 account 唯一索引。"""
    from models import db

    rows = db.session.execute(text("PRAGMA table_info(UserInfo)")).fetchall()
    if not rows:
        # 表尚未创建（create_all 还没跑到）时无需处理
        return
    existing_columns = {row[1] for row in rows}
    if "role" not in existing_columns:
        db.session.execute(text("ALTER TABLE UserInfo ADD COLUMN role VARCHAR(32)"))
    # token_version / disabled（第 13 轮整改）。ALTER TABLE ADD COLUMN 加 NOT NULL 列
    # 必须给默认值，否则 SQLite 直接报错；存量行的取值也就是这个默认值：
    # version=1、未禁用——与改造前的行为完全一致（老 token 里没有 ver，按 1 处理）。
    if "token_version" not in existing_columns:
        db.session.execute(
            text("ALTER TABLE UserInfo ADD COLUMN token_version INTEGER NOT NULL DEFAULT 1"))
    if "disabled" not in existing_columns:
        db.session.execute(
            text("ALTER TABLE UserInfo ADD COLUMN disabled BOOLEAN NOT NULL DEFAULT 0"))
    # 空角色一律回填 **viewer**，不回填 admin。
    #
    # 这里曾经回填 admin，理由是"角色模型引入前建的账号都是本机管理员手工创建的"。
    # 该理由已不成立：现在任何导入、手工写库或旧版本遗漏都可能留下空角色行，而
    # 启动迁移是每次开机都跑的——一条空角色行等于一次静默提权。口径与
    # DbUtil.get_role（空值按 viewer 兜底）保持一致：空值最多让人少看几个页面，
    # 不会让人多写几个接口。
    #
    # 首个管理员不再靠这条迁移产生，改用显式引导命令：
    #   cd backend && python create_admin.py --account <账号>     （见 create_admin.py）
    db.session.execute(text("UPDATE UserInfo SET role = 'viewer' WHERE role IS NULL OR role = ''"))
    # token_version 兜底：NULL 会让 token_version_of 回落到 1，与库里的 NULL 比较
    # 在 SQL 里恒为假——那会把所有老账号判成"版本不符"，登录后立刻 401。
    db.session.execute(
        text("UPDATE UserInfo SET token_version = 1 WHERE token_version IS NULL"))
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
        # 外键约束默认是**关**的（SQLite 的历史包袱，为兼容旧库保留），不显式打开时
        # models.py 里声明的 ForeignKey 只是一句注释：删掉一个已被关系引用的事件，
        # 关系表会留下指向不存在 id 的孤儿行。这里逐连接打开，且必须在任何事务之前
        # 执行——PRAGMA foreign_keys 在事务内是静默无效的，连接钩子正是事务外。
        #
        # 打开后删除顺序有了硬约束：必须**先删引用方（关系行）再删被引用方（实体行）**，
        # 这条约束由 DbUtil.delete_node 与 import_json_to_sqlite.clear_migration_tables
        # 各自遵守；违反时 SQLite 会直接报 FOREIGN KEY constraint failed，而不是静默留下脏数据。
        cursor.execute("PRAGMA foreign_keys=ON")
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
        # 补偿队列（outbox）：create_all 只建缺失的表，不会给已存在的 neo4j_sync_jobs
        # 补上第 13 轮新增的 graph_key / operation 等列，所以显式跑一次补列迁移。
        from sync_compensation import ensure_sync_jobs_table

        ensure_sync_jobs_table()
        # 登录限流表：它的建表时机与业务表分离（限流是登录路径的依赖，而登录
        # 在 create_all 之前就可能被调用），这里显式建一次让首个请求不必等 DDL。
        import login_guard

        login_guard.ensure_table()
        db.session.commit()
        logger.info("✅ SQLite schema 检查完成（WAL 模式由连接钩子设置）")
    except Exception as e:
        logger.warning(f"⚠️ SQLite schema 检查失败: {e}")


# ================== 初始化函数 ==================









# ================== 初始化函数 ==================











































def initialize_entity_extractor():
    """把进程内单例（实体提取器 / 规则推理引擎）挂到 flask.g。

    单例的构建与加锁都搬到了 llm_pipeline.get_shared_extractors()；这里只做请求级绑定：
    flask.g 按请求隔离，多线程下不会串号。两个单例都可能为 None（初始化失败），
    此时路由自己会按请求临时建一份。

    注册点见下方 before() 之后的 `app.before_request(initialize_entity_extractor)`：
    它必须晚于鉴权钩子，否则未登录请求也会触发单例构建。
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

# 放行路径：登录、注册、运行状态与静态资源
#
# /api/health 免登录是有意的（第 13 轮复核整改 §2.4）：监控与部署自检要在**没有账号
# 凭据**时探活，而"登录是不是全 503"恰恰是最需要探活的时刻。它只回"活着"与限流的
# 失败策略，不含版本/路径/阈值（见 blueprints/workspace.py 的 health）。
PASS_URLS = {"/", "/api/login", "/api/sign_in", "/api/health"}

# 内部接口前缀（服务间调用，第 13 轮复核）。这些请求由 RAG 服务发出，**没有用户 token**，
# 所以必须在全局鉴权这里放行；它们自己的守卫是 internal_bp 上的服务间密钥校验
# （蓝图级 before_request，对蓝图内所有路由生效）。两处缺一不可：
# 这里的放行不等于"开放"，密钥校验那一步才是真正的门。
INTERNAL_PREFIX = "/api/internal/"

# 角色常量（WRITE_ROLES / ROLE_RANKS / ADMIN_MENU_IDS / EDITOR_MENU_IDS）与鉴权装饰器
# （require_write_role / require_admin）已随蓝图拆分搬到 roles.py，供 app 与各蓝图共用。

@app.before_request
def before():
    """全局请求拦截器（权限校验）"""
    url = request.path

    if url.startswith("/static") or url in PASS_URLS:
        return None

    # 内部接口：身份来自服务间密钥，不经过用户 token（校验在 blueprints/internal.py）
    if url.startswith(INTERNAL_PREFIX):
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

    # JWT 自身验不了的两件事必须回库查（第 13 轮整改）：账号是否已被停用、
    # token 里的 ver 与库里 token_version 是否一致（改密码/踢人后 +1）。
    # 没有这一步，"封号"与"改密码"都要等 token 自然过期（最长 7 天）才生效。
    usable, reason = DbUtil.check_token_usable(payload)
    if not usable:
        logger.warning("拒绝已失效的登录凭证：user_id=%s 原因=%s", payload.get("user_id"), reason)
        # 话术统一、不区分具体原因：透露"密码改过"还是"账号被停用"等于泄露账号状态
        return jsonify({"code": 401, "msg": "登录已失效，请重新登录"}), 401

    # 请求身份放 flask.g，按请求隔离；模块级全局变量在多线程下会串号
    g.user_id = payload.get('user_id')
    return None


# 初始化钩子刻意在此处注册（而不是用装饰器写在函数定义处）：Flask 按**注册顺序**执行
# before_request，前一个返回了响应就短路后面的。走到这里说明鉴权已通过——未登录/凭证失效
# 的请求不会再触发实体提取器与规则引擎的构建（进程内单例，首次构建最贵）。
app.before_request(initialize_entity_extractor)


# ================== 用户相关接口 ==================

# ================== 用户管理（仅管理员）==================
# 角色体系（admin/editor/viewer）此前只能靠手写 SQL 提权，没有界面入口。
# 这两个接口把提权变成管理员页面上的一次选择；防呆全部在服务端做——
# 界面藏入口只是体验，判断留在接口里。

# ================== 知识图谱接口 - 节点管理操作SQLite，查询仍可用Neo4j ==================

# ================== 其他Neo4j查询接口（保持原样）====================

# ================== 子页面关系图谱接口 ==================

# ================== 智能问答接口====================

# ================== 路由蓝图（P2-1 收官）==================
# 路由按业务分五组迁到 blueprints/，**不加 url_prefix**：URL 必须与拆分前逐字相同。
# 全局鉴权（before_request）与 initialize_entity_extractor 留在本文件、对全部蓝图生效，
# 蓝图不重复实现鉴权。
from blueprints import (  # noqa: E402
    auth_bp,
    graph_bp,
    internal_bp,
    llm_bp,
    node_bp,
    workspace_bp,
)

for _bp in (auth_bp, node_bp, graph_bp, workspace_bp, llm_bp, internal_bp):
    app.register_blueprint(_bp)


# ================== 应用生命周期管理 ==================

def graceful_shutdown():
    """优雅关闭（仅在以 `python app.py` 启动服务时注册，见文件末尾）。"""
    try:
        logger.info("🛑 正在关闭应用...")
        logger.info("✅ 应用已安全关闭")
    except ValueError:
        # 解释器退出阶段 stdout/日志流可能已被关闭（pytest 捕获、重定向、管道关闭），
        # 此时写日志会抛 "I/O operation on closed file"。关闭流程本身无事可做，
        # 不该让一条收尾日志变成异常退出——所以这里咽掉，而不是往上抛。
        pass


# atexit 注册刻意放在 `if __name__ == "__main__"` 里（文件末尾），不在模块级：
# app.py 也会被当库导入（backend/tools/*、tests/conftest），那些进程退出时打一行
# "正在关闭应用"既无意义，又会撞上已被关闭的日志流（第 12 轮审查实测到的退出告警）。

# ================== 启动应用 ==================

if __name__ == "__main__":
    atexit.register(graceful_shutdown)

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
