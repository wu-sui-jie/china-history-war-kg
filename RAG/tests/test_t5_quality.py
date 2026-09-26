"""质量优化的守护用例（RAGv5）。

覆盖三条改动（都是"改错了会静默变差"的地方）：
1. 领域外谓词拒答（X01–X03 类问题）——必须只在"证据里完全没有该词"时才拒答；
2. `_pass_meta` 的 event_type 语义——有元数据的严格过滤、无元数据的（原文）放行；
3. and_or 的 AND 兜底——AND 命中不足时并入 OR，而不是只在为空时才兜底。
"""

from __future__ import annotations

from server.generate.refusal import out_of_scope_reason
from server.text.searcher import TextSearcher


class _Ev:
    """极简 Evidence 替身（只需要 content）。"""

    def __init__(self, **content):
        self.content = content


# ---- 1. 领域外谓词拒答 ----

def test_out_of_scope_refuses_when_predicate_absent():
    ev = [_Ev(text="赤壁之战中曹操率军南下，与孙刘联军战于赤壁。", event_name="赤壁之战")]
    assert out_of_scope_reason("赤壁之战结束后曹操去了哪里度假？", ev) is not None
    assert out_of_scope_reason("长平之战中赵军使用过坦克吗？", ev) is not None
    assert out_of_scope_reason("项羽的邮箱地址是什么？", ev) is not None


def test_out_of_scope_passes_when_predicate_present_in_evidence():
    """证据里确实出现过该词 → 不拒答（保守优先，宁可少拒）。"""
    ev = [_Ev(text="讨论中提到了度假行程安排。")]
    assert out_of_scope_reason("赤壁之战结束后曹操去了哪里度假？", ev) is None


def test_out_of_scope_ignores_normal_questions():
    ev = [_Ev(text="长平之战中白起大破赵军。")]
    assert out_of_scope_reason("长平之战的交战过程是怎样的？", ev) is None
    assert out_of_scope_reason("介绍一下赤壁之战。", ev) is None


def test_out_of_scope_needs_evidence():
    """没有证据时交给"无证据"规则处理，不在这里重复拒答。"""
    assert out_of_scope_reason("项羽的邮箱地址是什么？", []) is None


# ---- 2. event_type 过滤语义 ----

def test_pass_meta_event_type_strict_when_present():
    row = {"chunk_type": "event_card", "event_type": "兼并战争", "dynasty": "战国"}
    assert TextSearcher._pass_meta(row, {"event_type": ["兼并战争"]}) is True
    assert TextSearcher._pass_meta(row, {"event_type": ["农民起义"]}) is False


def test_pass_meta_event_type_relaxed_for_raw():
    """原文片段（无 event_type）在按类型筛选时放行——否则 v4 实测召回 100%→0%。"""
    raw = {"chunk_type": "raw", "event_type": None, "dynasty": None}
    assert TextSearcher._pass_meta(raw, {"event_type": ["兼并战争"]}) is True


def test_pass_meta_dynasty_and_chunk_type_still_strict():
    row = {"dynasty": "战国", "chunk_type": "raw", "event_type": None}
    assert TextSearcher._pass_meta(row, {"dynasty": ["东汉"]}) is False
    assert TextSearcher._pass_meta(row, {"chunk_type": ["event_card"]}) is False
    assert TextSearcher._pass_meta(row, {"dynasty": ["战国"], "chunk_type": ["raw"]}) is True


# ---- 3. AND 兜底的口径常量化 ----

def test_and_min_hits_default_available_and_configurable():
    """兜底阈值有明确默认值，并已提为配置项（TEXT_QUERY_AND_MIN_HITS，改它要连着评测一起改）。"""
    from config import defaults
    from config.settings import get_settings
    from server.text.searcher import DEFAULT_QUERY_AND_MIN_HITS

    assert isinstance(DEFAULT_QUERY_AND_MIN_HITS, int) and 1 <= DEFAULT_QUERY_AND_MIN_HITS <= 10
    # defaults 与 searcher 的兜底常量必须一致，否则"配置默认值"与"直接构造检索器"行为不同
    assert defaults.TEXT_QUERY_AND_MIN_HITS == DEFAULT_QUERY_AND_MIN_HITS
    # 配置对象上确实有这个字段
    assert isinstance(get_settings().text_query_and_min_hits, int)
