"""敏感配置的唯一读取入口。

历史上 Neo4j 口令与 JWT 密钥硬编码在 model_search.py、sync_sqlite_to_neo4j.py、
jwt_util.py 里。本仓库是**公开仓库**，真实值不能入库，因此改为按以下顺序读取：

    环境变量  →  backend/.env  →  占位默认值

真实值写在 `backend/.env`（已被 .gitignore 忽略，格式见 `backend/.env.example`）。
环境变量优先，便于容器/CI 注入。
"""

from __future__ import annotations

import os
import secrets as _secrets
from pathlib import Path

_ENV_FILE = Path(__file__).resolve().parent / ".env"
_env_loaded = False


def _load_env_file() -> None:
    """把 backend/.env 的键值读进 os.environ（不覆盖已存在的环境变量）。"""
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True
    if not _ENV_FILE.is_file():
        return
    for raw in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def get(name: str, default: str = "") -> str:
    _load_env_file()
    return os.environ.get(name) or default


# ---- Neo4j（图谱库；旧问答与可视化都依赖它）----
NEO4J_URI = get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = get("NEO4J_PASSWORD", "")


def require_neo4j_password() -> str:
    """取 Neo4j 口令；未配置时抛出带指引的错误，避免表现为一个含糊的连接失败。"""
    if NEO4J_PASSWORD:
        return NEO4J_PASSWORD
    raise RuntimeError(
        "未配置 Neo4j 口令：请在 backend/.env 中设置 NEO4J_PASSWORD（或设置同名环境变量）。"
        "可复制 backend/.env.example 作为模板。"
    )


# ---- JWT（登录鉴权）----
_jwt_secret_cache: str | None = None


def jwt_secret() -> str:
    """取 JWT 密钥。

    未配置时**在进程内随机生成**：本地开发无需配置即可跑，副作用是进程重启后
    旧 token 失效（前端会要求重新登录）。生产/多进程部署必须显式配置
    JWT_SECRET，否则各进程密钥不同、token 互不认可。
    """
    global _jwt_secret_cache
    if _jwt_secret_cache is None:
        _jwt_secret_cache = get("JWT_SECRET", "") or _secrets.token_urlsafe(32)
    return _jwt_secret_cache
