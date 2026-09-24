"""非流式问答（POST /api/query/json）的聚合逻辑守护用例。

对应 feishu-bot/docs/开发文档.md 第十节 10.1 的"聚合逻辑（喂假 SSE 帧）"一行：
正常序列、cache_hit 分支、error+done、超时中断、answer 拼接保真。

为什么值得单独锁住：非流式接口的存在意义是"与 SSE 同一份编排、结果天然一致"，
一旦聚合规则被顺手改坏（比如把增量 strip 掉、把 panel 事件漏掉），
飞书通道与网页通道就会静默分叉，而这种分叉在网页侧完全看不出来。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from contracts.request import QueryRequest
from contracts.sse import FinishReason, SSEEventType, StatusStage
from server.api import _FrameAggregator, _collect, _parse_frame
from server.sse import sse_format


def _frame(type_: str, data=None, stage: str | None = None) -> dict:
    return {"type": type_, "session_id": "ou_x:oc_y", "stage": stage, "data": data}


def _feed(agg: _FrameAggregator, frames: list[dict]) -> None:
    for frame in frames:
        agg.feed(frame)


# ---- 1) 正常序列：answer 增量拼接 + citations + panel + done ----


def _normal_frames() -> list[dict]:
    return [
        _frame(SSEEventType.SESSION_START.value, stage=StatusStage.START.value),
        _frame(SSEEventType.STATUS.value, stage=StatusStage.ENTITY_LINKING.value),
        _frame(SSEEventType.ENTITIES.value, {"entities": [{"name": "长平之战"}]}),
        _frame(SSEEventType.STATUS.value, stage=StatusStage.GRAPH_SEARCH.value),
        _frame(SSEEventType.GRAPH_RESULTS.value, {"evidence": [], "hit_entities": []}),
        _frame(SSEEventType.STATUS.value, stage=StatusStage.TEXT_SEARCH.value),
        _frame(SSEEventType.TEXT_RESULTS.value, {"evidence": [], "mode": "hybrid"}),
        _frame(SSEEventType.STATUS.value, stage=StatusStage.FUSION.value),
        _frame(SSEEventType.FUSION.value, {"evidence_count": 9, "conflicts": [],
                                           "citation_index": []}),
        _frame(SSEEventType.STATUS.value, stage=StatusStage.GENERATING.value),
        _frame(SSEEventType.ANSWER.value, {"delta": "# 长平之战\n\n秦赵"}),   # 首帧带换行
        _frame(SSEEventType.ANSWER.value, {"delta": "决战 [1]。"}),
        _frame(SSEEventType.ANSWER.value, {"delta": "  "}),                  # 纯空白增量不得丢失
        _frame(SSEEventType.ANSWER.value, {"delta": "\n结束。"}),
        _frame(SSEEventType.CITATIONS.value,
               {"citations": [{"index": 1, "evidence_id": "e1", "kind": "graph_triple",
                               "title": "长平之战—主战场→长平", "snippet": ""}],
                "conflicts": []}),
        _frame(SSEEventType.PANEL.value,
               {"entity_cards": [{"entity_id": "ev1", "type": "事件", "name": "长平之战"}],
                "subgraph": {"nodes": [], "edges": []},
                "timeline": {"groups": []},
                "map_points": []}),
        _frame(SSEEventType.DONE.value,
               {"finish_reason": FinishReason.NORMAL.value,
                "model_used": "deepseek/deepseek-v4.1-flash"}),
    ]


def test_normal_sequence_aggregates_all_parts():
    agg = _FrameAggregator()
    _feed(agg, _normal_frames())
    result = agg.result()

    assert result.answer_md == "# 长平之战\n\n秦赵决战 [1]。  \n结束。"
    assert result.finish_reason == FinishReason.NORMAL.value
    assert result.model_used == "deepseek/deepseek-v4.1-flash"
    assert result.cache_hit is False
    assert result.truncated is False
    assert result.error is None
    assert [c["index"] for c in result.citations] == [1]
    assert result.panel["entity_cards"][0]["name"] == "长平之战"


def test_answer_join_is_byte_exact():
    """拼接必须严格等于原文（与 SSE 通道 `_chunk_answer_stream` 的教训同源）。"""
    text = "第一段。\n\n- 甲\n- 乙\n\n尾句。"
    agg = _FrameAggregator()
    for ch in text:                       # 极端情况：一字一帧
        agg.feed(_frame(SSEEventType.ANSWER.value, {"delta": ch}))
    assert agg.result().answer_md == text


def test_truncated_flag_propagates():
    agg = _FrameAggregator()
    _feed(agg, [_frame(SSEEventType.DONE.value,
                       {"finish_reason": FinishReason.NORMAL.value, "model_used": "m",
                        "truncated": True})])
    assert agg.result().truncated is True


# ---- 2) cache_hit 分支：answer 整段 + citations + panel + done(cache_hit) ----


def test_cache_hit_branch_replays_full_payload():
    agg = _FrameAggregator()
    _feed(agg, [
        _frame(SSEEventType.STATUS.value, stage=StatusStage.CACHE_HIT.value),
        _frame(SSEEventType.ANSWER.value, {"delta": "整段回放的回答"},
               stage=StatusStage.CACHE_HIT.value),
        _frame(SSEEventType.CITATIONS.value,
               {"citations": [{"index": 1, "evidence_id": "e1", "kind": "raw_text",
                               "title": "t", "snippet": "s"}],
                "conflicts": [{"subject": "x", "field": "y", "evidence_ids": [],
                               "conflict_type": "different_object"}]},
               stage=StatusStage.CACHE_HIT.value),
        _frame(SSEEventType.PANEL.value, {"entity_cards": [], "subgraph": {"nodes": [],
                                                                          "edges": []},
                                          "timeline": {"groups": []}, "map_points": []},
               stage=StatusStage.CACHE_HIT.value),
        _frame(SSEEventType.DONE.value,
               {"finish_reason": FinishReason.NORMAL.value, "model_used": "m",
                "cache_hit": True}),
    ])
    result = agg.result()
    assert result.answer_md == "整段回放的回答"
    assert result.cache_hit is True
    assert result.panel == {"entity_cards": [], "subgraph": {"nodes": [], "edges": []},
                            "timeline": {"groups": []}, "map_points": []}
    assert result.conflicts and result.conflicts[0]["subject"] == "x"


def test_cache_hit_without_panel_event_yields_empty_panel():
    """缓存命中时 panel 为 None 的条目不会发 panel 事件 → 聚合结果必须是 {}。"""
    agg = _FrameAggregator()
    _feed(agg, [_frame(SSEEventType.ANSWER.value, {"delta": "x"}),
                _frame(SSEEventType.DONE.value, {"finish_reason": "normal",
                                                 "model_used": "", "cache_hit": True})])
    assert agg.result().panel == {}


# ---- 3) error + done(failed)：错误码保留，终态取 done ----


def test_error_event_then_failed_done():
    agg = _FrameAggregator()
    _feed(agg, [
        _frame(SSEEventType.STATUS.value, stage=StatusStage.GENERATING.value),
        _frame(SSEEventType.ANSWER.value, {"delta": "只写了一半"}),
        _frame(SSEEventType.ERROR.value, {"error_code": "server_busy", "message": "池满"}),
        _frame(SSEEventType.DONE.value,
               {"finish_reason": FinishReason.FAILED.value, "model_used": ""}),
    ])
    result = agg.result()
    assert result.error == {"error_code": "server_busy", "message": "池满"}
    assert result.finish_reason == FinishReason.FAILED.value
    # 部分正文仍被保留（调用方拿到的是 5xx，正文不会展示，但聚合本身不吞数据）
    assert result.answer_md == "只写了一半"


def test_first_error_wins():
    agg = _FrameAggregator()
    _feed(agg, [_frame(SSEEventType.ERROR.value, {"error_code": "server_busy",
                                                  "message": "第一次"}),
                _frame(SSEEventType.ERROR.value, {"error_code": "internal",
                                                  "message": "第二次"})])
    assert agg.result().error["error_code"] == "server_busy"


def test_refused_answer_carries_no_citations():
    """拒答：只有 answer + done(refused)，没有 citations/panel 事件。"""
    agg = _FrameAggregator()
    _feed(agg, [_frame(SSEEventType.ANSWER.value, {"delta": "知识库未检索到相关史料"}),
                _frame(SSEEventType.DONE.value,
                       {"finish_reason": FinishReason.REFUSED.value, "model_used": ""})])
    result = agg.result()
    assert result.finish_reason == FinishReason.REFUSED.value
    assert result.citations == [] and result.panel == {} and result.error is None


# ---- 4) 帧解析：非 data 帧与坏帧不得让聚合崩掉 ----


def test_parse_frame_ignores_non_data_lines():
    assert _parse_frame(": ping\n\n") is None
    assert _parse_frame("") is None
    assert _parse_frame("data: {broken") is None
    assert _parse_frame("data: [1,2]") is None      # 顶层不是对象
    assert _parse_frame('data: {"type": "answer"}') == {"type": "answer"}


def test_unknown_event_type_is_ignored():
    agg = _FrameAggregator()
    _feed(agg, [{"type": "thinking", "data": {"delta": "推理中"}},
                {"type": "session_start", "data": None},
                {"no_type": True}])
    assert agg.result().answer_md == ""


# ---- 5) _collect：真跑一次异步聚合，覆盖超时与生成器回收 ----


class _FakeRuntime:
    """_collect 只把 runtime 透传给 run_query，这里给个占位对象即可。"""


def _request() -> QueryRequest:
    return QueryRequest.from_dict({"session_id": "ou_x:oc_y", "question": "介绍一下长平之战"})


def test_collect_consumes_generator(monkeypatch):
    frames = [sse_format(f) for f in _normal_frames()]

    async def fake_run_query(rt, q):
        for frame in frames:
            yield frame

    monkeypatch.setattr("server.api.run_query", fake_run_query)
    result = asyncio.run(_collect(_FakeRuntime(), _request(), deadline=5.0))

    assert result.finish_reason == FinishReason.NORMAL.value
    assert result.answer_md.startswith("# 长平之战")
    assert result.error is None


def test_collect_times_out_and_closes_generator(monkeypatch):
    """超时：返回 error_code=timeout，且生成器被关闭（run_query 的 finally 得以执行）。"""
    state = {"closed": False}

    async def slow_run_query(rt, q):
        try:
            yield sse_format(_frame(SSEEventType.ANSWER.value, {"delta": "半句"}))
            await asyncio.sleep(30)          # 远超 deadline
            yield sse_format(_frame(SSEEventType.DONE.value, {"finish_reason": "normal"}))
        finally:
            state["closed"] = True

    monkeypatch.setattr("server.api.run_query", slow_run_query)
    result = asyncio.run(_collect(_FakeRuntime(), _request(), deadline=0.05))

    assert result.error is not None
    assert result.error["error_code"] == "timeout"
    assert result.answer_md == "半句"        # 超时前收到的增量保留
    assert state["closed"] is True
    # 超时轮没有 done 帧 → 终态为空（不得被误写成 normal）
    assert result.finish_reason == ""


def test_collect_closes_generator_on_error_frame(monkeypatch):
    state = {"closed": False}

    async def error_run_query(rt, q):
        try:
            yield sse_format(_frame(SSEEventType.ERROR.value,
                                    {"error_code": "internal", "message": "炸了"}))
            yield sse_format(_frame(SSEEventType.DONE.value,
                                    {"finish_reason": FinishReason.FAILED.value,
                                     "model_used": ""}))
        finally:
            state["closed"] = True

    monkeypatch.setattr("server.api.run_query", error_run_query)
    result = asyncio.run(_collect(_FakeRuntime(), _request(), deadline=5.0))

    assert result.error["error_code"] == "internal"
    assert result.finish_reason == FinishReason.FAILED.value
    assert state["closed"] is True


def test_collect_swallows_aclose_failure(monkeypatch):
    """收尾失败不得盖住真实结果（与 _stream_with_heartbeat 的处理一致）。"""

    async def broken_close_run_query(rt, q):
        try:
            yield sse_format(_frame(SSEEventType.ANSWER.value, {"delta": "答"}))
            yield sse_format(_frame(SSEEventType.DONE.value,
                                    {"finish_reason": "normal", "model_used": ""}))
        finally:
            raise RuntimeError("收尾时炸了")

    monkeypatch.setattr("server.api.run_query", broken_close_run_query)
    result = asyncio.run(_collect(_FakeRuntime(), _request(), deadline=5.0))
    assert result.answer_md == "答"
    assert result.finish_reason == "normal"


# ---- 6) 响应契约：data 字段齐全且不含 error ----


def test_result_to_dict_matches_contract():
    agg = _FrameAggregator()
    _feed(agg, _normal_frames())
    data = agg.result().to_dict()
    assert set(data) == {"answer_md", "citations", "conflicts", "panel",
                         "finish_reason", "model_used", "cache_hit", "truncated"}
    assert "error" not in data
    # 可 JSON 序列化（响应要直接进 JSONResponse）
    json.dumps(data, ensure_ascii=False)


def test_result_error_field_present_when_failed():
    agg = _FrameAggregator()
    agg.note_timeout(30.0)
    assert agg.result().to_dict()["error"]["error_code"] == "timeout"


@pytest.mark.parametrize("code", ["timeout", "server_busy", "internal"])
def test_error_http_status_mapping(code):
    from server.api import _ERROR_HTTP_STATUS

    assert _ERROR_HTTP_STATUS[code] in (500, 504)


# ---- 7) 路由层：鉴权、状态码与响应体形状（不起真实 runtime，只挂假 runtime）----


@pytest.fixture
def api_client(monkeypatch):
    """挂一个假 runtime 的 TestClient（不走 lifespan：那会真去加载数据制品）。"""
    from fastapi.testclient import TestClient

    import server.api as api_mod

    frames = [sse_format(f) for f in _normal_frames()]

    async def fake_run_query(rt, q):
        for frame in frames:
            yield frame

    monkeypatch.setattr("server.api.run_query", fake_run_query)
    saved = {name: getattr(api_mod.app.state, name, None)
             for name in ("runtime", "load_error", "settings", "rate_limiter")}
    api_mod.app.state.runtime = _FakeRuntime()
    api_mod.app.state.load_error = None
    try:
        yield TestClient(api_mod.app), api_mod
    finally:
        for name, value in saved.items():
            setattr(api_mod.app.state, name, value)


_BODY = {"session_id": "ou_x:oc_y", "question": "介绍一下长平之战"}


def test_route_returns_aggregated_json(api_client):
    client, _ = api_client
    resp = client.post("/api/query/json", json=_BODY)
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "ok"
    assert payload["data"]["answer_md"].startswith("# 长平之战")
    assert payload["data"]["finish_reason"] == FinishReason.NORMAL.value
    assert payload["data"]["citations"][0]["title"] == "长平之战—主战场→长平"
    assert "error" not in payload["data"]


def test_route_rejects_bad_request_before_orchestration(api_client):
    """参数不合法在进入编排前就用 400 拒绝（与 /api/query 同一口径）。"""
    client, _ = api_client
    resp = client.post("/api/query/json", json={"session_id": "", "question": ""})
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "invalid_request"


def test_route_503_when_runtime_missing(monkeypatch):
    from fastapi.testclient import TestClient

    import server.api as api_mod

    saved = {name: getattr(api_mod.app.state, name, None)
             for name in ("runtime", "load_error")}
    api_mod.app.state.runtime = None
    api_mod.app.state.load_error = "模拟：数据制品缺失"
    try:
        client = TestClient(api_mod.app)
        resp = client.post("/api/query/json", json=_BODY)
    finally:
        for name, value in saved.items():
            setattr(api_mod.app.state, name, value)
    assert resp.status_code == 503
    assert resp.json()["error_code"] == "internal"


def test_route_requires_bot_key_when_configured(api_client):
    """配置了 RAG_BOT_API_KEY 后：无头/错头 401，正确头放行。"""
    client, api_mod = api_client
    settings = api_mod.app.state.settings
    saved = settings.bot_api_key
    settings.bot_api_key = "s3cret-key"
    try:
        missing = client.post("/api/query/json", json=_BODY)
        wrong = client.post("/api/query/json", json=_BODY,
                            headers={"X-Bot-Key": "not-the-key"})
        ok = client.post("/api/query/json", json=_BODY,
                         headers={"X-Bot-Key": "s3cret-key"})
    finally:
        settings.bot_api_key = saved

    assert missing.status_code == 401
    assert missing.json()["error_code"] == "unauthorized"
    assert wrong.status_code == 401
    assert ok.status_code == 200


def test_route_timeout_maps_to_504(api_client):
    client, _ = api_client
    # 直接替换成慢生成器，模拟编排超预算
    import server.api as api_mod
    from unittest.mock import patch

    async def slow_run_query(rt, q):
        yield sse_format(_frame(SSEEventType.ANSWER.value, {"delta": "半句"}))
        await asyncio.sleep(30)

    original = api_mod.app.state.settings.query_json_timeout_seconds
    api_mod.app.state.settings.query_json_timeout_seconds = 0.05
    try:
        with patch("server.api.run_query", slow_run_query):
            resp = client.post("/api/query/json", json=_BODY)
    finally:
        api_mod.app.state.settings.query_json_timeout_seconds = original

    assert resp.status_code == 504
    assert resp.json()["error_code"] == "timeout"

