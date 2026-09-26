"""
字段值解析：多值字段拆分、年份解析、起止时间定序。

这三段逻辑在 `main.py` 与 `war_extraction/extractors/` 下都只有**一份实现**（就在这里）：
多值拆分、年份解析、起止时间定序各存一份的话，改一处忘一处就会两边漂移。
"""
from __future__ import annotations

import re
from typing import List, Optional, Set

__all__ = [
    "MULTI_VALUE_SEPARATORS",
    "PLACEHOLDERS_FULL",
    "split_multi_value",
    "parse_year_for_order",
    "ensure_event_date_order",
]

#: 多值字段的分隔符。**顺序有意义**：先把长分隔符换成 "|"，再处理单字符分隔符。
MULTI_VALUE_SEPARATORS = ("、", "，", ",", "；", ";", "及", "与", "和", "/", " vs ", " VS ", "vs.")

#: 排除集：把"没有值"的占位词也算作空值。**只有这一套口径**，全模块统一——
#: 否则同一段 `"甲、未知、乙"` 会在两处分别拆成 `["甲","乙"]` 与 `["甲","未知","乙"]`，
#: "未知"被当成真名字留在实体/事件字段里。这里刻意不提供"窄集"可选值，
#: 免得又出现两套排除集各自演化。
#: 影响面：多值字段会多滤掉"未知/无/None"，属**抽取产物口径**——下一次抽取才见效。
PLACEHOLDERS_FULL = frozenset({"不详", "未知", "null", "None", "无"})


def split_multi_value(value: str, placeholders: Set[str] = PLACEHOLDERS_FULL) -> List[str]:
    """把"甲、乙、丙"这类多值字段拆成去空、去占位词后的列表。"""
    if not value:
        return []
    normalized = str(value)
    for sep in MULTI_VALUE_SEPARATORS:
        normalized = normalized.replace(sep, "|")
    parts = [part.strip() for part in normalized.split("|")]
    return [part for part in parts if part and part not in placeholders]


def parse_year_for_order(value: str) -> Optional[int]:
    """
    把日期文本解析成可比较的年份（公元前取负数）；解析不出返回 None。

    只认"公元前 X 年 / 前 X 年"与"X 年"两种写法，够用来判断两个时间点的先后。
    """
    value = (value or "").strip()
    if not value or value in {"不详", "未知", "进行中"}:
        return None
    match = re.search(r"(公元前|前)\s*(\d{1,4})\s*年?", value)
    if match:
        return -int(match.group(2))
    match = re.search(r"(?<!前)(\d{1,4})\s*年", value)
    if match:
        return int(match.group(1))
    return None


def ensure_event_date_order(event_obj):
    """
    两端年份都能解析时，保证 StartDate 不晚于 EndDate（就地交换并记 Remark）。

    返回传入的同一个对象，方便链式调用。
    """
    start_year = parse_year_for_order(getattr(event_obj, "StartDate", None))
    end_year = parse_year_for_order(getattr(event_obj, "EndDate", None))
    if start_year is None or end_year is None or start_year <= end_year:
        return event_obj
    event_obj.StartDate, event_obj.EndDate = event_obj.EndDate, event_obj.StartDate
    remark = "已自动校正开始时间晚于结束时间的问题"
    event_obj.Remark = "\n".join([part for part in [getattr(event_obj, "Remark", None), remark] if part])
    return event_obj
