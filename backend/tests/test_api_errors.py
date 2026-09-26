"""统一错误响应层的守护用例（文档第九节）。

**框架层**错误默认返回 Flask 的 HTML 页面：未捕获异常 → 500 HTML（调试模式下还
带完整堆栈），路径/方法不对 → 404/405 的 HTML。前端只有 `apiErrorMessage` 一处会读
`error.response.data.msg`，拿到 HTML 时读不到任何文案，界面只能显示通用的"操作失败"。

这里钉三件事：
1. 框架层的每类错误都是 **JSON + 正确 HTTP 状态**；
2. 内部异常**不外发**（真实原因只在日志里，响应里只有安全文案 + request_id）；
3. 各路由自己返回的响应**一字不变**（处理器不能把已有响应覆盖掉）。

注意：全局鉴权（`before_request`）先于路由解析执行，所以**不带 token 访问不存在的路径
得到的是 401 而不是 404**——那是既有行为，也是正确的（未登录时不暴露"这个路径存在吗"）。
本文件的 404/405 用例因此都带上合法 token。
"""

from __future__ import annotations


def test_路径不存在返回_json_而不是_html(client, make_user, auth):
    viewer = make_user("viewer-1", "viewer")

    response = client.get("/api/there-is-no-such-route", headers=auth(viewer))

    assert response.status_code == 404
    payload = response.get_json()
    assert payload is not None, "框架层的 404 也必须是 JSON，前端才有 msg 可读"
    assert payload["code"] == 404
    assert payload["msg"] == payload["message"]
    assert payload["data"] is None
    assert payload["request_id"]


def test_方法不对返回_405_json(client, make_user, auth):
    viewer = make_user("viewer-1", "viewer")

    response = client.delete("/api/admin/users", headers=auth(viewer))

    assert response.status_code == 405
    assert response.get_json()["code"] == 405


def test_未捕获异常返回_500_json_且不外发内部信息(client, make_user, auth, monkeypatch):
    """`str(e)` 常常带文件路径、SQL、库名——原样回客户端等于送出内部结构图。"""
    import app as app_module

    def _boom():  # noqa: ANN202
        raise RuntimeError("内部细节：/opt/china-war/backend/database 里的口令是 xxx")

    # 直接替换已注册的视图函数（Flask 首次请求后不允许再 add_url_rule）
    monkeypatch.setitem(app_module.app.view_functions, "auth.userinfo", _boom)
    viewer = make_user("viewer-1", "viewer")

    response = client.get("/api/userinfo", headers=auth(viewer))

    assert response.status_code == 500
    payload = response.get_json()
    assert payload["code"] == 500
    assert "内部细节" not in payload["msg"]
    assert "xxx" not in str(payload)
    assert payload["request_id"], "500 必须带 request_id，用户报障时才能定位到日志"


def test_响应头带_request_id_且采信上游传入(client, make_user, auth):
    """nginx 传下来的追踪号要沿用，否则前后端与日志三方对不上。"""
    viewer = make_user("viewer-1", "viewer")

    response = client.get("/api/there-is-no-such-route",
                          headers={**auth(viewer), "X-Request-Id": "trace-from-nginx"})

    assert response.headers["X-Request-Id"] == "trace-from-nginx"
    assert response.get_json()["request_id"] == "trace-from-nginx"


def test_上游传入的非法追踪号被丢弃(client, make_user, auth):
    """它是外部输入，直接回显到日志与响应头里有注入风险（换行会破坏响应头）。"""
    viewer = make_user("viewer-1", "viewer")

    response = client.get("/api/userinfo",
                          headers={**auth(viewer), "X-Request-Id": "附注-invalid"})

    value = response.headers["X-Request-Id"]
    assert value != "附注-invalid", "非 ASCII 的上游值不该被沿用"
    assert value.isalnum(), "回落到自动生成的值（uuid 片段）"


def test_路由自己的响应形状不变(client, make_user, auth):
    """处理器只在路由没返回响应时生效——登录失败那套"200 + code 403"必须保持原样。"""
    response = client.post("/api/login", json={"account": "nobody", "password": "nope"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["code"] == 403
    assert payload["msg"] == "用户名或密码错误"
    assert "request_id" not in payload, "路由自己拼的响应不该被处理器改写"


def test_未带_token_仍是原有的_401_文案(client):
    response = client.get("/api/admin/users")

    assert response.status_code == 401
    assert response.get_json()["msg"] == "您还未登录，请先登录"


def test_请求体不是_json_时按参数错误处理(client, make_user, auth):
    """内容类型声明为 JSON 但内容不是：Werkzeug 抛 400，必须是 JSON 形状而不是 HTML。"""
    editor = make_user("editor-1", "editor")

    response = client.post("/create_node", data="not-json",
                           content_type="application/json", headers=auth(editor))

    assert response.status_code in (400, 415, 422)
    assert response.get_json() is not None


def test_未登录访问受保护接口一律_401(client):
    """文档第九节的 HTTP 状态表：未登录 = 401（不是 200 + code 401）。"""
    assert client.get("/api/admin/users").status_code == 401
    assert client.get("/api/node/list").status_code == 401
