"""对外文本的脱敏。

## 为什么单独成一个模块

RAG 里有两条链路的异常文案会走到用户面前：SSE 的 error 帧（`server/sse.py`）与
HTTP 响应体（`server/api.py`）。原实现把脱敏函数写在 `api.py` 里，于是 `sse.py`
用不上它——同一类泄露一半被堵、一半漏着，只有"工具放在两边都能拿到的地方"才不会再犯。

## 脱敏做什么、不做什么

只处理**服务器侧的绝对路径**（含 RAG 根目录），把它们换成占位符：

    /srv/rag/data/snapshot/x.json  →  <RAG_ROOT>/data/snapshot/x.json

**不做**别的判断：异常的类型名、内部端点、库名仍会保留。这是有意的下限——
完全不给原因会让"模型没起"这类用户可自行处理的故障变得无从判断（与旧后端
`common_utils.brief_error` 同一取舍）。真正的原文（含堆栈）始终只进日志。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# RAG 根目录：`lib/` 的上一级。用于把仓库内的绝对路径收成 <RAG_ROOT>。
_RAG_ROOT = str(Path(__file__).resolve().parents[1])

# 其它绝对路径（Unix 与 Windows）：整段替换成 <path>。
# 只匹配"看起来像路径"的连续片段，避免把普通句子切碎。
_ABS_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/]|/)[\w.\-/\\]{3,}")


def public_text(value: Any) -> Any:
    """把对外响应文本里的服务器绝对路径收敛为占位符；空值原样返回。"""
    if value is None:
        return value
    text = str(value)
    if not text:
        return text
    if _RAG_ROOT:
        text = text.replace(_RAG_ROOT, "<RAG_ROOT>")
    return _ABS_PATH_RE.sub("<path>", text)
