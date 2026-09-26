"""启动迁移不得把空角色账号提成管理员。

`ensure_user_table_schema()` 若对 `role IS NULL OR role = ''` 的历史账号执行
`role='admin'`，那么任何来源的空角色行——导入脚本、人工写库、旧版本遗漏——都会在
下一次重启（这条迁移**每次启动都跑**）时静默变成管理员。它与 `DbUtil.get_role`
的最小权限兜底（空值按 viewer）正好相反：同一个空值，读接口按 viewer 放行，
启动迁移却把人写成了 admin，界面与权限对不上。

两侧口径统一为 viewer；首个管理员由显式引导命令产生（create_admin.py）。
"""

from sqlalchemy import text

from app import app as flask_app, ensure_user_table_schema
from models import UserInfo, db


def _insert_raw(account: str, role_value) -> None:
    """绕过 ORM 写一行：模型上 role 有 Python 侧默认值 'viewer'，走 ORM 造不出 NULL/空串。"""
    db.session.execute(
        text("INSERT INTO UserInfo (account, password, name, role) VALUES (:a, 'x', :a, :r)"),
        {"a": account, "r": role_value},
    )
    db.session.commit()


def test_空角色回填为_viewer(_app_context):
    _insert_raw("遗留-null", None)
    _insert_raw("遗留-空串", "")

    ensure_user_table_schema()

    assert UserInfo.query.filter_by(account="遗留-null").first().role == "viewer"
    assert UserInfo.query.filter_by(account="遗留-空串").first().role == "viewer"


def test_已有角色不被迁移覆盖(_app_context):
    """迁移只填空白，不能顺手改掉已经明确的角色。"""
    db.session.add(UserInfo(account="既有-admin", name="既有-admin", password="x", role="admin"))
    db.session.add(UserInfo(account="既有-editor", name="既有-editor", password="x", role="editor"))
    db.session.commit()

    ensure_user_table_schema()

    assert UserInfo.query.filter_by(account="既有-admin").first().role == "admin"
    assert UserInfo.query.filter_by(account="既有-editor").first().role == "editor"


def test_空角色回填后仍与_get_role_口径一致(_app_context):
    """回填结果必须与读接口的兜底一致，否则用户管理页显示的角色和实际权限会对不上。"""
    from db_utils import DbUtil

    _insert_raw("遗留-null", None)

    ensure_user_table_schema()

    user = UserInfo.query.filter_by(account="遗留-null").first()
    assert user.to_dict()["role"] == DbUtil.get_role(user.id) == "viewer"


def test_迁移是幂等的(_app_context):
    """每次启动都会跑；连跑两次不该产生任何变化。"""
    _insert_raw("遗留-null", None)

    ensure_user_table_schema()
    first = UserInfo.query.filter_by(account="遗留-null").first().role
    ensure_user_table_schema()

    assert first == UserInfo.query.filter_by(account="遗留-null").first().role == "viewer"


def test_空角色账号无法调用写接口(client, auth):
    """端到端：回填成 viewer 后确实拿不到写权限（而不是只改了显示值）。"""
    _insert_raw("遗留-null", None)
    with flask_app.app_context():
        ensure_user_table_schema()
        user_id = UserInfo.query.filter_by(account="遗留-null").first().id

    # 挑一个真实存在的写接口（require_write_role），而不是管理员接口——
    # 这里考的是"角色是不是 admin"，viewer/editor 在管理员接口上都会 403，区分不出来。
    response = client.post("/create_node",
                           json={"type": "Event", "name": "越权-测试事件", "properties": {}},
                           headers=auth(user_id))
    assert response.status_code == 403
