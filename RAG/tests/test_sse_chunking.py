"""SSE 增量切分的守护用例（RAGv5）。

背景：离线摘要回答器/拒答文案走 `_chunk_answer_stream` 分帧发送。按句末标点切分时
若吞掉标点后的空白与换行，"流式拼出来的答案"会比原文少若干字符（换行丢失），
而缓存回放的是完整文本 → 同一问题"首次"与"缓存命中"两次看到的文本不一致
（实测 518 vs 537 字符）。本用例锁住"拼接必须严格等于原文"。
"""

from __future__ import annotations

from server.sse import _chunk_answer_stream


def _join(text: str) -> str:
    return "".join(_chunk_answer_stream(text))


def test_roundtrip_preserves_newlines():
    text = "关于「介绍一下诸葛亮」，基于检索到的资料整理如下：\n[1] 诸葛亮 与 蜀汉 的关系为「辅佐」。\n[2] 诸葛亮是蜀汉丞相。\n（当前为离线摘要模式。）"
    assert _join(text) == text


def test_roundtrip_preserves_spaces_after_punctuation():
    text = "第一句。  第二句！   第三句？\n换行后的内容；分号后也有空格。 "
    assert _join(text) == text


def test_roundtrip_single_line_long_text():
    text = "甲" * 400
    assert _join(text) == text


def test_roundtrip_crlf_and_empty_lines():
    text = "第一行\r\n\r\n第三行。\n"
    assert _join(text) == text


def test_chunks_are_reasonable_size():
    """整行/整句为单位，且不会把大段文本压成一帧（保持打字机观感）。"""
    text = "句子一。句子二。句子三。" * 30
    chunks = list(_chunk_answer_stream(text, chunk_chars=160))
    assert len(chunks) > 1
    assert all(len(c) <= 200 for c in chunks)


def test_empty_text_yields_nothing():
    assert list(_chunk_answer_stream("")) == []
