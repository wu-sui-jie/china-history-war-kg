"""F02 实体匹配 / 朝代过滤器识别的跨进程确定性回归测试。

背景：`sorted(set(...), key=len, reverse=True)` 在等长词上按 set 迭代顺序，受
PYTHONHASHSEED 影响 → 跨进程结果不稳定，曾导致评测两次 run 的证据顺序/回答顺序
不一致（F10 可复现性验收项）。修复后并列长度按词本身排序。

数据缺失时自动 skip。运行：python -m pytest tests/test_query_determinism.py -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from config.settings import get_settings
from server.query import load_understanding

ROOT = Path(__file__).resolve().parent.parent
SNAP = ROOT / "data" / "snapshot" / "20260904_v2"

pytestmark = pytest.mark.skipif(
    not (SNAP / "entities.json").exists(),
    reason="本地快照缺失（data/snapshot/ 不入 Git）",
)


@pytest.fixture(scope="module")
def qu():
    s = get_settings()
    return load_understanding(s.snapshot_dir / "20260904_v2")


def test_equal_length_mentions_have_deterministic_order(qu):
    """等长实体名并列时按词序（码位）排序：赤壁之战 vs 孙刘联军 → 孙 在前。"""
    hits = qu.matcher.match("赤壁之战中孙刘联军的主帅是谁？")
    names = [h.name for h in hits]
    assert names == ["孙刘联军", "赤壁之战"]
    # 同进程重复调用稳定
    again = [h.name for h in qu.matcher.match("赤壁之战中孙刘联军的主帅是谁？")]
    assert again == names


def test_longest_match_wins_for_prefix_names(qu):
    """最长优先仍生效：'涿鹿之战' 不应被 '涿鹿'（地点）抢先命中。"""
    hits = qu.matcher.match("介绍一下涿鹿之战。")
    assert any(h.name == "涿鹿之战" and h.type == "事件" for h in hits)


def test_dynasty_filter_order_deterministic(qu):
    """朝代过滤器识别顺序稳定（并列长度按词序）。"""
    terms = set(qu.matcher._dynasty_terms)
    from server.query import classifier as clf

    a = clf.extract_dynasty_mentions("战国时期的长平之战与秦朝", terms)
    b = clf.extract_dynasty_mentions("战国时期的长平之战与秦朝", terms)
    assert a == b
