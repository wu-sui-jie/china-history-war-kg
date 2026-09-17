"""F02 LLM 兜底的提示词与解析（server/query/prompts.py，RAGv5 §4.5）。

只在**词典完全未命中**时调用一次（结构化、非流式），用于从问句中抽取实体名与类型。
设计原则：
- 指令要求"只抽问题里明确出现的实体、不推理不补充"，避免模型引入知识库外的臆造实体；
- 只接受 JSON；解析失败即视为"未命中"（降级回词典结果，不报错）；
- 类型限定为知识库的四类，未知类型丢弃。
"""

from __future__ import annotations

import json
import re
from typing import Optional

# 与快照实体类型一致（contracts.governance / data 快照）
ENTITY_TYPES = ("事件", "人物", "组织", "地点")

_PROMPT_TEMPLATE = """你是中国历代战争史知识库的查询理解助手。
下面这个问题中的实体没有在知识库词典里匹配到，请从问题中**抽取**实体。

规则：
1. 只抽取问题里**明确出现**的实体名（战争/战役名、人物、组织、地名），不要推理、不要补充同义词、不要编造。
2. 类型只能是：事件 / 人物 / 组织 / 地点；拿不准就不要输出该项。
3. 只输出一个 JSON 对象，不要解释、不要 markdown 代码块，格式：
{{"entities": [{{"name": "实体名", "type": "类型"}}]}}
4. 若问题中没有任何可识别实体，输出 {{"entities": []}}。

问题：{question}"""


def build_entity_messages(question: str) -> list[dict]:
    return [
        {"role": "system", "content": "你只输出严格的 JSON，不输出任何其他文字。"},
        {"role": "user", "content": _PROMPT_TEMPLATE.format(question=question.strip())},
    ]


def parse_entities(text: str) -> list[dict]:
    """从模型输出里解析实体列表；任何异常都返回空列表（调用方按"未命中"处理）。"""
    if not text:
        return []
    raw = text.strip()
    # 容错：模型可能包了 ```json 或前后带说明，取第一个 { 到最后一个 } 之间的内容
    if "```" in raw:
        raw = re.sub(r"```(?:json)?", "", raw).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(raw[start:end + 1])
    except Exception:  # noqa: BLE001
        return []
    items = data.get("entities") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()
        etype = str(it.get("type") or "").strip()
        if not name or name in seen or etype not in ENTITY_TYPES:
            continue
        seen.add(name)
        out.append({"name": name, "type": etype})
    return out


def should_fallback(*, dictionary_hits: int, enable_llm: bool, llm_client) -> bool:
    """兜底触发条件：开关打开、客户端可用、且词典完全未命中（RAGv5 §4.5）。"""
    return bool(enable_llm and llm_client is not None and dictionary_hits == 0)
