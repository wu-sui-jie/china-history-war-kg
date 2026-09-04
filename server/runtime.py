"""服务启动加载（server/runtime.py）。

加载顺序：
1. 解析快照/索引版本（默认最新一致版本；校验 source_snapshot 一致）；
2. F02 question understanding（词典匹配器）；
3. F03 graph（内存图谱）；
4. F04 text searcher（FTS5，探测向量可用性）；
5. F05 fusion（快照 cards + field map）；
6. F06 answer generator（LLM 客户端 + 回答缓存）。

数据源全部来自 RAG/data，不依赖旧服务。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from config.settings import Settings
from lib import versions

# 允许运行期覆盖的版本（测试用）
DEFAULT_VERSION = None  # None → 取最新一致版本


@dataclass
class Runtime:
    settings: Settings
    version: str
    snapshot_dir: Path
    index_dir: Path
    question: object = None          # QuestionUnderstanding
    graph: object = None             # GraphIndex
    text: object = None              # TextSearcher
    fusion: object = None            # FusionService
    generate: object = None          # AnswerGenerator
    meta: dict = field(default_factory=dict)

    def shutdown(self) -> None:
        """释放资源（数据库连接等）。"""
        pass


def resolve_version(settings: Settings, version: Optional[str] = None) -> tuple[str, Path, Path]:
    """定位快照+索引同版本目录；校验 manifest 一致。找不到则抛 FileNotFoundError。"""
    if version is None:
        snaps = versions.list_versions(settings.snapshot_dir)
        if not snaps:
            raise FileNotFoundError(f"无可用快照: {settings.snapshot_dir}")
        for v in snaps:
            if (settings.index_dir / v).exists():
                return v, settings.snapshot_dir / v, settings.index_dir / v
        # 快照有但索引没同版本：回退最新快照（F03 可服务但 F04 不可用）
        v = snaps[0]
        raise FileNotFoundError(
            f"快照 {v} 无同版本索引目录，无法启动（快照与索引版本须一致）"
        )
    snap_dir = settings.snapshot_dir / version
    index_dir = settings.index_dir / version
    if not snap_dir.exists():
        raise FileNotFoundError(f"快照版本不存在: {snap_dir}")
    if not index_dir.exists():
        raise FileNotFoundError(f"索引版本不存在: {index_dir}")
    # 校验 manifest 版本一致
    idx_manifest = json.loads((index_dir / "manifest.json").read_text(encoding="utf-8"))
    if idx_manifest.get("source_snapshot") != version:
        raise ValueError(f"索引 manifest.source_snapshot={idx_manifest.get('source_snapshot')} "
                         f"与版本 {version} 不一致")
    return version, snap_dir, index_dir


def build_runtime(settings: Settings, version: Optional[str] = None) -> Runtime:
    version, snap_dir, index_dir = resolve_version(settings, version)
    rt = Runtime(settings=settings, version=version,
                 snapshot_dir=snap_dir, index_dir=index_dir)

    # F02
    from server.query import load_understanding
    rt.question = load_understanding(
        snap_dir,
        llm_client=None,           # 词典优先，LLM 兜底预留（接入 llm_client 后可开）
        enable_llm=False,
        history_max_turns=settings.history_max_turns,
    )

    # F03
    from server.graph import load_graph
    rt.graph = load_graph(snap_dir, version, top_k=settings.query_top_k_graph)

    # F04
    from server.text import load_searcher
    rt.text = load_searcher(index_dir, version, top_k=settings.query_top_k_text)

    # F05
    from server.fusion import load_fusion
    rt.fusion = load_fusion(snap_dir, version)

    # F06
    from server.generate import AnswerGenerator
    rt.generate = AnswerGenerator(settings, settings.cache_dir, version)

    rt.meta = {
        "version": version,
        "graph_entities": len(rt.graph.entities),
        "graph_relations": len(rt.graph.relations),
        "text_mode": "keyword",
        "vector_available": bool(rt.text.vector_available),
        "llm_available": rt.generate.llm.available,
        "llm_model": settings.llm_model,
    }
    return rt
