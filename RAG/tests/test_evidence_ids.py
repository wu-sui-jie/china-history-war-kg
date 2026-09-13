"""F03 图谱证据 ID 唯一性回归（修复跨 legacy 表行号重复）。

背景：`source_row_id` 只在各 legacy 表内唯一（实测 6437 个号跨表重复），旧实现
`graph_{source_row_id}` 会让不同证据共享同一 ID。修复后 ID 为
`graph_{legacy表名}_{source_row_id}`（缺行号时用含表名的内容哈希回退）。

数据缺失自动 skip。运行：python -m pytest tests/test_evidence_ids.py -q
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from config.settings import get_settings

ROOT = Path(__file__).resolve().parent.parent
SNAP = ROOT / "data" / "snapshot" / "20260904_v2"

pytestmark = pytest.mark.skipif(
    not (SNAP / "relations.json").exists(),
    reason="本地快照缺失（data/snapshot/ 不入 Git）",
)


def test_all_relation_rows_map_to_unique_ids():
    """用被测的 evidence_id_of 给全部 17,700 行生成 ID，必须两两不同。"""
    from server.graph.search import evidence_id_of

    rels = json.loads((SNAP / "relations.json").read_text(encoding="utf-8"))
    ids = [evidence_id_of(r) for r in rels]
    assert len(ids) == len(set(ids)) == len(rels)


def test_evidence_id_of_is_deterministic_and_qualified():
    """ID 规则单元测试（与被测代码同一函数，改坏规则时本用例会红）。"""
    from server.graph.search import evidence_id_of

    row = {"legacy_table": "event_person_relations", "source_row_id": 772,
           "source_name": "巨鹿之战", "relation": "统帅", "target_name": "项羽"}
    assert evidence_id_of(row) == "graph_event_person_relations_772"
    # 跨表同号必须得到不同 ID
    row2 = dict(row, legacy_table="event_event_relations")
    assert evidence_id_of(row) != evidence_id_of(row2)
    # 缺行号 → 确定性哈希回退（含表名），两次调用一致
    row3 = {"legacy_table": "event_person_relations", "relation": "统帅",
            "source_name": "巨鹿之战", "target_name": "项羽"}
    h1, h2 = evidence_id_of(row3), evidence_id_of(row3)
    assert h1 == h2                                  # 确定性
    assert h1.startswith("graph_event_person_relations_h")  # 带表名的哈希回退
    assert h1 != evidence_id_of(row)                 # 与有行号版本不同
    # 缺行号但表名不同 → 回退 ID 也不同
    assert h1 != evidence_id_of(dict(row3, legacy_table="event_event_relations"))


def test_runtime_graph_evidence_ids_unique_and_qualified():
    """真实检索一条会跨表命中的实体：证据 ID 唯一且带 legacy 表名。"""
    from contracts.question import QuestionType
    from server.graph import load_graph, search as gsearch

    settings = get_settings()
    graph = load_graph(settings.snapshot_dir / "20260904_v2", "20260904_v2", top_k=80)
    res = gsearch(graph, ["赤壁之战", "曹操"], QuestionType.RELATION, top_k=80)
    ids = [ev.evidence_id for ev in res.evidence]
    assert ids, "应命中图谱证据"
    assert len(ids) == len(set(ids)), "证据 ID 必须唯一"
    assert all(i.startswith("graph_") for i in ids)
    assert any(i.startswith("graph_event_person_relations_") for i in ids)
