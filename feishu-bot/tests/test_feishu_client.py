"""飞书 SDK 客户端的组装路径守护用例（不联网、不发送真实消息）。

为什么单独有这么一份：`bot/feishu_client.py` 是唯一导入 SDK 的模块，
其余模块靠鸭子类型替身测试——于是**"用真实 SDK 构造请求对象"这条路一直没有被覆盖**。
代价是：真机第一次启动时才发现 `FeishuClient` 忘了保存 `app_secret`，
第一次真发消息才可能发现某个 builder 方法名写错。

这里用一个"记录式 HTTP 客户端"替掉 SDK 的出站层：请求对象仍由**真实 SDK 的 builder**
构造（要覆盖的正是这层），只是不发出去，改成断言"构造出来的请求长什么样"。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from bot.cards.builder import build_answer_card
from bot.feishu_client import MSG_TYPE_CARD, MSG_TYPE_TEXT, FeishuClient


class RecordingMessageApi:
    """记录 reply/create/patch 收到的请求对象。"""

    def __init__(self):
        self.calls: list[tuple[str, object]] = []
        self.fail = False

    def _record(self, name: str, request):
        self.calls.append((name, request))
        if self.fail:
            return SimpleNamespace(success=lambda: False, code=99991, msg="假的失败", data=None)
        return SimpleNamespace(
            success=lambda: True, code=0, msg="",
            data=SimpleNamespace(message_id=f"om_sent_{len(self.calls)}"),
        )

    def reply(self, request):
        return self._record("reply", request)

    def create(self, request):
        return self._record("create", request)

    def patch(self, request):
        return self._record("patch", request)


class RecordingImageApi:
    def __init__(self):
        self.requests: list[object] = []

    def create(self, request):
        self.requests.append(request)
        return SimpleNamespace(success=lambda: True, code=0, msg="",
                               data=SimpleNamespace(image_key="img_v3_test"))


def make_client(monkeypatch) -> tuple[FeishuClient, RecordingMessageApi, RecordingImageApi]:
    """构造真 SDK 客户端，但把出站层换成记录器。"""
    client = FeishuClient(app_id="cli_test", app_secret="secret_test", log_level="WARNING")
    messages = RecordingMessageApi()
    images = RecordingImageApi()
    fake_http = SimpleNamespace(im=SimpleNamespace(
        v1=SimpleNamespace(message=messages, image=images)))
    monkeypatch.setattr(client, "_http", fake_http)
    return client, messages, images


def test_reply_card_builds_interactive_request(monkeypatch):
    client, messages, _ = make_client(monkeypatch)
    card = build_answer_card(answer_md="正文", citations=[{"index": 1, "title": "题", "kind": "k"}])

    message_id = client.reply_card("om_user_msg", card)

    name, request = messages.calls[-1]
    assert name == "reply"
    assert request.message_id == "om_user_msg"
    assert request.request_body.msg_type == MSG_TYPE_CARD == "interactive"
    # content 必须是 JSON **字符串**（SDK 不会帮你序列化 dict）
    payload = json.loads(request.request_body.content)
    assert payload["schema"] == "2.0"
    assert payload["body"]["elements"][0]["content"] == "正文"
    assert message_id == "om_sent_1"


def test_reply_text_builds_text_request(monkeypatch):
    client, messages, _ = make_client(monkeypatch)
    client.reply_text("om_user_msg", "兜底文本")

    _, request = messages.calls[-1]
    assert request.request_body.msg_type == MSG_TYPE_TEXT
    assert json.loads(request.request_body.content) == {"text": "兜底文本"}


def test_send_card_uses_chat_id_and_returns_message_id(monkeypatch):
    client, messages, _ = make_client(monkeypatch)
    client.send_card("oc_operators", build_answer_card(answer_md="工单"))

    name, request = messages.calls[-1]
    assert name == "create"
    assert request.receive_id_type == "chat_id"
    assert request.request_body.receive_id == "oc_operators"
    assert request.request_body.msg_type == MSG_TYPE_CARD


def test_patch_card_returns_ok(monkeypatch):
    client, messages, _ = make_client(monkeypatch)
    assert client.patch_card("om_bot_msg", build_answer_card(answer_md="更新")) is True

    name, request = messages.calls[-1]
    assert name == "patch"
    assert request.message_id == "om_bot_msg"
    assert json.loads(request.request_body.content)["schema"] == "2.0"


def test_upload_image_passes_file_object(monkeypatch, tmp_path: Path):
    """image 必须是**文件对象**：传 bytes 会被 SDK 静默转成字符串发出去（实测坑）。"""
    client, _, images = make_client(monkeypatch)
    png = tmp_path / "x.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    assert client.upload_image(png) == "img_v3_test"
    body = images.requests[-1].request_body
    assert body.image_type == "message"
    assert hasattr(body.image, "read"), "必须是文件对象而不是 bytes"
    body.image.close()


def test_upload_image_missing_file_returns_none(monkeypatch, tmp_path):
    client, _, _ = make_client(monkeypatch)
    assert client.upload_image(tmp_path / "nope.png") is None


def test_send_failures_return_none_and_do_not_raise(monkeypatch):
    """飞书返回业务错误码时：记日志、返回 None，不抛异常（调用方据此走容错）。"""
    client, messages, _ = make_client(monkeypatch)
    messages.fail = True
    assert client.reply_card("om_x", build_answer_card(answer_md="x")) is None
    assert client.send_card("oc_x", build_answer_card(answer_md="x")) is None
    assert client.patch_card("om_x", build_answer_card(answer_md="x")) is False


def test_client_keeps_credentials(monkeypatch):
    client, _, _ = make_client(monkeypatch)
    assert client.app_id == "cli_test"
    assert client.app_secret == "secret_test"


def test_build_ws_client_registers_both_handlers(monkeypatch):
    """长连接客户端要同时挂上消息事件与卡片回调（缺一个就是"某个功能静默失效"）。"""
    from lark_oapi.ws.client import Client as WsClient

    client, _, _ = make_client(monkeypatch)
    ws = client.build_ws_client(on_message=lambda data: None, on_card_action=lambda data: None)
    assert isinstance(ws, WsClient)
    handlers = ws._event_handler
    registered = set(handlers._processorMap) | set(handlers._callback_processor_map)
    assert "p2.im.message.receive_v1" in registered
    assert "p2.card.action.trigger" in registered


def test_close_is_safe_without_connection(monkeypatch):
    client, _, _ = make_client(monkeypatch)
    client.close()          # 不应抛异常（SDK 客户端没有公开的 close）
