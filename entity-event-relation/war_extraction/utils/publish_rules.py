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
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

__all__ = ["DEFAULT_RULES", "load_publish_rules"]

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config" / "publish_rules.json"

#: 代码内默认值 = 搬进配置前的硬编码内容（逐字一致）。配置缺失时用它，行为不变。
DEFAULT_RULES: Dict[str, List[str]] = {
    "summary_only_event_text_markers": ["原文仅提及事件名称", "主要战争有", "北征南伐", "东攻西进"],
    "summary_only_event_names": ["少康中兴", "商代之远征"],
    "campaign_summary_suffixes": ["南征", "东征", "西征", "北伐", "征鬼方"],
    "weak_result_tokens": ["获得一些胜利", "暂时控制", "势力南至", "不详", "未知"],
    "ancient_event_markers": ["神农", "黄帝", "炎帝", "尧", "舜", "禹", "蚩尤"],
    "ancient_credible_dynasties": ["上古", "远古"],
}


def load_publish_rules(path: Path = None) -> Dict[str, List[str]]:
    """
    读取发布规则；缺项用默认值补齐，整文件缺失时全部用默认值并告警。

    逐键补齐而不是"有文件就整份替换"：加一条规则时不必把整张表抄一遍，
    也就不会因为漏抄某个键而把那条规则悄悄关掉。
    """
    target = Path(path) if path else DEFAULT_CONFIG_PATH
    rules = {key: list(value) for key, value in DEFAULT_RULES.items()}
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
        if isinstance(value, list) and value:
            rules[key] = [str(item) for item in value]
    return rules
