"""在线侧消费推理边的守护用例（P2；设计见 docs/RAG_v2/RAG规则推理移植-需求与设计.md §6）。

口径：
1. GraphIndex 把 inferred_relations.json 与原始关系统一灌进邻接表；缺文件降级为纯原始图谱；
2. 推理边的证据带 inferred/rule_id/rule_name/derived_from/derived_from_rows，
   且 source_type=kg_inference（与事实层区分）；
3. 原始边的证据结构不变（不回归）；
4. 引用标题对推理边标注规则来源，提示词对推理边加"[推理关系：…]"；
5. 事件-事件关系白名单包含推理关系名，否则事件类问题会把推理边过滤掉。
"""

from __future__ import annotations

import json
from pathlib import Path

from contracts.evidence import EvidenceKind, SourceType
from server.generate import AnswerGenerator
from server.generate.prompts import build_messages
from server.graph.graph_index import GraphIndex
from server.graph.query_strategies import EVENT_EVENT_RELATIONS
from server.graph.search import GraphSearch

INFERRED_RELATIONS = {"间接因果", "连续演进", "战争阶段", "隶属于战役"}


def _ent(eid: str, name: str, etype: str = "事件") -> dict:
    return {"entity_id": eid, "name": name, "type": etype}


def _original_row() -> dict:
    return {
        "source_entity_id": "event_1", "source_name": "赤壁之战", "relation": "主战场",
        "target_entity_id": "place_1", "target_name": "赤壁",
        "confidence": "high", "legacy_table": "event_place_relations",
        "source_row_id": 7, "pending_review": False,
    }


def _inferred_row(**over) -> dict:
    row = {
        "source_entity_id": "place_1", "source_name": "赤壁", "relation": "发生于",
        "target_entity_id": "event_1", "target_name": "赤壁之战",
        "confidence": "high", "source_type": "inference", "legacy_table": "inference",
        "inferred": True, "rule_id": "war_001", "rule_name": "主战场反向推理规则",
        "derived_from": "主战场", "derived_from_rows": [[7, "event_place_relations"]],
        "composite": False, "path": [], "source_version": "test_v1",
    }
    row.update(over)
    return row


def _snapshot(tmp_path: Path, inferred: list[dict] | None) -> Path:
    (tmp_path / "entities.json").write_text(json.dumps(
        [_ent("event_1", "赤壁之战"), _ent("place_1", "赤壁", "地点")],
        ensure_ascii=False), encoding="utf-8")
    (tmp_path / "relations.json").write_text(json.dumps(
        [_original_row()], ensure_ascii=False), encoding="utf-8")
    if inferred is not None:
        (tmp_path / "inferred_relations.json").write_text(json.dumps(
            inferred, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_graph_index_loads_inferred_edges(tmp_path):
    graph = GraphIndex(_snapshot(tmp_path, [_inferred_row()]), "test_v1")
    assert graph.inferred_edge_count == 1
    # 推理边进入邻接表：从地点端能查到"发生于 → 赤壁之战"
    rels = {(rel, other) for rel, other, _ in graph.out_edges("place_1")}
    assert ("发生于", "event_1") in rels


def test_graph_index_degrades_without_inferred_file(tmp_path):
    """未产出推理产物的快照：行为与移植前一致（只有原始边）。"""
    graph = GraphIndex(_snapshot(tmp_path, None), "test_v1")
    assert graph.inferred_edge_count == 0
    assert all(not row.get("inferred") for _, _, row in graph.out_edges("place_1"))


def test_evidence_marks_inferred_edge(tmp_path):
    graph = GraphIndex(_snapshot(tmp_path, [_inferred_row()]), "test_v1")
    search = GraphSearch(graph)
    ev = search._triple_from_row(graph.inferred_relations[0])
    assert ev.source_type == SourceType.KG_INFERENCE
    assert ev.evidence_id.startswith("graph_inference_")
    assert ev.content["inferred"] is True
    assert ev.content["rule_id"] == "war_001"
    assert ev.content["rule_name"] == "主战场反向推理规则"
    assert ev.content["derived_from"] == "主战场"
    assert ev.content["derived_from_rows"] == [[7, "event_place_relations"]]


def test_evidence_for_original_row_unchanged(tmp_path):
    """原始边的证据结构不因推理改造而变（不回归）。"""
    graph = GraphIndex(_snapshot(tmp_path, [_inferred_row()]), "test_v1")
    search = GraphSearch(graph)
    ev = search._triple_from_row(graph.relations[0])
    assert ev.source_type == SourceType.KG_RELATION
    assert ev.evidence_id == "graph_event_place_relations_7"
    assert "inferred" not in ev.content
    assert ev.content["relation"] == "主战场"


def test_citations_mark_inferred_edge(tmp_path):
    graph = GraphIndex(_snapshot(tmp_path, [_inferred_row()]), "test_v1")
    search = GraphSearch(graph)
    inferred = search._triple_from_row(graph.inferred_relations[0])
    inferred.citation_index = 1
    original = search._triple_from_row(graph.relations[0])
    original.citation_index = 2

    cites = {c["index"]: c for c in AnswerGenerator.build_citations([inferred, original])}
    assert "推理" in cites[1]["title"]
    assert "主战场反向推理规则" in cites[1]["title"]
    assert "由「主战场」推导" in cites[1]["snippet"]
    # 原始边标题保持原样（不出现"推理"标注）
    assert "推理" not in cites[2]["title"]
    assert cites[2]["title"] == "赤壁之战—主战场→赤壁"


def test_prompt_marks_inferred_evidence(tmp_path):
    graph = GraphIndex(_snapshot(tmp_path, [_inferred_row()]), "test_v1")
    search = GraphSearch(graph)
    ev = search._triple_from_row(graph.inferred_relations[0])
    ev.citation_index = 1
    messages, block = build_messages("赤壁之战在哪儿打？", "赤壁之战 主战场", [ev])
    assert "[推理关系：" in block
    assert "主战场反向推理规则" in block
    assert "赤壁 —发生于→ 赤壁之战" in block
    # 规则 4 必须告知模型"推理关系不是史料原文"
    system = messages[0]["content"]
    assert "推理关系" in system and "不是史料原文" in system


def test_event_event_whitelist_includes_inferred_relations():
    missing = INFERRED_RELATIONS - EVENT_EVENT_RELATIONS
    assert not missing, f"事件-事件白名单缺少推理关系名：{missing}"


def test_evidence_kind_unchanged_for_inferred_edge(tmp_path):
    """推理边仍是 graph_triple 证据（融合、面板、引用的下游分支不用改）。"""
    graph = GraphIndex(_snapshot(tmp_path, [_inferred_row()]), "test_v1")
    ev = GraphSearch(graph)._triple_from_row(graph.inferred_relations[0])
    assert ev.kind == EvidenceKind.GRAPH_TRIPLE
