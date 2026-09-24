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
import time
import uuid
from pathlib import Path

try:
    # requests 是 lark-oapi 的硬依赖（SDK 传输层用的就是它）；单独 try 只是为了让
    # 缺依赖时的报错仍然指向 lark-oapi。退化分支故意让 `except TransportError`
    # 什么都抓不到（不重试），这比"把所有异常都当传输错误重试"安全。
    from requests.exceptions import RequestException as TransportError
except ImportError:  # pragma: no cover - 只在依赖异常的环境触发
    class TransportError(Exception):
        """占位：拿不到 requests 时不做任何重试。"""

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

# 传输层重试：一次瞬时抖动不能让用户收到沉默（2026-09-24 线上实测——RAG 正常返回
# 5.9s，组卡后对 open.feishu.cn 的 TLS 连接被对端掐断（SSLEOFError），异常一路抛到
# worker 的兜底 catch，用户什么也没收到）。根因是 requests 的 HTTPAdapter 默认
# max_retries=0、SDK 也没配重试，所以这一层只能自己兜。
# 只重试**传输层**异常（连接/SSL/超时）；业务错误码（resp.success() == False）
# 重试也不会变好，不在此列。
RETRY_ATTEMPTS = 3                  # 首次 + 2 次重试
RETRY_BACKOFF_SECONDS = (0.5, 1.0)


