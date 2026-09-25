"""
字段值解析：多值字段拆分、年份解析、起止时间定序。

EER-6：这几段逻辑此前在 `main.py` 与 `war_extraction/extractors/` 下各存一份
（多值拆分两份、年份解析两份、起止时间定序两份），改一处忘一处就会两边漂移。
这里收成单一实现。多值拆分的占位词排除集原先两边不一致，**2026-09-25 已统一为宽口径**
（见下方 `PLACEHOLDERS_FULL` 的说明）。
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

#: 排除集：把"没有值"的占位词也算作空值。
#: 原先有两套——关系抽取器用这套宽的，`main.py` 用一套只排除"不详/null"的窄集，
#: 于是同一段 `"甲、未知、乙"` 在两边分别拆成 `["甲","乙"]` 与 `["甲","未知","乙"]`
#: （"未知"被当成真名字留下）。**2026-09-25 按决策统一为这一套宽口径**，窄集已删除
#: （留着它是死代码，与同期删掉的 `AlignmentTool` 同一类问题）。
#: 影响面：`main.py` 侧的多值字段会多滤掉"未知/无/None"，属**抽取产物口径变更**——
#: 只在下一次抽取的产物里可见，当前产物与评估指标不受影响。
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
