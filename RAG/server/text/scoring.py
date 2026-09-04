"""F04 文本检索：模式选择 / 分数归一化 / 可用性探测。

三种模式：keyword（FTS5）、vector（云端向量，需 F11 已构建且校验通过）、hybrid。
当前基线：vector_available=False 时无论请求何种模式都回落 keyword。
"""

from __future__ import annotations

from enum import Enum


class TextMode(str, Enum):
    KEYWORD = "keyword"
    VECTOR = "vector"
    HYBRID = "hybrid"
    NONE = "none"


def resolve_mode(requested: str | None, vector_available: bool) -> str:
    """返回实际执行模式；请求 vector/hybrid 但向量不可用 → 自动降级 keyword。"""
    req = (requested or "keyword").lower()
    if req in ("vector", "hybrid") and not vector_available:
        return "keyword"
    if req not in ("keyword", "vector", "hybrid"):
        return "keyword"
    return req
