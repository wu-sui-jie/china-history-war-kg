"""F05 timeline 组内排序的守护用例。

口径：按朝代分组、组内按可解析的 start_date 升序；无法解析的日期（"夏朝末年"、
"约四五千年前"等）保持数据原序置于组内末尾；"时间不详/仅知朝代"分组整体置尾。
"""

from __future__ import annotations

import json
from pathlib import Path

from server.fusion.panel_builder import (
    UNKNOWN_TIME_LABEL,
    PanelBuilder,
    _start_year,
    _sort_by_start_date,
)


def _snapshot(tmp_path: Path, cards: list[dict]) -> Path:
    (tmp_path / "entities.json").write_text("[]", encoding="utf-8")
    (tmp_path / "event_cards.json").write_text(
        json.dumps(cards, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "relations.json").write_text("[]", encoding="utf-8")
    return tmp_path


def _card(eid: str, name: str, start_date: str, dynasty: str = "战国") -> dict:
    return {"event_id": eid, "name": name, "start_date": start_date, "dynasty": dynasty}


def test_start_year_parses_reliable_formats_only():
    assert _start_year("前2179年") == -2179
    assert _start_year("公元前26世纪") == -2550
    assert _start_year("前1296年之后") == -1296
    # 模糊/中文数字表述不得猜年份，保持 None（数据原序）
    assert _start_year("夏朝末年") is None
    assert _start_year("约四五千年前") is None
    assert _start_year("不详") is None
    assert _start_year(None) is None


def test_timeline_sorted_within_dynasty(tmp_path):
    cards = [
        _card("e1", "晚战", "前300年"),
        _card("e2", "早战", "前500年"),
        _card("e3", "中战", "前400年"),
    ]
    pb = PanelBuilder(_snapshot(tmp_path, cards))
    tl = pb._build_timeline(["e1", "e2", "e3"])
    assert [g["label"] for g in tl["groups"]] == ["战国"]
    assert [it["name"] for it in tl["groups"][0]["items"]] == ["早战", "中战", "晚战"]


def test_unparsable_dates_keep_original_order_at_tail(tmp_path):
    cards = [
        _card("e1", "模糊甲", "夏朝末年"),
        _card("e2", "确切", "前2179年"),
        _card("e3", "模糊乙", "约四五千年前"),
    ]
    pb = PanelBuilder(_snapshot(tmp_path, cards))
    tl = pb._build_timeline(["e1", "e2", "e3"])
    assert [it["name"] for it in tl["groups"][0]["items"]] == ["确切", "模糊甲", "模糊乙"]


def test_century_sorts_before_later_year(tmp_path):
    cards = [
        _card("e1", "前2179年事", "前2179年"),
        _card("e2", "公元前26世纪事", "公元前26世纪"),
    ]
    pb = PanelBuilder(_snapshot(tmp_path, cards))
    tl = pb._build_timeline(["e1", "e2"])
    assert [it["name"] for it in tl["groups"][0]["items"]] == ["公元前26世纪事", "前2179年事"]


def test_unknown_time_group_still_last(tmp_path):
    cards = [
        _card("e1", "不详事", "不详"),
        _card("e2", "确切", "前100年"),
    ]
    pb = PanelBuilder(_snapshot(tmp_path, cards))
    tl = pb._build_timeline(["e1", "e2"])
    labels = [g["label"] for g in tl["groups"]]
    assert labels[-1] == UNKNOWN_TIME_LABEL
    assert [it["name"] for it in tl["groups"][0]["items"]] == ["确切"]


def test_sort_helper_is_stable_for_equal_years():
    from contracts.panel import TimelineItem

    items = [
        TimelineItem(event_id="a", name="甲", start_date="前100年"),
        TimelineItem(event_id="b", name="乙", start_date="夏朝末年"),
        TimelineItem(event_id="c", name="丙", start_date="前100年"),
        TimelineItem(event_id="d", name="丁", start_date="商汤时期"),
    ]
    out = _sort_by_start_date(items)
    assert [it.name for it in out] == ["甲", "丙", "乙", "丁"]
