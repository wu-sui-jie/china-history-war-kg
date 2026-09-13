"""评测指标与失败归因（evaluation.metrics）单元测试。

不依赖真实数据与运行链路，直接用小对象构造各覆盖率场景验证计算与归因逻辑。
运行：在 RAG/ 根目录执行  python -m pytest tests/test_metrics.py -q
"""

from __future__ import annotations

from types import SimpleNamespace

from evaluation.metrics import VERDICT_LABEL, classify, compute_metrics


def _ev(kind: str, content: dict, score: float = 1.0):
    return SimpleNamespace(kind=kind, content=content, score=score)


def _citations(snippets, kinds=None):
    out = []
    for i, s in enumerate(snippets, 1):
        out.append({"index": i, "kind": (kinds or ["raw_text"] * len(snippets))[i - 1],
                    "title": "t", "snippet": s})
    return out


def test_coverage_ok_path():
    evs = [_ev("raw_text", {"text": "长平之战 白起 大破赵军"})]
    auto = compute_metrics(
        expected_names=["长平之战", "白起"],
        entity_objs=[SimpleNamespace(name="长平之战", standard_name="长平之战")],
        text_evidence=evs, fused_evidence=evs,
        citations=_citations(["长平之战"]),
        answer_text="长平之战中白起大破赵军",
    )
    assert auto["counts"]["entity_matched"] == 1
    assert auto["counts"]["text_topk"] == 2
    assert auto["counts"]["answer"] == 2
    assert classify(auto, answerable=True) == "ok"


def test_refusal_zeroes_answer_coverage():
    """拒答文案机械复述问句包含标注词，但必须记 0，避免误判为'覆盖通过'。"""
    refusal = {"rule": "no_evidence", "text": "知识库未检索到与「赤壁之战」相关的史料…"}
    evs = [_ev("raw_text", {"text": "与赤壁之战无关的内容"})]
    auto = compute_metrics(
        expected_names=["赤壁之战"],
        text_evidence=evs,
        answer_text=refusal["text"],   # 文案里含标注词
        refusal=refusal,
    )
    assert auto["counts"]["answer"] == 0
    assert classify(auto, answerable=True, refusal=refusal) == "refused"
    assert classify(auto, answerable=False, refusal=refusal) == "correct_refusal"


def test_should_refuse_when_not_answerable_but_answered():
    auto = compute_metrics(expected_names=[], answer_text="随便回答了一段")
    assert auto["expected_total"] == 0
    assert classify(auto, answerable=False) == "should_refuse_answered"
    assert classify(auto, answerable=True) == "no_expected"


def test_retrieval_fail_when_nothing_covered():
    auto = compute_metrics(
        expected_names=["项羽"],
        entity_objs=[], text_evidence=[_ev("raw_text", {"text": "无关"})],
    )
    assert classify(auto, answerable=True) == "retrieval_fail"


def test_fusion_cut_when_text_dropped_before_answer():
    """文本 top-k 召回但融合后文本证据未携带 → fusion_cut。"""
    text_evs = [_ev("raw_text", {"text": "巨鹿之战项羽大破秦军"})]
    # 融合证据只剩图谱（不含文本），答案也不含标注词
    fused_evs = [_ev("graph_triple", {"subject": "某事件", "relation": "发生于",
                                      "object": "某地"})]
    auto = compute_metrics(
        expected_names=["巨鹿之战"],
        text_evidence=text_evs,
        fused_evidence=fused_evs,
        answer_text="某事件发生于某地",
    )
    assert auto["counts"]["text_topk"] == 1
    assert auto["counts"]["fused_text"] == 0
    assert classify(auto, answerable=True) == "fusion_cut"


def test_answer_miss_when_carried_but_not_quoted():
    """实体命中 + 融合携带但回答未引用标注词 → answer_miss。"""
    text_evs = [_ev("raw_text", {"text": "长平之战赵括战死"})]
    auto = compute_metrics(
        expected_names=["赵括"],
        entity_objs=[SimpleNamespace(name="赵括", standard_name="赵括")],
        text_evidence=text_evs, fused_evidence=text_evs,
        answer_text="秦军获胜",
    )
    assert auto["counts"]["fused"] == 1
    assert classify(auto, answerable=True) == "answer_miss"


def test_no_expected_verdict_label_present():
    assert "no_expected" in VERDICT_LABEL
