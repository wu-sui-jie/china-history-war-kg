"""云端客户端的请求参数必须与调用方要求的一致（不联网，用假的 OpenAI 客户端）。

**为什么钉这几条。** `json_mode` 在这条链路上曾经是**哑参数**：本地 `OllamaAdapter.call`
签名里有它、函数体里从未使用，而三个抽取器全都以 `json_mode=True` 调用——等于"以为输出
被约束成 JSON，其实没有"，解析失败再触发重试，既慢又可能整段失败（在线接口的 P0-2）。
2026-09-27 在线接口改用 `DeepSeekClient` 后这条才真正生效，这里把它固定下来：
请求里必须带 `response_format`，服务不认时要能降级重试一次。

思考模式同样要钉：`deepseek-flash` / `deepseek-v4-pro` 默认**进入思考模式**，不显式关掉时
可见内容会是空串（实测 prompt_tokens 33、内容为空），下游只会报"JSON 解析失败"——方向完全错。
"""

import json
from types import SimpleNamespace

import pytest

from war_extraction.core.llm_client import LLMAPIError, DeepSeekClient


def _client(monkeypatch, model, responses):
    """造一个客户端，OpenAI 层换成假的；返回 (client, 每次 create 的 kwargs 列表)。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-测试用")
    monkeypatch.setenv("DEEPSEEK_MODEL", model)
    calls = []
    monkeypatch.setattr("war_extraction.core.llm_client.OpenAI",
                        _fake_openai(calls, model, responses))
    return DeepSeekClient(), calls


def _response(model, content):
    return SimpleNamespace(model=model,
                           choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _fake_openai(calls, model, responses):
    """假的 `OpenAI(...)`：`chat.completions.create` 记录 kwargs 并吐出预设回答。"""
    queue = list(responses)

    class _Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            item = queue.pop(0) if queue else '{"ok": true}'
            if isinstance(item, Exception):
                raise item
            return _response(model, item)

    class _Chat:
        completions = _Completions()

    class _OpenAI:
        chat = _Chat()

        def __init__(self, *args, **kwargs):
            self.init_kwargs = kwargs

    return _OpenAI


def test_客户端初始化时设定单次调用超时(monkeypatch):
    """超时层次里"单次模型调用 60s"就是这个值，改小了会误杀正常调用、改大了会顶穿反代。"""
    client, _calls = _client(monkeypatch, "deepseek-chat", [])

    assert client.client.init_kwargs["timeout"] == 60.0


def test_json_mode_会带_response_format(monkeypatch):
    client, calls = _client(monkeypatch, "deepseek-chat", ['{"places": []}'])

    client.call("提示词", json_mode=True)

    assert calls[0]["response_format"] == {"type": "json_object"}


def test_不开_json_mode_就不带_response_format(monkeypatch):
    client, calls = _client(monkeypatch, "deepseek-chat", ['{"places": []}'])

    client.call("提示词", json_mode=False)

    assert "response_format" not in calls[0]


def test_服务不认_response_format_时降级重试一次(monkeypatch):
    """带 response_format 被拒 → 去掉它再试一次；两次都记下来，行为可追溯。"""
    client, calls = _client(monkeypatch, "deepseek-chat",
                            [TypeError("unexpected keyword argument 'response_format'"),
                             '{"places": []}'])

    assert client.call("提示词", json_mode=True) == '{"places": []}'
    assert len(calls) == 2
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in calls[1]


def test_默认进思考模式的模型要显式关掉思考(monkeypatch):
    client, calls = _client(monkeypatch, "deepseek-flash", ['{"places": []}'])

    client.call("提示词")

    assert calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert client.thinking_disabled is True


def test_不需要关思考的模型不带_extra_body(monkeypatch):
    client, calls = _client(monkeypatch, "deepseek-chat", ['{"places": []}'])

    client.call("提示词")

    assert "extra_body" not in calls[0]


def test_空内容会明确报错而不是静默返回空串(monkeypatch):
    """思考模式吃掉可见内容时，下游只会报"JSON 解析失败"，指不到真正的原因。"""
    client, _calls = _client(monkeypatch, "deepseek-flash", ["   "])

    with pytest.raises(LLMAPIError) as excinfo:
        client.call("提示词")

    assert "空内容" in str(excinfo.value)
    assert "思考" in str(excinfo.value)


def test_Markdown_代码块会被剥掉(monkeypatch):
    client, _calls = _client(monkeypatch, "deepseek-chat",
                             ['```json\n{"places": [{"geo_name": "牧野"}]}\n```'])

    result = client.call("提示词", json_mode=True)

    assert json.loads(result) == {"places": [{"geo_name": "牧野"}]}
