"""F05 证据融合对外入口（server/fusion/__init__.py）。

assemble(graph_result, text_result, qtype, snapshot_dir, source_version)
→ (FusionOutput, PanelData)：融合 + citation + conflicts + panel（唯一装配方）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from contracts.question import QuestionType
from contracts.retrieval import FusionOutput, GraphResult, TextResult
from server.fusion.fusion import fuse
from server.fusion.panel_builder import PanelBuilder


class FusionService:
    def __init__(self, snapshot_dir: Path, source_version: str):
        self.source_version = source_version
        self.panel_builder = PanelBuilder(snapshot_dir)
        self._field_map = None
        self._load_field_map(snapshot_dir)

    def _load_field_map(self, snapshot_dir: Path) -> None:
        p = Path(snapshot_dir) / "relation_card_field_map.json"
        if p.exists():
            self._field_map = json.loads(p.read_text(encoding="utf-8"))

    def assemble(self, graph: GraphResult, text: TextResult,
                 qtype: QuestionType,
                 limit: Optional[int] = None) -> tuple[FusionOutput, "object"]:
        ctx = {
            "field_map": self._field_map,
            "event_cards": list(self.panel_builder.event_cards.values()),
            "source_version": self.source_version,
        }
        fused = fuse(graph.evidence, text.evidence, qtype,
                     conflict_context=ctx, limit=limit or 18)
        panel = self.panel_builder.build(
            graph.hit_entities, fused.evidence,
            related_event_ids=graph.related_event_ids,
            neighbors=graph.neighbors,
        )
        return fused, panel


def load_fusion(snapshot_dir: Path, source_version: str) -> FusionService:
    return FusionService(snapshot_dir=snapshot_dir, source_version=source_version)
