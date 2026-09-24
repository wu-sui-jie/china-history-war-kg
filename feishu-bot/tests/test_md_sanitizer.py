"""Markdown 白名单收敛器守护用例（开发文档 10.1）。

样例源**必须含评测题库真实回答**（需求文档风险 1 的落地）：本文件既有固化的
`tests/data/eval_answers.json`，也会在 `RAG/data/eval` 存在时全量扫描当期 run
的 answer 全文，断言"无飞书不支持的语法残留"且"再收敛一次结果不变"。
不允许只用人工构造样例。
"""

from __future__ import annotations

import glob
import json
import re
from pathlib import Path

import pytest

from bot.cards.md_sanitizer import (MAX_CHARS, TRUNCATE_SUFFIX, looks_unsupported,
                                    sanitize, sanitize_with_counters)

BOT_ROOT = Path(__file__).resolve().parent.parent
RAG_EVAL = BOT_ROOT.parent / "RAG" / "data" / "eval"


# ---- 1) 白名单保留 ----


@pytest.mark.parametrize("text", [
    "**加粗**",
    "*斜体* 与 _下划线斜体_",
    "~~删除线~~",
    "行内 `code` 保留",
    "[链接](https://example.com/a?b=1)",
    "- 无序项\n- 第二项",
    "1. 有序项\n2. 第二项",
    "> 引用行",
    "---",
    "普通段落，含 [1] 这样的引用标记",
    "裸 URL https://example.com/x 保持原样",
])
def test_whitelist_preserved(text: str):
    assert sanitize(text) == text


# ---- 2) 五类转换 ----


@pytest.mark.parametrize("level", range(1, 7))
def test_headings_become_bold_lines(level: int):
    assert sanitize(f"{'#' * level} 长平之战") == "**长平之战**"


def test_heading_with_trailing_hashes_and_indent():
    assert sanitize("  ### 战争经过 ###") == "**战争经过**"


def test_empty_heading_becomes_blank_line():
    assert sanitize("#") == ""


def test_table_becomes_dot_separated_text():
    text = "| 项目 | 内容 |\n| --- | --- |\n| 时间 | 前260年 |\n| 地点 | 长平 |"
    assert sanitize(text) == "**项目** · **内容**\n时间 · 前260年\n地点 · 长平"


def test_table_without_header_separator_keeps_first_row_unbolded():
    text = "| 甲 | 乙 |\n| 丙 | 丁 |"
    assert sanitize(text) == "甲 · 乙\n丙 · 丁"


def test_alignment_separator_is_removed():
    text = "| 甲 | 乙 |\n| :--- | ---: |\n| 1 | 2 |"
    assert sanitize(text) == "**甲** · **乙**\n1 · 2"


def test_standalone_hr_is_not_treated_as_table_separator():
    """孤立的 `---` 是分割线（白名单），不能被表格规则吃掉——曾踩过这个坑。"""
    text = "上文\n\n---\n\n下文"
    assert sanitize(text) == text


def test_code_block_becomes_quote_lines():
    text = "```python\nprint('甲')\nprint('乙')\n```"
    assert sanitize(text) == "> print('甲')\n> print('乙')"


def test_code_block_keeps_blank_lines_as_bare_quote():
    """空行不写成裸空行，否则引用块会被截断成两段。"""
    text = "```\n甲\n\n乙\n```"
    assert sanitize(text) == "> 甲\n>\n> 乙"


def test_tilde_fence_supported():
    assert sanitize("~~~\n甲\n~~~") == "> 甲"


def test_image_only_line_is_dropped():
    assert sanitize("上文\n![图](https://example.com/a.png)\n下文") == "上文\n下文"


def test_inline_image_is_removed_but_text_kept():
    assert sanitize("文字前![图](https://a/b.png)文字后") == "文字前文字后"


def test_html_tags_stripped_but_inner_text_kept():
    assert sanitize("<div>内文</div>") == "内文"
    assert sanitize('前<b class="x">粗</b>后') == "前粗后"


def test_html_inside_inline_code_is_not_stripped():
    """行内代码里的内容是代码文本，不是 HTML。"""
    assert sanitize("`<div>`") == "`<div>`"


def test_angle_bracket_url_is_not_mistaken_for_html():
    assert sanitize("<https://example.com>") == "<https://example.com>"


