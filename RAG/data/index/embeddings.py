"""云端文本向量模型客户端（阿里云百炼 text-embedding-v4，OpenAI 兼容）。

RAGv5 已确认的模型口径（见 docs/RAG_v1/RAGv5-开发说明.md §四.3）：
- base_url  https://dashscope.aliyuncs.com/compatible-mode/v1
- model     text-embedding-v4，默认 1024 维（可用 dimensions 指定）
- 密钥      EMBEDDING_API_KEY 留空时读系统环境变量 DASHSCOPE_API_KEY
- 硬约束    单请求 input 最多 10 条文本；单条 ≤ 8192 token

本模块只负责"文本 → 向量"的调用与重试；分批落盘、断点续跑、写库在 vector_pipeline。
"""

from __future__ import annotations

import time
from typing import Callable, List, Optional

from config.settings import Settings

# 百炼硬上限：单请求最多 10 条文本
MAX_BATCH_PER_REQUEST = 10


def resolve_embedding_key(settings: Settings) -> str:
    """向量模型密钥：EMBEDDING_API_KEY 优先，其次系统环境变量 DASHSCOPE_API_KEY。"""
    import os

    return (settings.embedding_api_key
            or os.environ.get("DASHSCOPE_API_KEY", "")).strip()


def build_embed_fn(settings: Settings, logger=None) -> Optional[Callable[[List[str]], list]]:
    """构造 embed_fn(texts) -> List[List[float]]。

    密钥/地址/模型任一缺失时返回 None（调用方走占位或跳过，不静默产假向量）。
    """
    key = resolve_embedding_key(settings)
    if not (settings.embedding_base_url and settings.embedding_model and key):
        if logger:
            logger.info("未配置云端向量模型（EMBEDDING_BASE_URL/MODEL/密钥）→ 跳过向量构建")
        return None

    from openai import OpenAI

    client = OpenAI(base_url=settings.embedding_base_url, api_key=key,
                    timeout=settings.embedding_timeout_seconds)
    model = settings.embedding_model
    dim = settings.embedding_dim or 0

    def embed_fn(texts: List[str]) -> list:
        batch = list(texts)[:MAX_BATCH_PER_REQUEST]
        kwargs = {"model": model, "input": batch, "encoding_format": "float"}
        if dim:
            kwargs["dimensions"] = dim
        last_err: Optional[Exception] = None
        for attempt in range(3):
            try:
                resp = client.embeddings.create(**kwargs)
                vecs = [d.embedding for d in resp.data]
                if dim and vecs and len(vecs[0]) != dim:
                    # 静默错位是灾难性的（配 1024 实际存 2048），必须直接失败
                    raise ValueError(
                        f"向量维度与配置不一致：接口返回 {len(vecs[0])}，EMBEDDING_DIM={dim}"
                    )
                return vecs
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(0.8 * (attempt + 1))
        raise RuntimeError(f"向量化调用失败（重试 3 次）: {last_err}")

    if logger:
        logger.info(f"向量模型就绪: {model} @ {settings.embedding_base_url}（dim={dim or '模型默认'}）")
    return embed_fn
