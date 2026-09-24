"""EER-6：合并到公共模块的这几段逻辑，行为必须与原来的几份拷贝一致。

这几段此前在多个文件里各存一份（JSON 解析三份 + llm_client 一份扫描、多值拆分两份、
年份解析两份、起止时间定序两份、事件-事件关系仲裁两份），改一处忘一处就会两边漂移。
本轮把它们收成公共实现，**属于纯重构**——所以这里逐条钉住原行为，尤其是两处刻意保留的差异：

1. `extract_json_payload`（取第一个可解析值、先试整体解析）与
   `extract_largest_json_text`（取最大候选、返回文本、不试整体解析）**不是同一个策略**，
   合并掉任何一个都会改变抽取产物；
2. `split_multi_value` 的排除集：抽取器用宽的（含"未知/无/None"），
   main.py 用窄的（只排除"不详/null"）。这个分歧本轮**原样保留**，没顺手统一。
"""

import json
from types import SimpleNamespace

import pytest

from war_extraction.utils import Normalizer
from war_extraction.utils.json_payload import (
    extract_json_payload,
    extract_largest_json_text,
    iter_json_values,
)
from war_extraction.utils.relation_rules import arbitrate_event_event_relation
from war_extraction.utils.value_parsing import (
    PLACEHOLDERS_FULL,
    PLACEHOLDERS_MINIMAL,
    ensure_event_date_order,
    parse_year_for_order,
    split_multi_value,
)


# --------------------------------------------------------------------- JSON 载荷

def test_extract_json_payload_plain_object():
    assert extract_json_payload('{"EventName": "牧野之战"}') == {"EventName": "牧野之战"}


def test_extract_json_payload_list():
    assert extract_json_payload('[{"a": 1}, {"b": 2}]') == [{"a": 1}, {"b": 2}]


def test_extract_json_payload_inside_prose():
    """模型爱把 JSON 包在解释文字里（或 markdown 代码块里）。"""
    raw = '好的，以下是结果：\n```json\n{"entities": {"places": []}}\n```\n以上。'
    assert extract_json_payload(raw) == {"entities": {"places": []}}


def test_extract_json_payload_takes_the_first_candidate():
    """取**第一个**可解析值——这是三个抽取器的策略。"""
    raw = '{"first": 1} 以及 {"second": 2}'
    assert extract_json_payload(raw) == {"first": 1}


def test_extract_json_payload_returns_none_without_json():
    assert extract_json_payload('模型今天不太配合，没有输出 JSON') is None
    assert extract_json_payload('') is None
    assert extract_json_payload(None) is None


def test_extract_largest_json_text_takes_the_largest():
    """llm_client 的策略：多个候选里取最大的（示例块 + 真结果同现时才对）。"""
    raw = '示例：{"a": 1} 真正结果：{"events": [1, 2, 3, 4, 5]}'
    picked = json.loads(extract_largest_json_text(raw))
    assert picked == {"events": [1, 2, 3, 4, 5]}


def test_extract_largest_json_text_returns_text_and_none():
    assert extract_largest_json_text('没有 JSON') is None
    assert extract_largest_json_text('') is None
    # 返回的是文本，调用方若需要对象要自己 loads
    assert isinstance(extract_largest_json_text('{"a": 1}'), str)


def test_iter_json_values_yields_in_order():
    values = [obj for obj, _ in iter_json_values('{"a":1}{"b":2}')]
    assert values == [{"a": 1}, {"b": 2}]


# --------------------------------------------------------------------- 多值拆分

def test_split_multi_value_splits_on_all_separators():
    assert split_multi_value("甲、乙，丙;丁") == ["甲", "乙", "丙", "丁"]
    assert split_multi_value("刘邦与项羽") == ["刘邦", "项羽"]


def test_split_multi_value_wide_placeholders_are_removed():
    """宽口径（抽取器原来那份）：占位词也算"没有值"。"""
    assert split_multi_value("甲、未知、乙", placeholders=PLACEHOLDERS_FULL) == ["甲", "乙"]
    assert split_multi_value("甲、无、乙", placeholders=PLACEHOLDERS_FULL) == ["甲", "乙"]
    assert split_multi_value("甲、None、乙", placeholders=PLACEHOLDERS_FULL) == ["甲", "乙"]