def test_unsafe_link_scheme_downgrades_to_text():
    assert sanitize("[点我](javascript:alert(1))") == "点我"
    assert sanitize("[本地](file:///etc/passwd)") == "本地"


def test_link_target_with_nested_parens_is_kept():
    text = "[维基](https://zh.wikipedia.org/wiki/A_(b))"
    assert sanitize(text) == text


# ---- 3) 幂等性 ----


@pytest.mark.parametrize("text", [
    "# 标题\n\n正文 **加粗**\n\n| 甲 | 乙 |\n| --- | --- |\n| 1 | 2 |",
    "```\n代码 <div> 里的内容\n```",
    "![图](https://a/b.png)\n<div>内文</div>",
    "> 引用\n\n---\n\n- 列表",
    "",
])
def test_idempotent(text: str):
    once = sanitize(text)
    assert sanitize(once) == once


# ---- 4) 超长截断 ----


def test_long_text_truncated_with_suffix_and_within_limit():
    text = "甲" * (MAX_CHARS + 500)
    out = sanitize(text)
    assert len(out) <= MAX_CHARS
    assert out.endswith(TRUNCATE_SUFFIX)


def test_truncation_is_idempotent():
    text = "乙" * (MAX_CHARS + 10)
    once = sanitize(text)
    assert sanitize(once) == once


def test_exactly_max_chars_not_truncated():
    text = "丙" * MAX_CHARS
    assert sanitize(text) == text


def test_custom_max_chars_respected():
    out = sanitize("丁" * 50, max_chars=40)
    assert len(out) <= 40 and out.endswith(TRUNCATE_SUFFIX)


def test_tiny_max_chars_still_bounds_length():
    """上限小于提示文案本身时，连提示一起截断——长度是硬约定。"""
    out = sanitize("丁" * 50, max_chars=10)
    assert len(out) <= 10


# ---- 5) 真实回答：固化样例 ----


def test_real_answers_have_no_unsupported_syntax(eval_answers):
    for item in eval_answers:
        out = sanitize(item["answer"])
        assert not looks_unsupported(out), f"残留不支持语法：{item['question_id']}"
        assert out.strip(), f"收敛后为空：{item['question_id']}"


def test_real_answers_are_idempotent(eval_answers):
    for item in eval_answers:
        once = sanitize(item["answer"])
        assert sanitize(once) == once, f"非幂等：{item['question_id']}"


def test_real_answers_headings_converted(eval_answers):
    """真实回答里的 `#/##/###` 必须都变成加粗行（卡片 md 不支持标题）。"""
    converted = 0
    for item in eval_answers:
        answer = item["answer"]
        if re.search(r"^#{1,6} ", answer, re.M):
            assert re.search(r"^#{1,6} ", sanitize(answer), re.M) is None
            converted += 1
    assert converted, "样例里应当包含带标题的真实回答"


# ---- 6) 真实回答：当期 run 全量扫描（评测产物存在时才跑）----


def _live_answers(limit: int = 400) -> list[str]:
    answers: list[str] = []
    for path in sorted(glob.glob(str(RAG_EVAL / "*" / "runs" / "*" / "traces.jsonl"))):
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            trace = (json.loads(line).get("trace") or {})
            answer = (trace.get("answer") or {}).get("text")
            if answer and len(answer) > 100:
                answers.append(answer)
            if len(answers) >= limit:
                return answers
    return answers


def test_live_eval_answers_full_scan():
    """把当期评测 run 的 answer 全文批量跑一遍（无产物时跳过）。"""
    answers = _live_answers()
    if not answers:
        pytest.skip("未找到 RAG/data/eval 下的评测 run 产物（评测产物不入库，属正常）")
    for text in answers:
        out = sanitize(text)
        assert not looks_unsupported(out)
        assert sanitize(out) == out


# ---- 7) 计数（观测用）----


def test_counters_report_what_changed():
    text = "# 标题\n\n| 甲 | 乙 |\n| --- | --- |\n| 1 | 2 |\n```\n码\n```\n![i](http://a/b.png)"
    _, counters = sanitize_with_counters(text)
    assert counters["headings"] == 1
    assert counters["table_rows"] >= 2
    assert counters["code_lines"] >= 1
    assert counters["images"] == 1


def test_empty_input():
    assert sanitize("") == ""
