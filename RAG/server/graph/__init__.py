"""F03 图谱检索对外入口。

load_graph(snapshot_dir, source_version) 启动时调用一次（内存图）。
search(graph, names, qtype, filters, top_k, dynasty_bias) → GraphResult。
dynasty_bias 为问句自动识别的朝代，只影响节点排序（不剔除节点）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from contracts.question import QuestionType
from contracts.retrieval import GraphResult
from server.graph.graph_index import GraphIndex
from server.graph.search import GraphSearch


def load_graph(snapshot_dir: Path, source_version: str, top_k: int = 40) -> GraphIndex:
    return GraphIndex(snapshot_dir=snapshot_dir, source_version=source_version)


def search(graph: GraphIndex, entity_names: list[str], qtype: QuestionType,
           filters: Optional[dict] = None, top_k: Optional[int] = None,
           dynasty_bias: Optional[list] = None) -> GraphResult:
    gs = GraphSearch(graph, top_k or 40)
    return gs.search(entity_names, qtype, filters, top_k=top_k,
                     dynasty_bias=dynasty_bias)
