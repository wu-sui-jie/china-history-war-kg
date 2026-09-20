"""事件实体卡叙事字段的守护用例（2026-09-20 借鉴项 P0）。

快照 `event_cards.json` 本就带 aggressor/defender/action/impact/place 五个叙事字段，
但 F05 装配事件卡时曾把它们全部丢弃——答完"赤壁之战"看不到谁攻谁守、也没有历史影响。
本用例固化修复后的口径：

1. 事件卡装配时必须带出这五个字段；
2. 快照缺列或空串时为 None（`to_dict` 会剔除，F07 按缺失不渲染）；
3. 边界：只补事件类型，人物/组织/地点的卡片不受影响；
4. 装配链路（build → panel.entity_cards）不丢字段。
"""

from __future__ import annotations

import json
from pathlib import Path

from server.fusion.panel_builder import PanelBuilder


def _snapshot(tmp_path: Path, entities: list[dict], cards: list[dict]) -> Path:
    (tmp_path / "entities.json").write_text(
        json.dumps(entities, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "event_cards.json").write_text(
        json.dumps(cards, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "relations.json").write_text("[]", encoding="utf-8")
    return tmp_path


def _event_card(eid: str = "event_0224", name: str = "赤壁之战") -> dict:
    return {
        "event_id": eid, "name": name, "type": "事件", "dynasty": "东汉",
        "event_type": "战争", "start_date": "208年",
        "aggressor": "曹操", "defender": "孙刘联军", "action": "水战",
        "impact": "奠定三国鼎立格局", "place": "赤壁",
    }


def test_event_card_carries_narrative_fields(tmp_path):
    ents = [{"entity_id": "event_0224", "name": "赤壁之战", "type": "事件", "dynasty": "东汉"}]
    pb = PanelBuilder(_snapshot(tmp_path, ents, [_event_card()]))
    card = pb._entity_card(pb.entities["event_0224"])
    assert card.aggressor == "曹操"
    assert card.defender == "孙刘联军"
    assert card.action == "水战"
    assert card.impact == "奠定三国鼎立格局"
    assert card.place == "赤壁"
    # 原有字段不受影响
    assert card.event_type == "战争"
    assert card.start_date == "208年"


def test_missing_narrative_fields_are_none(tmp_path):
    """快照缺列/空串/None 一律归一为 None，序列化时被剔除。"""
    ents = [{"entity_id": "event_0002", "name": "某战", "type": "事件"}]
    cards = [{"event_id": "event_0002", "name": "某战",
              "aggressor": "", "defender": None, "impact": "   "}]
    pb = PanelBuilder(_snapshot(tmp_path, ents, cards))
    card = pb._entity_card(pb.entities["event_0002"])
    assert card.aggressor is None
    assert card.defender is None
    assert card.impact is None
    assert card.action is None
    assert card.place is None
    assert "impact" not in card.to_dict()


def test_non_event_cards_keep_narrative_fields_empty(tmp_path):
    """边界：只有事件卡补叙事字段，人物/组织/地点卡不出现这些字段。"""
    ents = [
        {"entity_id": "person_1", "name": "曹操", "type": "人物", "role": "统帅"},
        {"entity_id": "place_1", "name": "赤壁", "type": "地点", "province": "湖北省"},
    ]
    pb = PanelBuilder(_snapshot(tmp_path, ents, []))
    person = pb._entity_card(pb.entities["person_1"])
    place = pb._entity_card(pb.entities["place_1"])
    assert person.role == "统帅" and person.aggressor is None
    assert place.province == "湖北省" and place.place is None


def test_build_keeps_narrative_fields_in_panel(tmp_path):
    """装配链路（build → panel.entity_cards）不丢叙事字段。"""
    ents = [{"entity_id": "event_0224", "name": "赤壁之战", "type": "事件", "dynasty": "东汉"}]
    pb = PanelBuilder(_snapshot(tmp_path, ents, [_event_card()]))
    panel = pb.build(hit_entities=[{"entity_id": "event_0224"}], graph_evidence=[])
    assert len(panel.entity_cards) == 1
    card = panel.entity_cards[0]
    assert card.impact == "奠定三国鼎立格局"
    assert card.aggressor == "曹操"
    # to_dict（SSE panel 事件的序列化出口）同样保留
    payload = panel.to_dict()["entity_cards"][0]
    assert payload["defender"] == "孙刘联军"
    assert payload["place"] == "赤壁"
