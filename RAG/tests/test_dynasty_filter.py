"""F02 问句朝代识别（dynasty_bias）语义回归。

语义：问句里识别到的朝代进 `F02Output.dynasty_bias`，
**只做排序偏置，不做硬过滤**；显式筛选（F01 下拉）仍走 `filters.dynasty` 硬过滤。
原因：硬过滤会把"被问到的朝代"连同事件本身剔除（问"鸣条之战与商朝的建立有什么关系"
时事件朝代为夏，一旦把"商朝"当硬筛选则图谱/文本/融合全 0、直接拒答）。

数据缺失自动 skip。运行：python -m pytest tests/test_dynasty_filter.py -q
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.query import classifier as clf

ROOT = Path(__file__).resolve().parent.parent
DICTS = ROOT / "data" / "snapshot" / "20260904_v2" / "dicts.json"

pytestmark = pytest.mark.skipif(
    not DICTS.exists(), reason="本地快照缺失（data/snapshot/ 不入 Git）"
)


@pytest.fixture(scope="module")
def aliases() -> dict:
    return json.loads(DICTS.read_text(encoding="utf-8")).get("dynasty_aliases") or {}


@pytest.mark.parametrize("question,expected", [
    # 多字别名直接命中
    ("战国时期的长平之战。", ["战国"]),
    ("三国时期的赤壁之战。", ["三国"]),
    # 单字朝代 + 朝/国 后缀（恢复识别，但只作偏置）
    ("鸣条之战与商朝的建立有什么关系？", ["商"]),
    ("秦朝为什么能统一六国？", ["秦"]),
    ("楚国在城濮之战中失败了吗？", ["楚"]),
    # 单字朝代 + "代" 后缀（清代/宋代/唐代等正当写法）
    ("清代的人口有多少？", ["清"]),
    ("宋代经济", ["宋"]),
    # 反例："时代/近代" 等不是朝代指称
    ("这是什么时代的事？", []),
    ("近代史", []),
    # 与多字别名重叠时不重复计（"清朝"已是别名，不再追加"清"）
    ("清朝末年发生了什么？", ["清朝"]),
    # 无"朝/国/代"后缀的普通用词不再误判
    ("秦为什么会发动长平之战？", []),   # "秦为"不是朝代指称
    ("巨鹿之战的楚军主帅是谁？", []),   # "楚军"不是朝代指称
    ("巨鹿之战发生于什么朝代？", []),   # "朝代"不是朝代指称
    ("介绍一下长平之战。", []),
])
def test_dynasty_mentions(question, expected, aliases):
    assert clf.extract_dynasty_mentions(question, aliases) == expected


def test_mention_overlap_skipped(aliases):
    """术语落在更长实体 mention 内部 → 视为实体的一部分，不作朝代指称。"""
    got = clf.extract_dynasty_mentions(
        "东汉统一战争中的赤壁之战。", aliases,
        entity_mentions=["东汉统一战争"],
    )
    assert "东汉" not in got


def test_accepts_alias_mapping_or_term_set(aliases):
    """入参兼容 {别名: 标准名} 映射与字符串集合两种形态。"""
    terms = set(aliases.keys())
    assert clf.extract_dynasty_mentions("战国时期的长平之战。", terms) == ["战国"]
    assert clf.extract_dynasty_mentions("鸣条之战与商朝的建立有什么关系？", terms) == ["商"]
