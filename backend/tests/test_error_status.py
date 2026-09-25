"""各蓝图的错误状态与"不外发内部信息"（第 13 轮复核第七节）。

第 13 轮先做了**框架层**（未知路径 / 方法不对 / 未捕获异常 → JSON；见 test_api_errors.py），
但各蓝图**自己**返回的错误仍是老形态：

    HTTP 200 + body {"code": 500, "msg": str(e)}

两个后果都很具体：

1. 监控、反代与前端都按状态码分流，而这里所有失败都长成"200 成功"，只能靠读 body 分辨；
2. `str(e)` 会把绝对路径、库名、连接串送给客户端（文档第七节第 3 条明确禁止）。

本文件按蓝图钉住迁移结果：**失败一律 4xx/5xx + 统一 JSON + 不回显异常原文**。
写这些用例的前提是"能让接口真的失败"——所以下面用 monkeypatch 把底层构建器换成
抛异常的桩，而不是去构造一个坏数据库。
"""

from __future__ import annotations

import contextlib
import logging

import pytest


@contextlib.contextmanager
def backend_logs(level=logging.INFO):
    """收集 `backend.*` 命名空间下发出的日志记录。

    为什么不用 pytest 的 `caplog`：`logging_util._configure_root()` 把 `backend` 这个
    根 logger 的 `propagate` 设成了 False（免得日志被 Flask 再打印一遍），而 caplog 是
    挂在 root 上的 —— 它一条都收不到。这里直接在 `backend` 上加一个内存 handler。
    """
    records: list[logging.LogRecord] = []

    class _Handler(logging.Handler):
        def emit(self, record):  # noqa: ANN001
            records.append(record)

    logger = logging.getLogger("backend")
    handler = _Handler()
    handler.setLevel(level)
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)


def _ok_body(response) -> dict:
    payload = response.get_json()
    assert payload is not None, "错误响应必须是 JSON（HTML 页面前端读不到 msg）"
    return payload


def _assert_no_internal_leak(response, *, secret: str = "内部细节") -> None:
    """响应体里不得出现异常原文、堆栈、库名或路径。"""
    raw = response.get_data(as_text=True)
    assert secret not in raw, "异常原文被回给了客户端"
    assert "Traceback" not in raw
    assert "sqlalchemy" not in raw.lower()
    assert "china-war" not in raw or "request" in raw  # 允许 request_id，不允许落出仓库路径


# ---------------------------------------------------------------- graph