class FeishuClient:
    """飞书 HTTP 客户端 + ws 长连接客户端。"""

    def __init__(self, *, app_id: str, app_secret: str, log_level: str = "INFO",
                 domain: str | None = None):
        self.app_id = app_id
        # app_secret 必须留住：HTTP 客户端与 ws 客户端都要用它各自建连接/换 token
        self.app_secret = app_secret
        self.log_level = (log_level or "INFO").upper()
        builder = (LarkClient.builder()
                   .app_id(app_id)
                   .app_secret(app_secret)
                   .log_level(getattr(LogLevel, self.log_level, LogLevel.INFO)))
        if domain:
            builder = builder.domain(domain)
        self._http = builder.build()
        self._ws = None
        self._bot_open_id: str | None = None
        self._lock = threading.Lock()

    # ---- 传输层重试 ----
    def _call_with_retry(self, what: str, call, *, prepare=None):
        """把 SDK 出站调用包一层传输层重试（重试 2 次，退避 0.5s/1s）。

        - `call` 是**可重复调用**的闭包：每次尝试都重新组装请求对象。这是必须的，
          SDK 首次发送会把 `request.body` 换成 `MultipartEncoder`（图片上传），
          复用同一个请求对象重试读到的是空流；
        - `prepare` 每次尝试前调用（图片上传用来把文件指针复位到 0）；
        - 重试仍失败则**抛给调用方**：dispatcher 据此撤回本轮历史而不是让用户沉默，
          提示类消息则由调用方记日志。
        """
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            if prepare is not None:
                prepare()
            try:
                return call()
            except TransportError as e:
                if attempt >= RETRY_ATTEMPTS:
                    log.error("飞书 %s 传输层失败（已重试 %d 次）：%s",
                              what, attempt - 1, e)
                    raise
                delay = RETRY_BACKOFF_SECONDS[
                    min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
                log.warning("飞书 %s 传输层异常（第 %d 次尝试，%.1fs 后重试）：%s",
                            what, attempt, delay, e)
                time.sleep(delay)
        raise AssertionError("unreachable")   # pragma: no cover

    # ---- 发送 ----
    def reply_card(self, message_id: str, card: dict) -> str | None:
        """回复卡片到用户消息（reply）。返回机器人消息 id（落库用）。"""
        return self._reply(message_id, MSG_TYPE_CARD, card)

    def reply_text(self, message_id: str, text: str) -> str | None:
        return self._reply(message_id, MSG_TYPE_TEXT, {"text": text})

    def _reply(self, message_id: str, msg_type: str, payload: dict) -> str | None:
        # uuid 必须在重试之间**保持不变**：飞书按它做服务端幂等去重，
        # 否则"请求实际到达但响应丢失"的重试会发出第二条消息。
        msg_uuid = str(uuid.uuid4())

        def _send():
            body = (ReplyMessageRequestBody.builder()
                    .msg_type(msg_type)
                    # content 是 JSON **字符串**，不是对象
                    .content(json.dumps(payload, ensure_ascii=False))
                    .uuid(msg_uuid)
                    .build())
            req = (ReplyMessageRequest.builder().message_id(message_id)
                   .request_body(body).build())
            return self._http.im.v1.message.reply(req)

        resp = self._call_with_retry("回复消息", _send)
        if not resp.success():
            log.error("回复消息失败：code=%s msg=%s message_id=%s", resp.code, resp.msg,
                      message_id)
            return None
        return getattr(getattr(resp, "data", None), "message_id", None)

    def send_card(self, chat_id: str, card: dict) -> str | None:
        """主动发卡片到会话（工单投递等不使用 reply 的场景）。"""
        return self._create(chat_id, MSG_TYPE_CARD, card, what="发送卡片")

    def send_text(self, chat_id: str, text: str) -> str | None:
        return self._create(chat_id, MSG_TYPE_TEXT, {"text": text}, what="发送文本")

    def _create(self, chat_id: str, msg_type: str, payload: dict, *,
                what: str) -> str | None:
        msg_uuid = str(uuid.uuid4())          # 同 _reply：服务端幂等键，重试间不变

        def _send():
            body = (CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type(msg_type)
                    .content(json.dumps(payload, ensure_ascii=False))
                    .uuid(msg_uuid)
                    .build())
            req = (CreateMessageRequest.builder()
                   .receive_id_type("chat_id")
                   .request_body(body)
                   .build())
            return self._http.im.v1.message.create(req)

        resp = self._call_with_retry(what, _send)
        if not resp.success():
            log.error("%s失败：code=%s msg=%s chat_id=%s", what, resp.code, resp.msg, chat_id)
            return None
        return getattr(getattr(resp, "data", None), "message_id", None)

    def patch_card(self, message_id: str, card: dict) -> bool:
        """整卡替换（PATCH /open-apis/im/v1/messages/:message_id）。

        注意：这是**整卡替换**，不保留原卡片未重建的部分（panel 等），
        所以调用方必须先把完整卡片重建出来。
        PATCH 天然幂等（同样的 content 再发一次结果相同），不需要 uuid。
        """
        def _send():
            body = (PatchMessageRequestBody.builder()
                    .content(json.dumps(card, ensure_ascii=False))
                    .build())
            req = (PatchMessageRequest.builder().message_id(message_id)
                   .request_body(body).build())
            return self._http.im.v1.message.patch(req)

        resp = self._call_with_retry("更新卡片", _send)
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
            handle = open(path, "rb")
        except OSError as e:
            log.warning("图片读取失败：%s（%s）", path, e)
            return None
        try:
            def _send():
                # 每次尝试都重建请求体：SDK 首次发送会把 body 换成 MultipartEncoder
                body = (CreateImageRequestBody.builder()
                        .image_type("message")
                        .image(handle)
                        .build())
                req = CreateImageRequest.builder().request_body(body).build()
                return self._http.im.v1.image.create(req)

            resp = self._call_with_retry("上传图片", _send,
                                         prepare=lambda: handle.seek(0))
        finally:
            handle.close()
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
        """组装 ws 客户端。`start()` 会阻塞（SDK 自持事件循环）。

        `auto_reconnect=True` 是**有意固定**的：长连接断线要能自愈。代价是凭证写错时
        SDK 内部无限重试、线程不退出，服务"看起来活着"却永远收不到消息——排查办法见
        README「排查速查」里那条"假活"。日志级别跟随 `BOT_LOG_LEVEL`（排查长连接
        问题时可以降到 DEBUG）。
        """
        from lark_oapi.ws.client import Client as WsClient

        dispatcher = (EventDispatcherHandler.builder("", "")
                      .register_p2_im_message_receive_v1(on_message)
                      .register_p2_card_action_trigger(on_card_action)
                      .build())
        self._ws = WsClient(self.app_id, self.app_secret,
                            log_level=getattr(LogLevel, self.log_level, LogLevel.INFO),
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
