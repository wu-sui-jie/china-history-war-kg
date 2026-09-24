"""EER-7：主库路径解析（原来 4 处各写一遍"往上四层再拼 backend/database"）。

要求是**默认行为不变**、但能不改代码就指向别的库。**这条最重要**：默认值必须与改动前那个
四层 `dirname` 公式算出的一模一样，否则 geocoding 会悄悄去读另一个（不存在的）库。
"""

import os

from war_extraction.geocoding.db_path import (
    DB_PATH_ENV_VAR,
    default_db_path,
    repo_root,
    resolve_db_path,
)


def test_repo_root_is_repository_root():
    root = repo_root()
    assert os.path.isdir(os.path.join(root, "entity-event-relation"))
    assert os.path.isdir(os.path.join(root, "backend"))


def test_default_db_path_matches_legacy_formula():
    """默认路径 = 改动前的四层 dirname 公式，逐字符相同。"""
    legacy_file = os.path.join(repo_root(), "entity-event-relation", "war_extraction",
                               "geocoding", "export_unmapped_places.py")
    legacy = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(legacy_file)))),
        "backend", "database",
    )

    assert default_db_path() == legacy
    assert resolve_db_path() == legacy


def test_explicit_argument_wins(monkeypatch):
    monkeypatch.setenv(DB_PATH_ENV_VAR, "D:/env/database")
    assert resolve_db_path("/explicit/database") == "/explicit/database"


def test_env_var_overrides_default(monkeypatch):
    monkeypatch.setenv(DB_PATH_ENV_VAR, "D:/env/database")
    assert resolve_db_path() == "D:/env/database"
    assert default_db_path() != "D:/env/database"


def test_falls_back_to_default_without_env(monkeypatch):
    monkeypatch.delenv(DB_PATH_ENV_VAR, raising=False)
    assert resolve_db_path() == default_db_path()


def test_empty_env_var_falls_back(monkeypatch):
    """空字符串按"没设置"处理，避免误把空路径当成有效值。"""
    monkeypatch.setenv(DB_PATH_ENV_VAR, "")
    assert resolve_db_path() == default_db_path()
