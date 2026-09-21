"""F06 模型自拒识别的守护用例（2026-09-15 全项目审核 P0-1 整改）。

口径：提示词要求模型在证据不足时用固定句式说明（"依据现有资料无法确认"，见
prompts.py 规则 4）；生成返回前识别该句式（正文前 200 字内命中、且全文不含引用编号）
→ finish_reason=refused，前端据此显示"依据不足"。

反例是关键：带引用编号的"局部不确定"回答（"无法确认其出生年份，但据 [1] …"）不判拒答。
"""

from __future__ import annotations

import asyncio

from contracts.evidence import Confidence, Evidence, EvidenceKind, SourceType
from contracts.sse import FinishReason
from server.generate import AnswerGenerator
from server.generate.refusal import detect_model_refusal


class _FakeLLM:
    """替身 LLMClient：可控 available / 流式返回内容 / degraded 标记。"""

    def __init__(self, text: str, degraded: bool = False):
        self.available = True
        self._text = text
        self._degraded = degraded

    async def stream_chat(self, messages, on_delta, on_thinking=None, max_tokens=None,
                          stats_out=None):
        from server.generate.llm_client import LLMResponse

        if self._text:
            on_delta(self._text)
        return LLMResponse(text=self._text, model_used="fake-model", degraded=self._degraded)


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


# ---- 判定函数单测 ----

def test_detects_prompted_refusal_phrase():
    assert detect_model_refusal(
        "依据现有资料无法确认。当前证据中没有关于该问题的记载。") is True


def test_detects_common_equivalents():
    assert detect_model_refusal("根据现有资料无法回答：史料中没有相关记载。") is True
    assert detect_model_refusal("现有资料不足以确定其具体地点。") is True


def test_bare_cannot_answer_phrase_not_refusal():
    """「无法回答」单独短句不在词表内（词表刻意不收录短句，防误判正常回答）。

    2026-09-15 第二轮复查新增：词表将来被扩充时，这条是防口径回退的守护。
    """
    assert detect_model_refusal("无法回答") is False


def test_citation_near_miss_not_refusal():
    """带引用编号的“局部不确定”是正常回答，不判拒答。"""
    assert detect_model_refusal(
        "长平之战中赵军由赵括统率 [1]。其出生年份依据现有资料无法确认。") is False


def test_normal_answer_not_refusal():
    assert detect_model_refusal(
        "赤壁之战发生于公元208年，孙刘联军以火攻大败曹操 [1][2]。") is False
    assert detect_model_refusal("") is False


def test_phrase_after_head_window_not_refusal():
    """拒答句式出现在正文后段（前 200 字之外）属局部说明，不整体判拒答。"""
    filler = "该战役的经过与结果在史料中有详细记载，参见相关章节。" * 12
    assert detect_model_refusal(filler + "但主将的出生年份依据现有资料无法确认。") is False


# ---- generate() 集成 ----

def test_generate_marks_model_refusal_as_refused():
    g = _gen(_FakeLLM("依据现有资料无法确认。当前证据不足，无法给出有依据的回答。"))
    reason, _model, answer = asyncio.run(g.generate("问题", "问题", _evidence()))
    assert reason == FinishReason.REFUSED.value
    assert "无法确认" in answer


def test_generate_refusal_takes_precedence_over_degraded():
    """备用模型返回自拒文本：拒答语义优先于降级标记。"""
    g = _gen(_FakeLLM("根据现有资料无法确认。", degraded=True))
    reason, _model, _answer = asyncio.run(g.generate("问题", "问题", _evidence()))
    assert reason == FinishReason.REFUSED.value


def test_generate_keeps_normal_answer():
    g = _gen(_FakeLLM("长平之战中，赵军由赵括统率 [1]。"))
    reason, _model, _answer = asyncio.run(g.generate("问题", "问题", _evidence()))
    assert reason == FinishReason.NORMAL.value