def test_图谱子页接口失败返回_500(client, make_user, auth, monkeypatch):
    """改前是 HTTP 200 + code 500 + msg=str(e)。"""
    import blueprints.graph as graph_mod

    def _boom(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("内部细节：/opt/china-war/backend/database 连接失败")

    monkeypatch.setattr(graph_mod.neo4j_db_handle, "get_event_event_relations", _boom)
    viewer = make_user("viewer-1", "viewer")

    response = client.get("/api/graph/event_event", headers=auth(viewer))

    assert response.status_code == 500
    payload = _ok_body(response)
    assert payload["code"] == 500
    assert payload["msg"] == payload["message"]
    assert payload["request_id"]
    # 形状要保住：前端在 data.nodes / data.lines 上取值
    assert payload["data"] == {"nodes": [], "lines": []}
    _assert_no_internal_leak(response)


def test_全局搜索失败返回_500_且_data_仍是列表(client, make_user, auth, monkeypatch):
    import blueprints.graph as graph_mod

    def _boom(keyword):  # noqa: ANN001, ANN202
        raise RuntimeError("内部细节：库表 global_search 不存在")

    monkeypatch.setattr(graph_mod, "build_global_search", _boom)
    # GlobalSearch.vue 在 res.data 上做列表渲染，失败时给 {} 会在前端崩
    viewer = make_user("viewer-1", "viewer")

    response = client.get("/api/search/global?keyword=秦", headers=auth(viewer))

    assert response.status_code == 500
    assert _ok_body(response)["data"] == []
    _assert_no_internal_leak(response)


def test_关系分析的_depth_非法返回_400_而不是_500(client, make_user, auth):
    """改前 `int('abc')` 会被兜底 except 收成 500 + "invalid literal for int()" —— 
    一次参数错误被说成服务器故障，排查方向一开始就是错的。"""
    viewer = make_user("viewer-1", "viewer")

    response = client.get("/api/relation-analysis/query?name=秦&depth=abc", headers=auth(viewer))

    assert response.status_code == 400
    payload = _ok_body(response)
    assert "depth" in payload["msg"]
    _assert_no_internal_leak(response)


def test_节点子图缺少定位参数返回_400(client, make_user, auth):
    viewer = make_user("viewer-1", "viewer")

    response = client.get("/api/graph/node_context", headers=auth(viewer))

    assert response.status_code == 400
    assert _ok_body(response)["data"] == {"nodes": [], "lines": []}


def test_搜索接口的请求体不是_json_对象时返回_400(client, make_user, auth):
    """改前 request.get_json() 对数组/坏 JSON 抛异常 → 500。"""
    viewer = make_user("viewer-1", "viewer")

    response = client.post("/search_name_kg", json=["not", "an", "object"],
                           headers=auth(viewer))

    assert response.status_code == 400
    assert "JSON" in _ok_body(response)["msg"]


# ---------------------------------------------------------------- workspace


@pytest.mark.parametrize("path,prefix_key", [
    ("/api/dashboard/overview", "build_dashboard_overview"),
    ("/api/dataset/overview", "build_dataset_overview"),
    ("/api/quality/workbench", "build_quality_workbench"),
    ("/api/timeline/overview", "build_timeline_overview"),
])
def test_数据运营接口失败返回_500(client, make_user, auth, monkeypatch, path, prefix_key):
    import blueprints.workspace as workspace_mod

    def _boom(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("内部细节：report_builders 里的 SQL 出错")

    monkeypatch.setattr(workspace_mod, prefix_key, _boom)
    viewer = make_user("viewer-1", "viewer")

    response = client.get(path, headers=auth(viewer))

    assert response.status_code == 500
    payload = _ok_body(response)
    assert payload["code"] == 500
    assert payload["request_id"]
    _assert_no_internal_leak(response)


def test_时间轴事件失败时_data_保形状(client, make_user, auth, monkeypatch):
    """TimelineView 直接读 `res.data.events`：给 null 会让页面在渲染时崩。"""
    import blueprints.workspace as workspace_mod

    monkeypatch.setattr(workspace_mod, "build_timeline_overview",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("内部细节")))
    viewer = make_user("viewer-1", "viewer")

    response = client.get("/api/timeline/events", headers=auth(viewer))

    assert response.status_code == 500
    assert _ok_body(response)["data"] == {"events": []}


def test_实体详情参数缺失返回_400_不存在返回_404(client, make_user, auth, monkeypatch):
    import blueprints.workspace as workspace_mod

    viewer = make_user("viewer-1", "viewer")

    missing = client.get("/api/entity/detail?id=1", headers=auth(viewer))
    assert missing.status_code == 400

    monkeypatch.setattr(workspace_mod, "build_entity_detail", lambda *a, **k: None)
    not_found = client.get("/api/entity/detail?id=1&type=Event", headers=auth(viewer))
    assert not_found.status_code == 404
    assert _ok_body(not_found)["msg"] == "实体不存在"


# ---------------------------------------------------------------- node


def test_更新节点失败返回_500_并落日志(client, make_user, auth, monkeypatch):
    """改前：HTTP 200 + 无日志。失败在客户端表现为"成功但没变"，
    在服务端日志里没有任何痕迹——两处都查不出来。"""
    from db_utils import DbUtil

    def _boom(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("内部细节：update_node 的 SQL 失败")

    monkeypatch.setattr(DbUtil, "update_node", staticmethod(_boom))
    editor = make_user("editor-1", "editor")

    with backend_logs(logging.ERROR) as records:
        response = client.post("/update_node", json={"type": "Event", "id": 1, "name": "x"},
                               headers=auth(editor))

    assert response.status_code == 500
    assert _ok_body(response)["request_id"]
    assert any("更新节点失败" in record.getMessage() for record in records), \
        "异常必须写进服务端日志，否则这类故障无从定位"


def test_删除节点失败同样_500_且有日志(client, make_user, auth, monkeypatch):
    from db_utils import DbUtil

    def _boom(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("内部细节：delete_node 的 SQL 失败")

    monkeypatch.setattr(DbUtil, "delete_node", staticmethod(_boom))
    editor = make_user("editor-1", "editor")

    with backend_logs(logging.ERROR) as records:
        response = client.post("/delete_node", json={"type": "Event", "id": 1},
                               headers=auth(editor))

    assert response.status_code == 500
    assert any("删除节点失败" in record.getMessage() for record in records)


def test_分页参数非整数返回_400(client, make_user, auth):
    viewer = make_user("viewer-1", "viewer")

    response = client.post("/api/find_node_page", json={"pageNum": "abc", "pageSize": 10},
                           headers=auth(viewer))

    assert response.status_code == 400
    payload = _ok_body(response)
    assert payload["data"] == {"total": 0, "records": []}, "列表接口失败也要保持形状"


@pytest.mark.parametrize("path", [
    "/api/node/relations",
    "/api/node/by_type",
    "/api/node/by_relationship",
    "/api/node/search_by_name",
])
def test_节点读接口缺少必填参数返回_400(client, make_user, auth, path):
    viewer = make_user("viewer-1", "viewer")

    response = client.get(path, headers=auth(viewer))

    assert response.status_code == 400
    assert _ok_body(response)["code"] == 400


def test_写接口把_DbUtil_的业务码透传成_HTTP_状态(client, make_user, auth):
    """`DbUtil` 的写方法用 code 表达结果（200/400/404/500），而路由原先一律回 HTTP 200：
    "节点不存在"在监控、反代与前端重试逻辑眼里都是**成功**（第 13 轮复核第七节的状态码表）。"""
    editor = make_user("editor-1", "editor")

    unknown_type = client.post("/create_node", json={"type": "Bogus", "name": "x"},
                               headers=auth(editor))
    assert unknown_type.status_code == 400
    assert unknown_type.get_json()["code"] == 400

    missing = client.post("/update_node", json={"type": "Event", "id": 999999, "name": "x"},
                          headers=auth(editor))
    assert missing.status_code == 404
    assert missing.get_json()["code"] == 404


def test_写接口失败时_msg_不含异常原文(client, make_user, auth, monkeypatch):
    """`DbUtil` 原先返回 `f"创建失败: {str(e)}"`——SQL、列名与库文件路径一起送给客户端。"""
    from db_utils import DbUtil

    def _boom(node_type):  # noqa: ANN001, ANN202
        raise RuntimeError("内部细节：no such column: EventName 于 /opt/china-war/backend/database")

    monkeypatch.setattr(DbUtil, "_get_model_by_type", staticmethod(_boom))
    editor = make_user("editor-1", "editor")

    response = client.post("/create_node", json={"type": "Event", "name": "x"},
                           headers=auth(editor))

    assert response.status_code == 500
    payload = response.get_json()
    assert payload["code"] == 500
    raw = response.get_data(as_text=True)
    assert "内部细节" not in raw
    assert "no such column" not in raw


def test_节点详情不存在返回_404(client, make_user, auth, monkeypatch):
    import blueprints.node as node_mod

    monkeypatch.setattr(node_mod.neo4j_db_handle, "get_node_detail", lambda *a, **k: None)
    viewer = make_user("viewer-1", "viewer")

    response = client.get("/api/node/detail?id=999&type=Event", headers=auth(viewer))

    assert response.status_code == 404


# ---------------------------------------------------------------- llm


def test_推理失败不外发异常原文(client, make_user, auth, monkeypatch):
    """改前响应体里有 `error_detail = str(e)`，常带绝对路径与连接串。"""
    import llm_pipeline

    def _boom(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("内部细节：Ollama 连接 http://127.0.0.1:11434 失败")

    monkeypatch.setattr(llm_pipeline, "run_inference", _boom)
    editor = make_user("editor-1", "editor")

    with backend_logs(logging.ERROR) as records:
        response = client.post("/api/ai/inference", json={"question": "长平之战"},
                               headers=auth(editor))

    assert response.status_code == 500
    payload = response.get_json()
    assert payload["success"] is False
    assert "error_detail" not in payload, "异常原文不得回给客户端"
    assert "内部细节" not in response.get_data(as_text=True)
    assert "11434" not in response.get_data(as_text=True)
    # 但完整原因必须留在日志里
    assert any("RuntimeError" in record.getMessage() for record in records)
