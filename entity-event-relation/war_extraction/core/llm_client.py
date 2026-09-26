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


class DeepSeekClient:
    """封装 DeepSeek API 调用的客户端类"""

    def __init__(self):
        # 兼容两种密钥变量名：DEEPSEEK_API_KEY 为主，Chinese_txt 为历史遗留
        api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("Chinese_txt")
        base_url = os.getenv("API_BASE_URL", "https://api.deepseek.com/v1")
        model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

        if not api_key:
            raise ValueError(
                "未找到 DeepSeek API 密钥：请在 config/.env 中设置 "
                "DEEPSEEK_API_KEY=sk-xxx（兼容旧变量名 Chinese_txt）"
            )

        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=60.0, max_retries=2)
        self.model = model
        self.base_url = base_url  # 随产物 metadata 记录，换端点重跑后产物可自证来源

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
            try:
                kwargs = {
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": temperature,
                }
                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                try:
                    response = self.client.chat.completions.create(**kwargs)
                except AuthenticationError as e:
                    # 密钥无效/过期时重试无意义，直接给出明确错误
                    raise LLMAuthError(
                        "DeepSeek API 密钥无效（HTTP 401）：请检查 config/.env 或环境变量中的 DEEPSEEK_API_KEY 是否有效"
                    ) from e
                except Exception as e:
                    # 部分服务不支持 json_mode 时降级重试一次
                    if json_mode:
                        kwargs.pop("response_format", None)
                        try:
                            response = self.client.chat.completions.create(**kwargs)
                        except AuthenticationError as ae:
                            raise LLMAuthError(
                                "DeepSeek API 密钥无效（HTTP 401）：请检查 config/.env 或环境变量中的 DEEPSEEK_API_KEY 是否有效"
                            ) from ae
                        except Exception:
                            raise e
                    else:
                        raise

                content = response.choices[0].message.content

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
