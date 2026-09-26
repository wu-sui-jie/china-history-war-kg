"""
DeepSeek API 客户端封装模块
"""

import os
import re
import json
import time
from pathlib import Path

from openai import OpenAI, AuthenticationError, APIConnectionError, RateLimitError
from dotenv import load_dotenv

from war_extraction.utils.json_payload import extract_largest_json_text

# 以本文件位置锚定项目根目录，避免在任意工作目录下运行时找不到 config/.env
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(dotenv_path=_PROJECT_ROOT / "config" / ".env")


class LLMAuthError(Exception):
    """DeepSeek API 密钥无效或无权限（HTTP 401）。"""


class LLMAPIError(Exception):
    """API 调用在网络/限流/服务端等错误重试后仍失败。"""


#: **默认进入思考模式**的服务端模型名。命中这些名字时必须显式关掉思考，否则：
#: 提示词被算进 reasoning、可见内容为空——抽取链会拿到空字符串，报出来的却是
#: "JSON 解析失败"，排查起来完全指不到原因。实测（2026-09-26，项目自己的密钥）：
#:
#: | 请求 | served | prompt_tokens | 可见内容 |
#: | --- | --- | ---: | --- |
#: | `deepseek-chat` | `deepseek-flash` | 7 | 正常 |
#: | `deepseek-flash`（默认） | `deepseek-flash` | 33 | **空** |
#: | `deepseek-flash` + `thinking={"type":"disabled"}` | `deepseek-flash` | 7 | 正常 |
THINKING_DEFAULT_ON_MODELS = frozenset({"deepseek-flash", "deepseek-v4-pro"})


