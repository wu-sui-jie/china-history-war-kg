"""事件-事件关系的类型仲裁、方向判定与收敛规则。

**这组用例保护的是数据正确性，不只是评估口径。** 原实现有两处会静默改错数据：

1. **方向按事件名字典序定**：`EventName_A > EventName_B` 就交换两端，
   于是"谁先谁后"由汉字编码决定；而 `main.py` 只在证据同时含两个完整事件名时才换回来
   （实测 914 条里只有 23 条满足），错误方向因此留在了产物里。
2. **因果 → 顺承的无条件降级**：条件是 `has_sequential_keyword or evidence`，
   而 evidence 几乎恒成立，于是"因果关系"几乎必然被降级。产物分布是
   `顺承 798 / 因果 40`，而标注是 `因果 80 / 顺承 59`。
"""

from types import SimpleNamespace

import pytest

from war_extraction.models import EventEventRelation
from war_extraction.utils import Normalizer
from war_extraction.utils.relation_rules import (
    arbitrate_event_event_relation,
    build_event_start_years,
    evidence_order,
    reduce_event_event_relations,
    resolve_event_event_direction,
)


@pytest.fixture(scope="module")
def normalizer():
    return Normalizer()


def _rel(name_a, name_b, relation, evidence=""):
    return EventEventRelation(EventName_A=name_a, relation=relation, EventName_B=name_b, evidence=evidence)


# ---------------------------------------------------------------- 类型仲裁

def test_因果缺强因果词但有顺承词时降级为顺承(normalizer):
    rel = arbitrate_event_event_relation(normalizer, SimpleNamespace(relation="因果", evidence="此后"))
    assert rel.relation == "顺承关系"


def test_因果缺强因果词也没有顺承词时判并列(normalizer):
    """**这是修掉的那一处**：原先只要 evidence 非空就降级为顺承，于是降级恒成立。"""
    for evidence in ("双方在边境多次交锋", "战事绵延数月"):
        rel = arbitrate_event_event_relation(normalizer, SimpleNamespace(relation="因果", evidence=evidence))
        assert rel.relation == "并列关系", f"证据 {evidence!r} 里没有顺承词，不该降级为顺承"


def test_因果有强因果词时保留(normalizer):
    rel = arbitrate_event_event_relation(normalizer, SimpleNamespace(relation="因果", evidence="因此导致"))
    assert rel.relation == "因果关系"


def test_顺承遇并列词且无顺承词时改判并列(normalizer):
    rel = arbitrate_event_event_relation(normalizer, SimpleNamespace(relation="顺承", evidence="同时发生"))
    assert rel.relation == "并列关系"


# ---------------------------------------------------------------- 方向判定

def test_证据顺序优先于一切(normalizer):
    rel = SimpleNamespace(EventName_A="B战", EventName_B="A战", evidence="先是A战，随后B战", relation="顺承关系")
    assert resolve_event_event_direction(normalizer, rel, {"A战": 1000, "B战": 500}) is True
    # 证据里 A 在前 → A 放到 EventName_A，即使时间索引说 B 更早
    assert (rel.EventName_A, rel.EventName_B) == ("A战", "B战")


def test_证据判不出时按起始年份定方向(normalizer):
    rel = SimpleNamespace(EventName_A="晚战", EventName_B="早战", evidence="两者相继发生", relation="顺承关系")
    resolved = resolve_event_event_direction(normalizer, rel, {"晚战": 1900, "早战": 1600})
    assert resolved is True
    assert (rel.EventName_A, rel.EventName_B) == ("早战", "晚战")


def test_证据与时间都判不出时返回未判定(normalizer):
    rel = SimpleNamespace(EventName_A="乙战", EventName_B="甲战", evidence="", relation="顺承关系")
    assert resolve_event_event_direction(normalizer, rel, {}) is False
    # 未判定时**不猜方向**：保持原样，由调用方决定"不合并反向的两条"
    assert (rel.EventName_A, rel.EventName_B) == ("乙战", "甲战")


def test_年份相同也判不出(normalizer):
    rel = SimpleNamespace(EventName_A="乙战", EventName_B="甲战", evidence="", relation="顺承关系")
    assert resolve_event_event_direction(normalizer, rel, {"甲战": 1000, "乙战": 1000}) is False


def test_evidence_order_要求两个名字都出现():
    assert evidence_order("先有甲战后有乙战", "甲战", "乙战") == "a_first"
    assert evidence_order("先有乙战后有甲战", "甲战", "乙战") == "b_first"
    assert evidence_order("只有甲战", "甲战", "乙战") is None
    assert evidence_order("", "甲战", "乙战") is None


