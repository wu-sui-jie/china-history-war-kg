"""管理员接口的防呆（第 6 轮审核 H4）。

`/api/admin/*` 是"改角色"这类系统管理动作，只有 admin 能碰。三条防呆都在服务端：
① 非 admin 一律 403（不靠界面藏入口）；② 不能改自己（否则最后一个管理员能把自己降级、
再没人能提权）；③ 角色值必须在白名单里。

另外钉住"实时查库"这个性质：账号被删、或 token 指向一个不存在的 user_id 时必须拒绝——
原先只验签名的话，删号/伪造 int id 都能混过去。
"""


def test_未带_token_一律_401(client):
    response = client.get("/api/admin/users")
    assert response.status_code == 401
    assert response.get_json()["code"] == 401


def test_伪造_token_按未认证处理(client):
    response = client.get("/api/admin/users", headers={"Token": "not-a-jwt"})
    assert response.status_code == 401


def test_viewer_与_editor_都进不去管理员接口(client, make_user, auth):
    editor = make_user("editor-1", "editor")
    viewer = make_user("viewer-1", "viewer")

    for user in (editor, viewer):
        response = client.get("/api/admin/users", headers=auth(user))
        assert response.status_code == 403, f"{user.role} 不该能列用户"
        assert response.get_json()["code"] == 403

        response = client.post(f"/api/admin/users/{viewer.id}/role",
                              json={"role": "admin"}, headers=auth(user))
        assert response.status_code == 403, f"{user.role} 不该能改角色"


def test_账号已删除时原_token_立刻失效(client, make_user, auth):
    """鉴权是每次请求实时查库：删号后旧 token 不能继续用（fail-closed）。

    第 13 轮整改后这里返回 **401** 而不是 403：账号不存在是"这个凭证已经不对应
    任何身份"（未认证），而不是"身份有效但权限不够"。检查位置也从 `require_admin`
    里查角色提前到了全局拦截器的 token 可用性判断，响应因此更准确。
    """
    admin = make_user("admin-1", "admin")
    assert client.get("/api/admin/users", headers=auth(admin)).status_code == 200

    from models import UserInfo, db

    victim = make_user("to-be-deleted", "viewer")
    victim_id = victim.id
    victim_headers = auth(victim)

    db.session.delete(db.session.get(UserInfo, victim_id))
    db.session.commit()

    assert client.get("/api/admin/users", headers=victim_headers).status_code == 401


def test_停用账号后原_token_立刻失效(client, make_user, auth):
    """封号必须立刻生效，不能等 token 自然过期（最长 7 天）。"""
    admin = make_user("admin-1", "admin")
    victim = make_user("to-be-disabled", "viewer")
    victim_headers = auth(victim)
    assert client.get("/api/userinfo", headers=victim_headers).status_code == 200

    response = client.post(f"/api/admin/users/{victim.id}/status",
                           json={"disabled": True}, headers=auth(admin))
    assert response.status_code == 200
    assert response.get_json()["data"]["disabled"] is True

    assert client.get("/api/userinfo", headers=victim_headers).status_code == 401

    # 启用后旧 token 依然失效：停用时已经 +1 过 token_version，
    # 重新启用不该把已经流出去的旧凭证又变回有效凭证。
    assert client.post(f"/api/admin/users/{victim.id}/status",
                       json={"disabled": False}, headers=auth(admin)).status_code == 200
    assert client.get("/api/userinfo", headers=victim_headers).status_code == 401


def test_管理员不能停用自己(client, make_user, auth):
    """否则最后一个管理员能把自己锁在门外，之后再没人能启用任何账号。"""
    admin = make_user("admin-1", "admin")

    response = client.post(f"/api/admin/users/{admin.id}/status",
                           json={"disabled": True}, headers=auth(admin))

    assert response.status_code == 403


def test_停用接口拒绝非布尔值(client, make_user, auth):
    """`"disabled": "true"` 这种字符串不该被当成真——写错了要报错而不是猜。"""
    admin = make_user("admin-1", "admin")
    victim = make_user("viewer-1", "viewer")

    response = client.post(f"/api/admin/users/{victim.id}/status",
                           json={"disabled": "true"}, headers=auth(admin))

    assert response.status_code == 400


