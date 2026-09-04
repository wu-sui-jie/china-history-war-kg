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


def _path_env(key: str, default: Path) -> Path:
    v = os.environ.get(key)
    return Path(v).expanduser() if v else default


def _bool_env(key: str, default: bool) -> bool:
    v = os.environ.get(key)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    data_dir: Path
    raw_dir: Path
    snapshot_dir: Path
    index_dir: Path
    cache_dir: Path
    log_dir: Path

    legacy_sqlite_path: Path
    legacy_raw_texts: List[Path]

    governance_enable_relation_extraction: bool
    index_build_embeddings: bool

    chunk_max_chars: int
    chunk_overlap_chars: int

    # ---- 在线链路（RAGv2，F02–F06）----
    # 大模型（deepseek-v4-flash，OpenAI 兼容）
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "deepseek-v4-flash"
    llm_timeout_seconds: int = 60
    llm_max_retries: int = 2
    # 备用生成模型
    fallback_llm_base_url: str = ""
    fallback_llm_api_key: str = ""
    fallback_llm_model: str = ""
    # 云端文本向量模型（F11 构建 / F04 向量检索；无密钥则自动降级关键词）
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = ""
    embedding_dim: int = 0
    # 演示 / 限流 / 缓存
    demo_mode: bool = False
    rate_limit_per_minute: int = 30
    cache_ttl_seconds: int = 3600
    history_max_turns: int = 4
    query_top_k_graph: int = 40
    query_top_k_text: int = 30

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
        llm_api_key=os.environ.get("LLM_API_KEY", defaults.LLM_API_KEY),
        llm_model=os.environ.get("LLM_MODEL", defaults.LLM_MODEL),
        llm_timeout_seconds=int(
            os.environ.get("LLM_TIMEOUT_SECONDS", defaults.LLM_TIMEOUT_SECONDS)
        ),
        llm_max_retries=int(
            os.environ.get("LLM_MAX_RETRIES", defaults.LLM_MAX_RETRIES)
        ),
        fallback_llm_base_url=os.environ.get(
            "FALLBACK_LLM_BASE_URL", defaults.FALLBACK_LLM_BASE_URL
        ),
        fallback_llm_api_key=os.environ.get(
            "FALLBACK_LLM_API_KEY", defaults.FALLBACK_LLM_API_KEY
        ),
        fallback_llm_model=os.environ.get(
            "FALLBACK_LLM_MODEL", defaults.FALLBACK_LLM_MODEL
        ),
        embedding_base_url=os.environ.get("EMBEDDING_BASE_URL", ""),
        embedding_api_key=os.environ.get("EMBEDDING_API_KEY", ""),
        embedding_model=os.environ.get("EMBEDDING_MODEL", ""),
        embedding_dim=int(os.environ.get("EMBEDDING_DIM", "0") or 0),
        demo_mode=_bool_env("DEMO_MODE", defaults.DEMO_MODE),
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
    )
