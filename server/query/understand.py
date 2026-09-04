"""F02 问题理解编排（server/query/understand.py）。

执行链（默认词典优先，无 LLM）：
  1. 词典/规则实体识别（DictionaryMatcher.match）；
  2. 同名歧义降级：同一 mention 多实体 → 全部进 candidates，entities 只取首选项；
     若 filters 带朝代 → 自动按朝代消歧；
  3. 多轮指代消解（结合 history，把"它/这场战争"还原为上一轮事件实体）；
  4. 应用 corrected_entities（add/replace/remove）纠正重查；
  5. 判定问题类型 + 抽取朝代过滤器；
  6. 生成 rewritten_question（指代还原 + 实体全名化）。

LLM 兜底：若配置了 llm_client 且词典完全未命中，调用 deepseek 做实体识别/类型判定。
当前默认不启用 LLM（词典命中且置信度足够即跳过），避免占用首 Token。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from contracts.question import QuestionType
from contracts.request import (
    CandidateOption,
    CorrectedEntity,
    EntityCandidate,
    EntityRef,
    Filters,
    F02Output,
)
from server.query import classifier as clf
from server.query.dictionary_matcher import DictionaryMatcher, EntityHit


class QuestionUnderstanding:
    def __init__(self, matcher: DictionaryMatcher,
                 llm_client: Optional[object] = None,
                 enable_llm: bool = False,
                 history_max_turns: int = 4):
        self.matcher = matcher
        self.llm = llm_client
        self.enable_llm = enable_llm and llm_client is not None
        self.history_max_turns = max(1, history_max_turns)

    @staticmethod
    def _turn(turn) -> dict:
        """history 轮次统一转 dict（兼容 HistoryTurn dataclass 与 dict）。"""
        if hasattr(turn, "to_dict"):
            return turn.to_dict()
        return turn if isinstance(turn, dict) else {"role": "", "content": ""}

    # ---- 歧义消解 ----
    @staticmethod
    def _resolve_ambiguity(hits: list[EntityHit],
                           dynasty_filter: list[str]) -> list[EntityHit]:
        """同名多实体时按过滤器朝代自动消歧；无法消歧保留全部（上层转 candidates）。"""
        # 按 mention 分组统计
        groups: dict[str, list[EntityHit]] = {}
        for h in hits:
            groups.setdefault(h.name, []).append(h)
        resolved: list[EntityHit] = []
        for name, hs in groups.items():
            if len(hs) == 1:
                resolved.append(hs[0])
                continue
            # 多实体：按朝代过滤消歧
            if dynasty_filter:
                cand = [h for h in hs if h.dynasty and h.dynasty in dynasty_filter]
                if len(cand) == 1:
                    resolved.append(cand[0])
                    continue
            # 无法消歧 → 全部保留（由调用方判定进 candidates）
            resolved.extend(hs)
        return resolved

    def _trim_history(self, history: list[dict]) -> list[dict]:
        """只保留最近 history_max_turns 个 user 轮及其后的助手消息。"""
        max_n = self.history_max_turns
        user_idx = [i for i, t in enumerate(history) if t.get("role") == "user"]
        if not user_idx:
            return history[-max_n:]
        if len(user_idx) <= max_n:
            return history
        return history[user_idx[-max_n]:]

    def trim_history(self, history: list) -> list[dict]:
        """公开历史裁剪（与 understand 内部口径一致，供缓存键等外部使用）。

        避免缓存键基于“未裁剪原文”而实际上下文相同（超出裁剪窗口的历史）
        的两次请求互不命中的效率损耗。
        """
        return self._trim_history([self._turn(t) for t in history or []])

    # ---- 主流程 ----
    def understand(self, question: str, history: Optional[list] = None,
                   corrected: Optional[list[CorrectedEntity]] = None,
                   filters: Optional[Filters] = None) -> F02Output:
        history = history or []
        corrected = corrected or []
        # history 元素规整为 dict（兼容 HistoryTurn dataclass 与 dict）
        history = [self._turn(t) for t in history]
        # 后端按配置裁剪历史，避免前端超发导致缓存键/指代上下文膨胀
        history = self._trim_history(history)

        # 1) 指代消解（多轮）：先用词典在"上一轮实体"上补全指代
        coref_name = clf.detect_coref_mention(question)
        resolved_question = question
        if coref_name and history:
            last_entities = self._resolve_coref(coref_name, history)
            for e in last_entities:
                resolved_question = question.replace(coref_name, e.standard_name, 1)
                break

        # 2) 词典识别（对指代还原后的问题；与 1) 同一字符串，无需二次匹配）
        raw_hits = self.matcher.match(resolved_question)

        # 3) 朝代过滤器识别
        dynasty_terms = self.matcher._dynasty_terms
        dyn_filters = clf.extract_dynasty_filter(question, dynasty_terms)
        # 合并外部 filters（前端显式）与问题内识别
        filters_dynasty = list((filters.dynasty if filters else []) or [])
        for d in dyn_filters:
            if d not in filters_dynasty:
                filters_dynasty.append(d)
        f = Filters(dynasty=filters_dynasty,
                    event_type=list((filters.event_type if filters else []) or []))

        # 4) 候选与歧义：同 mention 多实体降级为 candidates
        entities: list[EntityRef] = []
        candidates: list[EntityCandidate] = []
        seen_names: set[str] = set()
        resolved = self._resolve_ambiguity(raw_hits, filters_dynasty)
        # 按 mention 分组构造 candidates（含多实体歧义项）
        mention_groups = self.matcher.group_by_mention(raw_hits)
        for mention, hs in mention_groups:
            if len(hs) > 1:
                opts = [
                    CandidateOption(
                        name=h.standard_name, standard_name=h.standard_name,
                        confidence=h.confidence, dynasty=h.dynasty,
                        event_type=h.event_type, entity_id=h.entity_id,
                    )
                    for h in hs
                ]
                candidates.append(EntityCandidate(
                    mention=mention,
                    entity_type=hs[0].type,
                    options=opts,
                ))
        # entities：取消歧后命中（每 mention 一个首选）
        for h in resolved:
            if h.name in seen_names:
                continue
            seen_names.add(h.name)
            entities.append(EntityRef(
                name=h.name,
                type=h.type,
                standard_name=h.standard_name,
                confidence=h.confidence,
                entity_id=h.entity_id,
                dynasty=h.dynasty,
            ))

        # 5) 应用 corrected_entities（add/replace/remove）
        entities, candidates = self._apply_corrections(entities, candidates, corrected)

        # 6) 问题类型判定
        qtype = self._decide_type(question, entities, history)

        # 7) rewritten_question：指代还原 + 实体全名化
        rewritten = self._build_rewritten(resolved_question, question, entities)

        return F02Output(
            rewritten_question=rewritten,
            question_type=qtype,
            entities=entities,
            candidates=candidates,
            filters=f,
        )

    # ---- 指代消解 ----
    def _resolve_coref(self, coref: str, history: list) -> list[EntityHit]:
        """从历史问题中找回上一轮命中的标准实体。"""
        # 简化：对最近一条历史问题重新匹配词典，取其首个事件
        for turn in history:
            td = self._turn(turn)
            if td.get("role") != "user":
                continue
            text = td.get("content") or ""
            hits = self.matcher.match(text)
            if hits:
                # 事件优先
                ev = [h for h in hits if h.type == "事件"]
                if ev:
                    return ev
                return hits[:1]
        return []

    def _decide_type(self, question: str, entities: list[EntityRef],
                     history: list) -> QuestionType:
        etypes = [e.type or "" for e in entities]
        # 指代 + 历史：若只有单指代问题，继承最近问题类型（简化规则）
        if clf.detect_coref_mention(question) and history:
            # 用上一轮问题类型
            last = [self._turn(t) for t in history if self._turn(t).get("role") == "user"]
            if last:
                prev_q = last[-1].get("content") or ""
                prev_hits = self.matcher.match(prev_q)
                prev_types = [h.type for h in prev_hits]
                if prev_types:
                    return clf.classify(prev_q, prev_types, True)
        return clf.classify(question, etypes, bool(history))

    def _build_rewritten(self, resolved_q: str, original: str,
                         entities: list[EntityRef]) -> str:
        """改写：把识别到的 mention 替换为标准全名（如"涿鹿"→"涿鹿之战"不会误做，
        仅当 mention 与标准名不同时替换；指代已还原）。
        无实体时返回原问题。
        """
        if not entities:
            return original
        out = resolved_q
        for e in entities:
            if e.name and e.standard_name and e.name != e.standard_name:
                out = out.replace(e.name, e.standard_name, 1)
        return out

    # ---- 纠正应用 ----
    def _apply_corrections(self, entities: list[EntityRef],
                           candidates: list[EntityCandidate],
                           corrected: list[CorrectedEntity]):
        for c in corrected:
            act = c.action.value if hasattr(c.action, "value") else c.action
            if act == "remove":
                entities = [e for e in entities
                            if not (e.name == c.original or e.standard_name == c.original)]
            elif act == "replace":
                entities = [
                    EntityRef(name=e.name,
                              type=c.entity_type or e.type,
                              standard_name=c.replacement,
                              confidence=e.confidence,
                              dynasty=e.dynasty)
                    if (e.name == c.original or e.standard_name == c.original) else e
                    for e in entities
                ]
            elif act == "add":
                # add 的 name = standard_name（契约规定前端回传标准名）
                nm = c.name or c.replacement
                if nm and not any(e.standard_name == nm for e in entities):
                    # 尝试从词典补充 entity_id / 朝代
                    eid, dtype = None, c.entity_type
                    for ent in self.matcher._by_name.get(nm, []):
                        eid = ent["entity_id"]
                        dtype = dtype or ent["type"]
                        break
                    entities.append(EntityRef(
                        name=nm, type=dtype, standard_name=nm,
                        confidence="high", entity_id=eid,
                    ))
        return entities, candidates
