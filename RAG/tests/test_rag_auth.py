"""RAG 服务端身份校验。

本服务默认不做身份校验：`/api/query`（SSE）没有任何鉴权，`/api/query/json` 只有一个
**可选**共享密钥，nginx 的 `auth_basic` 默认是注释掉的——知道 `/rag/` 地址就能直接调用
问答接口，既不受旧系统角色控制，也消耗模型配额；浏览器侧那个 uid 只用来拼 localStorage
key，不是服务端身份。开启 `RAG_AUTH_MODE=jwt` 后上游这些结论才成立。

这里的用例分两层：
1. **纯函数层**（verify_hs256 / identity_from_headers）：把每种不可信情形逐个钉死——
   算法混淆、alg=none、签名不符、过期、缺 exp、非法 base64、分段数不对、缺 user_id；
2. **路由层**（TestClient + 假 runtime）：开启校验后 SSE 与非流式接口都必须 401，
   带上正确 token 才放行，且非流式接口仍接受 X-Bot-Key（飞书机器人没有用户身份）。

token 由本文件自己按 HS256 现场签发（不依赖 PyJWT，与 server/auth.py 的实现选择一致）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest

from server import auth

SECRET = "test-secret-do-not-use"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def make_token(payload: dict, secret: str = SECRET, header: dict | None = None,
               *, corrupt_signature: bool = False) -> str:
    header = header if header is not None else {"alg": "HS256", "typ": "JWT"}
    segments = [
        _b64(json.dumps(header, separators=(",", ":")).encode("utf-8")),
        _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
    ]
    signing_input = ".".join(segments).encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    if corrupt_signature:
        signature = bytes([signature[0] ^ 0xFF]) + signature[1:]
    segments.append(_b64(signature))
    return ".".join(segments)


def valid_token(**overrides) -> str:
    """按**签发端的真实口径**造 token（iss / aud / sub / jti / ver 都带上）。

    纯函数层的用例直接调 `verify_hs256`（不传 issuer/audience），因此不受这些声明影响；
    这里是"经服务层验签"的那批用例的入口，必须与 backend/jwt_util.py 发出来的东西同形，
    否则测的就不是真实链路。
    """
    now = int(time.time())
    payload = {
        "user_id": 7, "sub": "7", "role": "editor",
        "iss": auth.ISSUER_DEFAULT, "aud": auth.AUDIENCE_DEFAULT,
        "jti": "test-jti-1", "ver": 1,
        "iat": now, "exp": now + 3600,
    }
    payload.update(overrides)
    return make_token(payload)


# ---------------------------------------------------------------- 纯函数层


def test_正确签发的_token_能验证通过():
    identity = auth.identity_from_headers({"Token": valid_token()}, SECRET)

    assert identity == {"user_id": 7, "role": "editor"}


def test_老_token_没有_role_声明时为空串而不是兜底成_viewer():
    """区分"没带角色"与"角色就是 viewer"：不猜、不制造看起来有含义的默认值。"""
    identity = auth.identity_from_headers({"Token": valid_token(role=None)}, SECRET)

    assert identity["role"] == ""


def test_alg_none_被拒绝():
    """经典攻击：把签名算法改成 none 让服务端跳过验签。"""
    token = make_token({"user_id": 7, "exp": int(time.time()) + 60},
                       header={"alg": "none", "typ": "JWT"})

    with pytest.raises(auth.AuthError) as excinfo:
        auth.verify_hs256(token, SECRET)
    assert "算法" in str(excinfo.value)


def test_其他算法_rs256_也被拒绝():
    """算法混淆攻击：诱导服务端拿公钥当 HMAC 密钥。白名单只有一个取值，直接不认。"""
    token = make_token({"user_id": 7, "exp": int(time.time()) + 60},
                       header={"alg": "RS256", "typ": "JWT"})

    with pytest.raises(auth.AuthError):
        auth.verify_hs256(token, SECRET)


def test_签名被篡改时拒绝():
    token = make_token({"user_id": 7, "exp": int(time.time()) + 60},
                       corrupt_signature=True)

    with pytest.raises(auth.AuthError) as excinfo:
        auth.verify_hs256(token, SECRET)
    assert "签名" in str(excinfo.value)


def test_换成别的密钥签的_token_被拒绝():
    token = make_token({"user_id": 7, "exp": int(time.time()) + 60}, secret="另一个密钥")

    with pytest.raises(auth.AuthError):
        auth.verify_hs256(token, SECRET)


def test_改了_payload_但沿用旧签名会被拒():
    """按"取真实签名拼到新 payload 上"的方式构造：签名校验必须覆盖 payload。"""
    good = valid_token(user_id=1)
    header, _payload, signature = good.split(".")
    forged_payload = _b64(json.dumps({"user_id": 999, "exp": int(time.time()) + 3600},
                                     separators=(",", ":")).encode("utf-8"))

    with pytest.raises(auth.AuthError):
        auth.verify_hs256(f"{header}.{forged_payload}.{signature}", SECRET)


def test_过期_token_被拒绝():
    token = valid_token(exp=int(time.time()) - 3600)

    with pytest.raises(auth.AuthError) as excinfo:
        auth.verify_hs256(token, SECRET)
    assert "过期" in str(excinfo.value)


def test_时钟偏移容忍范围内的_token_仍然可用():
    """零容忍会把"刚签发"判成过期，表现为登录后偶尔立刻 401。"""
    just_expired = valid_token(exp=int(time.time()) - 5)

    assert auth.verify_hs256(just_expired, SECRET)["user_id"] == 7


def test_缺少_exp_的永久凭证被拒绝():
    header = {"alg": "HS256", "typ": "JWT"}
    segments = [
        _b64(json.dumps(header).encode()),
        _b64(json.dumps({"user_id": 7}).encode()),
    ]
    signature = hmac.new(SECRET.encode(), ".".join(segments).encode(),
                         hashlib.sha256).digest()
    token = ".".join(segments + [_b64(signature)])

    with pytest.raises(auth.AuthError) as excinfo:
        auth.verify_hs256(token, SECRET)
    assert "exp" in str(excinfo.value)


def test_尚未生效的_token_被拒绝():
    token = valid_token(exp=int(time.time()) + 3600, nbf=int(time.time()) + 600)

    with pytest.raises(auth.AuthError) as excinfo:
        auth.verify_hs256(token, SECRET)
    assert "尚未生效" in str(excinfo.value)


def test_payload_缺少用户身份声明被拒绝():
    """`user_id` 与 `sub` 都没有 = 没有身份可验，必须拒绝而不是当成匿名放行。"""
    with pytest.raises(auth.AuthError):
        auth.identity_from_headers({"Token": valid_token(user_id=None, sub=None)}, SECRET)


@pytest.mark.parametrize("bad", ["", "only-one-part", "a.b", "a.b.c.d", "!!!.???.###",
                                 "e30.e30.not-base64!!"])
def test_形状不对的_token_一律拒绝(bad):
    with pytest.raises(auth.AuthError):
        auth.verify_hs256(bad, SECRET)


def test_未配密钥时_fail_closed():
    """没密钥却走到验签 = 调用方配置错误，必须拒绝而不是"没密钥就放行"。"""
    with pytest.raises(auth.AuthError) as excinfo:
        auth.verify_hs256(valid_token(), "")

    assert "密钥" in str(excinfo.value)


def test_从_Authorization_Bearer_也能取到_token():
    identity = auth.identity_from_headers(
        {"Authorization": f"Bearer {valid_token()}"}, SECRET)

    assert identity["user_id"] == 7


def test_Token_头优先于_Authorization():
    headers = {"Token": valid_token(user_id=7),
               "Authorization": f"Bearer {valid_token(user_id=999)}"}

    assert auth.identity_from_headers(headers, SECRET)["user_id"] == 7


# ---------------------------------------------------------------- 路由层


@pytest.fixture
def auth_client(monkeypatch):
    """挂假 runtime 的 TestClient（不起真实数据制品），并在用例后还原鉴权配置。"""
    from fastapi.testclient import TestClient

    import server.api as api_mod
    from server.sse import sse_format

    frames = [sse_format({"type": "answer", "session_id": "s", "data": {"delta": "好"}})]

    async def fake_run_query(rt, q):
        for frame in frames:
            yield frame

    monkeypatch.setattr("server.api.run_query", fake_run_query)

    # health 会读 runtime 的 meta/version/index_dir/text/generate 等属性；
    # 用 SimpleNamespace 拼一个零值替身，免得把整条数据集加载链拉进鉴权用例。
    from pathlib import Path
    from types import SimpleNamespace

    def _vector_status():
        return {"artifact_ready": False, "embedding_client_configured": False,
                "embedding_probe_ok": None, "last_vector_error": "",
                "effective_text_mode": "", "vector_error_count": 0,
                "declared_available": False}

    fake_runtime = SimpleNamespace(
        version="test",
        meta={},
        index_dir=Path("/tmp/rag-auth-test-index"),
        settings=SimpleNamespace(data_dir=Path("/tmp/rag-auth-test-data")),
        text=SimpleNamespace(vector_available=False, vector_status=_vector_status),
        generate=SimpleNamespace(llm=SimpleNamespace(available=False),
                                 cache=SimpleNamespace(stats=lambda: {})),
    )

    saved_state = {name: getattr(api_mod.app.state, name, None)
                   for name in ("runtime", "load_error")}
    api_mod.app.state.runtime = fake_runtime
    api_mod.app.state.load_error = None
    settings = api_mod.app.state.settings
    saved_auth = (settings.require_auth, settings.jwt_secret, settings.bot_api_key)
    try:
        yield TestClient(api_mod.app), settings
    finally:
        settings.require_auth, settings.jwt_secret, settings.bot_api_key = saved_auth
        for name, value in saved_state.items():
            setattr(api_mod.app.state, name, value)


BODY = {"session_id": "ou_x:oc_y", "question": "介绍一下长平之战"}


def test_开启校验后_SSE_无凭证返回_401(auth_client):
    client, settings = auth_client
    settings.require_auth = True
    settings.jwt_secret = SECRET

    resp = client.post("/api/query", json=BODY)

    assert resp.status_code == 401
    assert resp.json()["error_code"] == "unauthorized"


def test_开启校验后_SSE_带有效凭证放行(auth_client):
    client, settings = auth_client
    settings.require_auth = True
    settings.jwt_secret = SECRET

    resp = client.post("/api/query", json=BODY, headers={"Token": valid_token()})

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]


def test_开启校验后_SSE_带伪造凭证仍是_401(auth_client):
    """"带了个 token"不等于"身份可信"——签名不符必须拒。"""
    client, settings = auth_client
    settings.require_auth = True
    settings.jwt_secret = SECRET

    forged = make_token({"user_id": 999, "exp": int(time.time()) + 60},
                        secret="攻击者自己的密钥")
    resp = client.post("/api/query", json=BODY, headers={"Token": forged})

    assert resp.status_code == 401


def test_校验放在限流之前(auth_client):
    """未认证请求不该消耗限流配额：连续打不应触发 429（否则成了免费的配额打击手段）。"""
    client, settings = auth_client
    settings.require_auth = True
    settings.jwt_secret = SECRET

    codes = {client.post("/api/query", json=BODY).status_code for _ in range(5)}
    assert codes == {401}


def test_未开启校验时行为与改造前一致(auth_client):
    """内网默认（不开 RAG_REQUIRE_AUTH）不带任何凭证也能用——不能因为加了能力就改变现状。"""
    client, settings = auth_client
    settings.require_auth = False
    settings.jwt_secret = ""
    settings.bot_api_key = ""

    assert client.post("/api/query", json=BODY).status_code == 200


def test_开启校验后_非流式接口接受有效_JWT(auth_client):
    client, settings = auth_client
    settings.require_auth = True
    settings.jwt_secret = SECRET
    settings.bot_api_key = ""

    resp = client.post("/api/query/json", json=BODY, headers={"Token": valid_token()})

    assert resp.status_code == 200


def test_开启校验后_非流式接口仍接受_Bot_Key(auth_client):
    """飞书机器人没有用户身份，旧后端也不会给它签 token——这条路不能被堵死。"""
    client, settings = auth_client
    settings.require_auth = True
    settings.jwt_secret = SECRET
    settings.bot_api_key = "bot-key-for-test"

    resp = client.post("/api/query/json", json=BODY,
                       headers={"X-Bot-Key": "bot-key-for-test"})

    assert resp.status_code == 200


def test_开启校验且无_Bot_Key_时_非流式接口拒绝匿名(auth_client):
    client, settings = auth_client
    settings.require_auth = True
    settings.jwt_secret = SECRET
    settings.bot_api_key = ""

    resp = client.post("/api/query/json", json=BODY)

    assert resp.status_code == 401
    assert resp.json()["error_code"] == "unauthorized"


# ---- 凭证撤销查询：验签通过 ≠ 仍然有效 ----


def _stub_introspector(monkeypatch, *, active=True, exc=None, enabled=True, calls=None):
    """把撤销查询器换成"不真的发 HTTP"的替身，并挂到 api 模块上。

    `calls` 传入一个列表即可断言"这次请求到底问没问过后端"——机器人路径不该被拦
    这条结论，只有靠"它压根没调用查询"才说得清楚。
    """
    from server.introspection import Introspector, Verdict

    client = (Introspector(url="http://127.0.0.1:5000/api/internal/token/introspect",
                           service_key="stub-key")
              if enabled else Introspector(url="", service_key=""))

    async def fake_post(token):
        if calls is not None:
            calls.append(token)
        if exc is not None:
            raise exc
        return Verdict(active)

    monkeypatch.setattr(client, "_post", fake_post)
    monkeypatch.setattr("server.api._introspector", lambda: client)
    return client


def test_撤销查询判定已失效时拒绝(auth_client, monkeypatch):
    """旧后端说"这张凭证不算了"，RAG 必须跟着拒——否则会放行到 token 过期。"""
    client, settings = auth_client
    settings.require_auth = True
    settings.jwt_secret = SECRET
    _stub_introspector(monkeypatch, active=False)

    resp = client.post("/api/query", json=BODY, headers={"Token": valid_token()})

    assert resp.status_code == 401
    assert resp.json()["error_code"] == "unauthorized"
    # 话术与旧后端一致：同一个"凭证失效"在两条通道上只该有一种说法
    assert "登录已失效" in resp.json()["message"]


def test_非流式接口同样执行撤销检查(auth_client, monkeypatch):
    client, settings = auth_client
    settings.require_auth = True
    settings.jwt_secret = SECRET
    _stub_introspector(monkeypatch, active=False)

    resp = client.post("/api/query/json", json=BODY, headers={"Token": valid_token()})

    assert resp.status_code == 401


def test_撤销查询判定有效时照常放行(auth_client, monkeypatch):
    """启用前先钉住"没被撤销就正常"，否则"一律拒绝"也能让上一条用例变绿。"""
    client, settings = auth_client
    settings.require_auth = True
    settings.jwt_secret = SECRET
    calls = []
    _stub_introspector(monkeypatch, active=True, calls=calls)

    resp = client.post("/api/query", json=BODY, headers={"Token": valid_token()})

    assert resp.status_code == 200
    assert calls == [valid_token()] or len(calls) == 1


def test_未配置撤销查询时不额外拦截(auth_client, monkeypatch):
    """内网默认（没配 URL/密钥）= 不查询，行为与改造前一致，不能因为加了能力就改变现状。"""
    client, settings = auth_client
    settings.require_auth = True
    settings.jwt_secret = SECRET
    calls = []
    _stub_introspector(monkeypatch, enabled=False, calls=calls)

    resp = client.post("/api/query", json=BODY, headers={"Token": valid_token()})

    assert resp.status_code == 200
    assert calls == []


def test_后端不可用时按_fail_closed_拒绝_并回_503(auth_client, monkeypatch):
    """默认策略：无法确认凭证状态时拒绝。

    理由写在 introspection.py 里——这是一条安全查询，"把后端打挂"不该成为一种
    绕过撤销的手段。要放行必须显式把失败策略改成 open。

    **回 503 而不是 401**：两者都是拒绝，但含义完全不同——
    401 让用户去重新登录（而新 token 同样会被拒），503 告诉他"稍后重试"。
    把"问不到后端"当成"凭证已失效"不行：后端抖一下就把全体用户"登出"了。
    """
    client, settings = auth_client
    settings.require_auth = True
    settings.jwt_secret = SECRET
    _stub_introspector(monkeypatch, exc=RuntimeError("connection refused"))

    resp = client.post("/api/query", json=BODY, headers={"Token": valid_token()})

    assert resp.status_code == 503
    assert resp.json()["error_code"] == "server_busy"
    assert "稍后重试" in resp.json()["message"]


def test_明确撤销时仍回_401(auth_client, monkeypatch):
    """对照组：后端**明确**说这张凭证不算了 → 401（用户该重新登录，而不是重试）。"""
    client, settings = auth_client
    settings.require_auth = True
    settings.jwt_secret = SECRET
    _stub_introspector(monkeypatch, active=False)

    resp = client.post("/api/query", json=BODY, headers={"Token": valid_token()})

    assert resp.status_code == 401
    assert resp.json()["error_code"] == "unauthorized"


def test_后端不可用时可按_fail_open_放行(auth_client, monkeypatch):
    """显式选 open 时故障不影响可用性——另一种取舍，但必须是选出来的而不是默认的。"""
    client, settings = auth_client
    settings.require_auth = True
    settings.jwt_secret = SECRET
    stub = _stub_introspector(monkeypatch, exc=RuntimeError("connection refused"))
    stub.fail_mode = "open"

    resp = client.post("/api/query", json=BODY, headers={"Token": valid_token()})

    assert resp.status_code == 200


def test_机器人共享密钥路径不受撤销查询影响(auth_client, monkeypatch):
    """飞书机器人没有用户身份，也就没有"这张 token 该不该承认"这个问题。

    若这里被拦，现象是"机器人突然不回话"，而排障方向会跑到机器人那侧去。
    """
    client, settings = auth_client
    settings.require_auth = True
    settings.jwt_secret = SECRET
    settings.bot_api_key = "bot-key-for-test"
    calls = []
    _stub_introspector(monkeypatch, active=False, calls=calls)

    resp = client.post("/api/query/json", json=BODY,
                       headers={"X-Bot-Key": "bot-key-for-test"})

    assert resp.status_code == 200
    assert calls == []


def test_未认证请求不触发撤销查询(auth_client, monkeypatch):
    """没带凭证的请求在验签那一步就该被拒，不该为它去问一次后端。"""
    client, settings = auth_client
    settings.require_auth = True
    settings.jwt_secret = SECRET
    calls = []
    _stub_introspector(monkeypatch, active=True, calls=calls)

    resp = client.post("/api/query", json=BODY)

    assert resp.status_code == 401
    assert calls == []


def test_health_报出撤销查询的启用状态(auth_client, monkeypatch):
    """health 要能让运维一眼看出"停用账号后 RAG 到底拦不拦"，而不是去读环境变量文件。"""
    client, settings = auth_client
    settings.require_auth = True
    settings.jwt_secret = SECRET
    settings.auth_mode = "jwt"
    # 配置项与查询器都要指向"已启用"：health 的状态来自查询器、告警来自配置，
    # 两者在生产里同源，用例里也要一起给，否则测的是一个不存在的组合。
    monkeypatch.setattr(settings, "introspect_url",
                        "http://127.0.0.1:5000/api/internal/token/introspect")
    monkeypatch.setattr(settings, "introspect_service_key", "stub-key")
    _stub_introspector(monkeypatch, active=True)

    payload = client.get("/api/health").json()

    revocation = payload["auth"]["revocation"]
    assert revocation["enabled"] is True
    assert revocation["ttl_seconds"] == 30.0
    assert revocation["fail_mode"] == "closed"
    # §2.7：三态口径 + 明确给出"撤销生效延迟的上界"（启用查询时就是缓存 TTL）
    assert revocation["policy"] == "enforced"
    assert revocation["max_delay_seconds"] == 30.0
    assert not any("撤销" in w for w in payload.get("warnings", []))


def test_health_未启用撤销查询时给出边界告警(auth_client, monkeypatch):
    """文档要求的"边界必须写明"落在可观测处：这条告警就是那句声明。"""
    client, settings = auth_client
    settings.require_auth = True
    settings.jwt_secret = SECRET
    settings.auth_mode = "jwt"
    monkeypatch.setattr(settings, "introspect_url", "")
    monkeypatch.setattr(settings, "introspect_service_key", "")
    _stub_introspector(monkeypatch, enabled=False)

    payload = client.get("/api/health").json()

    assert payload["auth"]["revocation"]["enabled"] is False
    # 未启用时上界是"token 的自然过期时间"，那个数值由签发端决定，本服务不猜（None）
    assert payload["auth"]["revocation"]["policy"] == "delayed"
    assert payload["auth"]["revocation"]["max_delay_seconds"] is None
    assert any("停用或改密码后" in w for w in payload.get("warnings", []))


def test_health_区分显式接受延迟与漏配(auth_client, monkeypatch):
    """`enabled=false` 既可能是"显式接受了延迟"，也可能是"根本没配"。

    对运维来说这两者意义完全不同（前者是决定、后者是遗漏），因此 health 必须能区分。
    """
    client, settings = auth_client
    settings.require_auth = True
    settings.jwt_secret = SECRET
    settings.auth_mode = "jwt"
    monkeypatch.setattr(settings, "introspect_url", "")
    monkeypatch.setattr(settings, "introspect_service_key", "")
    # 用 monkeypatch 而不是直接赋值：auth_client 夹具只还原 require_auth/jwt_secret/
    # bot_api_key 三项，直接赋值会泄漏到后面的用例（实测过一次：把"只配了一半"那条
    # 用例的告警变成了"已显式接受"）。
    monkeypatch.setattr(settings, "allow_delayed_revocation", True)
    _stub_introspector(monkeypatch, enabled=False)

    payload = client.get("/api/health").json()

    assert payload["auth"]["revocation"]["policy"] == "delayed"
    assert payload["auth"]["revocation"]["delayed_revocation_accepted"] is True
    # 决定的产物不再当成告警反复提示——否则会训练人忽略告警
    assert not any("停用或改密码后" in w for w in payload.get("warnings", []))


def test_撤销查询只配一半时的告警指名缺哪一项(auth_client, monkeypatch):
    """只配 URL 或只配密钥是最容易漏的一步，告警要直接说出缺的是哪个变量。"""
    client, settings = auth_client
    settings.require_auth = True
    settings.jwt_secret = SECRET
    settings.auth_mode = "jwt"
    monkeypatch.setattr(settings, "introspect_url",
                        "http://127.0.0.1:5000/api/internal/token/introspect")
    monkeypatch.setattr(settings, "introspect_service_key", "")

    payload = client.get("/api/health").json()

    assert any("RAG_INTERNAL_SERVICE_KEY" in w for w in payload.get("warnings", []))


def test_匿名请求在_runtime_加载失败时仍先得到_401(auth_client):
    """准入顺序。

    `/api/query` 若先查 runtime 再鉴权：runtime 加载失败时**匿名请求**会先拿到 503
    与脱敏后的加载错误——服务状态与内部故障信息泄露给了未认证的人；
    而 `/api/query/json` 是反过来的，两条通道口径不一致。
    """
    import server.api as api_mod

    client, settings = auth_client
    settings.require_auth = True
    settings.jwt_secret = SECRET
    # 模拟"数据没加载起来"：两条通道都必须在鉴权之前先拒绝匿名请求
    api_mod.app.state.runtime = None
    api_mod.app.state.load_error = "内部细节：快照目录 /srv/rag/data 不存在"

    sse = client.post("/api/query", json=BODY)
    plain = client.post("/api/query/json", json=BODY)

    assert sse.status_code == 401, "SSE 通道必须先鉴权"
    assert plain.status_code == 401, "JSON 通道顺序本来就对，别改坏"
    for response in (sse, plain):
        # FastAPI 的 TestClient 用 .text（Flask 那套是 get_data）
        assert "load_error" not in response.text
        assert "快照目录" not in response.text, "未认证请求不该看到内部加载错误"


def test_已认证请求在_runtime_加载失败时看到_503(auth_client):
    """对照组：身份没问题时，加载失败要如实回报——否则运维会以为"问答正常"。"""
    import server.api as api_mod

    client, settings = auth_client
    settings.require_auth = True
    settings.jwt_secret = SECRET
    api_mod.app.state.runtime = None
    api_mod.app.state.load_error = "runtime not loaded"

    response = client.post("/api/query", json=BODY, headers={"Token": valid_token()})

    assert response.status_code == 503


def test_health_带出鉴权口径且未开启时告警(auth_client):
    client, settings = auth_client
    settings.require_auth = False
    settings.jwt_secret = ""
    settings.bot_api_key = ""
    settings.auth_mode = "disabled"

    payload = client.get("/api/health").json()

    assert payload["auth"] == {"mode": "disabled", "required": False, "jwt_configured": False,
                               "bot_key_configured": False, "token_header": "Token",
                               "accepts_bearer": True,
                               # 未验签的档位没有"这张凭证该不该承认"这个问题，
                               # 因此 revocation 明确报成 policy=not-applicable
                               # （而不是"配了但没启用"——那会让运维去查一个不存在的配置）
                               "revocation": {"enabled": False, "policy": "not-applicable",
                                              "reason": "本服务未验签（非 jwt 档），"
                                                        "不做凭证撤销查询"}}
    assert any("未启用服务端身份校验" in w for w in payload.get("warnings", []))


def test_配了密钥但没开开关时给出更具体的告警(auth_client):
    """多半是想开但漏了配置——告警要指到那一步，而不是泛泛说"未启用"。"""
    client, settings = auth_client
    settings.require_auth = False
    settings.auth_mode = "disabled"
    settings.jwt_secret = SECRET

    payload = client.get("/api/health").json()

    assert any("RAG_AUTH_MODE" in w and "jwt" in w for w in payload.get("warnings", []))


def test_要开校验却没密钥时启动判定报错():
    """配置自相矛盾必须 fail-fast：否则服务起来后每个问答都 401。"""
    from config.settings import Settings

    settings = Settings.__new__(Settings)
    settings.auth_mode = "jwt"
    settings.require_auth = True
    settings.jwt_secret = ""

    problem = settings.auth_startup_problem()
    assert problem and "RAG_JWT_SECRET" in problem

    settings.jwt_secret = SECRET
    assert settings.auth_startup_problem() is None


# ---- 鉴权模式分档 ----


def _bare_settings(**kwargs):
    """按字段逐个赋值的 Settings 替身：这些判定只读少数几项，不必构造全量配置。"""
    from config.settings import Settings

    settings = Settings.__new__(Settings)
    settings.auth_mode = "disabled"
    settings.require_auth = False
    settings.jwt_secret = ""
    settings.require_active_version = False
    settings.require_active_version_explicit = False
    for key, value in kwargs.items():
        setattr(settings, key, value)
    return settings


def test_生产档下_disabled_拒绝启动():
    """生产 + disabled = "知道地址就能白用模型"；只打 WARNING 等于没拦（会被忽略）。"""
    settings = _bare_settings(auth_mode="disabled",
                              require_active_version=True,
                              require_active_version_explicit=True)

    problem = settings.auth_startup_problem()
    assert problem and "RAG_AUTH_MODE" in problem
    assert "jwt" in problem and "nginx" in problem


def test_开发档下_disabled_不阻断只告警():
    """本地开发没配鉴权是正常状态，不能因此起不来。"""
    settings = _bare_settings(auth_mode="disabled")

    assert settings.auth_startup_problem() is None
    assert settings.auth_warning() and "未启用服务端身份校验" in settings.auth_warning()


def test_生产档下_nginx_档放行并说明依赖():
    """nginx 把关是合法部署（服务只监听回环）：门禁不能把它判成坏配置。"""
    settings = _bare_settings(auth_mode="nginx",
                              require_active_version=True,
                              require_active_version_explicit=True)

    assert settings.auth_startup_problem() is None
    assert "nginx" in settings.auth_warning()


def test_鉴权模式拼错时拒绝启动():
    """静默回落成 disabled 会让运维以为自己在验签——必须直接拒绝。"""
    for bad in ("none", "off", "false", "basic", "jwt,nginx"):
        settings = _bare_settings(auth_mode=bad, jwt_secret=SECRET)
        problem = settings.auth_startup_problem()
        assert problem and "RAG_AUTH_MODE" in problem, f"{bad!r} 应被拒绝"


def test_鉴权模式的大小写与空白被容忍():
    """环境变量写成 `JWT` 或带空格是同一件事，不该判成坏配置。"""
    settings = _bare_settings(auth_mode=" JWT ", jwt_secret=SECRET)

    assert settings.auth_startup_problem() is None


def test_模式解析_新配置优先且兼容旧开关():
    """RAG_AUTH_MODE 显式写出时以它为准；否则 RAG_REQUIRE_AUTH=true 等价于 jwt 档。"""
    import importlib
    import os

    from config import settings as settings_mod

    saved = {k: os.environ.get(k) for k in ("RAG_AUTH_MODE", "RAG_REQUIRE_AUTH")}
    try:
        os.environ.pop("RAG_AUTH_MODE", None)
        os.environ["RAG_REQUIRE_AUTH"] = "true"
        assert settings_mod.get_settings().auth_mode == "jwt"

        # 新配置写出时优先，即使旧开关还是 true
        os.environ["RAG_AUTH_MODE"] = "nginx"
        loaded = settings_mod.get_settings()
        assert loaded.auth_mode == "nginx"
        assert loaded.require_auth is False, "nginx 档不在服务端验签"

        os.environ["RAG_REQUIRE_AUTH"] = "false"
        os.environ.pop("RAG_AUTH_MODE", None)
        assert settings_mod.get_settings().auth_mode == "disabled"
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        importlib.reload(settings_mod)


# ---- iss / aud / sub 校验 ----


def test_iss_不符的_token_被拒绝():
    """签名只证明"这把密钥签的"；iss 才证明"谁签的"。

    同一把 HS256 密钥可能被多个服务共用（内网里很常见），只验签等于谁签的都收。
    """
    token = make_token({"user_id": 7, "iss": "别的服务", "aud": auth.AUDIENCE_DEFAULT,
                        "exp": int(time.time()) + 3600})

    with pytest.raises(auth.AuthError) as excinfo:
        auth.identity_from_headers({"Token": token}, SECRET)
    assert "iss" in str(excinfo.value)


def test_aud_不符的_token_被拒绝():
    """发给别的服务的 token 不该能调 RAG（反之亦然）。"""
    token = make_token({"user_id": 7, "iss": auth.ISSUER_DEFAULT, "aud": "另一个服务",
                        "exp": int(time.time()) + 3600})

    with pytest.raises(auth.AuthError) as excinfo:
        auth.identity_from_headers({"Token": token}, SECRET)
    assert "aud" in str(excinfo.value)


def test_缺_iss_aud_的旧_token_被拒绝():
    """缺失与写错在安全上是同一件事：这个 token 没证明它该被本服务接受。"""
    token = make_token({"user_id": 7, "exp": int(time.time()) + 3600})

    with pytest.raises(auth.AuthError):
        auth.identity_from_headers({"Token": token}, SECRET)


def test_纯函数层不传期望值时仍然只校验签名():
    """`verify_hs256` 是通用验签器：期望值由调用方给，没给就不拦——
    否则每个只想验签的调用点都被迫关心 iss/aud，而默认值一旦写死又没法应付
    将来换签名方的情形。服务层的默认值在 `identity_from_headers` 上。
    """
    token = make_token({"user_id": 7, "exp": int(time.time()) + 3600})

    assert auth.verify_hs256(token, SECRET)["user_id"] == 7


def test_只有_sub_没有_user_id_时用_sub_当身份():
    """标准声明 `sub` 与旧的 `user_id` 指向同一个事实：签发端将来只发 sub 时，
    两端不必同时上线。"""
    token = make_token({"sub": "42", "iss": auth.ISSUER_DEFAULT,
                        "aud": auth.AUDIENCE_DEFAULT, "exp": int(time.time()) + 3600})

    assert auth.identity_from_headers({"Token": token}, SECRET)["user_id"] == "42"


def test_identity_的返回值形状保持稳定():
    """RAG 内部按 {"user_id", "role"} 消费身份；加声明不能顺手改这个形状。"""
    identity = auth.identity_from_headers({"Token": valid_token()}, SECRET)

    assert set(identity) == {"user_id", "role"}


def test_只配密钥未开开关时不阻断请求(auth_client):
    """只配了密钥、忘了开 RAG_REQUIRE_AUTH：服务应照常工作并告警，而不是把所有请求判成未认证。

    这是我第一版实现里的真实缺陷：`_resolve_identity` 一上来就看 require_auth，导致
    "配了密钥没开开关"时既验不出身份、又被后面的兜底分支判成 401——一个只想观察的配置
    会把线上问答整体打断。开关只决定"验不过时拦不拦"，与"要不要尝试验签"是两件事。
    """
    client, settings = auth_client
    settings.require_auth = False
    settings.jwt_secret = SECRET
    settings.bot_api_key = ""

    # 不带任何凭证：照常放行（内网行为不变）
    assert client.post("/api/query", json=BODY).status_code == 200
    assert client.post("/api/query/json", json=BODY).status_code == 200
    # 带了有效凭证：也放行（身份能被验出来并进上下文，便于先观察再开开关）
    assert client.post("/api/query/json", json=BODY,
                       headers={"Token": valid_token()}).status_code == 200


def test_配了_Bot_Key_时头值不对仍然_401(auth_client):
    """加了 JWT 支持不能让已配好的 Bot Key 形同虚设——那是改造前的硬要求。"""
    client, settings = auth_client
    settings.require_auth = False
    settings.jwt_secret = ""
    settings.bot_api_key = "bot-key-for-test"

    assert client.post("/api/query/json", json=BODY).status_code == 401
    assert client.post("/api/query/json", json=BODY,
                       headers={"X-Bot-Key": "wrong"}).status_code == 401
    assert client.post("/api/query/json", json=BODY,
                       headers={"X-Bot-Key": "bot-key-for-test"}).status_code == 200
    # 本例没配 JWT 密钥，所以那个 token 无从验证、不是通行证。
    # （两者都配时 JWT 也算数——非流式接口的目的是"两类调用方各自有路可走"，
    #   不是"配了 Bot Key 就排斥 JWT"，见 test_开启校验后_非流式接口接受有效_JWT）
    assert client.post("/api/query/json", json=BODY,
                       headers={"Token": valid_token()}).status_code == 401


# ---------------------------------- jwt 档与机器人通道


def test_jwt_档未配_Bot_Key_时给出告警(auth_client, monkeypatch):
    """jwt 档推出 require_auth=True，而机器人只发 X-Bot-Key → 每问必 401。

    这条告警是唯一能把排障方向指对的地方：机器人侧只会显示"RAG 不可用"。
    """
    client, settings = auth_client
    settings.require_auth = True
    settings.jwt_secret = SECRET
    settings.auth_mode = "jwt"
    monkeypatch.setattr(settings, "bot_api_key", "")

    payload = client.get("/api/health").json()

    assert any("RAG_BOT_API_KEY" in w for w in payload.get("warnings", []))


def test_jwt_档配了_Bot_Key_后不再告警(auth_client, monkeypatch):
    """对照组：配上了就不该继续提示——否则会把"已处理"当成待办。"""
    client, settings = auth_client
    settings.require_auth = True
    settings.jwt_secret = SECRET
    settings.auth_mode = "jwt"
    monkeypatch.setattr(settings, "bot_api_key", "shared-bot-key")

    payload = client.get("/api/health").json()

    assert not any("RAG_BOT_API_KEY" in w for w in payload.get("warnings", []))


def test_非_jwt_档不提示_Bot_Key(auth_client, monkeypatch):
    """nginx/disabled 档下机器人本来就不该被要求带 X-Bot-Key（本服务不验身份）。"""
    client, settings = auth_client
    settings.require_auth = False
    settings.jwt_secret = ""
    settings.auth_mode = "nginx"
    monkeypatch.setattr(settings, "bot_api_key", "")

    payload = client.get("/api/health").json()

    assert not any("RAG_BOT_API_KEY" in w for w in payload.get("warnings", []))


# ---------------------------------------- 配置自相矛盾与重复账号


def test_鉴权模式与旧开关冲突时拒绝启动(monkeypatch):
    """`RAG_REQUIRE_AUTH=true` 说"本服务验签"，`RAG_AUTH_MODE=nginx` 说"交给网关"。

    原实现让新模式静默覆盖旧开关，于是"两边都不拦"——而配置看起来是写了的。
    """
    from config.settings import get_settings

    monkeypatch.setenv("RAG_AUTH_MODE", "nginx")
    monkeypatch.setenv("RAG_REQUIRE_AUTH", "true")

    problem = get_settings().auth_startup_problem()

    assert problem and "自相矛盾" in problem


def test_鉴权模式为_jwt_时旧开关不冲突(monkeypatch):
    """两者语义一致（都说要本服务验签）时不该拦——否则升级配置的人会被误拒。"""
    from config.settings import get_settings

    monkeypatch.setenv("RAG_AUTH_MODE", "jwt")
    monkeypatch.setenv("RAG_REQUIRE_AUTH", "true")
    monkeypatch.setenv("RAG_JWT_SECRET", "x" * 40)

    assert get_settings().auth_startup_problem() is None


def test_只有旧开关时不冲突(monkeypatch):
    """改造前写的配置（只有 RAG_REQUIRE_AUTH）必须继续可用。"""
    from config.settings import get_settings

    monkeypatch.delenv("RAG_AUTH_MODE", raising=False)
    monkeypatch.setenv("RAG_REQUIRE_AUTH", "true")
    monkeypatch.setenv("RAG_JWT_SECRET", "x" * 40)

    assert get_settings().auth_startup_problem() is None
