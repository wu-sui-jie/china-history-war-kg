"""LLM 客户端（server/generate/llm_client.py）。

OpenAI 兼容接口（deepseek-v4-flash），支持：
- 主模型（llm_base_url/api_key/model）
- 备用模型降级（fallback_*）
- 超时 / 重试
无密钥时 available=False，调用方走拒答/无模型路径。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import AsyncIterator, Optional

from config.settings import Settings


@dataclass
class LLMResponse:
    text: str = ""
    model_used: str = ""
    degraded: bool = False
    error: Optional[str] = None


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
                          on_delta) -> LLMResponse:
        """流式调用主模型，失败自动重试→降级备用。

        on_delta(delta_text) 收到每个增量。返回最终 LLMResponse。
        """
        if not self.available and not self._fallback:
            return LLMResponse(error="llm_unavailable")
        last_err = None
        for attempt in range(max(1, self.settings.llm_max_retries + 1)):
            try:
                client = self._primary
                model = self.settings.llm_model
                if client is None:
                    raise RuntimeError("primary not configured")
                return await self._stream_once(client, model, messages, on_delta,
                                               degraded=False)
            except Exception as e:
                last_err = e
                if self._fallback and attempt == self.settings.llm_max_retries:
                    # 主模型耗尽 → 备用
                    try:
                        return await self._stream_once(
                            self._fallback, self.settings.fallback_llm_model,
                            messages, on_delta, degraded=True,
                        )
                    except Exception as e2:
                        last_err = e2
                # 继续重试主模型
                await asyncio.sleep(0.5 * (attempt + 1))
        return LLMResponse(error=f"llm_error: {last_err}")

    async def _stream_once(self, client, model: str, messages: list[dict],
                           on_delta, degraded: bool) -> LLMResponse:
        collected = []
        stream = await client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                collected.append(delta.content)
                on_delta(delta.content)
        return LLMResponse(text="".join(collected), model_used=model,
                           degraded=degraded)