def test_改密码后旧_token_立刻失效(client, make_user, auth):
    """改密码是用户发现口令泄露时唯一能做的动作，旧凭证必须当场作废。"""
    user = make_user("someone", "viewer", password="old-password-123")
    old_headers = auth(user)
    assert client.get("/api/userinfo", headers=old_headers).status_code == 200

    response = client.post("/api/user/password",
                           json={"old_password": "old-password-123",
                                 "new_password": "brand-new-password-456"},
                           headers=old_headers)
    assert response.status_code == 200

    assert client.get("/api/userinfo", headers=old_headers).status_code == 401
    # 新口令能登录，且登录后拿到的新 token 可用
    login = client.post("/api/login", json={"account": "someone",
                                            "password": "brand-new-password-456"})
    assert login.get_json()["code"] == 200
    assert client.get("/api/userinfo", headers={"Token": login.get_json()["data"]}
                      ).status_code == 200


def test_改密码拒绝错误原口令与弱口令(client, make_user, auth):
    user = make_user("someone", "viewer", password="old-password-123")
    headers = auth(user)

    wrong = client.post("/api/user/password",
                        json={"old_password": "not-my-password",
                              "new_password": "brand-new-password-456"},
                        headers=headers)
    assert wrong.status_code == 403

    weak = client.post("/api/user/password",
                       json={"old_password": "old-password-123", "new_password": "short"},
                       headers=headers)
    assert weak.status_code == 400

    # 失败不该生效：原口令仍然可用
    assert client.post("/api/login", json={"account": "someone",
                                           "password": "old-password-123"}
                       ).get_json()["code"] == 200


def test_管理员可列用户且不下发口令(client, make_user, auth):
    admin = make_user("admin-1", "admin")
    make_user("viewer-1", "viewer")

    response = client.get("/api/admin/users", headers=auth(admin))
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["code"] == 200

    accounts = {item["account"]: item for item in payload["data"]}
    assert set(accounts) == {"admin-1", "viewer-1"}
    assert accounts["viewer-1"]["role"] == "viewer"
    # 口令散列绝不能出现在用户列表里
    assert all("password" not in item for item in payload["data"])


def test_不能改自己的角色(client, make_user, auth):
    admin = make_user("admin-1", "admin")

    response = client.post(f"/api/admin/users/{admin.id}/role",
                           json={"role": "viewer"}, headers=auth(admin))

    assert response.status_code == 403
    assert "不能修改自己" in response.get_json()["msg"]

    from models import UserInfo, db

    assert db.session.get(UserInfo, admin.id).role == "admin", "自己那行必须原样保留"


def test_角色白名单外的取值一律_400(client, make_user, auth):
    admin = make_user("admin-1", "admin")
    target = make_user("viewer-1", "viewer")

    for bad in ["Admin", "superuser", "", "  ", "admin; DROP TABLE", None, 123]:
        response = client.post(f"/api/admin/users/{target.id}/role",
                               json={"role": bad}, headers=auth(admin))
        assert response.status_code == 400, f"角色取值 {bad!r} 应被拒"
        assert response.get_json()["code"] == 400

    from models import UserInfo, db

    assert db.session.get(UserInfo, target.id).role == "viewer", "非法请求不能改动目标角色"


def test_用户不存在时_404(client, make_user, auth):
    admin = make_user("admin-1", "admin")

    response = client.post("/api/admin/users/999999/role",
                           json={"role": "editor"}, headers=auth(admin))

    assert response.status_code == 404


def test_改角色成功并落库(client, make_user, auth):
    admin = make_user("admin-1", "admin")
    target = make_user("viewer-1", "viewer")

    response = client.post(f"/api/admin/users/{target.id}/role",
                           json={"role": "editor"}, headers=auth(admin))

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["code"] == 200
    assert payload["data"]["role"] == "editor"

    from models import UserInfo, db

    assert db.session.get(UserInfo, target.id).role == "editor"


def test_请求体畸形时按非法角色处理(client, make_user, auth):
    """get_json(silent=True)：畸形 JSON 不能变成 HTML 400 页，而是我们的 JSON 错误响应。"""
    admin = make_user("admin-1", "admin")
    target = make_user("viewer-1", "viewer")

    response = client.post(f"/api/admin/users/{target.id}/role",
                           data="{不是 JSON", content_type="application/json",
                           headers=auth(admin))

    assert response.status_code == 400
    assert response.is_json
