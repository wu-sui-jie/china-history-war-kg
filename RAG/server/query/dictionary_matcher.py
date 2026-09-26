"""F02 词典/规则优先实体识别。

设计（对应 RAGv2 规划第 2 节）：
- 词典 + 规则优先匹配，词典命中且置信度足够 → 跳过 LLM（默认路径）；
- 同名歧义降级：同名多实体命中时全部进 candidates（按朝代/事件类型区分），
  filters 带朝代时自动消歧；
- 输出匹配的候选实体集合，由上层决定进 entities 还是 candidates。

词典来源（data/snapshot/<v>/）：
- entities.json：标准名（type 区分 事件/人物/组织/地点）+ aliases
- dicts.json：dynasty_aliases（朝代名）
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# 事件名后缀规则（"XX之战"/"XX之变" 等启发式用于兜底标注 type=事件）
# 简单疑问词规则
_RELATION_WORDS = {"谁", "哪些", "哪个", "什么关系", "关系", "参与", "发起", "进攻",
                   "攻打", "主帅", "将领", "统帅", "主将", "兵力", "是哪个"}
@dataclass
class EntityHit:
    """词典一次命中的候选实体。同名多实体时产生多条。"""

    name: str                    # 问题中出现的原文 mention
    entity_id: Optional[str]
    type: str                    # 事件/人物/组织/地点
    standard_name: str
    confidence: str = "high"
    dynasty: Optional[str] = None
    event_type: Optional[str] = None
    source: str = "dict"


def load_jieba(snapshot_dir: Path) -> None:
    """复用 RAGv1 实体名/别名入 jieba 词典，保证分词时整名命中。"""
    from data.index import fts as fts_mod
    fts_mod.load_jieba_dicts(snapshot_dir)


class DictionaryMatcher:
    """基于实体索引的词典匹配器。

    索引结构：
      self._by_name: 标准名 → list[entity dict]（同名多实体）
      self._alias: 别名 → list[entity dict]
      self._dynasty_terms: 朝代术语集合
    """

    def __init__(self, snapshot_dir: Path, index: Optional[dict] = None):
        # 预构建 name→实体 索引（由 factory 或直接传 entities）
        self.snapshot_dir = Path(snapshot_dir)
        self._by_name: dict[str, list[dict]] = {}
        # entity_id → 实体：同名不同朝代的实体共享标准名，纠正链路必须能按 ID 精确定位
        # （没有它就只能"按名字取第一项"）
        self._by_id: dict[str, dict] = {}
        self._alias: dict[str, list[dict]] = {}
        self._entity_type_terms: dict[str, set[str]] = {
            "事件": set(), "人物": set(), "组织": set(), "地点": set(),
        }
        self._dynasty_terms: set[str] = set()
        self._dicts: dict = {}
        if index is None:
            self._build_from_snapshot()
        else:
            self._from_index(index)

    # ---- 构建 ----
    def _build_from_snapshot(self) -> None:
        import json
        ents = json.loads((self.snapshot_dir / "entities.json").read_text(encoding="utf-8"))
        self._index_entities(ents)
        dicts_path = self.snapshot_dir / "dicts.json"
        if dicts_path.exists():
            self._dicts = json.loads(dicts_path.read_text(encoding="utf-8"))
            self._dynasty_terms = set(self._dicts.get("dynasty_aliases") or {})

    def _from_index(self, index: dict) -> None:
        ents = index["entities"]
        self._index_entities(ents)
        self._dicts = index.get("dicts", {})
        self._dynasty_terms = set(self._dicts.get("dynasty_aliases") or {})

    def by_id(self, entity_id: Optional[str]) -> Optional[dict]:
        """按 entity_id 精确取实体；不存在返回 None。"""
        if not entity_id:
            return None
        return self._by_id.get(entity_id)

    def _index_entities(self, ents: list[dict]) -> None:
        for e in ents:
            self._by_name.setdefault(e["name"], []).append(e)
            eid = e.get("entity_id")
            if eid and eid not in self._by_id:
                self._by_id[eid] = e
            # 按类型建词集便于规则排除太泛的词
            self._entity_type_terms.setdefault(e["type"], set()).add(e["name"])
            for a in e.get("aliases") or []:
                self._alias.setdefault(a, []).append(e)
                if len(a) <= 12:
                    self._entity_type_terms.setdefault(e["type"], set()).add(a)

    # ---- 单 token 命中 ----
    def _lookup(self, mention: str) -> list[dict]:
        """标准名 + 别名命中的实体列表（去重，保留全部同名）。"""
        out = []
        seen = set()
        for e in self._by_name.get(mention, []):
            if e["entity_id"] not in seen:
                seen.add(e["entity_id"])
                out.append(e)
        for e in self._alias.get(mention, []):
            if e["entity_id"] not in seen:
                seen.add(e["entity_id"])
                out.append(e)
        return out

    @property
    def dynasty_alias_map(self) -> dict:
        """朝代别名 → 标准朝代名 映射（dicts.json 的 dynasty_aliases）。"""
        return self._dicts.get("dynasty_aliases") or {}

    # ---- 主入口 ----
    def match(self, question: str) -> list[EntityHit]:
        """对问题文本做词典/规则实体识别，返回命中列表（含同名多实体）。

        方法：
        1. 先在原文中匹配实体标准名/别名（最长优先），切分 token；
        2. 未命中时用事件后缀规则识别事件（如"XX之战"），对照实体词典补全；
        3. 朝代术语独立识别为过滤器候选项。
        返回按出现顺序的 EntityHit 列表。
        """
        # 简称优先："X之战"式 token 若命中事件全名（如"牧野之战"→"周武王灭商牧野之战"），
        # 直接按事件处理，避免"牧野"(地点) 在通用扫描中抢先命中子串产生错误类型。
        m = re.search(r"([\u4e00-\u9fff]{1,10}(?:之战|之变|之役|大战|之围))", question)
        if m:
            event_hits = self._short_name_fallback(m.group(1))
            if event_hits:
                return event_hits

        # 长度降序排列实体名，贪心最长匹配（避免"涿鹿"先于"涿鹿之战"命中子串）。
        # 并列长度必须加确定性次序：仅按 len 排序时等长词顺序取决于 set 迭代顺序，
        # 而 set 顺序受 PYTHONHASHSEED 影响 → 跨进程结果不稳定（曾导致评测两次 run
        # 的证据顺序/回答顺序不一致，破坏可复现性）。并列按词本身排序保证稳定。
        names = sorted(
            set(list(self._by_name.keys()) + list(self._alias.keys())),
            key=lambda w: (-len(w), w),
        )
        hits: list[EntityHit] = []
        text = question
        used_spans: list[tuple[int, int]] = []
        for nm in names:
            if len(nm) < 2:
                continue
            start = 0
            while True:
                idx = text.find(nm, start)
                if idx < 0:
                    break
                # 检查是否已被更长词占用
                if any(s <= idx < e for s, e in used_spans):
                    start = idx + 1
                    continue
                used_spans.append((idx, idx + len(nm)))
                for e in self._lookup(nm):
                    hits.append(EntityHit(
                        name=nm,
                        entity_id=e["entity_id"],
                        type=e["type"],
                        standard_name=e["name"],
                        confidence="high",
                        dynasty=e.get("dynasty"),
                        event_type=e.get("event_type"),
                        source="dict",
                    ))
                start = idx + len(nm)

        # 兜底（上一步事件全名未命中时）
        if not hits:
            hits = self._short_name_fallback(text)
        return hits

    def _short_name_fallback(self, question: str) -> list[EntityHit]:
        """对未命中词典的"X之战/X之变/大战"式简称做事件名包含匹配。

        只处理形如「XX之战」的连续 token（1~10 字 + 之战后缀）。
        返回置信度 medium 的命中（唯一命中可升为实体，多命中由上层进 candidates）。
        """
        if isinstance(question, str):
            m = re.search(r"([\u4e00-\u9fff]{1,10}(?:之战|之变|之役|大战|之围))", question)
            if not m:
                return []
            token = m.group(1)
        else:  # 兼容旧调用（直接传 token）
            token = question
        out = []
        for name, ents in self._by_name.items():
            if name == token:
                continue
            if token in name and ents[0]["type"] == "事件":
                for e in ents[:8]:
                    out.append(EntityHit(
                        name=token,
                        entity_id=e["entity_id"],
                        type="事件",
                        standard_name=e["name"],
                        confidence="medium",
                        dynasty=e.get("dynasty"),
                        event_type=e.get("event_type"),
                        source="short_name_fallback",
                    ))
                if out:
                    return out
        return out

    # ---- 歧义降级：同一 mention 命中多实体 ----
    @staticmethod
    def group_by_mention(hits: list[EntityHit]) -> list[tuple[str, list[EntityHit]]]:
        groups: dict[str, list[EntityHit]] = {}
        for h in hits:
            groups.setdefault(h.name, []).append(h)
        return [(k, groups[k]) for k in groups]
