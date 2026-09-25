"""统一的 API 错误响应（第 13 轮整改，文档第九节）。

## 为什么需要

改造前，**框架层**的错误返回的是 Flask 的 HTML 页面：

- 路由里抛出的未捕获异常 → 500 HTML（`FLASK_DEBUG=1` 时还带上完整堆栈与本地变量）；
- 路径不存在 / 方法不对 → 404、405 的 HTML 页面；
- 请求体超过限制 → 413 的 HTML 页面。

前端只有一处 `apiErrorMessage` 会去读 `error.response.data.msg`，拿到 HTML 时读不到任何
文案，界面上只能显示一句通用的"操作失败"——**服务端明明知道原因，用户看不到**。

## 响应形状

    {
      "code": 404,                  # 与 HTTP 状态一致（前端按数值 code 分支）
      "msg": "节点不存在",           # 既有字段名，前端已经在读
      "message": "节点不存在",       # 文档第九节用的字段名，一并给出
      "data": null,
      "request_id": "a1b2c3d4e5f6a7b8"
    }

**为什么 `code` 保持数值而不是文档示例里的字符串**（`"NODE_NOT_FOUND"`）：
前端所有页面都按 `code == 200` 这类数值分支，`frontend/src/utils/apiError.ts` 也读 `msg`。
把 code 换成字符串是一次跨端迁移（前端、快照用例、文档同时改），不是修缺陷；
这里先把"框架层错误没有 JSON 形状"这个真实缺口补上，并**同时给出 `message`**
（文档用的名字），让将来迁移时前端有现成的字段可读。

## 三条硬约束

1. **内部异常不外发**：500 只回一句安全文案，真实原因（含堆栈）进服务端日志，
   并带上 `request_id`；用户把 id 报过来就能在日志里定位到那一次请求。
   绝不回 `str(e)`——那会把文件路径、SQL、库名一起送出去。
2. **已有响应不覆盖**：`register_error_handler` 只在路由**没有**返回响应时生效，
   各路由自己返回的 `{code, msg}` 形状与内容一字不变。
3. **可追踪**：`request_id` 优先取上游（nginx）传来的 `X-Request-Id`，没有就生成一个，
   同时写进响应头，方便前后端与日志三方对齐。
"""

from __future__ import annotations

import uuid

from flask import g, jsonify, request
from werkzeug.exceptions import HTTPException

from logging_util import get_logger

logger = get_logger(__name__)

# 这些框架错误是"客户端用错了接口"，不是服务端故障——记 WARNING 就够，
# 记 ERROR 会把真正的故障信号淹掉（扫描器一天能贡献几千条 404）。
_CLIENT_ERROR_MAX = 499

_REQUEST_ID_HEADER = "X-Request-Id"


def request_id() -> str:
    """取本次请求的追踪号：优先上游传入，否则生成一个。"""
    existing = getattr(g, "_request_id", None)
    if existing:
        return existing
    incoming = (request.headers.get(_REQUEST_ID_HEADER) or "").strip()
    # 只接受可打印且不过长的上游值：它是外部输入，直接回显到日志与响应头里有注入风险
    if incoming and len(incoming) <= 64 and incoming.isascii() and incoming.isprintable():
        value = incoming
    else:
        value = uuid.uuid4().hex[:16]
    g._request_id = value
    return value


def error_payload(http_status: int, msg: str, *, code=None, data=None) -> dict:
    """拼统一错误体。`code` 可以覆盖（业务码与 HTTP 状态不一致时用）。

    `data` 用于保留调用方的响应形状：列表接口出错时仍要回空列表，前端才不会在
    `data.map` 上崩（第 13 轮复核第七节把这条推广到了各蓝图自己返回的 4xx 上）。
    """
    return {
        "code": http_status if code is None else code,
        "msg": msg,
        "message": msg,
        "data": data,
        "request_id": request_id(),
    }


def server_error(prefix: str, exc: Exception, *, data=None):
    """路由内部异常的统一出口：日志里记全量，响应里只给安全文案。

    各写入路由原先是 `return jsonify({"code": 500, "msg": str(e)})`——**HTTP 仍是 200**，
    而且 `str(e)` 常带文件路径、SQL、库名（文档第九节明确要求"内部异常写日志，
    对客户端只返回安全错误信息"）。这个函数把两件事一次做对：

    - `logger.exception` 把完整堆栈与 `request_id` 写进服务端日志；
    - 客户端拿到的是 `prefix + "，请稍后重试"`，以及可用于对账的 `request_id`。

    `data` 用于保留调用方的响应形状（列表接口失败时仍要回空列表，前端才不会在
    `data.map` 上崩）。
    """
    rid = request_id()
    logger.exception("接口异常：%s（%s %s，request_id=%s）",
                     prefix, request.method, request.path, rid)
    return jsonify(error_payload(500, f"{prefix}，请稍后重试", data=data)), 500


