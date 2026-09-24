"""非流式问答结果契约（POST /api/query/json，飞书机器人通道）。

与流式通道（contracts/sse.py）的关系：本结构**不是新的编排产物**，
而是 `server/sse.py::run_query` 那串 SSE 帧按事件类型聚合的结果
（聚合规则见 feishu-bot/docs/开发文档.md 6.2）。字段与 SSE 事件 payload
一一对应，因此两通道天然一致，不存在各自实现的第二条链路。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from contracts.base import BaseModel


@dataclass
class QueryJsonResult(BaseModel):
    """一次问答的完整结果。

    - `answer_md`：`answer` 事件增量拼接结果（与 SSE 流逐帧拼回严格相等）；
    - `citations` / `conflicts` / `panel`：citations 与 panel 事件的 payload 原样；
      `panel` 无面板数据时为 `{}`（与 SSE 不发 panel 事件对应）；
    - `finish_reason`：done 事件取值，见 `contracts/sse.py::FinishReason`；
    - `truncated`：模型打满 `LLM_MAX_TOKENS` 时为真（机器人据此提示回答可能不完整）；
    - `error`：聚合过程中出现过 error 事件（含超时）时非空，
      由 API 层映射成 HTTP 非 200 —— 见 `server/api.py::query_json`。
    """

    answer_md: str = ""
    citations: List[Dict[str, Any]] = field(default_factory=list)
    conflicts: List[Dict[str, Any]] = field(default_factory=list)
    panel: Dict[str, Any] = field(default_factory=dict)
    finish_reason: str = ""
    model_used: str = ""
    cache_hit: bool = False
    truncated: bool = False
    error: Optional[Dict[str, str]] = None
