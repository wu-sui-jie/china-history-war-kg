"""旧后端（Flask，:5000）的常驻测试夹具（第 6 轮审核 H4）。

背景：账户提权与菜单裁剪这块此前只有"一次性脚本 + 实施记录里的一张表"，
脚本跑完即删，CI 里只有 compileall + ruff 兜底——防呆逻辑本身没有回归保护。
这里把它落成常驻用例。

两个必要的替身，都在导入 app **之前**装好：

1. **不连真 Neo4j**：`app` 导入时会构造 `neo4j_db()` 并测一次连接（见 model_search），
   失败会直接抛异常让导入挂掉。本套用例考的是鉴权/菜单/提权链路，与图数据库无关，
   因此把 `py2neo.Graph` 换成假实现（对应审核意见里的"打桩 Neo4j 即可"）。
   `Graph.run()` 返回空结果，写图谱的动作会得到"同步失败"但不影响接口层断言。
2. **不碰开发库**：`app.py` 在模块级把 SQLALCHEMY_DATABASE_URI 写死成
   `sqlite:///{backend}/database`（本机那个 13MB 的真实库，且被 .gitignore）。
   这里在 `SQLAlchemy.init_app` 之前把它换成临时文件，于是每个测试进程都从空库建表，
   既不污染本机数据，也不依赖 `data/` 下的大制品。

需要额外说明：`init_app` 之后 Flask-SQLAlchemy 3.x 会拒绝再次注册，
所以"换库"只能发生在注册之前——这也是为什么这里用包装而不是在用例里改 config。
"""

import os
import sys
import tempfile
import types

import pytest

# ---------------------------------------------------------------- 替身：py2neo
if "py2neo" not in sys.modules:
    _py2neo = types.ModuleType("py2neo")

    class _EmptyResult:
        def data(self):
            return []

    class _Graph:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, *args, **kwargs):
            return _EmptyResult()

    _py2neo.Graph = _Graph
    sys.modules["py2neo"] = _py2neo

# 本机开发从 backend/.env 读这两项；CI 没有该文件，用测试专用的占位值。
# 必须在导入 jwt_util / model_search 之前设置（它们在导入期就会读）。
os.environ.setdefault("NEO4J_PASSWORD", "ci-placeholder")
os.environ.setdefault("JWT_SECRET", "ci-test-secret")

# ---------------------------------------------------- 替身：把 SQLite 指向临时库
_TEST_DIR = tempfile.mkdtemp(prefix="china-war-backend-tests-")
TEST_DB_PATH = os.path.join(_TEST_DIR, "test-database.sqlite")

import flask_sqlalchemy  # noqa: E402

_original_init_app = flask_sqlalchemy.SQLAlchemy.init_app


def _init_app_with_temp_db(self, app):
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + TEST_DB_PATH
    return _original_init_app(self, app)


flask_sqlalchemy.SQLAlchemy.init_app = _init_app_with_temp_db

# 让 `import app`（而不是 backend.app）可用：pytest 以 backend/ 为 rootdir 运行时，
# 仓库根目录不一定在 sys.path 上。
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import app as app_module  # noqa: E402
from jwt_util import encode  # noqa: E402
from models import UserInfo, db  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402


@pytest.fixture(autouse=True)
def _app_context():
    """整个用例期间持有应用上下文：用例里直接用 db.session 造数据、查结果。"""
    with app_module.app.app_context():
        yield


@pytest.fixture(autouse=True)
def _clean_users(_app_context):
    """每个用例从"没有任何账号"开始：角色相关断言不该被上一条用例留下的账号影响。"""
    db.session.query(UserInfo).delete()
    db.session.commit()
    yield
    db.session.query(UserInfo).delete()
    db.session.commit()


@pytest.fixture(autouse=True)
def _clean_login_attempts(_app_context):
    """每个用例清空登录限流计数。

    限流表是**跨用例累积**的：IP 维度按来源地址计数，而所有用例都来自 127.0.0.1——
    不清的话前一条用例的失败登录会把后一条的登录直接顶成 429，
    表现为"单独跑绿、全量跑红"这种最难查的失败。
    """
    import login_guard
    from sqlalchemy import text

    login_guard.ensure_table()
    db.session.execute(text("DELETE FROM login_attempts"))
    db.session.commit()
    yield
    db.session.execute(text("DELETE FROM login_attempts"))
    db.session.commit()


@pytest.fixture()
def client():
    return app_module.app.test_client()


@pytest.fixture()
def make_user():
    """建一个账号（角色直接落库；注册接口只会建 viewer，见 test_privilege_chain）。"""
    def _make(account="user-1", role="viewer", password="pw-123456"):
        user = UserInfo(
            account=account,
            name=account,
            password=generate_password_hash(password),
            role=role,
        )
        db.session.add(user)
        db.session.commit()
        return user

    return _make


@pytest.fixture()
def auth():
    """给账号（或裸 user_id）生成请求头：Token 用 jwt_util.encode，与登录接口同源。"""
    def _auth(user):
        user_id = user if isinstance(user, int) else getattr(user, "id", None)
        return {"Token": encode(user_id)}

    return _auth


@pytest.fixture()
def user_ids():
    """/user/menu 返回的菜单 id 集合：分组会展开成它的 children。"""
    def _ids(payload):
        ids = set()

        def walk(items):
            for item in items or []:
                ids.add(item.get("id"))
                walk(item.get("children"))

        walk((payload or {}).get("data"))
        return ids

    return _ids
