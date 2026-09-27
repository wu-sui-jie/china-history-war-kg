"""
发布过滤与事件清理里的**书本特化规则**的唯一来源。

**为什么要把这些从 `main.py` 里搬出来。** 原先 `main.py` 里硬编码了事件名名单
（`{"少康中兴", "商代之远征"}`）、概括事件后缀（`南征/东征/西征/北伐`）、弱结果词表与
上古标记词。这些规则都是针对**《中国历代战争简史》这一本书**调的，换一本语料只能改代码，
而改动的后果是"整个知识库的内容变了"（`published` 子集是下游 SQLite/Neo4j/RAG 的输入），
下游不会报错，只会"数字变了"。搬进 `config/publish_rules.json` 之后，换语料只改配置。

**文件缺失时的口径**：用代码内的默认值（与搬进配置前的行为逐字一致）并打印告警——
不静默退化成空表。空表意味着"概括事件不再被过滤、弱结果不再被剔除"，
那会让发布子集整体变样，而且一点提示都没有。

**「过宽概括」判定只有一份实现（第三阶段 D5）。** 原先两处各写一张表、口径还不同：
`main._is_summary_only_event` 是"4 个文本标记 + 2 个事件名"，`EventExtractor._is_summary_style_event`
是"4 个名称标记 + 6 个文本标记"。同一个概念两处判定，改一处另一处不动，而且**两张表谁也不知道
对方的存在**。现在合并成配置里的 `overbroad_event_markers`，两边都调 `is_overbroad_event`。
合并属于**抽取侧**改动（抽取器据此丢事件），要随重跑生效。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

__all__ = ["DEFAULT_RULES", "load_publish_rules", "is_overbroad_event"]

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config" / "publish_rules.json"

#: 代码内默认值 = 搬进配置前的硬编码内容（逐字一致）。配置缺失时用它，行为不变。
#:
#: 唯一例外是 `overbroad_event_markers`：它是 D5 把**两处**表合并后的**并集**，
#: 所以合并前的两处各自都不是这个内容。配置文件在仓库里、正常都会加载到它；
#: 这份默认值只在配置整体缺失时兜底，那时用并集比用"只抄一半"更不容易让人误判。
DEFAULT_RULES: Dict[str, object] = {
    "overbroad_event_markers": {
        # 命中**事件名**即可（`EventExtractor._is_summary_style_event` 原有口径）
        "event_name_markers": ["时期", "系列", "多路征伐", "远征"],
        # 命中"事件名 + 文本"（两处原有口径的并集）。单字命中率过高，所以这里的条目都是
        # 两字以上短语或连写形式。
        "text_markers": [
            "原文仅提及事件名称",
            "主要战争有",
            "北征南伐",
            "曾北征南伐",
            "东攻西进",
            "几次大决战",
            "此后",
            "继后",
        ],
        # 事件名精确相等
        "event_names": ["少康中兴", "商代之远征"],
    },
    "campaign_summary_suffixes": ["南征", "东征", "西征", "北伐", "征鬼方"],
    "weak_result_tokens": ["获得一些胜利", "暂时控制", "势力南至", "不详", "未知"],
    "ancient_event_markers": ["神农", "黄帝", "炎帝", "尧", "舜", "禹", "蚩尤"],
    "ancient_credible_dynasties": ["上古", "远古"],
}

#: `overbroad_event_markers` 的三个子键。缺一个就退回默认值，不静默变成空表。
_OVERBROAD_SUBKEYS = ("event_name_markers", "text_markers", "event_names")


def load_publish_rules(path: Optional[Path] = None) -> Dict[str, object]:
    """
    读取发布规则；缺项用默认值补齐，整文件缺失时全部用默认值并告警。

    逐键补齐而不是"有文件就整份替换"：加一条规则时不必把整张表抄一遍，
    也就不会因为漏抄某个键而把那条规则悄悄关掉。

    取值有两种形状，都要按同样的"缺项补齐"口径处理：**列表**（如 `weak_result_tokens`）
    与**子表**（`overbroad_event_markers` 是"三个字符串列表"的字典）。子表按子键逐个补齐，
    不然配置里只写了 `text_markers` 时，名称标记会静默变成空表——"表格漏了一半"完全看不出来。
    """
    target = Path(path) if path else DEFAULT_CONFIG_PATH
    rules = {key: _copy_default(value) for key, value in DEFAULT_RULES.items()}
    if not target.exists():
        print(f"  [配置缺失] {target} 不存在：发布过滤按代码内默认规则运行"
              f"（与搬进配置前的行为一致，但换语料时的改动无处可查）")
        return rules
    try:
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"  [配置损坏] {target} 无法解析（{exc}）：发布过滤按代码内默认规则运行")
        return rules
    for key, default in DEFAULT_RULES.items():
        value = data.get(key)
        if isinstance(default, list) and isinstance(value, list) and value:
            rules[key] = [str(item) for item in value]
        elif isinstance(default, dict) and isinstance(value, dict):
            for subkey in default:
                subvalue = value.get(subkey)
                if isinstance(subvalue, list) and subvalue:
                    rules[key][subkey] = [str(item) for item in subvalue]
    return rules


def _copy_default(value):
    """默认值只读，但调用方可能就地改（如 `PUBLISH_RULES[...].append`），所以逐层深拷一层。"""
    if isinstance(value, dict):
        return {key: list(item) for key, item in value.items()}
    return list(value)


def is_overbroad_event(event_name: str, text: str, rules: Optional[Dict[str, object]] = None) -> bool:
    """
    「过宽概括」事件的**唯一**判定：`main` 的发布过滤与 `EventExtractor` 的识别过滤共用。

    口径（整改方案 10.1 第 1 项）：过宽概括如"蒙金战争"不算事件，具体战役算。
    三条判据任一命中即算：

    - `event_name_markers`：标记词出现在**事件名**里（如"…时期"、"…系列"）；
    - `text_markers`：标记词出现在 `text` 里；
    - `event_names`：事件名与名单**精确相等**（如"少康中兴"）。

    Args:
        event_name: 事件名。
        text: 判定用的文本。**调用方各自给**：`main` 给"事件名 + source_text + Remark"
            （发布期手上有的是整段正文），抽取器给"事件名 + evidence"（识别期只有证据句）。
            两处可用字段不同，但**判据与特征表是同一份**。
        rules: 已加载的发布规则；不给则读配置（缺文件时用默认值并告警）。

    Returns:
        是否属于"过宽概括"。
    """
    rules = rules if rules is not None else load_publish_rules()
    markers = rules.get("overbroad_event_markers") or {}
    name = event_name or ""
    haystack = text or ""
    if any(marker in name for marker in markers.get("event_name_markers") or []):
        return True
    if any(marker in haystack for marker in markers.get("text_markers") or []):
        return True
    return name.strip() in set(markers.get("event_names") or [])
