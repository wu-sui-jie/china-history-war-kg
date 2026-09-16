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

import inspect
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from config.settings import Settings
from lib import release_info, versions

# 允许运行期覆盖的版本（测试用）
DEFAULT_VERSION = None  # None → settings.active_version，未配置则取最新一致版本


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
    # 向量客户端（EmbeddingClient）：持有 HTTP 连接池，必须在 shutdown 中释放（P0-3）
    embedding_client: object = None
    meta: dict = field(default_factory=dict)
    _shutdown_done: bool = field(default=False, repr=False)

    def resources(self) -> list[tuple[str, object]]:
        """枚举全部需要释放的外部资源（工作单 P0-3）。

        新增外部客户端时必须登记在这里，否则会出现"Runtime 说关干净了、实际还留着连接池"。
        """
        return [
            ("generate.llm", getattr(self.generate, "llm", None)),
            ("question.llm", getattr(self.question, "llm", None)),
            ("embedding_client", self.embedding_client),
            ("text.embed_fn", getattr(self.text, "embed_fn", None)
             if getattr(self.text, "embed_fn", None) is not self.embedding_client else None),
        ]

    async def shutdown(self) -> None:
        """释放资源：关闭全部外部 HTTP 客户端（异步客户端必须 await 关闭）。

        第四轮复核 P0-3：旧实现是同步方法，在 FastAPI 正在运行的事件循环里对 coroutine
        调用 `asyncio.run()`，必然抛 RuntimeError（事件循环已在运行），异常又被吞掉，
        结果 AsyncOpenAI 客户端从未真正关闭。

        本工作单 P0-3 补齐三点：
        - 资源枚举统一走 `resources()`，向量客户端（EmbeddingClient）也在其中；
        - 幂等：重复 shutdown 不重复关闭同一资源；
        - 单个资源关闭失败只记 warning，其余资源继续关闭。
        """
        if self._shutdown_done:
            return
        self._shutdown_done = True
        for name, owner in self.resources():
            if owner is None:
                continue
            closer = getattr(owner, "aclose", None) or getattr(owner, "close", None)
            if not callable(closer):
                continue
            try:
                result = closer()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:  # noqa: BLE001
                logging.getLogger("rag.runtime").warning(
                    "关闭 %s 失败（其余资源继续释放）：%s", name, exc)
        # 关闭顺序：先释放外部客户端，再回收同步工作池（lifespan 里也会调用一次，
        # 那里针对"runtime 构建失败"的情况兜底）
        try:
            from server.sse import shutdown_sync_pool

            shutdown_sync_pool()
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("rag.runtime").warning("关闭同步工作池失败：%s", exc)


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

    版本优先级：显式参数 > settings.active_version（RAG_ACTIVE_VERSION）> 最新一致版本。
    显式指定（含环境变量）时**不做静默回退**：目录不存在就报错，
    否则"以为在用 A 数据、实际悄悄切到 B"会造成无法复现的线上现象（2026-09-15 审核 P0-7）。

    强制版本检查必须发生在**扫描目录之前**（2026-09-15 第四轮复核 P0-1）：旧实现把
    `require_active_version` 的判断放在扫描分支之后，未指定版本时会先返回"最新一致版本"，
    这段检查永远不会执行——生产环境设了 RAG_REQUIRE_ACTIVE_VERSION=true 却漏配
    RAG_ACTIVE_VERSION 时仍会静默启用最大目录名对应的数据。
    """
    explicit = version or (settings.active_version or None)
    if explicit is None:
        if settings.require_active_version:
            raise ValueError(
                "生产模式要求显式数据版本：请设置 RAG_ACTIVE_VERSION=<版本> 或启动时加 --version；"
                "若确实要按目录扫描最新一致版本，显式设置 RAG_REQUIRE_ACTIVE_VERSION=false"
            )
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
    return _resolve_index(settings, explicit)


def version_source(settings: Settings, version: Optional[str] = None) -> str:
    """版本来源（工作单 P0-4）：CLI 显式 / 环境变量固定 / 扫描最新。

    原先只用 `settings.active_version` 判断，CLI 传 `--version` 时 health 会显示成
    `latest_scan`——那会让"我到底跑的是哪份数据"这个问题的答案失真。
    """
    if version:
        return "cli_explicit"
    if settings.active_version:
        return "env_pinned"
    return "latest_scan"


def build_runtime(settings: Settings, version: Optional[str] = None) -> Runtime:
    version_arg = version
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
    # 用可关闭的客户端对象而不是裸闭包，服务端才能枚举并释放它的 HTTP 连接池（工作单 P0-3）
    from data.index.embeddings import build_embedding_client
    from server.text import load_searcher
    rt.embedding_client = build_embedding_client(settings)
    rt.text = load_searcher(index_dir, version, top_k=settings.query_top_k_text,
                            collection_name=settings.chroma_collection,
                            embed_fn=rt.embedding_client)

    # F05
    from server.fusion import load_fusion
    rt.fusion = load_fusion(snap_dir, version)

    # F06
    from server.generate import AnswerGenerator
    rt.generate = AnswerGenerator(settings, settings.cache_dir, version)

    artifacts = release_info.version_artifacts(snap_dir, index_dir)
    repo_root = release_info.repo_root()
    git_commit = release_info.git_commit(repo_root)
    git_dirty = release_info.git_dirty(repo_root)
    config_fp = release_info.config_fingerprint(settings)
    manifest_hash = release_info.artifact_manifest_sha256(settings.data_dir)
    version_pinned = bool(settings.active_version)
    rt.meta = {
        "version": version,
        "index_version": index_dir.name,
        "active_version_pinned": version_pinned,
        # 三种来源必须可区分（工作单 P0-4）：CLI 显式 / 环境变量固定 / 扫描最新
        "version_selection": version_source(settings, version_arg),
        "source_dirty": git_dirty,
        "config_fingerprint": config_fp,
        "artifact_manifest_sha256": manifest_hash,
        # release_id：把"哪份源码 + 哪份配置 + 哪份数据"压成一个可引用的标识
        "release_id": f"{git_commit or 'no-git'}:{version}:{config_fp[:12]}"
                      f"{'' if not git_dirty else '+dirty'}",
        "graph_entities": len(rt.graph.entities),
        "graph_relations": len(rt.graph.relations),
        "text_mode": settings.text_mode,          # 配置的目标模式（keyword/vector/hybrid）
        "vector_available": bool(rt.text.vector_available),
        "llm_available": rt.generate.llm.available,
        "llm_model": settings.llm_model,
        "llm_entity_fallback": bool(settings.enable_llm_entity_fallback),
        "expose_thinking": bool(settings.expose_thinking),
        **artifacts,
        "git_commit": git_commit,
    }
    return rt