def json_body():
    """取请求体并断言它是一个 JSON 对象；不合格时返回 `(None, 错误响应)`。

    `data = request.json` 之后直接 `data.get(...)` 有两个坑（文档第九节第 1 条）：

    - 请求体是数组或字符串时抛 AttributeError，被路由的兜底 `except` 收成 **500**，
      于是一次参数错误在响应里表现为"服务器内部错误"，排查方向一开始就是错的；
    - 请求体为空且没带 `Content-Type: application/json` 时 `request.json` 抛 415/400，
      同样被兜底 except 收成 500。

    两者都应该是 400 + 明确文案。用法：

        data, error = json_body()
        if error:
            return error
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        got = "空" if data is None else type(data).__name__
        logger.warning("请求体不是 JSON 对象（%s）：%s %s（request_id=%s）",
                       got, request.method, request.path, request_id())
        return None, (jsonify(error_payload(400, "请求体必须是一个 JSON 对象")), 400)
    return data, None



def _safe_message(exc: HTTPException, http_status: int) -> str:
    """给客户端的文案：用框架自带描述，缺失时按状态码给一句中文兜底。"""
    description = (getattr(exc, "description", "") or "").strip()
    if description and description.isascii():
        # Werkzeug 的描述是英文模板（"The method is not allowed for the requested URL."），
        # 直接回给中文界面等于没给信息，这里换成我们自己的话术。
        return _DEFAULT_MESSAGES.get(http_status, "请求无法完成")
    return description or _DEFAULT_MESSAGES.get(http_status, "请求无法完成")


_DEFAULT_MESSAGES = {
    400: "请求参数有误",
    401: "您还未登录，请先登录",
    403: "没有操作权限",
    404: "请求的资源不存在",
    405: "请求方法不被支持",
    406: "服务端无法按请求的方式返回内容",
    409: "请求与当前状态冲突",
    413: "请求体过大",
    415: "不支持的请求内容类型",
    422: "请求参数校验失败",
    429: "请求过于频繁，请稍后再试",
    500: "服务器内部错误，请稍后重试",
    503: "依赖的服务暂时不可用，请稍后重试",
}


def install(app) -> None:
    """把统一错误处理注册到 Flask 应用上。"""

    @app.before_request
    def _assign_request_id():  # noqa: ANN202 - Flask 钩子不需要返回值
        request_id()

    @app.after_request
    def _emit_request_id(response):  # noqa: ANN001, ANN202
        value = getattr(g, "_request_id", None)
        if value:
            response.headers[_REQUEST_ID_HEADER] = value
        return response

    @app.errorhandler(HTTPException)
    def _handle_http_exception(exc: HTTPException):  # noqa: ANN202
        """所有框架级 HTTP 错误 → JSON。

        用 `HTTPException` 一个基类兜住 404/405/413/… 而不是逐个注册：漏注册一个
        就意味着那条路径又回到 HTML 响应，而"漏了哪个"恰恰是最难发现的。
        """
        status = exc.code or 500
        msg = _safe_message(exc, status)
        if status and status <= _CLIENT_ERROR_MAX:
            logger.warning("请求被拒：%s %s → %s %s（request_id=%s）",
                           request.method, request.path, status, msg, request_id())
        else:
            logger.error("服务端错误：%s %s → %s %s（request_id=%s）",
                         request.method, request.path, status, msg, request_id())
        return jsonify(error_payload(status, msg)), status

    @app.errorhandler(Exception)
    def _handle_unexpected(exc: Exception):  # noqa: ANN202
        """未捕获异常 → 500 JSON；**真实原因只进日志**。

        要安全文案的另一个理由：异常文本里经常带文件路径、SQL 语句、库名甚至连接串，
        原样回给客户端等于免费送出一份内部结构图（文档第九节"内部异常写日志，
        对客户端只返回安全错误信息"）。
        """
        rid = request_id()
        logger.exception("未处理异常：%s %s（request_id=%s）", request.method, request.path, rid)
        return jsonify(error_payload(500, _DEFAULT_MESSAGES[500])), 500
