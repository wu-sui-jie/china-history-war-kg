"""LLM 客户端（server/generate/llm_client.py）。

OpenAI 兼容接口（deepseek-v4-flash），支持：
- 主模型（llm_base_url/api_key/model）
- 备用模型降级（fallback_*）
- 超时 / 重试
无密钥时 available=False，调用方走拒答/无模型路径。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import AsyncIterator, Optional

from config.settings import Settings


@dataclass
class LLMResponse:
    text: str = ""
    model_used: str = ""
    degraded: bool = False
    error: Optional[str] = None
    usage: Optional[dict] = None
    # 服务端返回的 finish_reason（"length" = 触及 max_tokens 被截断，需告警）
    api_finish_reason: Optional[str] = None


class LLMClient:
    """同步封装 AsyncOpenAI 流式补全（供 F06 generate 使用）。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.available = bool(settings.llm_base_url and settings.llm_api_key)
        self._primary = None
        self._fallback = None
        if self.available:
            from openai import AsyncOpenAI
            self._primary = AsyncOpenAI(
                base_url=settings.llm_base_url, api_key=settings.llm_api_key, timeout=settings.llm_timeout_seconds,
            )
        if settings.fallback_llm_base_url and settings.fallback_llm_api_key:
            from openai import AsyncOpenAI
            self._fallback = AsyncOpenAI(
                base_url=settings.fallback_llm_base_url,
                api_key=settings.fallback_llm_api_key,
                timeout=settings.llm_timeout_seconds,
            )

    def model_name(self) -> str:
        return self.settings.llm_model or ""

    # ---- 流式 ----
    async def stream_chat(self, messages: list[dict],
                          on_delta, on_thinking=None,
                          max_tokens: Optional[int] = None) -> LLMResponse:
        """流式调用主模型，失败自动重试→降级备用。

        on_delta(delta_text) 收到正文增量；on_thinking(text) 收到推理增量（可为 None）。
        推理增量的字段名两端不同（中转 reasoning / 官方 reasoning_content），见 _reasoning_of。
        """
        if not self.available and not self._fallback:
            return LLMResponse(error="llm_unavailable")
        # 主模型未配置但备用已配置：直接走备用，不为 None 主模型空转重试
        if self._primary is None:
            try:
                return await self._stream_once(
                    self._fallback, self.settings.fallback_llm_model,
                    messages, on_delta, degraded=True,
                    on_thinking=on_thinking, max_tokens=max_tokens,
                )
            except Exception as e:  # noqa: BLE001
                return LLMResponse(error=f"llm_error: {e}")
        last_err = None
        for attempt in range(max(1, self.settings.llm_max_retries + 1)):
            try:
                return await self._stream_once(self._primary, self.settings.llm_model,
                                               messages, on_delta, degraded=False,
                                               on_thinking=on_thinking,
                                               max_tokens=max_tokens)
            except Exception as e:
                last_err = e
                if self._fallback and attempt == self.settings.llm_max_retries:
                    # 主模型耗尽 → 备用
                    try:
                        return await self._stream_once(
                            self._fallback, self.settings.fallback_llm_model,
                            messages, on_delta, degraded=True,
                            on_thinking=on_thinking, max_tokens=max_tokens,
                        )
                    except Exception as e2:
                        last_err = e2
                # 继续重试主模型
                await asyncio.sleep(0.5 * (attempt + 1))
        return LLMResponse(error=f"llm_error: {last_err}")

    @staticmethod
    def _reasoning_of(delta) -> str:
        """取推理增量文本。

        RAGv5 实测：中转 endpoint（commandcode）用 `reasoning`（另有 reasoning_details），
        官方 DeepSeek 用 `reasoning_content`；两端都要兼容，否则 thinking 事件形同虚设。
        """
        for attr in ("reasoning_content", "reasoning"):
            val = getattr(delta, attr, None)
            if not val:
                continue
            if isinstance(val, str):
                return val
            if isinstance(val, dict):
                return str(val.get("text") or val.get("content") or "")
            return str(val)
        return ""

    async def _stream_once(self, client, model: str, messages: list[dict],
                           on_delta, degraded: bool,
                           on_thinking=None,
                           max_tokens: Optional[int] = None) -> LLMResponse:
        collected: list[str] = []
        usage: Optional[dict] = None
        api_finish_reason: Optional[str] = None
        kwargs: dict = {"model": model, "messages": messages, "stream": True}
        if max_tokens:
            # 推理模型必须给足输出预算，否则 token 全被 reasoning 吃掉、正文为空（v5 实测）
            kwargs["max_tokens"] = max_tokens
        try:
            # 流式也带回 usage（token 与 reasoning 占比可用于耗时/成本归因）
            stream = await client.chat.completions.create(
                **kwargs, stream_options={"include_usage": True})
        except Exception as e:  # noqa: BLE001
            msg = str(e).lower()
            if not any(k in msg for k in ("stream_options", "include_usage", "unknown")):
                raise
            # 端点不支持 stream_options → 去掉该参数重试一次
            stream = await client.chat.completions.create(**kwargs)
        async for chunk in stream:
            if getattr(chunk, "usage", None):
                try:
                    usage = chunk.usage.model_dump()
                except Exception:  # noqa: BLE001
                    usage = None
            if not chunk.choices:
                continue
            if chunk.choices[0].finish_reason:
                api_finish_reason = chunk.choices[0].finish_reason
            delta = chunk.choices[0].delta
            if not delta:
                continue
            reasoning = self._reasoning_of(delta)
            if reasoning and on_thinking:
                on_thinking(reasoning)
            if delta.content:
                collected.append(delta.content)
                on_delta(delta.content)
        if api_finish_reason == "length":
            # 推理模型把预算吃在 reasoning 上时正文会被截断：必须可见，否则表现为"答案戛然而止"
            logging.getLogger("rag.llm").warning(
                "LLM 输出触及 max_tokens=%s 被截断（model=%s）；考虑调大 LLM_MAX_TOKENS",
                max_tokens, model,
            )
        return LLMResponse(text="".join(collected), model_used=model,
                           degraded=degraded, usage=usage,
                           api_finish_reason=api_finish_reason)