def test_split_multi_value_narrow_placeholders_kept():
    """
    窄口径（main.py 原来那份）：只滤"不详/null"。

    这条用例的作用是**钉住这个分歧**——第 10 轮是纯重构，没统一两边的排除集。
    哪天决定统一成宽口径，改这条用例要在提交信息里说明这是抽取产物的口径变更。
    """
    assert split_multi_value("甲、未知、乙", placeholders=PLACEHOLDERS_MINIMAL) == ["甲", "未知", "乙"]
    assert split_multi_value("甲、无、乙", placeholders=PLACEHOLDERS_MINIMAL) == ["甲", "无", "乙"]
    assert split_multi_value("甲、不详、乙", placeholders=PLACEHOLDERS_MINIMAL) == ["甲", "乙"]
    assert split_multi_value("甲、null、乙", placeholders=PLACEHOLDERS_MINIMAL) == ["甲", "乙"]


def test_split_multi_value_empty_input():
    assert split_multi_value(None) == []
    assert split_multi_value("") == []
    assert split_multi_value("、、") == []


# --------------------------------------------------------------------- 年份与定序

@pytest.mark.parametrize(
    "text,expected",
    [
        ("公元前 260 年", -260),
        ("前260年", -260),
        ("公元前 202 年", -202),
        ("1840 年", 1840),
        ("618年", 618),
        ("不详", None),
        ("未知", None),
        ("进行中", None),
        ("", None),
        (None, None),
        ("很久以前", None),
    ],
)
def test_parse_year_for_order(text, expected):
    assert parse_year_for_order(text) == expected


def test_ensure_event_date_order_swaps_when_reversed():
    event = SimpleNamespace(StartDate="公元前 200 年", EndDate="公元前 260 年", Remark=None)
    result = ensure_event_date_order(event)

    assert result is event
    assert (event.StartDate, event.EndDate) == ("公元前 260 年", "公元前 200 年")
    assert "已自动校正开始时间晚于结束时间的问题" in event.Remark


def test_ensure_event_date_order_appends_to_existing_remark():
    event = SimpleNamespace(StartDate="公元前 100 年", EndDate="公元前 200 年", Remark="原有备注")
    ensure_event_date_order(event)
    assert event.Remark == "原有备注\n已自动校正开始时间晚于结束时间的问题"


def test_ensure_event_date_order_leaves_correct_order_alone():
    event = SimpleNamespace(StartDate="公元前 260 年", EndDate="公元前 200 年", Remark=None)
    ensure_event_date_order(event)
    assert (event.StartDate, event.EndDate) == ("公元前 260 年", "公元前 200 年")
    assert event.Remark is None


def test_ensure_event_date_order_skips_unparseable():
    event = SimpleNamespace(StartDate="不详", EndDate="公元前 200 年", Remark=None)
    ensure_event_date_order(event)
    assert (event.StartDate, event.EndDate) == ("不详", "公元前 200 年")
    assert event.Remark is None


# --------------------------------------------------------------------- 关系仲裁

@pytest.fixture(scope="module")
def normalizer():
    return Normalizer()


def _rel(relation, evidence):
    return SimpleNamespace(relation=relation, evidence=evidence)


def test_causal_without_strong_keyword_with_sequential_evidence(normalizer):
    """说是因果、但证据里只有顺承词 → 降级为顺承。"""
    rel = arbitrate_event_event_relation(normalizer, _rel("因果", "此后"))
    assert rel.relation == "顺承关系"


def test_causal_without_strong_keyword_and_no_evidence(normalizer):
    """说是因果、证据也空 → 判并列。"""
    rel = arbitrate_event_event_relation(normalizer, _rel("因果", ""))
    assert rel.relation == "并列关系"


def test_causal_with_strong_keyword_is_kept(normalizer):
    rel = arbitrate_event_event_relation(normalizer, _rel("因果", "因此导致了后续战事"))
    assert rel.relation == "因果关系"


def test_sequential_with_parallel_keyword_becomes_parallel(normalizer):
    rel = arbitrate_event_event_relation(normalizer, _rel("顺承", "同时发生"))
    assert rel.relation == "并列关系"


def test_sequential_with_both_keywords_stays_sequential(normalizer):
    """顺承词优先：既有顺承词又有并列词时不改判。"""
    rel = arbitrate_event_event_relation(normalizer, _rel("顺承", "之后同时"))
    assert rel.relation == "顺承关系"