class DeepSeekClient:
    """封装 DeepSeek API 调用的客户端类"""

    def __init__(self):
        # 兼容两种密钥变量名：DEEPSEEK_API_KEY 为主，Chinese_txt 为历史遗留
        api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("Chinese_txt")
        base_url = os.getenv("API_BASE_URL", "https://api.deepseek.com/v1")
        # 默认 `deepseek-flash`：`GET /models` 里只有它和 `deepseek-v4-pro`，
        # `deepseek-chat` 只是被路由到 flash 的别名（实测），写成显式名字更不容易误导；
        # 命中"默认进思考模式"的名字时 `_thinking_kwargs` 会自动关思考。
        model = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")

        if not api_key:
            raise ValueError(
                "未找到 DeepSeek API 密钥：请在 config/.env 中设置 "
                "DEEPSEEK_API_KEY=sk-xxx（兼容旧变量名 Chinese_txt）"
            )

        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=60.0, max_retries=2)
        self.model = model
        self.base_url = base_url  # 随产物 metadata 记录，换端点重跑后产物可自证来源
        #: **实际服务的模型名**（取自响应的 `model` 字段），不是请求名。
        #:
        #: 为什么要单独记：服务端把 `deepseek-chat` 这个别名路由到 `deepseek-flash`，
        #: 而客户端原先只存 env 里的请求名——于是产物 metadata 里的 `model` 是**错的**
        #: （写着 deepseek-chat，实际跑的是 flash）。"产物自证"自证的必须是实际服务方。
        #: 最后一次调用的值；没调过是 None（写进 metadata 时就是"未知"，
        #: 而不是拿请求名冒充）。
        self.model_served = None
        #: 是否已确认"关思考"参数被服务端接受（用于出错时定位：见 call 里的空内容守卫）
        self.thinking_disabled = False
        #: `DEEPSEEK_THINKING=auto` 可关掉自动关思考（默认 `disabled`：命中
        #: `THINKING_DEFAULT_ON_MODELS` 时显式关闭）
        self.thinking_mode = os.getenv("DEEPSEEK_THINKING", "disabled").strip().lower()

    def _thinking_kwargs(self) -> dict:
        """
        需要在请求里显式关思考时，返回要并入请求的额外字段；否则返回 `{}`。

        只在**已知默认进入思考模式**的模型上关（`THINKING_DEFAULT_ON_MODELS`），
        并且在**每次尝试**时重新计算：服务端拒绝该参数时会去掉它再试一次，
        去掉后不能再假装还关着（`thinking_disabled` 会跟着变），否则下面那道
        "空内容守卫"就失去了判据。
        """
        if self.thinking_mode == "auto":
            return {}
        if self.model in THINKING_DEFAULT_ON_MODELS:
            return {"thinking": {"type": "disabled"}}
        return {}

    def call(self, prompt: str, temperature: float = 0.1, max_retries: int = 3, json_mode: bool = False) -> str:
        """
        调用 DeepSeek API，带重试和响应清理

        Args:
            prompt: 提示词
            temperature: 温度参数
            max_retries: 最大重试次数

        Returns:
            清理后的响应字符串（可能是JSON或纯文本，由调用方处理）
        """
        last_error = None
        for attempt in range(max_retries):
            thinking = self._thinking_kwargs()
            try:
                kwargs = {
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": temperature,
                }
                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                if thinking:
                    kwargs["extra_body"] = thinking
                try:
                    response = self.client.chat.completions.create(**kwargs)
                except AuthenticationError as e:
                    # 密钥无效/过期时重试无意义，直接给出明确错误
                    raise LLMAuthError(
                        "DeepSeek API 密钥无效（HTTP 401）：请检查 config/.env 或环境变量中的 DEEPSEEK_API_KEY 是否有效"
                    ) from e
                except Exception as e:
                    # 部分服务不支持 json_mode 时降级重试一次
                    if json_mode and "response_format" in kwargs:
                        kwargs.pop("response_format", None)
                        try:
                            response = self.client.chat.completions.create(**kwargs)
                        except AuthenticationError as ae:
                            raise LLMAuthError(
                                "DeepSeek API 密钥无效（HTTP 401）：请检查 config/.env 或环境变量中的 DEEPSEEK_API_KEY 是否有效"
                            ) from ae
                        except Exception:
                            raise e
                    elif thinking:
                        # 服务端不认 `thinking` 参数：去掉它再试——
                        # 但要记住"没关成"，这样一旦返回空内容会给出明确报错而不是静默空串。
                        kwargs.pop("extra_body", None)
                        response = self.client.chat.completions.create(**kwargs)
                        thinking = {}
                    else:
                        raise

                self.model_served = getattr(response, "model", None) or self.model_served
                self.thinking_disabled = bool(thinking)

                content = response.choices[0].message.content or ""

                # 空内容守卫：**不能**让它静默流下去。默认进思考模式的模型在没关思考时
                # 会把可见内容留在 reasoning 里，返回的就是空串；此时下游只会报"JSON 解析失败"，
                # 排查方向完全错。这里直接给出可行动的报错。
                if not content.strip():
                    raise LLMAPIError(
                        f"模型 {self.model_served or self.model} 返回了空内容。"
                        "若用的是 deepseek-flash / deepseek-v4-pro，通常是**思考模式**吃掉了可见内容"
                        "（实测不关思考时 prompt_tokens 33、可见内容为空）；"
                        "请确认请求里带上了 thinking={'type':'disabled'}（本客户端默认会带，"
                        "但设了 DEEPSEEK_THINKING=auto 或服务端拒绝了该参数时不会带）"
                    )

                # 清理Markdown代码块
                content = self._clean_response(content)

                # 简单检查：如果看起来是JSON，尝试验证
                if content.strip().startswith('{') or content.strip().startswith('['):
                    try:
                        json.loads(content)  # 验证JSON有效性
                        return content  # JSON有效，直接返回
                    except json.JSONDecodeError:
                        # JSON格式有问题，尝试修复
                        fixed = self._extract_json(content)
                        if fixed:
                            return fixed

                # 如果不是JSON格式（如Q1只需要返回类型名称），直接返回
                return content

            except LLMAuthError:
                raise
            except LLMAPIError:
                raise
            except Exception as e:
                print(f"API调用失败，尝试重试 {attempt + 1}/{max_retries}: {e}")
                last_error = e
                if attempt < max_retries - 1:
                    time.sleep(min(2 ** attempt, 8))

        if isinstance(last_error, (APIConnectionError, RateLimitError)):
            error_type = "连接失败或限流"
        else:
            error_type = "API错误"
        raise LLMAPIError(f"DeepSeek API 调用{error_type}，重试 {max_retries} 次后仍失败: {last_error}")

    def _clean_response(self, content: str) -> str:
        """清理响应中的Markdown标记"""
        content = content.strip()

        # 移除```json标记
        if content.startswith("```json"):
            content = content[7:].strip()
        elif content.startswith("```"):
            content = content[3:].strip()

        if content.endswith("```"):
            content = content[:-3].strip()

        return content

    def _extract_json(self, content: str) -> str:
        """尝试从文本中提取JSON"""
        # 用解码器逐位置扫描，不用贪婪正则——正则遇到嵌套或多个 JSON 块会切错边界。
        # 本处的取舍是"取最大候选、返回 JSON 文本"（同一段回复里可能既有示例块又有真结果），
        # 与三个抽取器用的 extract_json_payload"取第一个"**不同且不可互换**，别顺手改。
        largest = extract_largest_json_text(content)
        if largest is not None:
            return largest

        patterns = [r'\{[\s\S]*\}', r'\[[\s\S]*\]']
        for pattern in patterns:
            match = re.search(pattern, content)
            if match:
                extracted = match.group(0)
                try:
                    json.loads(extracted)
                    return extracted
                except json.JSONDecodeError:
                    continue

        return None
