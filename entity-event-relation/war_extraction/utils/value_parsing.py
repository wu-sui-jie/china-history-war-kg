"""
字段值解析：多值字段拆分、年份解析、起止时间定序、事件身份键。

这几段逻辑在 `main.py` 与 `war_extraction/extractors/` 下都只有**一份实现**（就在这里）：
多值拆分、年份解析、起止时间定序、事件身份各存一份的话，改一处忘一处就会两边漂移。
"""
from __future__ import annotations

import re
from typing import List, Optional, Set, Tuple

__all__ = [
    "MULTI_VALUE_SEPARATORS",
    "PLACEHOLDERS_FULL",
    "split_multi_value",
    "parse_year_for_order",
    "ensure_event_date_order",
    "first_effective_place",
    "event_identity_key",
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

    入参一律先转成字符串：人工标注里的年份有整数写法（`StartDate: 618`），
    直接 `.strip()` 会在这里抛 `AttributeError`，而那会让整个评估跑不起来。
    """
    value = str(value).strip() if value is not None else ""
    if not value or value in {"不详", "未知", "进行中", "none", "null"}:
        return None
    # 纯数字（标注里的整数年份）直接当公元年
    if value.isdigit() and len(value) <= 4:
        return int(value)
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


def first_effective_place(normalizer, value: str) -> str:
    """事件身份里的"首个有效地点"：多值拆分后跳过长噪声名的第一个地点。"""
    for place_name in split_multi_value(value):
        if not normalizer.is_noisy_place_name(place_name):
            return normalizer.normalize_entity_name(place_name)
    return ""


def event_identity_key(normalizer, name, dynasty=None, start_date=None, place=None) -> Tuple[str, str, str, str]:
    """
    事件身份的**唯一定义**：归一后名称 + 朝代 + 起始时间 + 首个有效地点。

    **为什么要收成一处。** 原先四处口径互不相同：

    | 位置 | 原来的键 |
    | --- | --- |
    | 抽取器 `_postprocess_events` | 只有规范化名称 |
    | 合并期 `ResultMerger._event_merge_key` | 名称 + 朝代 |
    | 清理期 `main.cleanup_events` | 名称 + 朝代 + 时间 + 地点 |
    | 发布期 `main.split_publishable_outputs` | 名称 + 时间 + 地点（**没有朝代**） |

    后果是同名不同年代的两场战争在一处被合并、在另一处不被合并：事件计数、关系分母、
    发布子集三者互相打架，而"哪一处才对"无法从产物看出来。

    已定口径（整改方案 10.1 第 4 项）：**名称 + 朝代 + 起始时间 + 首个地点**。
    连带效果是"同名不同年代事件不再被合并"，事件计数与关系分母都会变——这是预期变化。

    事件识别阶段拿不到朝代（那一阶段的输出只有 id/name/time/location/parties/evidence），
    此时传空朝代，键的**定义**仍然一致，只是可用字段少一个。
    """
    return (
        normalizer.normalize_event_name(name),
        _as_text(dynasty),
        _as_text(start_date),
        first_effective_place(normalizer, place),
    )


def _as_text(value) -> str:
    """
    一律转成字符串再 `strip`。

    **为什么必须转**：人工标注里的年份有整数写法（`StartDate: 618`），直接 `.strip()` 会抛
    `AttributeError`（`parse_year_for_order` 早就为这件事加固过，这里是同一个坑）。
    抽取链路上的字段来自 pydantic 模型、本来就是字符串，所以这个坑只在**读参考集**时踩到——
    而"读参考集把工具崩掉"会把失败伪装成"参考集有问题"，排查方向从一开始就是错的。
    """
    return str(value).strip() if value is not None else ""
