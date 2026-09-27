#!/usr/bin/env python
"""
参考集（`data/annotations/`）的**读取与身份口径**：一处实现，多处使用。

**为什么单独一个模块。** 同一批记录要在几条链路上被认出是"同一条"：

- `tools/sample_for_review.py` 从**产物**造定位键（B 组核验表与跨版本 `--compare` 靠它）；
- 参考集重建时要从**标注**造定位键（判定实验的抽样表、稳定 ID、新旧对比）。

两处各写一遍 f-string 就会漂移，而漂移的表现是"跨版本对不上、少一行多一行"——**不报错**。
所以键的构造只留在这里，别的工具 import 它。

**两种"身份"要分清，别混用**：

- **定位键**（`event_key` / `place_key` / …）：给人工表与跨版本比较用的**可读**键，
  名称用**标注/产物里的原样写法**。改了写法就是另一条记录——这是有意的，因为它是给人看的。
- **稳定 ID**（`stable_id`）：给"引用同一条记录"用的**短哈希**，核心是
  `名称 + 朝代`（实体的再带类型、关系的用三元组），**不含时间/地点这类会被改的字段**——
  否则把某条事件的地点改对了，ID 就变了，diff 会把它显示成"删一条 + 加一条"。
- **归一键**（`normalized_*`）：给**去重与冲突检测**用（同一条记录写了两遍、或同名不同年代）。
  它走 `Normalizer`，所以别名表改了它就会变——它只用于当场判定，**不进任何持久化文件**。

用法：

    from tools.annotation_io import load_annotations, event_key, stable_id

    gold = load_annotations(Path("data/annotations"))
    gold["events"], gold["places"], gold["persons"], gold["organizations"], gold["relations"]
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from war_extraction.utils.normalizer import Normalizer
from war_extraction.utils.value_parsing import event_identity_key

__all__ = [
    "load_annotations", "gold_counts",
    "event_key", "place_key", "person_key", "organization_key", "relation_key",
    "stable_id", "event_core", "entity_core", "relation_core",
    "RELATION_CATEGORY_LABELS",
]

#: `sample_relations.json` 的类别键 → 定位键前缀与人工表的分层名。
#: 三者必须一致：前缀决定 `expand_review_context` 怎么定位，分层名决定汇总时归到哪一类。
RELATION_CATEGORY_LABELS = {
    "event_place_relations": ("event-place", "关系:事件-地点"),
    "event_organization_relations": ("event-org", "关系:事件-组织"),
    "event_person_relations": ("event-person", "关系:事件-人物"),
    "event_event_relations": ("event-event", "关系:事件-事件"),
}

#: 参考集三份文件的固定名（评估器直接读它们，不要改名）。
GOLD_FILES = {
    "entities": "sample_entities.json",
    "events": "sample_events.json",
    "relations": "sample_relations.json",
}


def load_annotations(directory: Path) -> dict:
    """
    读参考集三份文件，返回一个扁平字典：

        {"events": [...], "places": [...], "persons": [...], "organizations": [...],
         "relations": {"event-place": [{"head","relation","tail"}, ...], ...}}

    关系那份的顶层键是**连字符形式**（`event-place`），与实体/事件的键混在一层容易被
    `payload["event_place_relations"]` 这类写法取错（那是**产物**的键名），所以在这里
    一次性统一成上面这一种，工具侧只认这一种。
    """
    directory = Path(directory)
    payload = {}
    entities = _read(directory / GOLD_FILES["entities"])
    payload["places"] = entities.get("places") or []
    payload["persons"] = entities.get("persons") or []
    payload["organizations"] = entities.get("organizations") or []
    payload["events"] = _read(directory / GOLD_FILES["events"]).get("events") or []
    relations = _read(directory / GOLD_FILES["relations"])
    payload["relations"] = {label: (relations.get(label) or [])
                            for label, _layer in RELATION_CATEGORY_LABELS.values()}
    return payload


def _read(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def gold_counts(gold: dict) -> dict:
    """条数速览（报告与 JSON 的 provenance 都用它，免得各处各数一遍）。"""
    return {
        "events": len(gold["events"]),
        "places": len(gold["places"]),
        "persons": len(gold["persons"]),
        "organizations": len(gold["organizations"]),
        "relations": {label: len(rows) for label, rows in gold["relations"].items()},
    }


# --------------------------------------------------------------------- 定位键
# 与 `tools/sample_for_review.py` 逐字符一致（那边现在 import 这里，避免两套）。

def event_key(normalizer, name, dynasty, start, place) -> str:
    """`event|归一名|朝代|起始时间|首个地点`（`event_identity_key` 是全项目唯一实现）。"""
    return "event|" + "|".join(event_identity_key(normalizer, name, dynasty, start, place))


def place_key(name, dynasty, modern_name=None) -> str:
    return f"entity:place|{(name or '').strip()}|{dynasty or ''}|{modern_name or ''}"


def person_key(name, dynasty) -> str:
    return f"entity:person|{(name or '').strip()}|{dynasty or ''}"


def organization_key(name, dynasty) -> str:
    return f"entity:organization|{(name or '').strip()}|{dynasty or ''}"


def relation_key(attribute, event_name, relation, target) -> str:
    return f"{attribute}|{(event_name or '').strip()}|{relation or ''}|{(target or '').strip()}"


# --------------------------------------------------------------------- 稳定 ID
#: 各类记录 ID 的前缀（可读性：一眼看出这条是什么）。
ID_PREFIXES = {
    "events": "evt",
    "places": "plc",
    "persons": "per",
    "organizations": "org",
    "relations": "rel",
}


def event_core(row: dict, normalizer=None) -> str:
    """事件的**稳定核心**：归一名 + 朝代（不含时间与地点——那两个是常被改的字段）。"""
    normalizer = normalizer or Normalizer()
    return f"{normalizer.normalize_event_name(row.get('EventName'))}|{row.get('DynastyName') or ''}"


def entity_core(kind: str, row: dict, normalizer=None) -> str:
    normalizer = normalizer or Normalizer()
    key = {"places": "geo_name", "persons": "PersonName", "organizations": "OrgName"}[kind]
    return f"{kind}|{normalizer.normalize_entity_name(row.get(key))}|{row.get('DynastyName') or ''}"


def relation_core(attribute: str, row: dict, normalizer=None) -> str:
    """关系没有"核心 vs 属性"之分：三元组就是它的身份。"""
    normalizer = normalizer or Normalizer()
    return (f"{attribute}|{normalizer.normalize_event_name(row.get('head'))}"
            f"|{normalizer.normalize_relation(row.get('relation'))}"
            f"|{normalizer.normalize_entity_name(row.get('tail'))}")


def stable_id(prefix: str, core: str) -> str:
    """
    `前缀 + 核心串的 sha256 前 10 位`。

    为什么用哈希而不是序号：序号会随"前面插一条/删一条"整体位移，于是"改了一处"
    在 diff 里看起来像"全变"；哈希只跟内容有关，插入删除都不影响别人的 ID。
    10 位十六进制（40 位）在这个规模上碰撞概率可忽略，且短到能直接写进文档与对话。
    """
    digest = hashlib.sha256((core or "").encode("utf-8")).hexdigest()[:10]
    return f"{prefix}-{digest}"
