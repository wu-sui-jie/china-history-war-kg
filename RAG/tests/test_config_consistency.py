"""配置三处一致性的守护用例（RAG-7）。

配置项要在三个地方各写一次：`config/defaults.py` 的默认值、`Settings` 的 dataclass 字段、
`get_settings()` 里的 `os.environ.get(...)`。漏掉第三处时配置项会"永远等于默认值"，
而且不报错——这类漏接线只能靠机械校验发现。
"""

from __future__ import annotations

import inspect

from config import defaults
from config import settings as settings_mod
from config.settings import Settings, get_settings


# 不是 get_settings 输入的常量：
#   RAG_ROOT            —— defaults.py 内部拼路径用
#   ACTIVE_VERSION      —— 版本解析链路的默认输入（settings 走 RAG_ACTIVE_VERSION 环境变量，
#                          测试也会直接引用它断言"未固定版本"的行为）
#   VERSION_DATE_FORMAT —— 版本号日期格式，供 export/build 侧引用
_NON_CONFIG_CONSTANTS = {"RAG_ROOT", "ACTIVE_VERSION", "VERSION_DATE_FORMAT"}


def test_defaults_constants_are_wired_into_get_settings():
    """defaults.py 的每个配置常量都应被 get_settings 读取（防止加默认值忘接线）。"""
    src = inspect.getsource(settings_mod.get_settings)
    missing = [
        name
        for name, value in vars(defaults).items()
        if name.isupper() and not name.startswith("_") and not inspect.ismodule(value)
        and name not in _NON_CONFIG_CONSTANTS
        and f"defaults.{name}" not in src
    ]
    assert not missing, (
        f"这些 defaults 常量没有被 get_settings 读取：{missing}。"
        "要么在 get_settings 里接线，要么确认它不该留在 defaults.py"
    )


def test_dataclass_fields_are_all_populated_by_get_settings():
    """Settings 的每个字段都应被 get_settings 赋值（防止加了字段但没填值）。"""
    src = inspect.getsource(settings_mod.get_settings)
    missing = [
        name
        for name, field in Settings.__dataclass_fields__.items()  # type: ignore[attr-defined]
        if f"{name}=" not in src
    ]
    assert not missing, f"这些 Settings 字段没有被 get_settings 赋值：{missing}"


def test_get_settings_returns_expected_types():
    """抽查几个关键配置的类型，防止手工 int()/float() 漏写导致字符串流进下游。"""
    settings = get_settings()
    for name in ("rate_limit_per_minute", "query_top_k_text", "query_fusion_limit",
                 "text_query_max_words", "text_query_and_min_hits"):
        assert isinstance(getattr(settings, name), int), f"{name} 应是 int"
    for name in ("sse_heartbeat_seconds", "text_hybrid_keyword_weight"):
        assert isinstance(getattr(settings, name), float), f"{name} 应是 float"


def test_llm_key_alias_order_prefers_underscore_names(monkeypatch):
    """密钥别名链：下划线名优先于历史连字符名（Linux 只能用前者）。"""
    monkeypatch.setenv("RAG_COMMAND", "from-underscore")
    monkeypatch.setenv("RAG-command", "from-hyphen")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    settings = get_settings()
    assert settings.llm_api_key == "from-underscore"

    # 只保留历史名时仍能读到（兼容存量机器）
    monkeypatch.delenv("RAG_COMMAND", raising=False)
    settings = get_settings()
    assert settings.llm_api_key == "from-hyphen"
