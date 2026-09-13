"""题库（evaluation.bank）单元测试。

运行：在 RAG/ 根目录执行
    python -m pytest tests/test_bank.py -q
题库文件 data/eval/<version>/questions.jsonl 为本地数据资产（不入 Git），
文件缺失时自动跳过相关用例。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evaluation.bank import CATEGORIES, GoldQuestion, QuestionBank, load_bank, save_bank

ROOT = Path(__file__).resolve().parent.parent
BANK_PATH = ROOT / "data" / "eval" / "20260904_v2" / "questions.jsonl"

pytestmark = pytest.mark.skipif(
    not BANK_PATH.exists(),
    reason="本地题库资产缺失（data/eval/<version>/questions.jsonl 不入 Git）",
)


@pytest.fixture(scope="module")
def bank() -> QuestionBank:
    return load_bank(BANK_PATH)


def test_load_count_and_version(bank: QuestionBank):
    assert len(bank.items) == 39
    assert bank.version == "20260904_v2"
    assert bank.annotation_version


def test_validate_ok(bank: QuestionBank):
    res = bank.validate()
    assert res["ok"] is True
    assert res["errors"] == []


def test_suite_distribution(bank: QuestionBank):
    got = bank.validate()["summary"]["suites"]
    assert got == {"main": 28, "long_rewrite": 4, "filter_loss": 4, "refusal": 3}


def test_id_unique_and_review_flags(bank: QuestionBank):
    ids = bank.ids()
    assert len(ids) == len(set(ids))
    for q in bank.items:
        # 审核状态随 annotation_version 演进（当前 reviewed-2：39/39 通过）；
        # 本用例只校验字段类型与类别取值合法，具体审核结论由 validate 与审核表负责
        assert isinstance(q.reviewed, bool)
        assert isinstance(q.answerable, bool)
        assert q.category in CATEGORIES


def test_expected_entities_min_len(bank: QuestionBank):
    for q in bank.items:
        for e in q.expected_entities:
            assert len(e.strip()) >= 2


def test_filter_loss_items_have_event_type(bank: QuestionBank):
    for q in bank.items:
        if q.suite == "filter_loss":
            assert (q.filters or {}).get("event_type"), q.id


def test_roundtrip(tmp_path, bank: QuestionBank):
    out = tmp_path / "questions.jsonl"
    save_bank(bank, out)
    again = load_bank(out)
    assert [q.id for q in again.items] == [q.id for q in bank.items]
    first = bank.items[0]
    again_first = again.by_id()[first.id]
    assert again_first.question == first.question
    assert again_first.expected_entities == first.expected_entities


def test_single_question_validate():
    q = GoldQuestion(id="Q1", suite="bad", category="?",
                     question="", expected_entities=["a"])
    problems = q.validate()
    assert len(problems) >= 3  # suite / category / question / expected 长度均非法
