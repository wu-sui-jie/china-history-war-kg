"""服务间内部接口（文档第四节方案 B：Token Introspection）。

## 为什么需要

旧后端做到了"改密码 / 封号后旧 token 立刻失效"——`before_request` 每次都
回库查 `disabled` 与 `token_version`。但 **RAG 不查库**：它只验签名与声明，于是
管理员停用某个账号、或本人改了密码之后，旧 token 仍能调用 RAG 的问答接口，
直到 JWT 自然过期（默认 7 天）。安全动作在一半的系统上生效，等于没生效。

本模块提供一个只给内部服务用的查询接口，让 RAG 能问一句"这张凭证现在还作不作数"：

    POST /api/internal/token/introspect
    请求头：X-Internal-Service-Key: <INTERNAL_SERVICE_KEY>
    请求体：{"token": "<旧后端签发的 JWT>"}
    响应：  {"code": 200, "data": {"active": true, "user_id": 3, "role": "viewer",
                                   "token_version": 2, "disabled": false, "reason": ""}}

判定规则**完全复用 `DbUtil.token_status`**（与旧后端自己的守卫是同一份实现）：
账号不存在 / 已停用 / token 版本不符都返回 `active: false`。

## 为什么不用"缩短 token 有效期"这套（文档方案 A）

方案 A 需要 refresh token 与前端刷新流程（当前前端没有），改完最直接的后果是
"所有人每 30 分钟被登出一次"。方案 B 不碰用户的登录体验，只在服务间加一次
带缓存的查询，且立刻满足"停用后 RAG 也拒绝"这条验收标准。

## 为什么是 200 + active=false，而不是错误码

introspection 的语义就是"这是一次查询，答案可以是'无效'"。用 401 表达会让
调用方分不清"我的服务密钥不对"（那才是 401）与"被查的凭证已失效"。
所以：

| 情况 | HTTP | 含义 |
| --- | --- | --- |
| 服务密钥不对 / 缺失 | 401 | **调用方**没通过认证 |
| 未配置 INTERNAL_SERVICE_KEY | 503 | 本接口整体关闭（fail-closed） |
| 被查 token 无效 / 已撤销 | 200 | 查询成功，`active=false` |
| 请求体不是 JSON 对象 / 缺 token | 400 | 调用方用法错误 |

## 安全边界（三条，都由用例钉住）

1. **未配置密钥 = 接口不可用（503），而不是"不校验"**。这属于最容易被漏掉的
   一类缺陷：可选的保护开关漏配时会静默变成"没有保护"。一个匿名的 introspection
   接口不仅是信息泄露，还等于给爆破口令之外的第二种探测手段。
2. **失败的具体原因不回给调用方**，只写服务端日志（`_PUBLIC_FIELDS` 是白名单，
   响应字段必须是显式列出来的）。调用方只需要知道"这张凭证还作不作数"；
   "改过密码"与"被停用"的区别对排障有价值，对调用方没有，泄露出去反而等于
   把账号状态变成任何持有旧 token 的人都能查询的事实。
3. **公网必须不可达**。本服务只监听回环，且 nginx 对 `/api/internal/` 前缀直接
   返回 404（见 deploy/nginx/china-war.conf）——两道防线都不依赖"记得配密钥"。
"""

from __future__ import annotations

import hmac

from flask import Blueprint, jsonify, request

import local_settings
from db_utils import DbUtil
from jwt_util import TokenError, decode
from logging_util import get_logger

internal_bp = Blueprint("internal", __name__)
logger = get_logger(__name__)

# 内部接口的统一前缀。**app.py 的全局鉴权按这个前缀放行**（这些请求没有用户 token），
# 因此这里每一条路由都由下面的 before_request 统一守卫——新增路由不必记得加装饰器，
# 也不可能漏加。
INTERNAL_PREFIX = "/api/internal/"

# 服务间共享密钥的环境变量名。与 RAG 侧的 RAG_INTERNAL_SERVICE_KEY 必须同值。
SERVICE_KEY_ENV = "INTERNAL_SERVICE_KEY"

_HEADER = "X-Internal-Service-Key"

# 允许出现在响应里的字段**白名单**。写成白名单而不是"排除 reason"：
# 将来给 token_status 加字段时，默认行为是"不对外"，而不是"自动泄露"。
_PUBLIC_FIELDS = ("active", "user_id", "role", "token_version", "disabled")


def _configured_key() -> str:
    return (local_settings.get(SERVICE_KEY_ENV, "") or "").strip()


def _public(status: dict) -> dict:
    """裁出可外发的字段。"""
    return {name: status.get(name) for name in _PUBLIC_FIELDS}


def _invalid(user_id=None) -> dict:
    """统一的"这张凭证不作数"响应体（不含任何失败细节）。"""
    return {"active": False, "user_id": user_id, "role": "",
            "token_version": None, "disabled": False}


@internal_bp.before_request
def _require_service_key():
    """蓝图级守卫：本蓝图下所有路由都要求正确的服务密钥。

    放在蓝图钩子而不是逐个路由的装饰器上：漏加装饰器的后果是"某个内部接口对匿名开放"，
    而这种漏加在 review 里几乎看不出来（其余路由都长得一样）。钩子对蓝图内**所有**
    路由生效，新增接口自动被覆盖，失败方向只能是"忘了配密钥导致接口不可用"
    （503 一眼可见），不会是"忘了加校验导致接口开放"。
    """
    expected = _configured_key()
    if not expected:
        logger.error("内部接口被调用，但未配置 %s —— 按不可用处理（fail-closed）", SERVICE_KEY_ENV)
        return jsonify({
            "code": 503,
            "msg": f"内部接口未启用：未配置 {SERVICE_KEY_ENV}",
        }), 503
    provided = request.headers.get(_HEADER) or ""
    # 常量时间比较：按字符逐位比较会从响应时间上泄漏密钥前缀
    if not hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        logger.warning("内部接口服务密钥校验失败：%s %s from %s",
                       request.method, request.path, request.remote_addr)
        return jsonify({"code": 401, "msg": "服务密钥无效"}), 401
    return None


@internal_bp.route('/api/internal/token/introspect', methods=['POST'])
def introspect_token():
    """查询一张 JWT 当前是否仍然有效（是否被停用/撤销）。

    只接受 POST + JSON 对象：GET 会把 token 带进 URL，而 URL 会进 access log、
    浏览器历史与反代的日志，等于把凭证抄送一遍。
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"code": 400, "msg": "请求体必须是一个 JSON 对象"}), 400
    token = data.get("token")
    if not isinstance(token, str) or not token.strip():
        return jsonify({"code": 400, "msg": "缺少 token 字段"}), 400

    try:
        payload = decode(token.strip())
    except TokenError as exc:
        # 验签失败也是一种"查询成功"：结论是这张凭证不可信。
        # 具体原因只进日志——它对外是攻击者可以用来做指纹的信息。
        logger.warning("introspect：token 验签失败（%s）from %s", exc, request.remote_addr)
        return jsonify({"code": 200, "data": _invalid()})

    status = DbUtil.token_status(payload)
    if not status["active"]:
        # 这一条日志是"撤销是否真的生效"的证据：改密码/封号后应能在这里看到对应 user_id
        # 与原因；响应里则只回 active=false。
        logger.info("introspect：token 已失效 user_id=%s 原因=%s",
                    status.get("user_id"), status.get("reason"))
    return jsonify({"code": 200, "data": _public(status)})
