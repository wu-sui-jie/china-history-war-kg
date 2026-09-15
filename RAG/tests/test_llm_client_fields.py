"""LLM 客户端字段兼容的守护用例（RAGv5 T2）。

背景：两个 endpoint 的流式推理字段名不同——中转（commandcode）用 `reasoning`，
官方 DeepSeek 用 `reasoning_content`。只认一个字段会导致另一端的思考增量被静默丢弃。
"""

from __future__ import annotations

from types import SimpleNamespace

from server.generate.llm_client import LLMClient


def _delta(**kwargs):
    return SimpleNamespace(content=None, **kwargs)


def test_reasoning_official_field():
    assert LLMClient._reasoning_of(_delta(reasoning_content="思考中")) == "思考中"


def test_reasoning_relay_field():
    """中转 endpoint 的字段名（实测）。"""
    assert LLMClient._reasoning_of(_delta(reasoning="推理片段")) == "推理片段"


def test_reasoning_official_takes_precedence():
    both = _delta(reasoning_content="官方字段", reasoning="中转字段")
    assert LLMClient._reasoning_of(both) == "官方字段"


def test_reasoning_dict_and_empty():
    assert LLMClient._reasoning_of(_delta(reasoning={"text": "字典形态"})) == "字典形态"
    assert LLMClient._reasoning_of(_delta(reasoning_content="", reasoning=None)) == ""
    assert LLMClient._reasoning_of(_delta()) == ""


def test_max_tokens_config_readable():
    """LLM_MAX_TOKENS 必须可配（推理模型设小会导致正文为空/截断）。"""
    from config.settings import get_settings

    s = get_settings()
    assert isinstance(s.llm_max_tokens, int) and s.llm_max_tokens >= 512


def test_llm_key_alias_chain_present():
    """密钥别名链：至少能取到一把 Key（本机实测有 RAG-command / DASHSCOPE 等）。"""
    from config.settings import get_settings

    s = get_settings()
    # 只断言"字段存在且为字符串"，不依赖本机是否配了密钥（CI/他人机器可能没配）
    assert isinstance(s.llm_api_key, str)
    assert isinstance(s.fallback_llm_api_key, str)
