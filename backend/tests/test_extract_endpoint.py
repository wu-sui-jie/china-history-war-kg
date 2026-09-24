"""抽取接口的成功/失败口径（第 6 轮审核 M1、M2）。

M1：编排层曾经把三个阶段（实体/事件/关系）的异常全部 `warning` 掉继续跑，于是 Ollama
宕机时接口给出 **HTTP 200 + code 200 + "识别完成" + 全空结果**——把故障说成"这段文本没有
实体"，是最坏的一种错。现在的口径：
- 一个阶段都没成功 → `ExtractionUnavailable`，接口 5xx；
- 部分阶段失败 → 结果照常返回（局部成功仍有用），但响应体带 `partial_errors`。

M2：失败分支的 HTTP 状态必须与响应体的 code 一致（原先全是 HTTP 200 包 body 500/400）。

这里不打真模型：`extract_all_optimized` 与 `OllamaAdapter` 都由用例替换。
阶段统计本身另有一组直接调用 `llm_pipeline.extract_all_optimized` 的用例（用假 LLM 客户端）。
"""

import pytest

import llm_pipeline
from jwt_util import encode

# 一条通过实体抽取的模型输出（假客户端的第一答复）
ENTITY_JSON_ONE_PLACE = '{"places": [{"geo_name": "赤壁"}], "organizations": [], "persons": []}'


class FakeLLM:
    """假的大模型客户端。

    按顺序消费 `answers`；用完后若给了 `error` 就抛它，否则回一个空 JSON。
    三个抽取器都只通过 `llm.call(prompt, temperature=…, json_mode=…)` 取结果，
    所以这个形状足够驱动整条编排链（不需要任何网络）。
    """

    def __init__(self, answers=None, error=None):
        self.answers = list(answers or [])
        self.error = error
        self.calls = 0

    def call(self, prompt, temperature=0.1, json_mode=False):
        self.calls += 1
        if self.answers:
            return self.answers.pop(0)
        if self.error is not None:
            raise self.error
        return "{}"


# ---------------------------------------------------------------- 编排层（直调）


def test_所有阶段都失败时抛_ExtractionUnavailable():
    llm = FakeLLM(error=ConnectionError("Ollama 未启动（连接被拒绝）"))

    with pytest.raises(llm_pipeline.ExtractionUnavailable) as excinfo:
        llm_pipeline.extract_all_optimized(llm, "赤壁之战发生于公元 208 年。")

    assert "Ollama 未启动" in str(excinfo.value), "真实原因要带给调用方"
    assert excinfo.value.reasons, "失败明细可供接口与日志使用"


def test_部分阶段失败时返回结果并带_partial_errors():
    """实体阶段成功、事件阶段失败：结果能用（不能整体作废），但必须如实标注不完整。"""
    llm = FakeLLM(answers=[ENTITY_JSON_ONE_PLACE], error=ConnectionError("模型中途抽风"))

    entities, _event_result, _relations, diagnostics = llm_pipeline.extract_all_optimized(
        llm, "赤壁之战发生于公元 208 年。")

    assert [p.geo_name for p in entities.places] == ["赤壁"]
    assert diagnostics["stage_ok"]["entity"] == 1
    assert diagnostics["stage_ok"]["event"] == 0
    assert diagnostics["partial_errors"], "部分失败必须留下说明"
    assert any("事件抽取" in item for item in diagnostics["partial_errors"])


# ---------------------------------------------------------------- 接口层（HTTP）


@pytest.fixture()
def extract_env(monkeypatch):
    """装上假的 LLM 客户端与编排器，返回"让编排器返回什么"的开关。"""
    state = {"result": None, "error": None}

    monkeypatch.setattr(llm_pipeline, "OllamaAdapter", lambda *a, **k: object())

    def fake_extract(llm, text):
        if state["error"] is not None:
            raise state["error"]
        return state["result"]

    monkeypatch.setattr(llm_pipeline, "extract_all_optimized", fake_extract)
    return state


