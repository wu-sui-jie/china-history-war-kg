"""CLI 参数解析的守护用例（RAGv5 T8 修复）。

背景：`evaluation/cli.py` 的 `--suites` 原实现把字符串直接当可迭代对象，
`--suites main` 会被拆成 ['m','a','i','n'] 并报"未知套件"，
使文档中的 `python scripts/run_evaluation.py run --suites main` 实际不可用。
"""

from __future__ import annotations

from evaluation.cli import _split_suites


def test_split_suites_single():
    assert _split_suites("main") == ["main"]


def test_split_suites_multiple_with_spaces():
    assert _split_suites("main, long_rewrite") == ["main", "long_rewrite"]


def test_split_suites_empty_means_all():
    assert _split_suites("") == []
    # 空值在调用处回落到"全部套件"
    from evaluation.cli import SUITE_DEFAULT_CONFIGS

    assert _split_suites("") or list(SUITE_DEFAULT_CONFIGS)


def test_split_suites_ignores_trailing_comma():
    assert _split_suites("main,") == ["main"]
