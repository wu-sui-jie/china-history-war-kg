"""
主库（旧后端的 SQLite）路径解析。

EER-7：geocoding 子系统此前在 4 处各写了一遍"往上四层目录再拼 backend/database"
（`export_unmapped_places.py` 三处、`import_coordinates.py` 一处），既重复、又把这个离线模块
和 backend 的目录结构硬绑在一起——想换库或换机器就得改代码。这里收成一处解析，顺序为：

1. 显式传入的 `db_path` 参数；
2. 环境变量 `EER_DB_PATH`（想指向别处的库时用这个，不必改代码）；
3. 默认的仓库内 `backend/database`——**保持与原行为完全一致**，所以默认路径下行为不变。

注意：这里只解析路径，不保证文件存在——调用方自己检查并按原有方式报错
（`FileNotFoundError: 数据库文件不存在: ...`），避免把错误口径也一起改掉。
"""
from __future__ import annotations

import os

#: 覆盖默认库路径的环境变量名
DB_PATH_ENV_VAR = "EER_DB_PATH"

__all__ = ["DB_PATH_ENV_VAR", "repo_root", "default_db_path", "resolve_db_path"]


def repo_root() -> str:
    """仓库根目录（本文件位于 `<repo>/entity-event-relation/war_extraction/geocoding/`）。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def default_db_path() -> str:
    """默认主库路径：`<repo>/backend/database`。"""
    return os.path.join(repo_root(), "backend", "database")


def resolve_db_path(db_path: str = None) -> str:
    """按 参数 → `EER_DB_PATH` → 默认路径 的顺序解析主库路径。"""
    if db_path:
        return db_path
    from_env = os.environ.get(DB_PATH_ENV_VAR)
    if from_env:
        return from_env
    return default_db_path()
