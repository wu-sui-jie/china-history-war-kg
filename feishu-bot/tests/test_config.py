"""配置加载与校验的守护用例（开发文档第八节）。

配置错误必须在**启动时**炸掉，而不是等第一条消息进来才报错：
机器人是后台服务，没人会盯着日志看"第一条消息为什么没回"。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from config import DEFAULT_DB_PATH, Config, ConfigError, load_config

BOT_ROOT = Path(__file__).resolve().parent.parent


def test_missing_required_keys_are_listed_together():
    with pytest.raises(ConfigError) as err:
        load_config(env={}, skip_dotenv=True)
    message = str(err.value)
    assert "FEISHU_APP_ID" in message and "FEISHU_APP_SECRET" in message
    assert ".env.example" in message          # 给出可执行的修复路径


def test_required_keys_present_passes():
    config = load_config(env={"FEISHU_APP_ID": "cli_x", "FEISHU_APP_SECRET": "s"},
                         skip_dotenv=True)
    assert config.feishu_app_id == "cli_x"
    assert config.rag_base_url == "http://127.0.0.1:8000"
    assert config.rag_query_timeout == 25.0
    assert config.db_path == DEFAULT_DB_PATH


def test_env_overrides_are_applied():
    config = load_config(env={
        "FEISHU_APP_ID": "cli_x", "FEISHU_APP_SECRET": "s",
        "RAG_BASE_URL": "http://127.0.0.1:9000/",       # 末尾斜杠要被规整
        "RAG_QUERY_TIMEOUT": "12.5",
        "BOT_DB_PATH": "/tmp/x.db",
        "SESSION_TTL_HOURS": "48",
        "DEMO_EXAMPLES_ENABLED": "false",
        "SUBGRAPH_RENDER_ENABLED": "0",
        "BOT_LOG_LEVEL": "debug",
        "FEISHU_OPERATORS_CHAT_ID": "oc_ops",
    }, skip_dotenv=True)
    assert config.rag_base_url == "http://127.0.0.1:9000"
    assert config.rag_query_timeout == 12.5
    assert config.session_ttl_hours == 48.0
    assert config.demo_examples_enabled is False
    assert config.subgraph_render_enabled is False
    assert config.log_level == "DEBUG"
    assert config.feishu_operators_chat_id == "oc_ops"
    assert str(config.db_path).endswith("x.db")


@pytest.mark.parametrize("key,value", [
    ("RAG_QUERY_TIMEOUT", "abc"),
    ("SESSION_TTL_HOURS", "一天"),
    ("HISTORY_MAX_BYTES", "很多"),
])
def test_non_numeric_values_reported(key, value):
    with pytest.raises(ConfigError) as err:
        load_config(env={"FEISHU_APP_ID": "cli_x", "FEISHU_APP_SECRET": "s", key: value},
                    skip_dotenv=True)
    assert key in str(err.value)


def test_timeout_must_be_less_than_rag_budget():
    """机器人超时必须小于 RAG 非流式接口的 30s 预算（层层截断口径，开发文档 5.4）。"""
    with pytest.raises(ConfigError) as err:
        load_config(env={"FEISHU_APP_ID": "cli_x", "FEISHU_APP_SECRET": "s",
                         "RAG_QUERY_TIMEOUT": "30"}, skip_dotenv=True)
    assert "QUERY_JSON_TIMEOUT_SECONDS" in str(err.value)


@pytest.mark.parametrize("key,value", [
    ("RAG_QUERY_TIMEOUT", "0"),
    ("SESSION_TTL_HOURS", "-1"),
    ("HISTORY_MAX_BYTES", "0"),
    ("HISTORY_MAX_ITEMS", "0"),
    ("SUBGRAPH_RENDER_TIMEOUT", "-2"),
    ("CARD_DEDUPE_WINDOW_SECONDS", "-1"),
])
def test_out_of_range_values_reported(key, value):
    with pytest.raises(ConfigError) as err:
        load_config(env={"FEISHU_APP_ID": "cli_x", "FEISHU_APP_SECRET": "s", key: value},
                    skip_dotenv=True)
    assert key in str(err.value)


def test_invalid_log_level_reported():
    with pytest.raises(ConfigError) as err:
        load_config(env={"FEISHU_APP_ID": "cli_x", "FEISHU_APP_SECRET": "s",
                         "BOT_LOG_LEVEL": "VERBOSE"}, skip_dotenv=True)
    assert "BOT_LOG_LEVEL" in str(err.value)


def test_validate_can_be_called_directly():
    Config(feishu_app_id="cli_x", feishu_app_secret="s").validate()
    with pytest.raises(ConfigError):
        Config(feishu_app_id="", feishu_app_secret="").validate()


def test_defaults_match_documented_contract():
    """默认值对照开发文档第八节的表（改默认值必须同步文档）。"""
    config = Config(feishu_app_id="a", feishu_app_secret="b")
    assert config.rag_query_timeout == 25.0
    assert config.session_ttl_hours == 24.0
    assert config.history_max_bytes == 40 * 1024
    assert config.history_max_items == 40
    assert config.history_content_max_chars == 4000
    assert config.demo_examples_count == 3
    assert config.processed_events_ttl_hours == 24.0


# ---- 配置项与文档的一致性（防"文档里写了、代码里没读"）----


def _documented_keys() -> set[str]:
    """`.env.example` 里出现的键名（去掉注释与空行）。"""
    text = (BOT_ROOT / ".env.example").read_text(encoding="utf-8")
    keys = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.add(line.split("=", 1)[0].strip())
    return keys


def _read_keys() -> set[str]:
    """config.py 真正读取的环境变量名（`_env("X")` / `_float_env("X")` … 的首参）。"""
    source = (BOT_ROOT / "config.py").read_text(encoding="utf-8")
    return set(re.findall(
        r"_(?:env|float_env|int_env|bool_env)\(\s*\"([A-Z0-9_]+)\"", source))


def test_env_example_only_documents_keys_that_are_read():
    """文档里承诺的配置项必须真的被读取——写了不生效比没写更坑人。"""
    documented = _documented_keys()
    assert documented, ".env.example 里没有解析到任何配置项"
    unread = documented - _read_keys()
    assert not unread, f"这些配置项写在 .env.example 里但 config.py 不读：{sorted(unread)}"


def test_all_read_keys_are_documented():
    """反过来：代码读的每一项都要在模板里有位置，否则运维不知道能配它。"""
    missing = _read_keys() - _documented_keys()
    assert not missing, f"这些配置项 config.py 会读但 .env.example 没写：{sorted(missing)}"
