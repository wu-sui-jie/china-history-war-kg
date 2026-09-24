"""
从 LLM 输出里取出 JSON 载荷。

EER-6：此前 entity / event / relation 三个抽取器各有一份**逐字相同**的
`_extract_json_payload`，而 `core/llm_client._extract_json` 里还有第四份扫描循环
（它多一层"取最大候选"的策略，所以只能合并扫描、不能强行合并成同一个函数——
强行合并会改变抽取产物）。这里把扫描逻辑收成一处，各方保留各自的取舍策略。
"""
from __future__ import annotations

import json
from typing import Any, Iterator, Optional, Tuple

__all__ = ["iter_json_values", "extract_json_payload", "extract_largest_json_text"]


def iter_json_values(text: str) -> Iterator[Tuple[Any, int]]:
    """
    扫描文本，按出现位置依次 yield ``(解析出的对象, 相对于 text 的结束下标)``。

    用 `JSONDecoder.raw_decode` 逐位置尝试，而不是贪婪正则——正则遇到嵌套或
    多个 JSON 块会切错边界。
    """
    if not text:
        return
    decoder = json.JSONDecoder()
    for idx, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            obj, end = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            continue
        yield obj, end


def extract_json_payload(text: str) -> Optional[Any]:
    """
    取**第一个**可解析的 JSON 值（dict 或 list），没有则返回 None。

    先试整体解析（最常见：模型规规矩矩只输出一个 JSON），失败再逐个位置扫描找子串。
    """
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    for obj, _ in iter_json_values(text):
        return obj
    return None


def extract_largest_json_text(text: str) -> Optional[str]:
    """
    取**最大**的候选并重新序列化成 JSON 文本；没有候选则 None。

    这是 `llm_client._extract_json` 的策略：模型有时会在同一段回复里给出多个
    JSON 块（含示例/片段），取最大的那个才更可能是真结果。注意它返回的是**文本**
    而非对象，且**不做**整体解析的快速路径——与 `extract_json_payload` 的
    取舍不同是刻意的，别把两者合并。
    """
    candidates = list(iter_json_values(text))
    if not candidates:
        return None
    obj = max(candidates, key=lambda item: len(json.dumps(item[0], ensure_ascii=False)))[0]
    return json.dumps(obj, ensure_ascii=False)
