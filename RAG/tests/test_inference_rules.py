"""规则推理固化的守护用例（P2；设计见 docs/RAG_v2/RAG规则推理移植-需求与设计.md）。

用合成快照覆盖推理口径，不依赖本地数据产物：

1. 反向规则交换两端、标记齐备（inferred/rule_id/rule_name/derived_from/derived_from_rows）；
2. forward 方向不交换两端；
3. pending_review 关系跳过；
4. 复合规则 N 步链：中间节点写入 path、derived_from="<关系>链"、置信度取路径最低、
   同 (起点, 终点) 只产一条；
5. 无路径可走时产出 0 并在报告里注明原因（war_020 在真实快照上的情形）；
6. 稳定排序与幂等（两次构建字节一致）、单规则上限截断、规则文件校验。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from data.snapshot.inference import build_inferred_relations, load_rules


def _ent(eid: str, name: str, etype: str = "事件") -> dict:
    return {"entity_id": eid, "name": name, "type": etype}


def _rel(sid: str, sname: str, relation: str, tid: str, tname: str,
         table: str = "event_place_relations", rid: int = 1,
         pending: bool = False, confidence: str = "high") -> dict:
    return {"source_entity_id": sid, "source_name": sname, "relation": relation,
            "target_entity_id": tid, "target_name": tname, "confidence": confidence,
            "legacy_table": table, "source_row_id": rid, "pending_review": pending}


def _snapshot(tmp_path: Path, entities: list[dict], relations: list[dict]) -> Path:
    (tmp_path / "entities.json").write_text(
        json.dumps(entities, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "relations.json").write_text(
        json.dumps(relations, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def _rules_file(tmp_path: Path, rules: list[dict]) -> Path:
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
    return path


def _reverse_rule(rule_id: str = "war_001", relation: str = "主战场",
                  inferred: str = "发生于", direction: str = "reverse") -> dict:
    return {"rule_id": rule_id, "name": "主战场反向推理规则",
            "condition": {"relation": relation},
            "inference": {"relation": inferred, "direction": direction}}


def _composite_rule(rule_id: str = "war_014", relation: str = "因果关系",
                    inferred: str = "间接因果", path_length: int = 2) -> dict:
    return {"rule_id": rule_id, "name": "事件因果链推理规则",
            "condition": {"relation": relation, "composite": True, "path_length": path_length},
            "inference": {"relation": inferred, "direction": "forward", "transitive": True}}


def _rows(snap: Path) -> list[dict]:
    return json.loads((snap / "inferred_relations.json").read_text(encoding="utf-8"))


def test_reverse_rule_swaps_ends_and_marks(tmp_path):
    snap = _snapshot(
        tmp_path,
        [_ent("event_1", "赤壁之战"), _ent("place_1", "赤壁", "地点")],
        [_rel("event_1", "赤壁之战", "主战场", "place_1", "赤壁", rid=7)],
    )
    report = build_inferred_relations(snap, rules_path=_rules_file(tmp_path, [_reverse_rule()]))
    rows = _rows(snap)
    assert len(rows) == 1
    row = rows[0]
    # reverse：地点 → 事件
    assert (row["source_entity_id"], row["target_entity_id"]) == ("place_1", "event_1")
    assert (row["source_name"], row["target_name"]) == ("赤壁", "赤壁之战")
    assert row["relation"] == "发生于"
    # 标记齐备（引用与证据可溯源）
    assert row["inferred"] is True
    assert row["source_type"] == "inference"
    assert row["rule_id"] == "war_001"
    assert row["rule_name"] == "主战场反向推理规则"
    assert row["derived_from"] == "主战场"
    assert row["derived_from_rows"] == [[7, "event_place_relations"]]
    assert row["composite"] is False and row["path"] == []
    assert report.output_count == 1


def test_forward_direction_keeps_ends(tmp_path):
    snap = _snapshot(
        tmp_path,
        [_ent("event_1", "甲战"), _ent("place_1", "某地", "地点")],
        [_rel("event_1", "甲战", "主战场", "place_1", "某地", rid=3)],
    )
    build_inferred_relations(
        snap, rules_path=_rules_file(tmp_path, [_reverse_rule(direction="forward")]))
    row = _rows(snap)[0]
    assert (row["source_entity_id"], row["target_entity_id"]) == ("event_1", "place_1")


def test_pending_review_skipped(tmp_path):
    snap = _snapshot(
        tmp_path,
        [_ent("event_1", "甲战"), _ent("place_1", "某地", "地点")],
        [_rel("event_1", "甲战", "主战场", "place_1", "某地", rid=1, pending=True)],
    )
    report = build_inferred_relations(snap, rules_path=_rules_file(tmp_path, [_reverse_rule()]))
    assert _rows(snap) == []
    assert report.skipped_pending_review == 1


def test_composite_two_step_chain(tmp_path):
    snap = _snapshot(
        tmp_path,
        [_ent("e1", "A"), _ent("e2", "B"), _ent("e3", "C")],
        [_rel("e1", "A", "因果关系", "e2", "B", table="event_event_relations", rid=11),
         _rel("e2", "B", "因果关系", "e3", "C", table="event_event_relations", rid=12)],
    )
    report = build_inferred_relations(snap, rules_path=_rules_file(tmp_path, [_composite_rule()]))
    rows = _rows(snap)
    assert len(rows) == 1
    row = rows[0]
    assert (row["source_name"], row["relation"], row["target_name"]) == ("A", "间接因果", "C")
    assert row["composite"] is True
    assert row["derived_from"] == "因果关系链"
    assert row["path"] == [{"entity_id": "e2", "name": "B", "type": "事件"}]
    assert row["derived_from_rows"] == [[11, "event_event_relations"],
                                        [12, "event_event_relations"]]
    assert report.per_rule[0]["produced"] == 1


def test_composite_three_step_chain_and_confidence_is_lowest(tmp_path):
    snap = _snapshot(
        tmp_path,
        [_ent("e1", "A"), _ent("e2", "B"), _ent("e3", "C"), _ent("e4", "D")],
        [_rel("e1", "A", "包含关系", "e2", "B", table="event_event_relations", rid=1, confidence="high"),
         _rel("e2", "B", "包含关系", "e3", "C", table="event_event_relations", rid=2, confidence="low"),
         _rel("e3", "C", "包含关系", "e4", "D", table="event_event_relations", rid=3, confidence="high")],
    )
    rules = _rules_file(tmp_path, [_composite_rule("war_020", "包含关系", "战争阶段", 3)])
    build_inferred_relations(snap, rules_path=rules)
    rows = _rows(snap)
    assert len(rows) == 1
    assert (rows[0]["source_name"], rows[0]["target_name"]) == ("A", "D")
    assert [p["name"] for p in rows[0]["path"]] == ["B", "C"]
    assert rows[0]["confidence"] == "low", "复合规则置信度取路径上最低的一档"


def test_composite_without_chain_records_note(tmp_path):
    """war_020 在真实快照上的情形：数据没有 3 步链 → 产出 0 且报告注明原因。"""
    snap = _snapshot(
        tmp_path,
        [_ent("e1", "A"), _ent("e2", "B")],
        [_rel("e1", "A", "包含关系", "e2", "B", table="event_event_relations", rid=1)],
    )
    rules = _rules_file(tmp_path, [_composite_rule("war_020", "包含关系", "战争阶段", 3)])
    report = build_inferred_relations(snap, rules_path=rules)
    assert _rows(snap) == []
    assert report.per_rule[0]["produced"] == 0
    assert "无命中" in report.per_rule[0]["note"]


def test_dedup_stable_order_and_idempotent(tmp_path):
    """重复原始行 → 推理去重；两次构建字节一致（发布哈希对账依赖）。"""
    snap = _snapshot(
        tmp_path,
        [_ent("event_1", "甲战"), _ent("place_1", "某地", "地点")],
        [_rel("event_1", "甲战", "主战场", "place_1", "某地", rid=1),
         _rel("event_1", "甲战", "主战场", "place_1", "某地", rid=2)],   # 重复行
    )
    rules = _rules_file(tmp_path, [_reverse_rule()])
    build_inferred_relations(snap, rules_path=rules)
    first = (snap / "inferred_relations.json").read_bytes()
    assert len(_rows(snap)) == 1, "同一 (源, 关系, 目标, 规则) 只留一条"
    build_inferred_relations(snap, rules_path=rules)
    assert (snap / "inferred_relations.json").read_bytes() == first


def test_max_per_rule_truncates_and_reports(tmp_path):
    snap = _snapshot(
        tmp_path,
        [_ent("event_1", "甲战"), _ent("place_1", "某地", "地点"), _ent("place_2", "乙地", "地点")],
        [_rel("event_1", "甲战", "主战场", "place_1", "某地", rid=1),
         _rel("event_1", "甲战", "主战场", "place_2", "乙地", rid=2)],
    )
    report = build_inferred_relations(
        snap, rules_path=_rules_file(tmp_path, [_reverse_rule()]), max_per_rule=1)
    assert len(_rows(snap)) == 1
    assert report.truncated_rules == ["war_001"]


def test_dry_run_writes_nothing(tmp_path):
    snap = _snapshot(
        tmp_path,
        [_ent("event_1", "甲战"), _ent("place_1", "某地", "地点")],
        [_rel("event_1", "甲战", "主战场", "place_1", "某地", rid=1)],
    )
    report = build_inferred_relations(
        snap, rules_path=_rules_file(tmp_path, [_reverse_rule()]), dry_run=True)
    assert report.output_count == 1
    assert not (snap / "inferred_relations.json").exists()
    assert not (snap / "inference_report.json").exists()


def test_load_rules_rejects_incomplete_rule(tmp_path):
    path = _rules_file(tmp_path, [{"rule_id": "x", "name": "坏规则",
                                   "condition": {}, "inference": {"relation": "y"}}])
    with pytest.raises(ValueError):
        load_rules(path)


def test_shipped_rule_base_has_twenty_rules():
    """随仓库发布的规则库必须仍是 20 条（与旧系统 rule_base.json 对齐）。"""
    rules = load_rules(Path(__file__).resolve().parent.parent / "data" / "rules" / "rule_base.json")
    assert len(rules) == 20
    assert {r.rule_id for r in rules} == {f"war_{i:03d}" for i in range(1, 21)}
    composite = [r for r in rules if r.condition.composite]
    assert {r.rule_id for r in composite} == {"war_014", "war_015", "war_020"}
