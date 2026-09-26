"""服务间 token introspection（文档第四节方案 B）。

背景：旧后端做到了"改密码 / 封号后旧 token 立刻失效"，但 RAG 只验签名
不查库——同一张旧 token 仍能调 RAG 的问答接口，直到自然过期。这个接口就是补那条缝：
RAG 拿 token 来问一句"现在还作不作数"。

用例分四组：

1. **门禁**：未配密钥 = 503（不是"不校验"）；密钥不对 = 401；
2. **判定**：有效 token → active；改密码 / 封号 / 删号 → active=false；
3. **与旧后端口径一致**：同一张 token 在全局鉴权被拒时，introspect 也必须是 false；
4. **不外发**：响应里不出现 token 本身，也不出现失败的具体原因。
"""

from __future__ import annotations

import json

import pytest
from jwt_util import ISSUER, AUDIENCE, encode

ENDPOINT = "/api/internal/token/introspect"
SERVICE_KEY = "test-internal-service-key"


@pytest.fixture()
def service_key(monkeypatch):
    """配置服务间密钥（默认不配，用例按需打开）。"""
    monkeypatch.setenv("INTERNAL_SERVICE_KEY", SERVICE_KEY)
    return SERVICE_KEY


def _introspect(client, token, *, key=SERVICE_KEY, body=None):
    """发一次 introspect；`body` 显式给出时按原样发送（用于测请求体形状）。"""
    headers = {"X-Internal-Service-Key": key} if key is not None else {}
    payload = {"token": token} if body is None else body
    return client.post(ENDPOINT, json=payload, headers=headers)


def test_未配置密钥时接口整体不可用(client):
    """可选的保护开关漏配时**不能**退化成"没有保护"。

    一个匿名的 introspection 接口不只是信息泄露：它还给了攻击者一条"这张凭证
    失效没有"的探测通道。
    """
    response = _introspect(client, encode(1))

    assert response.status_code == 503
    assert "未配置" in response.get_json()["msg"]


def test_服务密钥不对返回_401(client, service_key):
    assert _introspect(client, encode(1), key="wrong-key").status_code == 401
    assert _introspect(client, encode(1), key=None).status_code == 401


def test_有效_token_返回_active(client, make_user, service_key):
    """启用前先把正常路径钉住：否则"全部返回 false"也能让撤销类用例变绿。"""
    user = make_user("someone", "editor")
    token = encode(user.id, "editor", token_version=getattr(user, "token_version", 1) or 1)

    data = _introspect(client, token).get_json()["data"]

    assert data["active"] is True
    assert data["user_id"] == user.id
    assert data["role"] == "editor"
    assert data["disabled"] is False


def test_改密码后_token_立刻失效(client, make_user, service_key):
    """RAG 能靠这个接口做到"改密码后立刻拒绝"，而不必等 JWT 自然过期（默认 7 天）。"""
    user = make_user("someone", "viewer", password="old-password-1")
    token = encode(user.id, "viewer", token_version=getattr(user, "token_version", 1) or 1)
    assert _introspect(client, token).get_json()["data"]["active"] is True

    from db_utils import DbUtil

    DbUtil.bump_token_version(user.id)

    data = _introspect(client, token).get_json()["data"]
    assert data["active"] is False
    assert data["token_version"] == 2


def test_停用账号后_token_立刻失效(client, make_user, service_key):
    user = make_user("banned", "viewer")
    token = encode(user.id, "viewer", token_version=getattr(user, "token_version", 1) or 1)

    from db_utils import DbUtil

    DbUtil.set_user_disabled(user.id, True)

    data = _introspect(client, token).get_json()["data"]
    assert data["active"] is False
    assert data["disabled"] is True


