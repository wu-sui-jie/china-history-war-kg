"""地图点位装配（F05 → F07）的守护用例（RAGv5 2026-09-15 加坐标后）。

快照里同一地名有多行（跨朝代重复），实测规律是**带地址线索（省/今址）的那一簇才是正确
位置**——长平 → 山西高平市、河内 → 河南沁阳；无地址线索的行会被地理编码落到同名村庄
（云南的"河内"、贵州的"长平"）。本用例用合成快照固化四条口径：

1. 同簇重复行合并成一个点、事件取并集；
2. 同名多簇时优先带地址线索的簇（不是"行数最多"也不是"在图里"）；
3. 都没有地址线索时退回行数最多的簇；
4. 无坐标的地点不出点（前端降级为地点列表）；点位按事件数排序并受上限约束。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contracts.evidence import Confidence, Evidence, EvidenceKind, SourceType
from server.fusion.panel_builder import MAP_MAX_POINTS, PanelBuilder


def _place(eid: str, name: str, lng=None, lat=None, province="", modern="", dynasty="") -> dict:
    return {"entity_id": eid, "name": name, "type": "地点", "longitude": lng, "latitude": lat,
            "province": province, "modern_name": modern, "dynasty": dynasty}


def _snapshot(tmp_path: Path, entities: list[dict], rels: list[dict] | None = None) -> Path:
    (tmp_path / "entities.json").write_text(json.dumps(entities, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "event_cards.json").write_text("[]", encoding="utf-8")
    (tmp_path / "relations.json").write_text(json.dumps(rels or [], ensure_ascii=False), encoding="utf-8")
    return tmp_path


def _evidence(names: list[str]) -> list[Evidence]:
    """构造"地点作为图谱三元组宾语"的证据，等价于 _build_map_points 的输入。"""
    return [
        Evidence(
            evidence_id=f"ev-{i}", kind=EvidenceKind.GRAPH_TRIPLE,
            source_type=SourceType.KG_RELATION, source_version="test",
            confidence=Confidence.HIGH, score=1.0, citation_index=i + 1,
            content={"subject": "某战役", "subject_type": "事件", "relation": "主战场",
                     "object": nm, "object_type": "地点"},
        )
        for i, nm in enumerate(names)
    ]


def test_duplicate_rows_merge_with_events_union(tmp_path):
    """同坐标的多行合并为一点，事件取并集（洛阳 40 行的场景）。"""
    ents = [_place("place_1", "洛阳", 112.45, 34.62, province="河南省", modern="洛阳市"),
            _place("place_2", "洛阳", 112.4501, 34.6202, dynasty="唐"),
            _place("place_3", "洛阳", 112.45, 34.62, dynasty="东汉")]
    rels = [{"source_entity_id": "place_1", "target_entity_id": "event_a", "relation": "主战场"},
            {"source_entity_id": "place_3", "target_entity_id": "event_b", "relation": "主战场"}]
    ents.append({"entity_id": "event_a", "name": "甲战", "type": "事件"})
    ents.append({"entity_id": "event_b", "name": "乙战", "type": "事件"})
    pb = PanelBuilder(_snapshot(tmp_path, ents, rels))
    pts = pb._build_map_points(_evidence(["洛阳"]))
    assert len(pts) == 1
    assert pts[0].longitude == pytest.approx(112.45)
    assert set(pts[0].events) == {"event_a", "event_b"}      # 合并去重
    assert pts[0].modern_name == "洛阳市"                     # 取地址最全的行


def test_prefers_cluster_with_address_clue(tmp_path):
    """同名多簇：优先带地址线索的簇，而不是行数更多但无地址的簇。"""
    ents = [
        # 无地址簇：3 行（会被地理编码落到同名村庄，如"贵州长平"）
        _place("p_south1", "长平", 107.41, 27.52), _place("p_south2", "长平", 107.41, 27.52),
        _place("p_south3", "长平", 107.41, 27.52),
        # 有地址簇：1 行（山西高平市 = 真正的长平古战场）
        _place("p_real", "长平", 112.92, 35.80, province="山西省", modern="高平市"),
    ]
    pb = PanelBuilder(_snapshot(tmp_path, ents))
    pts = pb._build_map_points(_evidence(["长平"]))
    assert len(pts) == 1
    assert (pts[0].longitude, pts[0].latitude) == pytest.approx((112.92, 35.80))
    assert pts[0].place_id == "p_real"


def test_falls_back_to_majority_cluster_without_address(tmp_path):
    """两簇都没有地址线索时，退回行数最多的簇（数据共识）。"""
    ents = [_place("a1", "某地", 100.0, 30.0), _place("a2", "某地", 100.0, 30.0),
            _place("b1", "某地", 120.0, 40.0)]
    pb = PanelBuilder(_snapshot(tmp_path, ents))
    pts = pb._build_map_points(_evidence(["某地"]))
    assert len(pts) == 1 and (pts[0].longitude, pts[0].latitude) == pytest.approx((100.0, 30.0))


def test_place_without_coords_excluded(tmp_path):
    """无坐标的地点不出点（前端按现有降级显示地点列表）。"""
    ents = [_place("p1", "赤壁"), _place("p2", "乌林", 113.5, 29.9, province="湖北省")]
    pb = PanelBuilder(_snapshot(tmp_path, ents))
    pts = pb._build_map_points(_evidence(["赤壁", "乌林"]))
    assert [p.name for p in pts] == ["乌林"]


def test_sorted_by_events_and_capped(tmp_path):
    """按事件数排序，且不超过上限。"""
    ents, rels = [], []
    for i in range(MAP_MAX_POINTS + 3):
        eid = f"p{i}"
        ents.append(_place(eid, f"地{i}", 100.0 + i, 30.0 + i, province="河南省"))
        ents.append({"entity_id": f"ev{i}", "name": f"战{i}", "type": "事件"})
        # 事件数随 i 递减 → 期望排序后地0在前
        for k in range(MAP_MAX_POINTS + 3 - i):
            rels.append({"source_entity_id": eid, "target_entity_id": f"ev{k % 5}", "relation": "主战场"})
    pb = PanelBuilder(_snapshot(tmp_path, ents, rels))
    pts = pb._build_map_points(_evidence([f"地{i}" for i in range(MAP_MAX_POINTS + 3)]))
    assert len(pts) == MAP_MAX_POINTS
    assert pts[0].name == "地0"
    assert len(pts[0].events) >= len(pts[-1].events)


def test_no_place_in_evidence_returns_empty(tmp_path):
    ents = [_place("p1", "长安", 108.9, 34.2, province="陕西省")]
    pb = PanelBuilder(_snapshot(tmp_path, ents))
    assert pb._build_map_points([]) == []