def test_build_event_start_years_同名取最早(normalizer):
    events = [
        SimpleNamespace(EventName="扬州之战", StartDate="前100年"),
        SimpleNamespace(EventName="扬州之战", StartDate="前200年"),
        SimpleNamespace(EventName="无名", StartDate="不详"),
    ]
    years = build_event_start_years(normalizer, events)
    key = normalizer.normalize_event_name("扬州之战")
    assert years[key] == -200, "同名事件取最早的一年（取最晚会把后续阶段判成更早）"
    assert normalizer.normalize_event_name("无名") not in years, "解析不出年份的不进索引"


# ---------------------------------------------------------------- 收敛规则

def test_方向判定成功时同一对只留一条(normalizer):
    relations = [
        _rel("前战", "后战", "顺承关系", "前战之后是后战"),
        _rel("后战", "前战", "顺承关系", "后战之后是前战"),
    ]
    reduced = reduce_event_event_relations(normalizer, relations)
    assert len(reduced) == 1
    assert reduced[0].EventName_A == "前战"


def test_方向判不出时反向两条都保留(normalizer):
    """已定口径：判不出方向就不合并 A→B 与 B→A，而不是按字典序硬定一个方向。"""
    relations = [
        _rel("乙战", "甲战", "顺承关系", ""),
        _rel("甲战", "乙战", "顺承关系", ""),
    ]
    reduced = reduce_event_event_relations(normalizer, relations)
    assert len(reduced) == 2


def test_并列关系按对称对去重(normalizer):
    relations = [
        _rel("甲战", "乙战", "并列关系", "同时"),
        _rel("乙战", "甲战", "并列关系", "同时"),
    ]
    reduced = reduce_event_event_relations(normalizer, relations)
    assert len(reduced) == 1


def test_丢掉自环与事件ID占位名(normalizer):
    relations = [
        _rel("甲战", "甲战", "顺承关系", "x"),
        _rel("E1", "乙战", "顺承关系", "x"),
        _rel("乙战", "E12", "顺承关系", "x"),
    ]
    assert reduce_event_event_relations(normalizer, relations) == []


def test_name_allowed_过滤掉不在最终事件名单里的端点(normalizer):
    relations = [_rel("甲战", "乙战", "顺承关系", "x")]
    allowed = {normalizer.normalize_event_name("甲战")}
    assert reduce_event_event_relations(normalizer, relations, name_allowed=lambda n: n in allowed) == []


def test_枚举外类型保留在产物里但被单独计数(normalizer):
    """
    `主战场` 是地点关系名，被模型写进事件-事件关系里时：**保留**在 raw 产物里、
    由发布拆分阶段路由进候选区，并由 `quarantine` 提供计数通道。

    **这条口径改过一次**：原先是"直接丢掉"，后果是这批数据**无痕消失**——
    既不在 raw 产物里、也不在候选区里，`moved_to_candidate_by_enum` 也看不到它们，
    与另外三类关系（raw 保留 + 发布拆分时进候选区）也不一致。现在四类统一。
    """
    relations = [_rel("甲战", "乙战", "主战场", "x")]
    quarantine = []
    reduced = reduce_event_event_relations(normalizer, relations, quarantine=quarantine)

    assert len(reduced) == 1, "枚举外类型必须留在产物里，否则它在候选区里也看不到"
    assert reduced[0].relation == "主战场"
    assert len(quarantine) == 1 and quarantine[0].relation == "主战场", "计数通道要有内容"


def test_输出定序与输入顺序无关(normalizer):
    """评估器对关系顺序敏感，所以收敛结果必须定序（否则跨进程漂移）。"""
    forward = [_rel("甲战", "乙战", "包含关系", "a"), _rel("丙战", "丁战", "包含关系", "b")]
    backward = list(reversed(forward))
    assert ([r.model_dump() for r in reduce_event_event_relations(normalizer, forward)]
            == [r.model_dump() for r in reduce_event_event_relations(normalizer, backward)])


def test_不修改传入对象(normalizer):
    """方向判定会就地改字段，所以内部必须用副本——否则调用方的对象被悄悄改了。"""
    rel = _rel("晚战", "早战", "顺承关系", "")
    reduce_event_event_relations(normalizer, [rel], {"晚战": 1900, "早战": 1600})
    assert (rel.EventName_A, rel.EventName_B) == ("晚战", "早战")
