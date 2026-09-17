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


class EmbeddingClient:
    """带生命周期的向量模型客户端（2026-09-16 工作单 P0-3）。

    为什么不再返回裸闭包：`OpenAI(...)` 内部持有 HTTP 连接池，闭包形式让 Runtime
    拿不到任何句柄，服务退出/热重载/测试反复启动时连接池无处释放。
    本类把 embedding 调用与资源释放放在一起：
    - `__call__(texts)`：与旧的 `embed_fn(texts)` 行为完全一致（可直接替换）；
    - `close()`：关闭底层客户端，幂等（重复调用安全）；
    - `available`：供调用方判断是否真的可用。
    """

    def __init__(self, settings: Settings, logger=None):
        self.settings = settings
        self.model = settings.embedding_model
        self.dim = settings.embedding_dim or 0
        self._client = None
        self._closed = False
        self.available = False
        key = resolve_embedding_key(settings)
        if settings.embedding_base_url and self.model and key:
            from openai import OpenAI

            self._client = OpenAI(
                base_url=settings.embedding_base_url, api_key=key,
                timeout=settings.embedding_timeout_seconds,
            )
            self.available = True
        if logger:
            if self.available:
                logger.info(f"向量模型就绪: {self.model} @ {settings.embedding_base_url}"
                            f"（dim={self.dim or '模型默认'}）")
            else:
                logger.info("未配置云端向量模型（EMBEDDING_BASE_URL/MODEL/密钥）→ 跳过向量构建")

    def __call__(self, texts: List[str]) -> list:
        return self.embed(texts)

    def embed(self, texts: List[str]) -> list:
        if self._client is None:
            raise RuntimeError("向量客户端不可用（未配置 base_url/模型/密钥）")
        batch = list(texts)[:MAX_BATCH_PER_REQUEST]
        kwargs = {"model": self.model, "input": batch, "encoding_format": "float"}
        if self.dim:
            kwargs["dimensions"] = self.dim
        last_err: Optional[Exception] = None
        for attempt in range(3):
            try:
                resp = self._client.embeddings.create(**kwargs)
                vecs = [d.embedding for d in resp.data]
                if self.dim and vecs and len(vecs[0]) != self.dim:
                    # 静默错位是灾难性的（配 1024 实际存 2048），必须直接失败
                    raise ValueError(
                        f"向量维度与配置不一致：接口返回 {len(vecs[0])}，"
                        f"EMBEDDING_DIM={self.dim}"
                    )
                return vecs
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(0.8 * (attempt + 1))
        raise RuntimeError(f"向量化调用失败（重试 3 次）: {last_err}")

    def close(self) -> None:
        """关闭底层 HTTP 客户端；幂等，重复调用不做任何事。"""
        if self._closed:
            return
        self._closed = True
        client, self._client = self._client, None
        self.available = False
        if client is None:
            return
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


def build_embed_fn(settings: Settings, logger=None) -> Optional[Callable[[List[str]], list]]:
    """构造 embed_fn(texts) -> List[List[float]]（兼容旧调用方）。

    密钥/地址/模型任一缺失时返回 None（调用方走占位或跳过，不静默产假向量）。

    注意：返回的闭包**不可关闭**，服务端请改用 `build_embedding_client()`，
    这样 Runtime 才能枚举并在退出时释放连接池（工作单 P0-3）。
    """
    client = build_embedding_client(settings, logger=logger)
    if client is None:
        return None
    return client


def build_embedding_client(settings: Settings, logger=None) -> Optional[EmbeddingClient]:
    """构造可关闭的向量客户端；未配置时返回 None（服务端走关键词降级）。"""
    client = EmbeddingClient(settings, logger=logger)
    return client if client.available else None
