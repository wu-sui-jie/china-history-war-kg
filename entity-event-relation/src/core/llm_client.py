"""
DeepSeek API 客户端封装模块
"""

import os
import re
import json
import time
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv(dotenv_path="config/.env")


class DeepSeekClient:
    """封装 DeepSeek API 调用的客户端类"""

    def __init__(self):
        api_key = os.getenv("DEEPSEEK_API_KEY")
        base_url = os.getenv("API_BASE_URL", "https://api.deepseek.com/v1")
        model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

        if not api_key:
            raise ValueError("请设置 DEEPSEEK_API_KEY 环境变量")

        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

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
                except Exception:
                    if not json_mode:
                        raise
                    kwargs.pop("response_format", None)
                    response = self.client.chat.completions.create(**kwargs)
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

            except Exception as e:
                print(f"API调用失败，尝试重试 {attempt + 1}/{max_retries}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(1)
                else:
                    raise Exception(f"多次重试后仍失败: {e}")

        raise Exception("多次重试后仍无法获取有效响应")

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
        # Changed 2026-04-20 16:33:36 +08:00: Prefer decoder scanning over
        # greedy regex so nested/multiple JSON blocks are handled safely.
        decoder = json.JSONDecoder()
        candidates = []
        for idx in range(len(content)):
            if content[idx] not in "{[":
                continue
            try:
                obj, end = decoder.raw_decode(content[idx:])
                candidates.append((obj, end))
            except json.JSONDecodeError:
                continue

        if candidates:
            obj = max(candidates, key=lambda item: len(json.dumps(item[0], ensure_ascii=False)))[0]
            return json.dumps(obj, ensure_ascii=False)

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