def _empty_result(partial_errors=None):
    from war_extraction.models import EntityExtractionResult, EventExtractionResult, RelationExtractionResult

    return (
        EntityExtractionResult(places=[], organizations=[], persons=[]),
        EventExtractionResult(events=[]),
        RelationExtractionResult(),
        {"stage_ok": {"entity": 1, "event": 1, "relation": 0},
         "partial_errors": list(partial_errors or [])},
    )


def test_整体不可用时返回_5xx_而不是假的识别完成(client, make_user, auth, extract_env):
    editor = make_user("editor-1", "editor")
    extract_env["error"] = llm_pipeline.ExtractionUnavailable(["实体抽取失败: 连接被拒绝"])

    response = client.post("/api/extract/entities-events", json={"text": "赤壁之战"},
                           headers=auth(editor))

    assert response.status_code == 500, "不能让客户端以为成功了"
    payload = response.get_json()
    assert payload["code"] == 500
    assert "不可用" in payload["msg"]
    assert payload["data"] == {}


def test_部分失败时_200_且响应体带_partial_errors(client, make_user, auth, extract_env):
    editor = make_user("editor-1", "editor")
    extract_env["result"] = _empty_result(["事件抽取失败: 模型超时"])

    response = client.post("/api/extract/entities-events", json={"text": "赤壁之战"},
                           headers=auth(editor))

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["code"] == 200
    assert payload["data"]["partial_errors"] == ["事件抽取失败: 模型超时"]
    assert payload["data"]["summary"]["event_count"] == 0


def test_全部成功时响应体不带_partial_errors(client, make_user, auth, extract_env):
    editor = make_user("editor-1", "editor")
    extract_env["result"] = _empty_result()

    response = client.post("/api/extract/entities-events", json={"text": "赤壁之战"},
                           headers=auth(editor))

    assert response.status_code == 200
    assert "partial_errors" not in response.get_json()["data"]


def test_异常字符串不回显内部细节(client, make_user, auth, extract_env):
    """异常原文可能带路径/连接串；回给客户端的只有第一行且截断。"""
    editor = make_user("editor-1", "editor")
    extract_env["error"] = RuntimeError(
        "第一行原因\nTraceback (most recent call last):\n  File \"C:/secret/path.py\", line 1")

    payload = client.post("/api/extract/entities-events", json={"text": "赤壁之战"},
                          headers=auth(editor)).get_json()

    assert "第一行原因" in payload["msg"]
    assert "Traceback" not in payload["msg"]
    assert "C:/secret/path.py" not in payload["msg"]


@pytest.mark.parametrize("body,expected_msg", [
    ({}, "请求参数不能为空"),
    ({"text": "   "}, "文本内容不能为空"),
    ({"text": "战" * 1001}, "文本内容过长"),
])
def test_参数错误返回_400_且带状态码(client, make_user, auth, body, expected_msg):
    editor = make_user("editor-1", "editor")

    response = client.post("/api/extract/entities-events", json=body, headers=auth(editor))

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["code"] == 400
    assert expected_msg in payload["msg"]


def test_畸形_JSON_返回_JSON_而不是_HTML_错误页(client, make_user, auth):
    editor = make_user("editor-1", "editor")

    response = client.post("/api/extract/entities-events", data="{不是 JSON",
                           content_type="application/json", headers=auth(editor))

    assert response.status_code == 400
    assert response.is_json, "get_json(silent=True) 的意义就是这里给 JSON 而不是 Flask 的 HTML 页"
    assert response.get_json()["code"] == 400


def test_未授权请求不会触达抽取逻辑(client, make_user, extract_env):
    """拦住未授权的请求要发生在调模型之前——否则配额已经被烧掉了。"""
    viewer = make_user("viewer-1", "viewer")
    extract_env["error"] = AssertionError("不该走到编排层")

    response = client.post("/api/extract/entities-events", json={"text": "赤壁之战"},
                           headers={"Token": encode(viewer.id)})

    assert response.status_code == 403
