# JWT（JSON Web Token） 常用于登录鉴权 / 接口身份校验
"""
JWT认证工具模块

功能: JWT Token生成与解析
  - encode(user_id): 生成Token（含签发时间与过期时间）
  - decode(token): 解析Token获取user_id，过期或伪造会抛出 jwt 异常
"""

from datetime import datetime, timedelta, timezone

import jwt

import local_settings

# 密钥来源见 local_settings（环境变量 → backend/.env → 进程内随机）。
# 不要在源码里硬编码：本仓库是公开仓库。
secret = local_settings.jwt_secret()

# Token 有效期（秒），默认 7 天；用 JWT_TTL_SECONDS 覆盖。
DEFAULT_TTL_SECONDS = 7 * 24 * 3600


class TokenError(Exception):
    """token 校验失败（过期、签名不符、格式错误）的统一异常类型。

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


# 把 user_id 生成一个 JWT 字符串（token）并返回
def encode(user_id):
    now = datetime.now(timezone.utc)
    payload = {
        "user_id": user_id,
        "iat": now,
        # exp 是 JWT 标准声明，PyJWT 在 decode 时自动校验并拒绝过期 token
        "exp": now + timedelta(seconds=_ttl_seconds()),
    }
    return jwt.encode(payload, secret, algorithm='HS256') # HS256是JWT的签名算法，是对称加密（加密和解密用同一个密钥）

# 把前面生成的 token 解析回来，验证是否合法，并返回 {'user_id':123, ...}
# 过期抛 ExpiredSignatureError，签名不符或格式错误抛 InvalidTokenError，统一包成 TokenError。
def decode(encoded_jwt):
    try:
        return jwt.decode(encoded_jwt, secret, algorithms=['HS256']) # encoded_jwt是token字符串
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("登录已过期，请重新登录") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("登录凭证无效，请重新登录") from exc
