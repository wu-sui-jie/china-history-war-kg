"""F02 同名实体的朝代消歧（RAGv5 2026-09-14）。

背景：数据里同名但确属不同朝代的实体必须**保留**（如井陉之战有战国与西汉两条，
合并会丢掉其中一个朝代），所以问题不在数据、在"选哪条"。改进：问句里提到的朝代
参与**同名候选之间**的选择（命中者前置），但——

- 只做偏好、不做硬过滤：v4 实测过硬过滤的坑（问"商朝"时鸣条之战属夏 → 全链为空转拒答）；
- 候选集合不变：全部同名选项仍进 candidates，页面可点选纠正；
- 问句没提朝代 / 没有朝代相符的候选 → 行为与改进前完全一致（取首个）。

另覆盖朝代名的宽松比对（唐朝 ≡ 唐、明代 ≡ 明朝）与可观测字段 dynasty_disambiguated。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.query.dictionary_matcher import EntityHit
from server.query.understand import QuestionUnderstanding, _dynasty_hit

ROOT = Path(__file__).resolve().parent.parent
SNAP = ROOT / "data" / "snapshot" / "20260904_v2"

_resolve = QuestionUnderstanding._resolve_ambiguity


def hit(mention: str, dynasty: str | None, eid: str = "", etype: str = "事件") -> EntityHit:
    return EntityHit(name=mention, entity_id=eid or f"{mention}-{dynasty}", type=etype,
                     standard_name=mention, confidence="high", dynasty=dynasty)


# ---------- 朝代名宽松比对 ----------

@pytest.mark.parametrize("entity_dynasty,bias,expected", [
    ("唐", ["唐朝"], True),          # 问句带后缀
    ("唐朝", ["唐"], True),          # 实体带后缀
    ("明", ["明朝"], True),
    ("明朝", ["明"], True),
    ("西汉", ["西汉"], True),        # 精确
    ("南北朝", ["南北朝"], True),
    ("五代十国", ["五代"], False),   # 前缀但后缀不是朝/代 → 不算命中
    ("唐", ["宋"], False),
    (None, ["唐"], False),           # 实体无朝代
    ("唐", [], False),
])
def test_dynasty_hit(entity_dynasty, bias, expected):
    assert _dynasty_hit(entity_dynasty, bias) is expected


# ---------- _resolve_ambiguity：排序与标记 ----------

def test_no_bias_keeps_order():
    hits = [hit("井陉之战", "战国"), hit("井陉之战", "西汉")]
    resolved, picked = _resolve(raw_hits := hits, [], None)
    assert resolved == raw_hits and picked is False


def test_bias_prefers_matching_candidate():
    zg, xh = hit("井陉之战", "战国"), hit("井陉之战", "西汉")
    resolved, picked = _resolve([zg, xh], [], ["西汉"])
    assert [h.dynasty for h in resolved] == ["西汉", "战国"]   # 命中者前置
    assert picked is True
    assert set(id(h) for h in resolved) == {id(zg), id(xh)}     # 候选集合不变（不删除）


def test_flag_false_when_first_already_matches():
    xh, zg = hit("井陉之战", "西汉"), hit("井陉之战", "战国")
    resolved, picked = _resolve([xh, zg], [], ["西汉"])
    assert [h.dynasty for h in resolved] == ["西汉", "战国"]    # 顺序本就满足
    assert picked is False                                      # 没有改变选择


def test_bias_without_match_keeps_order():
    hits = [hit("平阳之战", "战国"), hit("平阳之战", "南北朝")]
    resolved, picked = _resolve(hits, [], ["明"])
    assert resolved != [] and [h.dynasty for h in resolved] == ["战国", "南北朝"]
    assert picked is False


def test_several_matching_candidates_all_fronted():
    hits = [hit("洛阳", "秦"), hit("洛阳", "唐"), hit("洛阳", "唐")]
    resolved, picked = _resolve(hits, [], ["唐朝"])
    assert [h.dynasty for h in resolved] == ["唐", "唐", "秦"]  # 两条唐都前置
    assert picked is True


def test_hard_filter_takes_precedence_over_bias():
    hits = [hit("潼关之战", "南北朝"), hit("潼关之战", "明")]
    resolved, _ = _resolve(hits, ["明"], ["南北朝"])
    assert [h.dynasty for h in resolved] == ["明"]              # 显式筛选仍是唯一命中


def test_single_hit_untouched():
    resolved, picked = _resolve([hit("赤壁之战", "东汉")], [], ["西汉"])
    assert len(resolved) == 1 and picked is False


def test_multiple_mentions_independent():
    a1, a2 = hit("井陉之战", "战国"), hit("井陉之战", "西汉")
    b1 = hit("赤壁之战", "东汉")
    resolved, picked = _resolve([a1, a2, b1], [], ["西汉"])
    assert resolved[0].dynasty == "西汉" and resolved[-1] is b1
    assert picked is True


# ---------- 真快照端到端（词典路径，不调模型） ----------

pytestmark_snapshot = pytest.mark.skipif(
    not (SNAP / "entities.json").exists() or not (SNAP / "dicts.json").exists(),
    reason="本地快照缺失（data/ 不入 Git）",
)


def _understand():
    from server.query import load_understanding
    return load_understanding(SNAP)


@pytestmark_snapshot
def test_real_snapshot_picks_dynasty_named_in_question():
    """问"西汉的井陉之战"→ 选中西汉那条（此前会选到战国），候选仍含两条。"""
    out = _understand().understand("西汉的井陉之战是怎么回事？")
    assert out.entities and out.entities[0].dynasty == "西汉"
    assert out.dynasty_disambiguated is True
    opts = [(o.dynasty, o.entity_id) for c in out.candidates for o in c.options]
    assert ("战国", "event_0175") in opts and ("西汉", "event_0219") in opts


@pytestmark_snapshot
def test_real_snapshot_without_dynasty_keeps_old_behavior():
    """不问朝代 → 与改进前一致（取首个），且可观测字段为 False。"""
    out = _understand().understand("井陉之战是怎么回事？")
    assert out.entities and out.entities[0].dynasty == "战国"
    assert out.dynasty_disambiguated is False
    assert any(len(c.options) >= 2 for c in out.candidates)


@pytestmark_snapshot
def test_real_snapshot_dynasty_suffix_variant():
    """问"唐朝的潼关之战"→ 实体朝代是"唐"，靠宽松比对命中。"""
    out = _understand().understand("唐朝的潼关之战是怎么回事？")
    assert out.entities and out.entities[0].dynasty == "唐"
    assert out.dynasty_disambiguated is True
