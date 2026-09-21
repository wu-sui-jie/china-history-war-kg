# JWT（JSON Web Token） 常用于登录鉴权 / 接口身份校验
"""
JWT认证工具模块

功能: JWT Token生成与解析
  - encode(user_id): 生成Token
  - decode(token): 解析Token获取user_id
"""

import jwt

import local_settings

# 密钥来源见 local_settings（环境变量 → backend/.env → 进程内随机）。
# 不要在源码里硬编码：本仓库是公开仓库。
secret = local_settings.jwt_secret()


# 把 user_id 生成一个 JWT 字符串（token）并返回
def encode(user_id):
    return jwt.encode({'user_id': user_id}, secret, algorithm='HS256') # HS256是JWT的签名算法，是对称加密（加密和解密用同一个密钥）

# 把前面生成的 token 解析回来，验证是否合法，并返回 {'user_id':123}
def decode(encoded_jwt):
    return jwt.decode(encoded_jwt, secret, algorithms=['HS256']) # encoded_jwt是token字符串
