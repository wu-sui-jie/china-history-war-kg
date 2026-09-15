"""配置加载：读取 .env 并合并环境变量，返回 Settings。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from dotenv import load_dotenv

from config import defaults

# 从仓库根 / RAG 根加载 .env（若存在）。keys=True 表示不覆盖已有环境变量。
load_dotenv(defaults.RAG_ROOT / ".env", override=False)

# 云端向量模型单请求上限（百炼 text-embedding-v4：最多 10 条文本；配大只会报错）
MAX_EMBEDDING_BATCH = 10


def _path_env(key: str, default: Path) -> Path:
    v = os.environ.get(key)
    return Path(v).expanduser() if v else default


def _bool_env(key: str, default: bool) -> bool:
    v = os.environ.get(key)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _first_env(*keys: str, default: str = "") -> str:
    """按优先级返回第一个非空环境变量（密钥别名链用）。

    为什么需要别名链：Windows 连字符变量名（如 RAG-command）在 Linux 上不合法，
    部署到 Linux 时无法 export；且系统环境变量需重启进程才会被继承。见开发说明 §四.11。
    """
    for key in keys:
        val = os.environ.get(key)
        if val and val.strip():
            return val.strip()
    return default


@dataclass
class Settings:
    data_dir: Path
    raw_dir: Path
    snapshot_dir: Path
    index_dir: Path
    cache_dir: Path
    log_dir: Path
    # 同源托管（D8）：后端挂载的前端构建产物目录；不存在时跳过挂载
    frontend_dist: Path

    legacy_sqlite_path: Path
    legacy_raw_texts: List[Path]

    governance_enable_relation_extraction: bool
    index_build_embeddings: bool

    chunk_max_chars: int
    chunk_overlap_chars: int

    # ---- 在线链路（RAGv2，F02–F06）----
    # 大模型（OpenAI 兼容；RAGv5 起默认中转 endpoint，地址与模型在 .env 配）
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "deepseek/deepseek-v4.1-flash"
    llm_timeout_seconds: int = 60
    llm_max_retries: int = 2
    # 输出上限：推理模型会先消耗 reasoning token，设小会导致正文为空（v5 实测 1024/2048 均触顶）
    llm_max_tokens: int = 3072
    # F02 LLM 兜底（词典完全未命中 → 模型抽实体）：默认关闭，每问会多一次串行调用
    enable_llm_entity_fallback: bool = False
    llm_entity_timeout_seconds: int = 8
    # 备用生成模型
    fallback_llm_base_url: str = ""
    fallback_llm_api_key: str = ""
    fallback_llm_model: str = ""
    # 云端文本向量模型（F11 构建 / F04 向量检索；无密钥则自动降级关键词）
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-v4"
    embedding_dim: int = 1024
    # 百炼硬上限：单请求最多 10 条文本（配大不会生效，只会报错）
    embedding_batch_size: int = 10
    embedding_timeout_seconds: int = 60
    # 向量库（D2）：Chroma 持久化集合名
    chroma_collection: str = "chunks_v1"
    # 文本检索模式（T3）：keyword / vector / hybrid —— 部署级全局开关（D3 推荐形态）
    # 兜底值与 defaults 对齐 hybrid/rrf（2026-09-15 审核整改，消除静默回退）
    text_mode: str = "hybrid"
    # hybrid 融合策略与权重（§四.2）：weighted / rrf / fallback
    text_hybrid_strategy: str = "rrf"
    text_hybrid_keyword_weight: float = 0.5
    # 向量/hybrid 下"无共享词"拒答的分数阈值
    vector_refusal_min_score: float = 0.25
    # 演示 / 限流 / 缓存
    rate_limit_per_minute: int = 30
    cache_ttl_seconds: int = 3600
    history_max_turns: int = 4
    query_top_k_graph: int = 40
    query_top_k_text: int = 30
    # 送入 F06 的融合证据条数上限（18 = v4 口径；调小可压制推理长度/截断与首延迟）
    query_fusion_limit: int = 18

    @property
    def default_snapshot_name(self) -> str:
        """生成默认快照子目录名：YYYYMMDD_v1。"""
        import datetime

        return datetime.datetime.now().strftime(defaults.VERSION_DATE_FORMAT) + "_v1"


def get_settings() -> Settings:
    return Settings(
        data_dir=_path_env("RAG_DATA_DIR", defaults.DATA_DIR),
        raw_dir=_path_env("RAG_RAW_DIR", defaults.RAW_DIR),
        snapshot_dir=_path_env("RAG_SNAPSHOT_DIR", defaults.SNAPSHOT_DIR),
        index_dir=_path_env("RAG_INDEX_DIR", defaults.INDEX_DIR),
        cache_dir=_path_env("RAG_CACHE_DIR", defaults.CACHE_DIR),
        log_dir=_path_env("RAG_LOG_DIR", defaults.LOG_DIR),
        frontend_dist=_path_env("FRONTEND_DIST", defaults.FRONTEND_DIST),
        legacy_sqlite_path=_path_env("LEGACY_SQLITE_PATH", defaults.LEGACY_SQLITE_PATH),
        legacy_raw_texts=[
            Path(p.strip()).expanduser()
            for p in os.environ.get(
                "LEGACY_RAW_TEXTS",
                ";".join(str(p) for p in defaults.LEGACY_RAW_TEXTS),
            ).split(";")
            if p.strip()
        ],
        governance_enable_relation_extraction=_bool_env(
            "GOVERNANCE_ENABLE_RELATION_EXTRACTION",
            defaults.GOVERNANCE_ENABLE_RELATION_EXTRACTION,
        ),
        index_build_embeddings=_bool_env(
            "INDEX_BUILD_EMBEDDINGS", defaults.INDEX_BUILD_EMBEDDINGS
        ),
        chunk_max_chars=int(os.environ.get("CHUNK_MAX_CHARS", defaults.CHUNK_MAX_CHARS)),
        chunk_overlap_chars=int(
            os.environ.get("CHUNK_OVERLAP_CHARS", defaults.CHUNK_OVERLAP_CHARS)
        ),
        llm_base_url=os.environ.get("LLM_BASE_URL", defaults.LLM_BASE_URL),
        # 密钥别名链（按优先级）：LLM_API_KEY → DEEPSEEK_API_KEY
        #   → RAG-command（中转，项目期优先）→ RAG-deepseek-v4（官方，项目结束后启用）
        # 切回官方 = 从系统环境变量删掉 RAG-command，零代码切换
        llm_api_key=_first_env("LLM_API_KEY", "DEEPSEEK_API_KEY",
                               "RAG-command", "RAG-deepseek-v4",
                               default=defaults.LLM_API_KEY),
        llm_model=os.environ.get("LLM_MODEL", defaults.LLM_MODEL),
        llm_timeout_seconds=int(
            os.environ.get("LLM_TIMEOUT_SECONDS", defaults.LLM_TIMEOUT_SECONDS)
        ),
        llm_max_retries=int(
            os.environ.get("LLM_MAX_RETRIES", defaults.LLM_MAX_RETRIES)
        ),
        llm_max_tokens=int(
            os.environ.get("LLM_MAX_TOKENS", defaults.LLM_MAX_TOKENS)
        ),
        enable_llm_entity_fallback=_bool_env(
            "ENABLE_LLM_ENTITY_FALLBACK", defaults.ENABLE_LLM_ENTITY_FALLBACK
        ),
        llm_entity_timeout_seconds=int(os.environ.get(
            "LLM_ENTITY_TIMEOUT_SECONDS", defaults.LLM_ENTITY_TIMEOUT_SECONDS)),
        fallback_llm_base_url=os.environ.get(
            "FALLBACK_LLM_BASE_URL", defaults.FALLBACK_LLM_BASE_URL
        ),
        # 备用模型默认可用官方密钥（FALLBACK_LLM_BASE_URL 配好即生效，未配则不降级）
        fallback_llm_api_key=_first_env(
            "FALLBACK_LLM_API_KEY", "RAG-deepseek-v4",
            default=defaults.FALLBACK_LLM_API_KEY,
        ),
        fallback_llm_model=os.environ.get(
            "FALLBACK_LLM_MODEL", defaults.FALLBACK_LLM_MODEL
        ),
        embedding_base_url=os.environ.get("EMBEDDING_BASE_URL", defaults.EMBEDDING_BASE_URL),
        embedding_api_key=os.environ.get("EMBEDDING_API_KEY", defaults.EMBEDDING_API_KEY),
        embedding_model=os.environ.get("EMBEDDING_MODEL", defaults.EMBEDDING_MODEL),
        embedding_dim=int(os.environ.get("EMBEDDING_DIM", defaults.EMBEDDING_DIM) or 0),
        embedding_batch_size=max(1, min(
            MAX_EMBEDDING_BATCH,
            int(os.environ.get("EMBEDDING_BATCH_SIZE", defaults.EMBEDDING_BATCH_SIZE)),
        )),
        embedding_timeout_seconds=int(os.environ.get(
            "EMBEDDING_TIMEOUT_SECONDS", defaults.EMBEDDING_TIMEOUT_SECONDS)),
        chroma_collection=os.environ.get("CHROMA_COLLECTION", defaults.CHROMA_COLLECTION),
        text_mode=(os.environ.get("TEXT_MODE", defaults.TEXT_MODE) or "hybrid").strip().lower(),
        text_hybrid_strategy=(os.environ.get(
            "TEXT_HYBRID_STRATEGY", defaults.TEXT_HYBRID_STRATEGY) or "rrf").strip().lower(),
        text_hybrid_keyword_weight=float(os.environ.get(
            "TEXT_HYBRID_KEYWORD_WEIGHT", defaults.TEXT_HYBRID_KEYWORD_WEIGHT)),
        vector_refusal_min_score=float(os.environ.get(
            "VECTOR_REFUSAL_MIN_SCORE", defaults.VECTOR_REFUSAL_MIN_SCORE)),
        rate_limit_per_minute=int(
            os.environ.get("RATE_LIMIT_PER_MINUTE", defaults.RATE_LIMIT_PER_MINUTE)
        ),
        cache_ttl_seconds=int(
            os.environ.get("CACHE_TTL_SECONDS", defaults.CACHE_TTL_SECONDS)
        ),
        history_max_turns=int(
            os.environ.get("HISTORY_MAX_TURNS", defaults.HISTORY_MAX_TURNS)
        ),
        query_top_k_graph=int(
            os.environ.get("QUERY_TOP_K_GRAPH", defaults.QUERY_TOP_K_GRAPH)
        ),
        query_top_k_text=int(
            os.environ.get("QUERY_TOP_K_TEXT", defaults.QUERY_TOP_K_TEXT)
        ),
        query_fusion_limit=max(4, int(
            os.environ.get("QUERY_FUSION_LIMIT", defaults.QUERY_FUSION_LIMIT)
        )),
    )
