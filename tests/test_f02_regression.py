"""F02 端到端回归：四题不得再被朝代硬筛选打回拒答（审核报告 T2 步骤 3）。

守护对象（首轮评审里因 F02 朝代误判被拒答的 4 题）：
B01 秦为什么会发动长平之战？ / E01 鸣条之战与商朝的建立有什么关系？
R05 巨鹿之战的楚军主帅是谁？ / T03 巨鹿之战发生于什么朝代？

额外覆盖审核报告的关键实验：**即便词典补上"商朝"别名，也不得把 E01 打回拒答**
（自动识别的朝代只进 dynasty_bias 排序偏置，不进 filters 硬过滤）。

数据缺失自动 skip。运行：python -m pytest tests/test_f02_regression.py -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from config.settings import get_settings

ROOT = Path(__file__).resolve().parent.parent
SNAP = ROOT / "data" / "snapshot" / "20260904_v2"
IDX = ROOT / "data" / "index" / "20260904_v2"

pytestmark = pytest.mark.skipif(
    not (SNAP / "entities.json").exists() or not (IDX / "chunks_fts.db").exists(),
    reason="本地快照/索引缺失（data/ 不入 Git）",
)

CASES = [
    ("B01", "秦为什么会发动长平之战？"),
    ("E01", "鸣条之战与商朝的建立有什么关系？"),
    ("R05", "巨鹿之战的楚军主帅是谁？"),
    ("T03", "巨鹿之战发生于什么朝代？"),
]


@pytest.fixture(scope="module")
def runtime():
    from server.runtime import build_runtime

    s = get_settings()
    s.llm_base_url = ""
    s.llm_api_key = ""
    s.fallback_llm_base_url = ""
    s.fallback_llm_api_key = ""
    return build_runtime(s, "20260904_v2")


def _run_question(runtime, question):
    import asyncio

    from evaluation.chain import CONFIG_DEFAULT, run_question

    return asyncio.run(run_question(runtime, CONFIG_DEFAULT, question,
                                    filters={}, expected_names=[]))


@pytest.mark.parametrize("qid,question", CASES)
def test_four_questions_not_refused(runtime, qid, question):
    tr = _run_question(runtime, question)
    assert tr["understand"]["filters"]["dynasty"] == [], f"{qid} 不应有朝代硬过滤"
    assert tr["refusal"] is None, f"{qid} 不应拒答"
    assert tr["graph"]["n"] > 0 and tr["text"]["n"] > 0, f"{qid} 图谱/文本证据不得为空"
    assert tr["fused"]["n"] > 0, f"{qid} 融合证据不得为空"
    assert tr["answer"]["finish_reason"] != "refused"


def test_dynasty_is_bias_not_hard_filter(runtime):
    """E01 的"商朝"应只出现在 dynasty_bias，不出现在 filters（软偏置）。"""
    tr = _run_question(runtime, "鸣条之战与商朝的建立有什么关系？")
    assert tr["understand"]["dynasty_bias"] == ["商"]
    assert tr["understand"]["filters"]["dynasty"] == []


def test_adding_alias_does_not_break_e01(runtime):
    """审核实验复现：给词典补"商朝"别名后，E01 仍不得被拒答（回归守护）。"""
    aliases = runtime.question.matcher._dicts.setdefault("dynasty_aliases", {})
    existed = "商朝" in aliases
    aliases["商朝"] = "商"
    try:
        tr = _run_question(runtime, "鸣条之战与商朝的建立有什么关系？")
    finally:
        if not existed:
            aliases.pop("商朝", None)
    assert tr["refusal"] is None
    assert tr["text"]["n"] > 0 and tr["fused"]["n"] > 0
