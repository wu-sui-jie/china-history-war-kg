"""请求身份校验：验证旧后端签发的 JWT。

## 为什么需要

`/api/query`（SSE）默认没有任何鉴权，`/api/query/json` 只有一个**可选的共享密钥**，
nginx 里的 `auth_basic` 默认是注释掉的。因此知道 `/rag/` 地址的人就能直接调用问答接口，
既不受旧系统的 admin/editor/viewer 控制，也能消耗模型费用与限流配额；
浏览器侧那个 uid 只用来拼 localStorage key，根本不是服务端身份。

这里让 RAG 用**旧后端同一把密钥**验签：能验出 `user_id` 就说明这个请求确实来自
已登录的主应用，身份进入请求上下文（`request.state.identity`），后续要按角色收敛
界面/配额时不必再引入第二套账号体系。

## 为什么用标准库自己实现 HS256，而不是 JWT 库

RAG 的依赖是**带哈希锁文件**管理并逐条审计的（CI 用 `--require-hashes` 安装），
而环境里既没有 PyJWT 也没在 `requirements.txt` 里声明。为了这一处引入一个新依赖，
就要重新生成锁文件并补一次供应链审计——而 HS256 验签本身只用
`hmac` + `hashlib` + `base64` 就能写全，且下面每一条拒绝分支都有对应用例。
把"少一个未审计依赖"与"多 40 行可读代码"放在一起权衡，这里选择后者。

## 安全口径（每条都由 tests/test_rag_auth.py 钉住）

- **算法白名单**：header.alg 必须严格等于 `HS256`。这一条挡的是经典攻击——
  把 alg 改成 `none` 让服务端跳过验签，或改成 `RS256` 骗服务端拿公钥当 HMAC 密钥。
- **签名用 `hmac.compare_digest` 比较**，不是 `==`，避免按字节比较带来的时序侧信道。
- **base64 严格解码**（`validate=True`）：宽松解码会放过非法字符，使同一个 token
  存在多种可接受拼写。
- **`exp` 必须存在且未过期**。缺 exp 的 token 是永久凭证，一旦泄露无法作废；
  旧后端签发的 token 一定带 exp（见 backend/jwt_util.py），要求它不会误伤正常流程。
- **有 `leeway` 容忍**（默认 30s）：服务端与签发端时钟不可能完全一致，零容忍会把
  刚签发的 token 判成过期。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from typing import Optional

# 允许的签名算法。只有一个取值是刻意的：放宽成"支持 RS256"就等于把算法混淆攻击
# 的入口留在代码里，而本系统两端的密钥完全由同一个部署方掌控，没有多算法需求。
ALLOWED_ALG = "HS256"

# 时钟偏移容忍（秒）。两端 Java/Python/systemd 的时钟可能有秒级差异，
# 零容忍会把"刚签发"的 token 判为过期，表现为用户登录后偶尔立刻 401。
DEFAULT_LEEWAY_SECONDS = 30.0

# 本系统对 `iss` / `aud` 的约定值（签发端见 backend/jwt_util.py）。
# 两者都把"谁签的"与"发给谁的"钉住：签名正确但来源不对的 token 不该被接受，
# 同一把密钥被多个服务共用时尤其重要。改这两个值必须两端同时改。
ISSUER_DEFAULT = "china-war-backend"
AUDIENCE_DEFAULT = "china-war-rag"


class AuthError(Exception):
    """token 不可信（缺失、格式错、签名不符、过期、算法不在白名单）。

    调用方只需捕获它并按"未认证"返回 401，不必逐个识别内部原因；
    `reason` 保留可读原因，只写日志、不回给客户端（避免给攻击者做指纹）。
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _b64url_decode(segment: str) -> bytes:
    """严格 base64url 解码：非法字符直接失败，不接受"跳过坏字符凑合解出来"。"""
    if not isinstance(segment, str) or not segment:
        raise AuthError("token 分段为空")
    padding = "=" * (-len(segment) % 4)
    try:
        return base64.b64decode(segment + padding, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AuthError(f"token 分段不是合法 base64url: {exc}") from exc


def _decode_json_object(raw: bytes, label: str) -> dict:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuthError(f"{label}不是合法 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise AuthError(f"{label}必须是 JSON 对象")
    return value


def verify_hs256(token: str, secret: str, *, now: Optional[float] = None,
                 leeway: float = DEFAULT_LEEWAY_SECONDS,
                 issuer: Optional[str] = None,
                 audience: Optional[str] = None) -> dict:
    """验签并返回 payload；任何不可信情形抛 AuthError。

    `issuer` / `audience` 传了才校验（传 None = 不检查这一项）。为什么不做成"永远校验、
    缺声明即拒绝"：token 一旦多一个必填声明，签发端没同步升级时所有请求会立刻 401，
    而这类失败在线上表现为"问答全挂"、在本地复现不出来。把期望值交给调用方
    （服务层从配置读，见 `identity_from_headers` 的默认值），纯函数层保持成一个
    通用的 HS256 验签器，两种用法都能被同一份用例覆盖。

    校验 `aud` 是有实际价值的：同一个密钥可能被多个服务共用，把给 RAG 的 token
    拿去调另一个服务（或反过来）本来不该成立。`iss` 同理——它把"谁签的"与
    "发给谁的"钉在了一起，签名正确但来源不对的 token 也进不来。

    `now` 可注入，便于用例构造"过期/未生效"的确定场景而不必 sleep。
    """
    if not secret:
        # 没配密钥却走到验签：这是调用方的配置错误，不是客户端的问题。仍然抛 AuthError，
        # 让上层 fail-closed（拒绝），而不是"没密钥就放行"。
        raise AuthError("未配置 JWT 密钥，无法验签")
    if not token or not isinstance(token, str):
        raise AuthError("缺少 token")

    parts = token.split(".")
    if len(parts) != 3:
        raise AuthError(f"token 分段数应为 3，实际 {len(parts)}")

    header = _decode_json_object(_b64url_decode(parts[0]), "header")
    alg = header.get("alg")
    if alg != ALLOWED_ALG:
        # 这一条同时挡住 alg=none（跳过验签）与 alg=RS256（算法混淆）
        raise AuthError(f"不支持的签名算法 {alg!r}（只接受 {ALLOWED_ALG}）")

    signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
    expected = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    provided = _b64url_decode(parts[2])
    if not hmac.compare_digest(expected, provided):
        raise AuthError("签名不匹配")

    payload = _decode_json_object(_b64url_decode(parts[1]), "payload")

    exp = payload.get("exp")
    if exp is None:
        raise AuthError("payload 缺少 exp（不接受无过期的永久凭证）")
    if not isinstance(exp, (int, float)) or isinstance(exp, bool):
        raise AuthError("exp 必须是数字")
    current = time.time() if now is None else now
    if current > float(exp) + leeway:
        raise AuthError("token 已过期")

    # nbf/iat 若存在也校验（旧后端当前不发 nbf，这里为将来留出正确行为）
    nbf = payload.get("nbf")
    if isinstance(nbf, (int, float)) and not isinstance(nbf, bool):
        if current < float(nbf) - leeway:
            raise AuthError("token 尚未生效")

    _check_claim(payload, "iss", issuer)
    _check_claim(payload, "aud", audience)

    return payload


def _check_claim(payload: dict, name: str, expected: Optional[str]) -> None:
    """期望值非空时校验该声明；缺失或不符都抛 AuthError。

    缺失也算不符（而不是"没写就跳过"）：漏掉声明与写错声明在安全上是同一件事——
    这个 token 没有证明它该被本服务接受。
    """
    if not expected:
        return
    actual = payload.get(name)
    if actual != expected:
        raise AuthError(f"{name} 不符（期望 {expected!r}，实际 {actual!r}）")


def extract_token(headers) -> str:
    """从请求头取 token：优先 `Token`（与旧后端一致），兼容 `Authorization: Bearer`。

    用 `Token` 作为主名是有意的：主应用已经用这个名字与旧后端通信，
    同一个 token 换一个头名传给 RAG 只会让两处的排障方式分叉。
    """
    token = (headers.get("Token") or "").strip()
    if token:
        return token
    authorization = (headers.get("Authorization") or "").strip()
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def identity_from_headers(headers, secret: str, *, now: Optional[float] = None,
                          issuer: Optional[str] = ISSUER_DEFAULT,
                          audience: Optional[str] = AUDIENCE_DEFAULT) -> dict:
    """验签并返回身份字典 `{"user_id": ..., "role": ...}`；失败抛 AuthError。

    `issuer` / `audience` 默认取本系统的约定值（签发端见 backend/jwt_util.py）。
    默认非空是有意的：调用方忘记传也不会退化成"不校验"——只有显式传 None 才跳过，
    而那些地方（纯函数用例）本来就在测验签本身。

    role 来自 token 的自定义声明：旧后端在签发时写入（见 backend/jwt_util.py），
    老 token 没有这个声明时取空串——**不猜、不兜底成 viewer**，让"有没有角色信息"
    这件事对调用方是可见的，避免又造一个"看起来有权限、其实是默认值"的假象。

    用户 id 优先读 `user_id`（与旧后端的既有契约），缺失时读标准声明 `sub`：
    两个名字指向同一个事实，同时接受是为了让"签发端将来只发 sub"这件事不需要
    两端同时上线——但**不发明**第三个名字。
    """
    payload = verify_hs256(extract_token(headers), secret, now=now,
                           issuer=issuer, audience=audience)
    user_id = payload.get("user_id")
    if user_id is None:
        user_id = payload.get("sub")
    if user_id is None:
        raise AuthError("payload 缺少 user_id / sub")
    role = payload.get("role") or ""
    return {"user_id": user_id, "role": str(role)}
