"""飞书 SDK 封装：ws 接入、发消息/卡片、上传图片、PATCH 卡片（开发文档 5.1/5.6）。

**锁版本**：长连接客户端对卡片回调帧的分发是 SDK 内部实现细节，
升级可能静默改变行为——`requirements.txt` 里用 `==` 锁定 `lark-oapi`，
升级前必须回归按钮回调（需求文档风险 4）。

本模块是**唯一**导入 lark-oapi 的地方，其余模块（dispatcher / skills / cards）
只依赖鸭子类型，因此测试不需要 SDK、也不需要网络。
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

try:
    from lark_oapi import Client as LarkClient
    from lark_oapi import LogLevel
    from lark_oapi.api.im.v1 import (CreateImageRequest, CreateImageRequestBody,
                                     CreateMessageRequest, CreateMessageRequestBody,
                                     PatchMessageRequest, PatchMessageRequestBody,
                                     ReplyMessageRequest, ReplyMessageRequestBody)
    from lark_oapi.core.enum import AccessTokenType, HttpMethod
    from lark_oapi.core.http import Transport
    from lark_oapi.core.model import BaseRequest, RequestOption
    from lark_oapi.core.token.auth import verify
    from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
except ImportError as e:  # pragma: no cover - 只在依赖缺失/环境不对时触发
    # 裸 ModuleNotFoundError 对使用者毫无指导价值：这一处报错通常有两种原因
    # （真没装依赖 / 装了但用了另一个 Python），把两种都写清楚。
    raise ImportError(
        "缺少依赖 lark-oapi（飞书官方 SDK），或当前 Python 解释器不是装依赖的那个。\n"
        "  1) 安装依赖：pip install -r feishu-bot/requirements.txt\n"
        "  2) 若已装仍报此错，核对解释器是否一致：\n"
        "       python -c \"import sys, lark_oapi; print(sys.executable)\"\n"
        "     Windows 上用 `py main.py` 启动**不会**进入 conda 环境（py 启动器有自己的\n"
        "     默认解释器），请用 `python main.py`（先 conda activate AI_Agent）或环境的\n"
        "     绝对路径，例如 E:\\anaconda\\envs\\AI_Agent\\python.exe main.py\n"
        f"  原始错误：{e}"
    ) from e

log = logging.getLogger(__name__)

MSG_TYPE_CARD = "interactive"
MSG_TYPE_TEXT = "text"


class FeishuClient:
    """飞书 HTTP 客户端 + ws 长连接客户端。"""

    def __init__(self, *, app_id: str, app_secret: str, log_level: str = "INFO",
                 domain: str | None = None):
        self.app_id = app_id
        # app_secret 必须留住：HTTP 客户端与 ws 客户端都要用它各自建连接/换 token
        self.app_secret = app_secret
        builder = (LarkClient.builder()
                   .app_id(app_id)
                   .app_secret(app_secret)
                   .log_level(getattr(LogLevel, log_level.upper(), LogLevel.INFO)))
        if domain:
            builder = builder.domain(domain)
        self._http = builder.build()
        self._ws = None
        self._bot_open_id: str | None = None
        self._lock = threading.Lock()

    # ---- 发送 ----
    def reply_card(self, message_id: str, card: dict) -> str | None:
        """回复卡片到用户消息（reply）。返回机器人消息 id（落库用）。"""
        return self._reply(message_id, MSG_TYPE_CARD, card)

    def reply_text(self, message_id: str, text: str) -> str | None:
        return self._reply(message_id, MSG_TYPE_TEXT, {"text": text})

    def _reply(self, message_id: str, msg_type: str, payload: dict) -> str | None:
        body = (ReplyMessageRequestBody.builder()
                .msg_type(msg_type)
                # content 是 JSON **字符串**，不是对象
                .content(json.dumps(payload, ensure_ascii=False))
                .build())
        req = ReplyMessageRequest.builder().message_id(message_id).request_body(body).build()
        resp = self._http.im.v1.message.reply(req)
        if not resp.success():
            log.error("回复消息失败：code=%s msg=%s message_id=%s", resp.code, resp.msg,
                      message_id)
            return None
        return getattr(getattr(resp, "data", None), "message_id", None)

    def send_card(self, chat_id: str, card: dict) -> str | None:
        """主动发卡片到会话（工单投递等不使用 reply 的场景）。"""
        body = (CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type(MSG_TYPE_CARD)
                .content(json.dumps(card, ensure_ascii=False))
                .build())
        req = (CreateMessageRequest.builder()
               .receive_id_type("chat_id")
               .request_body(body)
               .build())
        resp = self._http.im.v1.message.create(req)
        if not resp.success():
            log.error("发送卡片失败：code=%s msg=%s chat_id=%s", resp.code, resp.msg, chat_id)
            return None
        return getattr(getattr(resp, "data", None), "message_id", None)

    def send_text(self, chat_id: str, text: str) -> str | None:
        body = (CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type(MSG_TYPE_TEXT)
                .content(json.dumps({"text": text}, ensure_ascii=False))
                .build())
        req = (CreateMessageRequest.builder()
               .receive_id_type("chat_id")
               .request_body(body)
               .build())
        resp = self._http.im.v1.message.create(req)
        if not resp.success():
            log.error("发送文本失败：code=%s msg=%s chat_id=%s", resp.code, resp.msg, chat_id)
            return None
        return getattr(getattr(resp, "data", None), "message_id", None)

    def patch_card(self, message_id: str, card: dict) -> bool:
        """整卡替换（PATCH /open-apis/im/v1/messages/:message_id）。

        注意：这是**整卡替换**，不保留原卡片未重建的部分（panel 等），
        所以调用方必须先把完整卡片重建出来。
        """
        body = (PatchMessageRequestBody.builder()
                .content(json.dumps(card, ensure_ascii=False))
                .build())
        req = PatchMessageRequest.builder().message_id(message_id).request_body(body).build()
        resp = self._http.im.v1.message.patch(req)
        if not resp.success():
            log.warning("卡片更新失败：code=%s msg=%s message_id=%s", resp.code, resp.msg,
                        message_id)
            return False
        return True

    # ---- 图片 ----
    def upload_image(self, path: Path | str) -> str | None:
        """上传图片得 image_key。

        必须传**文件对象**：SDK 的 multipart 组装会把 bytes/str 静默转成字符串，
        结果是发出去一个 `b'...'` 字面量（P0-4 实测坑）。
        """
        path = Path(path)
        try:
            with open(path, "rb") as handle:
                body = (CreateImageRequestBody.builder()
                        .image_type("message")
                        .image(handle)
                        .build())
                req = CreateImageRequest.builder().request_body(body).build()
                resp = self._http.im.v1.image.create(req)
        except OSError as e:
            log.warning("图片读取失败：%s（%s）", path, e)
            return None
        if not resp.success():
            log.error("图片上传失败：code=%s msg=%s path=%s", resp.code, resp.msg, path)
            return None
        return getattr(getattr(resp, "data", None), "image_key", None)

    # ---- 机器人身份 ----
    def get_bot_open_id(self) -> str | None:
        """取机器人自己的 open_id（群聊里判断是否 @ 了本机器人）。

        SDK 没有生成 bot.v3 资源，只能自己发一手请求；
        结果缓存：整个进程生命周期内不变。
        """
        with self._lock:
            if self._bot_open_id:
                return self._bot_open_id
        try:
            req = BaseRequest()
            req.http_method = HttpMethod.GET
            req.uri = "/open-apis/bot/v3/info"
            req.token_types = {AccessTokenType.TENANT}
            option = RequestOption()
            verify(self._http.config, req, option)     # 填充 tenant_access_token
            raw = Transport.execute(self._http.config, req, option)
            payload = json.loads(raw.content.decode("utf-8"))
            bot = payload.get("bot") or (payload.get("data") or {}).get("bot") or {}
            open_id = bot.get("open_id") or None
        except Exception as e:  # noqa: BLE001 - 拿不到就退化为"文本开头判断"
            log.warning("获取机器人 open_id 失败（群聊 @ 判断将退化为按 mention 文本判断）：%s", e)
            return None
        with self._lock:
            self._bot_open_id = open_id
        log.info("机器人 open_id：%s", open_id)
        return open_id

    # ---- 长连接 ----
    def build_ws_client(self, on_message, on_card_action):
        """组装 ws 客户端。`start()` 会阻塞（SDK 自持事件循环）。"""
        from lark_oapi.ws.client import Client as WsClient

        dispatcher = (EventDispatcherHandler.builder("", "")
                      .register_p2_im_message_receive_v1(on_message)
                      .register_p2_card_action_trigger(on_card_action)
                      .build())
        self._ws = WsClient(self.app_id, self.app_secret,
                            log_level=getattr(LogLevel, "INFO", LogLevel.INFO),
                            event_handler=dispatcher,
                            auto_reconnect=True)
        return self._ws

    def run_ws_forever(self) -> None:
        """阻塞运行 ws 客户端（主线程调用）。"""
        if self._ws is None:
            raise RuntimeError("请先调用 build_ws_client 组装事件处理器")
        self._ws.start()

    def close(self) -> None:
        """关闭底层连接池（SDK 的 Client 没有公开 close，只有 config/request）。"""
        for attr in ("_http", "http", "_session"):
            transport = getattr(self._http, attr, None)
            closer = getattr(transport, "close", None)
            if callable(closer):
                try:
                    closer()
                    return
                except Exception:  # noqa: BLE001
                    continue
        log.debug("未找到可关闭的底层连接（不影响进程退出）")