def test_删除账号后_token_失效(client, make_user, service_key):
    """token 里的 user_id 已经没有任何对应账号，继续放行等于给已删除的身份开权限。"""
    user = make_user("gone", "viewer")
    token = encode(user.id, "viewer", token_version=1)

    from models import UserInfo, db

    db.session.delete(db.session.get(UserInfo, user.id))
    db.session.commit()

    data = _introspect(client, token).get_json()["data"]
    assert data["active"] is False
    assert data["user_id"] is None or data["user_id"] == user.id


def test_验签失败按无效处理而不是报错(client, service_key):
    """被查的凭证不可信是"查询的答案"，不是"查询失败"——调用方要能区分这两者。"""
    response = _introspect(client, "not-a-jwt")

    assert response.status_code == 200
    assert response.get_json()["data"]["active"] is False


def test_过期_token_返回无效(client, make_user, service_key):
    """用过期时间直接构造：验签通过但 exp 已过，仍必须是 false。"""
    import jwt_util

    user = make_user("someone", "viewer")
    original = jwt_util._ttl_seconds
    jwt_util._ttl_seconds = lambda: -60  # 签发即过期
    try:
        token = jwt_util.encode(user.id, "viewer", token_version=1)
    finally:
        jwt_util._ttl_seconds = original

    reply = _introspect(client, token).get_json()
    assert reply["code"] == 200
    assert reply["data"]["active"] is False


def test_iss_aud_不符的_token_被拒(client, make_user, service_key):
    """另一个服务拿同一把密钥签的 token 不该被当成有效凭证（与 RAG 侧同一口径）。"""
    import jwt
    import jwt_util

    user = make_user("someone", "viewer")
    payload = {
        "user_id": user.id, "sub": str(user.id),
        "iss": "other-service", "aud": AUDIENCE,
        "ver": 1, "exp": 4102444800,
    }
    token = jwt.encode(payload, jwt_util.secret, algorithm="HS256")

    assert _introspect(client, token).get_json()["data"]["active"] is False

    # 反方向：iss 对、aud 不对，同样拒绝（防止把给别的服务的 token 拿来用）
    payload["iss"] = ISSUER
    payload["aud"] = "other-audience"
    token = jwt.encode(payload, jwt_util.secret, algorithm="HS256")
    assert _introspect(client, token).get_json()["data"]["active"] is False


def test_响应不回显_token_也不回显失败原因(client, make_user, service_key):
    """introspect 的响应会进日志、可能被上报：token 与失败原因都不该出现在里面。

    "改过密码"与"被停用"的区别对排障有价值，对调用方没有；泄露出去等于把账号状态
    变成任何持有旧 token 的人都能查的事实。响应字段走白名单（见 internal.py）。
    """
    user = make_user("someone", "viewer")
    token = encode(user.id, "viewer", token_version=1)
    from db_utils import DbUtil

    DbUtil.bump_token_version(user.id)

    raw = _introspect(client, token).get_data(as_text=True)

    assert token not in raw
    assert "版本" not in raw
    assert "停用" not in raw
    data = json.loads(raw)["data"]
    assert data["active"] is False
    assert "reason" not in data


def test_请求体必须是_json_对象(client, service_key):
    """GET 会把 token 带进 URL，从而进 access log 与浏览器历史，因此只接受 POST + JSON。"""
    assert client.get(ENDPOINT, headers={"X-Internal-Service-Key": SERVICE_KEY}).status_code == 405
    assert _introspect(client, None, body=[]).status_code == 400
    assert _introspect(client, None, body={}).status_code == 400


def test_全局鉴权被拒时_introspect_同样为_false(client, make_user, service_key):
    """两条路径必须同口径：RAG 说 active 而旧后端拒之门外，就是必须堵上的那条缝。"""
    user = make_user("someone", "viewer")
    token = encode(user.id, "viewer", token_version=1)
    from db_utils import DbUtil

    DbUtil.bump_token_version(user.id)

    backend_reply = client.get("/api/userinfo", headers={"Token": token})
    introspect_reply = _introspect(client, token).get_json()

    assert backend_reply.status_code == 401
    assert introspect_reply["data"]["active"] is False
