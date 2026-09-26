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
from typing import Optional

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
    # 已把部分正文推给调用方之后才失败：调用方**不得**再叠加重试/降级文本
    # （透明重试会把两次尝试的正文拼在一起，用户看到重复答案）。
    partial: bool = False


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

    async def aclose(self) -> None:
        """关闭底层 HTTP 客户端（lifespan 关闭时调用，避免连接池悬挂）。"""
        for client in (self._primary, self._fallback):
            if client is None:
                continue
            try:
                await client.close()
            except Exception:  # noqa: BLE001
                pass
        self._primary = None
        self._fallback = None

    # ---- 流式 ----
    async def stream_chat(self, messages: list[dict],
                          on_delta, on_thinking=None,
                          max_tokens: Optional[int] = None,
                          stats_out: Optional[dict] = None) -> LLMResponse:
        """流式调用主模型，失败自动重试→降级备用。

        on_delta(delta_text) 收到正文增量；on_thinking(text) 收到推理增量（可为 None）。
        推理增量的字段名两端不同（中转 reasoning / 官方 reasoning_content），见 _reasoning_of。

        重试边界：**只要已经向调用方推过正文增量，就不再重试、
        不再切换备用模型**——已发送的文本无法撤回，第二次尝试只会把两段答案拼在一起。
        此时以 partial=True 返回，由上层标记为"回答可能不完整"。

        stats_out：可选的可变 dict，回填本次调用的 model/usage/truncated 等，
        供并发场景取本次调用的准确统计（实例属性 last_usage 在多请求下会互相覆盖）。
        """
        emitted = {"chars": 0}

        def _counting_on_delta(text: str) -> None:
            emitted["chars"] += len(text or "")
            if on_delta:
                on_delta(text)

        if not self.available and not self._fallback:
            return LLMResponse(error="llm_unavailable")
        # 主模型未配置但备用已配置：直接走备用，不为 None 主模型空转重试
        if self._primary is None:
            collected: list[str] = []
            try:
                resp = await self._stream_once(
                    self._fallback, self.settings.fallback_llm_model,
                    messages, _counting_on_delta, degraded=True,
                    on_thinking=on_thinking, max_tokens=max_tokens,
                    collected=collected,
                )
            except Exception as e:  # noqa: BLE001
                resp = LLMResponse(error=f"llm_error: {e}", text="".join(collected),
                                   model_used=self.settings.fallback_llm_model,
                                   partial=emitted["chars"] > 0)
            self._fill_stats(stats_out, resp)
            return resp
        last_err = None
        for attempt in range(max(1, self.settings.llm_max_retries + 1)):
            collected = []
            try:
                resp = await self._stream_once(
                    self._primary, self.settings.llm_model,
                    messages, _counting_on_delta, degraded=False,
                    on_thinking=on_thinking, max_tokens=max_tokens,
                    collected=collected,
                )
                self._fill_stats(stats_out, resp)
                return resp
            except Exception as e:  # noqa: BLE001
                last_err = e
                if emitted["chars"] > 0:
                    # 正文已流出：重试会拼接重复答案，立即止损
                    logging.getLogger("rag.llm").warning(
                        "流式生成在已输出 %s 字符后失败，放弃重试/降级：%s",
                        emitted["chars"], str(e)[:200],
                    )
                    resp = LLMResponse(error=f"llm_error: {e}",
                                       text="".join(collected),
                                       model_used=self.settings.llm_model,
                                       partial=True)
                    self._fill_stats(stats_out, resp)
                    return resp
                if self._fallback and attempt == self.settings.llm_max_retries:
                    # 主模型耗尽 → 备用（仅当尚未输出任何正文）
                    try:
                        resp = await self._stream_once(
                            self._fallback, self.settings.fallback_llm_model,
                            messages, _counting_on_delta, degraded=True,
                            on_thinking=on_thinking, max_tokens=max_tokens,
                            collected=collected,
                        )
                        self._fill_stats(stats_out, resp)
                        return resp
                    except Exception as e2:  # noqa: BLE001
                        last_err = e2
                        if emitted["chars"] > 0:
                            break
                # 继续重试主模型
                await asyncio.sleep(0.5 * (attempt + 1))
        resp = LLMResponse(error=f"llm_error: {last_err}",
                           text="".join(collected) if emitted["chars"] else "",
                           partial=emitted["chars"] > 0)
        self._fill_stats(stats_out, resp)
        return resp

    @staticmethod
    def _fill_stats(stats_out: Optional[dict], resp: LLMResponse) -> None:
        if stats_out is None:
            return
        stats_out.update({
            "model_used": resp.model_used,
            "usage": resp.usage,
            "truncated": resp.api_finish_reason == "length",
            "api_finish_reason": resp.api_finish_reason,
            "degraded": resp.degraded,
            "error": resp.error,
            "partial": resp.partial,
        })

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
                           max_tokens: Optional[int] = None,
                           collected: Optional[list] = None) -> LLMResponse:
        # collected 由调用方传入：流中途抛错时上层还能拿到"已经流出去的那部分正文"
        if collected is None:
            collected = []
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
