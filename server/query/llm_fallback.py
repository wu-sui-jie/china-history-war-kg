"""F02 LLM 兜底的同步调用封装（server/query/llm_fallback.py，RAGv5 §4.5）。

为什么单独一个同步客户端：F02 的 `understand()` 在同步链路里被调用（`server/sse.py` 的
异步生成器里直接同步调用），而 F06 的 `LLMClient` 是异步的。在这里用同步 OpenAI 客户端
可以避免把 F02 改成 async 而牵动整条链（评测、测试、前端事件的顺序都依赖现状）。

口径（与开发说明 §4.5 一致）：
- 独立超时 `LLM_ENTITY_TIMEOUT_SECONDS`（默认 8 s），不吃 F06 的首 Token 预算；
- 单次尝试 + 短超时；异常/超时/解析失败一律返回空列表（调用方按"未命中"降级）；
- 只做"抽取"，不做推理（提示词见 server/query/prompts.py）。
"""

from __future__ import annotations

from typing import Optional

from config.settings import Settings
from server.query import prompts as q_prompts


class EntityFallbackClient:
    """词典未命中时的实体抽取客户端（同步）。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.model = settings.llm_model
        self._client = None
        key = settings.llm_api_key
        if settings.llm_base_url and key:
            try:
                from openai import OpenAI

                self._client = OpenAI(
                    base_url=settings.llm_base_url, api_key=key,
                    timeout=settings.llm_entity_timeout_seconds,
                )
            except Exception:  # noqa: BLE001
                self._client = None
        self.available = self._client is not None

    def close(self) -> None:
        """关闭同步 HTTP 客户端（lifespan 关闭时调用）。"""
        client, self._client = self._client, None
        self.available = False
        if client is None:
            return
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass

    def extract(self, question: str) -> list[dict]:
        """返回 [{"name","type"}]；任何失败都返回空列表（不抛错）。

        注意 max_tokens：deepseek 系列是推理模型，实测 256 会被 reasoning 吃满
        （finish_reason=length、正文为空 → 解析出空列表），故给到 1024；
        超时仍按 `LLM_ENTITY_TIMEOUT_SECONDS`（实测抽取通常 3–4 s，超时即降级）。
        """
        if not self.available or not question.strip():
            return []
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=q_prompts.build_entity_messages(question),
                max_tokens=1024,
                stream=False,
            )
            text = ""
            if resp.choices:
                text = resp.choices[0].message.content or ""
            return q_prompts.parse_entities(text)
        except Exception:  # noqa: BLE001
            return []
