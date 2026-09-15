"""F09 别名与名称归一工具。

处理旧数据里“涿鹿 / 涿鹿县 / 涿鹿县(今河北)”这类表述差异：
- 从 place.modern_name 建现代地名别名：modern_name 是“涿鹿县”时，标准名“涿鹿”的别名含“涿鹿县”。
- 提供 normalize_name()：去掉地名后缀“县/市/区/省”、去掉括号注释（今…）、全半角/空白归一。
- duplicate_name_groups()：按归一后名称分组，找出“同名歧义组”（同 display 名但实体不同），
  供治理报告列待人工审核，不自动 merge。
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable

# 地名行政区划后缀（现代地名归一用）
_PLACE_SUFFIX = ("省", "市", "区", "县")
# 括号及其中内容（含全半角）
_PAREN_RE = re.compile(r"[（(][^）)]*[）)]")


def strip_parens(s: str) -> str:
    return _PAREN_RE.sub("", s)


def normalize_name(name: str, drop_place_suffix: bool = False) -> str:
    """基础归一：去空白/全半角统一 → 去括号注释 → 可选去行政区划后缀。"""
    if not name:
        return ""
    s = str(name).strip().replace("\u3000", " ").replace("\ufeff", "")
    s = s.translate(str.maketrans("０１２３４５６７８９（）", "0123456789()"))
    s = strip_parens(s).strip()
    if drop_place_suffix:
        # 地名末尾是 省/市/区/县 时去掉（"涿鹿县"→"涿鹿"），但要保留 "县" 在名称中间的
        while len(s) > 1 and s.endswith(_PLACE_SUFFIX):
            s = s[:-1]
    return s


def build_place_aliases(places: list[dict]) -> dict[str, list[str]]:
    """根据旧 places 的 name/modern_name 构建 标准名→别名 映射。

    规则（低风险）：
    - 若 modern_name 与 name 不同且不是"不详/空"，把 modern_name 归一化去后缀后的名字、
      以及原样 modern_name，作为 name 的别名候选。
    - 例如 name=涿鹿, modern_name=涿鹿县 → 别名含 "涿鹿县"、"涿鹿"(自指略)。
    """
    alias_map: dict[str, list[str]] = defaultdict(list)
    for p in places:
        name = (p.get("name") or "").strip()
        modern = (p.get("modern_name") or "").strip()
        if not name or not modern or modern in ("不详",):
            continue
        std = normalize_name(name, drop_place_suffix=False)
        if not std:
            continue
        cands = []
        m_norm = normalize_name(modern, drop_place_suffix=True)
        if m_norm and m_norm != std:
            cands.append(m_norm)
        if modern != m_norm and modern not in cands and modern != name:
            cands.append(modern)
        alias_map[std].extend(c for c in cands if c not in alias_map[std])
    return dict(alias_map)


def build_person_org_aliases(persons: list[dict], orgs: list[dict]) -> dict[str, list[str]]:
    """人物/组织别名：初版保留空映射（不自动猜测简称，避免高误合并）。

    后续若需“曹操→曹孟德”这类别名，应由人工维护别名配置表，本函数读取配置合并。
    """
    return {}


def duplicate_name_groups(records: Iterable[dict]) -> list[dict]:
    """按 display 名称（归一）对实体分组，找出 1 个名称对应多个实体的歧义组。

    返回 [{"display_name":..., "entity_ids":[快照id...]或 records, "count":N}]
    仅统计、列报告，不自动合并。
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        nm = normalize_name(r.get("name") or "", drop_place_suffix=False)
        if nm:
            groups[nm].append(r)
    dup = [
        {"display_name": nm, "records": recs, "count": len(recs)}
        for nm, recs in groups.items()
        if len(recs) > 1
    ]
    dup.sort(key=lambda x: -x["count"])
    return dup
