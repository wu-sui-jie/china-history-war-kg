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


def _resolve_index(settings: Settings, index_name: str) -> tuple[str, Path, Path]:
    """由索引目录名定位 (快照版本, 快照目录, 索引目录)。

    普通索引：目录名 == 快照版本号，manifest.source_snapshot 必须一致（RAGv1 起的约定）。
    索引变体：目录名 = <快照版本><后缀> 且 manifest 带 variant 标记，此时以
    manifest.source_snapshot 回指真实快照（供分块/向量参数对比等实验使用）。
    """
    index_dir = settings.index_dir / index_name
    if not index_dir.exists():
        raise FileNotFoundError(f"索引版本不存在: {index_dir}")
    manifest_path = index_dir / "manifest.json"
    source_version = index_name
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        source_version = manifest.get("source_snapshot") or index_name
        if source_version != index_name and not manifest.get("variant"):
            raise ValueError(
                f"索引 manifest.source_snapshot={source_version} 与索引目录 {index_name} "
                f"不一致（且无 variant 标记）"
            )
    snap_dir = settings.snapshot_dir / source_version
    if not snap_dir.exists():
        raise FileNotFoundError(
            f"索引 {index_name} 声明的来源快照不存在: {snap_dir}"
        )
    return source_version, snap_dir, index_dir


def resolve_version(settings: Settings, version: Optional[str] = None) -> tuple[str, Path, Path]:
    """定位快照+索引目录，返回 (快照版本, 快照目录, 索引目录)。

    version=None 取"最新快照中已有同版本索引"的那一个（仅精确同名匹配，
    不会自动选到索引变体）；显式传入时可传索引目录名（含变体后缀）。
    """
    if version is None:
        snaps = versions.list_versions(settings.snapshot_dir)
        if not snaps:
            raise FileNotFoundError(f"无可用快照: {settings.snapshot_dir}")
        for v in snaps:
            if (settings.index_dir / v).exists():
                return _resolve_index(settings, v)
        # 快照有但索引没同版本：回退最新快照（F03 可服务但 F04 不可用）
        v = snaps[0]
        raise FileNotFoundError(
            f"快照 {v} 无同版本索引目录，无法启动（快照与索引版本须一致）"
        )
    return _resolve_index(settings, version)


def build_runtime(settings: Settings, version: Optional[str] = None) -> Runtime:
    version, snap_dir, index_dir = resolve_version(settings, version)
    rt = Runtime(settings=settings, version=version,
                 snapshot_dir=snap_dir, index_dir=index_dir)

    # F02
    from server.query import load_understanding
    from server.query.llm_fallback import EntityFallbackClient
    # LLM 兜底默认关闭（每问多一次串行调用，吃首 Token 预算）；开启后词典完全未命中才触发
    fb_client = EntityFallbackClient(settings) if settings.enable_llm_entity_fallback else None
    rt.question = load_understanding(
        snap_dir,
        llm_client=fb_client,
        enable_llm=bool(settings.enable_llm_entity_fallback and fb_client and fb_client.available),
        history_max_turns=settings.history_max_turns,
    )

    # F03
    from server.graph import load_graph
    rt.graph = load_graph(snap_dir, version, top_k=settings.query_top_k_graph)

    # F04（向量检索：查询侧要调云端向量模型；密钥缺失/集合缺失/条数不一致 → 自动降级关键词）
    from data.index.embeddings import build_embed_fn
    from server.text import load_searcher
    rt.text = load_searcher(index_dir, version, top_k=settings.query_top_k_text,
                            collection_name=settings.chroma_collection,
                            embed_fn=build_embed_fn(settings))

    # F05
    from server.fusion import load_fusion
    rt.fusion = load_fusion(snap_dir, version)

    # F06
    from server.generate import AnswerGenerator
    rt.generate = AnswerGenerator(settings, settings.cache_dir, version)

    rt.meta = {
        "version": version,
        "index_version": index_dir.name,
        "graph_entities": len(rt.graph.entities),
        "graph_relations": len(rt.graph.relations),
        "text_mode": settings.text_mode,          # 配置的目标模式（keyword/vector/hybrid）
        "vector_available": bool(rt.text.vector_available),
        "llm_available": rt.generate.llm.available,
        "llm_model": settings.llm_model,
        "llm_entity_fallback": bool(settings.enable_llm_entity_fallback),
    }
    return rt
