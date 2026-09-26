"""F06 生成降级的守护用例（RAGv5）。

实测背景：deepseek 系列是推理模型，`max_tokens` 会被 reasoning token 吃满，
此时接口返回**空正文**且 `finish_reason=length`（不是 error）。28 题评测里有 2 题如此——
对演示是致命的（提问得到空白）。口径：**空正文一律降级到离线摘要回答器**，
`finish_reason=degraded`（不入缓存），保证任何情况下都有可读回答。
"""

from __future__ import annotations

import asyncio


from contracts.evidence import Confidence, Evidence, EvidenceKind, SourceType
from contracts.sse import FinishReason
from server.generate import AnswerGenerator


class _FakeLLM:
    """替身 LLMClient：可控 available / 流式返回内容。"""

    def __init__(self, text: str = "", error: str | None = None, truncated: bool = False):
        self.available = True
        self._text = text
        self._error = error
        self._truncated = truncated

    async def stream_chat(self, messages, on_delta, on_thinking=None, max_tokens=None,
                          stats_out=None):
        from server.generate.llm_client import LLMResponse

        if self._text:
            on_delta(self._text)
        return LLMResponse(
            text=self._text, model_used="fake-model",
            error=self._error, api_finish_reason="length" if self._truncated else "stop",
        )


def _evidence() -> list[Evidence]:
    return [Evidence(
        evidence_id="text_1", kind=EvidenceKind.RAW_TEXT,
        source_type=SourceType.ORIGINAL_TEXT, source_version="test",
        confidence=Confidence.MEDIUM, content={"text": "长平之战中白起大破赵军。"},
        related_entities=[], score=1.0, citation_index=1,
    )]


def _gen(llm) -> AnswerGenerator:
    from config.settings import get_settings

    g = AnswerGenerator(get_settings(), get_settings().cache_dir, "test")
    g.llm = llm
    return g


def test_empty_llm_output_degrades_to_heuristic():
    """空正文（推理吃满预算）→ 降级离线回答器，保证有可读回答。"""
    g = _gen(_FakeLLM(text="", truncated=True))
    reason, model, answer = asyncio.run(g.generate("介绍一下长平之战。", "介绍一下长平之战。", _evidence()))
    assert reason == FinishReason.DEGRADED.value
    assert model == "heuristic-offline"
    assert answer.strip(), "降级后必须有内容"
    assert g.last_truncated is True


def test_whitespace_only_output_also_degrades():
    g = _gen(_FakeLLM(text="   \n  "))
    reason, _model, answer = asyncio.run(g.generate("问题", "问题", _evidence()))
    assert reason == FinishReason.DEGRADED.value and answer.strip()


def test_normal_output_kept():
    g = _gen(_FakeLLM(text="长平之战中，赵军由赵括统率。"))
    reason, model, answer = asyncio.run(g.generate("问题", "问题", _evidence()))
    assert reason == FinishReason.NORMAL.value
    assert model == "fake-model" and "赵括" in answer
    assert g.last_truncated is False


def test_error_path_still_degrades():
    g = _gen(_FakeLLM(text="", error="llm_error: timeout"))
    reason, model, answer = asyncio.run(g.generate("问题", "问题", _evidence()))
    assert reason == FinishReason.DEGRADED.value and answer.strip()


def test_error_path_logs_reason(caplog):
    """降级必须留下可诊断的日志（静默降级会让排障困难）。"""
    import logging

    g = _gen(_FakeLLM(text="", error="llm_error: Connection error."))
    with caplog.at_level(logging.WARNING, logger="rag.generate"):
        reason, _model, _answer = asyncio.run(g.generate("问题", "问题", _evidence()))
    assert reason == FinishReason.DEGRADED.value
    assert any("Connection error" in r.getMessage() for r in caplog.records)
