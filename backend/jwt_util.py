# JWT（JSON Web Token） 常用于登录鉴权 / 接口身份校验
"""
JWT认证工具模块

功能: JWT Token生成与解析
  - encode(user_id): 生成Token（含签发时间、过期时间、来源与受众）
  - decode(token): 解析Token获取载荷，过期/伪造/来源不符会统一抛 TokenError
  - token_version_of(payload): 取 token 的用户版本号，供"强制下线"判断

## 第 13 轮整改：补全标准声明

原先只发 `user_id` / `role` / `iat` / `exp`，于是**验签只能证明"这把密钥签的"**：
同一把 HS256 密钥在内网里常被多个服务共用，任何知道密钥的服务都能为任意 user_id
签发一个能被本系统接受的凭证。现在补齐：

| 声明 | 作用 |
| --- | --- |
| `sub` | 标准的主体声明，与 `user_id` 同值（RAG 侧两者都认） |
| `iss` | 谁签的。RAG 校验它，挡住"别的服务拿同一把密钥签的 token" |
| `aud` | 发给谁的。防止把给 A 服务的 token 拿去调 B 服务 |
| `jti` | 本 token 的唯一编号，为将来做黑名单留出定位手段 |
| `ver` | 用户的 token 版本号。改密码/禁用账号时 +1，旧 token 立即失效 |

`iss` / `aud` 的取值必须与 RAG 侧 `RAG_JWT_ISSUER` / `RAG_JWT_AUDIENCE`
（默认 `china-war-backend` / `china-war-rag`）一致，否则表现为"登录成功但问答全 401"。

## 为什么用 HS256 而不是非对称签名

文档建议长期改用 RS256/ES256（backend 持私钥签发、RAG 只持公钥验签），这样 RAG
被入侵也无法伪造用户 token。当前保留 HS256 的原因：RAG 的依赖是带哈希锁文件逐条
审计的（CI 用 `--require-hashes` 安装），引入非对称签名需要新增密码学依赖并重走一次
供应链审计。这是一次**有记录的取舍**，不是遗漏——见 docs 的方案文档第三节第 3 条。
"""

import uuid
from datetime import datetime, timedelta, timezone

import jwt

import local_settings

# 密钥来源见 local_settings（环境变量 → backend/.env → 进程内随机）。
# 不要在源码里硬编码：本仓库是公开仓库。
secret = local_settings.jwt_secret()

# Token 有效期（秒），默认 7 天；用 JWT_TTL_SECONDS 覆盖。
#
# 文档建议把访问 token 缩短到 15–30 分钟。**这里没有直接改**：本系统只有访问 token、
# 没有 refresh token，把默认值调短等于让所有人在部署后立刻被登出、之后每半小时登出一次。
# 缩短有效期必须与刷新流程一起做，否则只是把安全成本转嫁给用户。当前用的是
# "token_version + 账号禁用"这条撤销路径（见 db_utils.check_token_usable），
# 它覆盖了实际的安全需求（改密码、封号后旧凭证立刻失效）。
DEFAULT_TTL_SECONDS = 7 * 24 * 3600

# 签发者与受众。改这两个值必须同步改 RAG 的 RAG_JWT_ISSUER / RAG_JWT_AUDIENCE。
ISSUER = "china-war-backend"
AUDIENCE = "china-war-rag"

# 未显式设置 token_version 的历史账号按 1 处理（迁移已把存量行回填为 1，
# 这里只是兜底，保证"空值"不会被当成"版本 0 的合法 token"）。
DEFAULT_TOKEN_VERSION = 1


class TokenError(Exception):
    """token 校验失败（过期、签名不符、格式错误、来源/受众不符）的统一异常类型。

    调用方只需捕获 TokenError 并按"未认证"处理，不必逐个识别 PyJWT 的异常类。
    """


def _ttl_seconds():
    """取 token 有效期；配置缺失或非法时回落到默认值。"""
    raw = local_settings.get("JWT_TTL_SECONDS", "")
    try:
        value = int(raw) if raw else DEFAULT_TTL_SECONDS
    except (TypeError, ValueError):
        return DEFAULT_TTL_SECONDS
    return value if value > 0 else DEFAULT_TTL_SECONDS


def token_version_of(payload):
    """取载荷里的用户版本号（`ver`），缺失或非法时回落到默认值。

    回落而不是报错：改造之前签发的 token 没有这个声明，报错会让所有在线用户
    在升级瞬间被登出；而"缺失 = 1"恰好与迁移回填的存量账号取值一致。
    """
    value = (payload or {}).get("ver", DEFAULT_TOKEN_VERSION)
    if isinstance(value, bool) or not isinstance(value, int):
        return DEFAULT_TOKEN_VERSION
    return value


# 把 user_id 生成一个 JWT 字符串（token）并返回
def encode(user_id, role=None, token_version=DEFAULT_TOKEN_VERSION):
    """签发 token。

    `role` 是可选的自定义声明（第 12 轮审查 P1-1）：RAG 服务端验签后能一并拿到角色，
    按角色收敛配额/界面时不必回查旧库、也不必引入第二套账号体系。
    老 token 没有这个声明，RAG 侧按空串处理——所以缺省是 None 而不是 "viewer"：
    把"没带角色"和"角色就是 viewer"区分开，避免制造一个看起来有含义的默认值。

    `token_version` 必须传当前用户的版本号（见 `UserInfo.token_version`）：
    它是"改密码/封号后旧 token 立刻失效"的唯一依据，调用方漏传会退化成 1，
    与库里已被 +1 的版本不符——那种情况下旧 token 仍是失效的（fail-closed）。
    """
    now = datetime.now(timezone.utc)
    payload = {
        "user_id": user_id,
        # sub 是 JWT 标准声明；与 user_id 同值并发是为了让 RAG 侧可以只认标准名，
        # 而不用在两端同时上线一个新的字段名。
        "sub": str(user_id),
        "iss": ISSUER,
        "aud": AUDIENCE,
        # 每个 token 一个编号：将来做黑名单时，撤销一条"还没过期但已泄露"的凭证
        # 需要能指名道姓地定位它，而 (user_id, iat) 这种组合并不唯一。
        "jti": uuid.uuid4().hex,
        "ver": int(token_version or DEFAULT_TOKEN_VERSION),
        "iat": now,
        # exp 是 JWT 标准声明，PyJWT 在 decode 时自动校验并拒绝过期 token
        "exp": now + timedelta(seconds=_ttl_seconds()),
    }
    if role:
        payload["role"] = str(role)
    return jwt.encode(payload, secret, algorithm='HS256')  # HS256 是对称签名：签发与验签同一把密钥


# 把前面生成的 token 解析回来，验证是否合法，并返回 {'user_id':123, ...}
# 过期抛 ExpiredSignatureError，签名不符/格式错误/来源受众不符抛 InvalidTokenError，
# 统一包成 TokenError。
def decode(encoded_jwt):
    """验签并返回载荷。

    `audience` / `issuer` 必须显式传：PyJWT 只要发现载荷里有 `aud` 就会要求一个期望值，
    不传会直接抛 InvalidAudienceError。更重要的是**只有传了才会真的比对**——
    这正是补 iss/aud 的意义所在（见模块文档）。
    """
    try:
        return jwt.decode(encoded_jwt, secret, algorithms=['HS256'],
                          audience=AUDIENCE, issuer=ISSUER)
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("登录已过期，请重新登录") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("登录凭证无效，请重新登录") from exc
