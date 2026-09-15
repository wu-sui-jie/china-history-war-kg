"""F02 问题理解编排（server/query/understand.py）。

执行链（默认词典优先，无 LLM）：
  1. 词典/规则实体识别（DictionaryMatcher.match）；
  2. 同名歧义降级：同一 mention 多实体 → 全部进 candidates，entities 只取首选项；
     若 filters 带朝代 → 自动按朝代消歧；
  3. 多轮指代消解（结合 history，把"它/这场战争"还原为上一轮事件实体）；
  4. 应用 corrected_entities（add/replace/remove）纠正重查；
  5. 判定问题类型 + 抽取朝代过滤器；
  6. 生成 rewritten_question（指代还原 + 实体全名化）。

LLM 兜底（RAGv5 §4.5 已实现）：词典**完全未命中**时，用一次结构化调用让模型抽取
"实体名 + 类型"（`server/query/prompts.py`），随后走与词典命中等价的后续流程。
开关 `ENABLE_LLM_ENTITY_FALLBACK`（默认关闭）、独立超时 `LLM_ENTITY_TIMEOUT_SECONDS`（默认 8 s，
避免吃满 F06 的首 Token 预算）；调用失败/超时/解析失败一律降级回词典结果（返回空实体，不报错）。
是否触发记在 `F02Output.llm_entity_used`（进 entities 事件与评测 trace）。
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
from server.query import prompts as q_prompts
from server.query.dictionary_matcher import DictionaryMatcher, EntityHit


def _dynasty_hit(entity_dynasty: Optional[str], bias: list[str]) -> bool:
    """实体朝代是否命中问句提到的朝代（宽松比对：唐 ≡ 唐朝 ≡ 唐代）。

    只在"同名候选之间做偏好选择"时使用；不参与证据过滤（见 _resolve_ambiguity 说明）。
    """
    d = (entity_dynasty or "").strip()
    if not d:
        return False
    for b in bias or []:
        q = (b or "").strip()
        if not q:
            continue
        if q == d:
            return True
        short, long_ = (q, d) if len(q) <= len(d) else (d, q)
        if long_.startswith(short) and long_[len(short):] in ("朝", "代", "王朝"):
            return True
    return False


class QuestionUnderstanding:
    def __init__(self, matcher: DictionaryMatcher,
                 llm_client: Optional[object] = None,
                 enable_llm: bool = False,
                 history_max_turns: int = 4):
        self.matcher = matcher
        self.llm = llm_client
        self.enable_llm = enable_llm and llm_client is not None
        self.history_max_turns = max(1, history_max_turns)
        # LLM 兜底结果缓存（问句 → [{name,type}]）：同题重复提问不重复付费
        self._llm_cache: dict[str, list[dict]] = {}
        self._llm_cache_max = 256

    @staticmethod
    def _turn(turn) -> dict:
        """history 轮次统一转 dict（兼容 HistoryTurn dataclass 与 dict）。"""
        if hasattr(turn, "to_dict"):
            return turn.to_dict()
        return turn if isinstance(turn, dict) else {"role": "", "content": ""}

    # ---- 歧义消解 ----
    @staticmethod
    def _resolve_ambiguity(hits: list[EntityHit],
                           dynasty_filter: list[str],
                           dynasty_bias: Optional[list[str]] = None,
                           ) -> tuple[list[EntityHit], bool]:
        """同名多实体消歧，返回 (命中排序, 是否由问句朝代选定)。

        优先级：显式筛选（硬过滤，取唯一命中）＞ 问句提到的朝代（**偏好**，命中者前置）
        ＞ 保留全部（首个成为 entities，其余仍进 candidates 供页面纠正）。

        问句朝代只做偏好、不做硬过滤：硬过滤会把"所属朝代与问句不同"的事件整题剔除，
        v4 已实测踩坑（问"商朝"时鸣条之战属夏 → 全链为空转拒答，见 classifier 注释）。
        这里只是把同名候选里朝代相符的那条排到前面，候选集合不变。
        """
        bias = [b for b in (dynasty_bias or []) if b]
        groups: dict[str, list[EntityHit]] = {}
        for h in hits:
            groups.setdefault(h.name, []).append(h)
        resolved: list[EntityHit] = []
        picked_by_dynasty = False
        for name, hs in groups.items():
            if len(hs) == 1:
                resolved.append(hs[0])
                continue
            # 多实体：显式筛选朝代（硬）优先
            if dynasty_filter:
                cand = [h for h in hs if h.dynasty and h.dynasty in dynasty_filter]
                if len(cand) == 1:
                    resolved.append(cand[0])
                    continue
            # 次之：问句提到的朝代（偏好）——命中者前置，不改候选集合
            if bias:
                pref_idx = [i for i, h in enumerate(hs) if _dynasty_hit(h.dynasty, bias)]
                if pref_idx:
                    picked_by_dynasty = picked_by_dynasty or pref_idx[0] != 0
                    first = set(pref_idx)
                    resolved.extend(hs[i] for i in pref_idx)
                    resolved.extend(h for i, h in enumerate(hs) if i not in first)
                    continue
            # 无法消歧 → 全部保留（由调用方判定进 candidates）
            resolved.extend(hs)
        return resolved, picked_by_dynasty

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

        # 3) 问句朝代识别 → dynasty_bias（仅排序加权，不作硬过滤）；
        #    显式筛选（F01 下拉）仍走 filters.dynasty 硬过滤。
        #    硬过滤会把"被问到的朝代"连同事件本身剔除（如问"商朝"时鸣条之战属夏），
        #    见 classifier.extract_dynasty_mentions 的语义说明。
        dynasty_aliases = self.matcher.dynasty_alias_map or self.matcher._dynasty_terms
        dynasty_bias = clf.extract_dynasty_mentions(
            question, dynasty_aliases,
            entity_mentions=[h.name for h in raw_hits],
        )
        filters_dynasty = list((filters.dynasty if filters else []) or [])
        # 显式筛选优先：已硬过滤的朝代不再重复作为偏置
        dynasty_bias = [d for d in dynasty_bias if d not in filters_dynasty]
        f = Filters(dynasty=filters_dynasty,
                    event_type=list((filters.event_type if filters else []) or []))

        # 4) 候选与歧义：同 mention 多实体降级为 candidates
        entities: list[EntityRef] = []
        candidates: list[EntityCandidate] = []
        seen_names: set[str] = set()
        resolved, dynasty_disambiguated = self._resolve_ambiguity(
            raw_hits, filters_dynasty, dynasty_bias)
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

        # 5.5) LLM 兜底（RAGv5 §4.5）：词典完全未命中且开关打开时，用模型抽实体。
        #      触发条件保守：raw_hits 为空（词典一条都没命中）才走；失败即降级不报错。
        llm_entity_used = False
        if q_prompts.should_fallback(dictionary_hits=len(raw_hits),
                                     enable_llm=self.enable_llm,
                                     llm_client=self.llm):
            fb = self._llm_entity_fallback(resolved_question)
            if fb:
                llm_entity_used = True
                entities = fb
                candidates = []

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
            dynasty_bias=dynasty_bias,
            llm_entity_used=llm_entity_used,
            dynasty_disambiguated=dynasty_disambiguated,
        )

    # ---- LLM 兜底（词典完全未命中）----
    def _llm_entity_fallback(self, question: str) -> list[EntityRef]:
        """用 LLM 抽取实体；失败/超时/解析失败返回空列表（调用方走降级）。

        两层保护：客户端自身吞异常是它的事，这里再兜一层——契约是"兜底永不抛错"，
        不能依赖调用方实现是否规矩。失败结果**不缓存**（避免一次抖动被长期记住）。
        """
        key = question.strip()
        cached = self._llm_cache.get(key)
        if cached is None:
            extract = getattr(self.llm, "extract", None)
            try:
                cached = extract(question) if callable(extract) else []
            except Exception:  # noqa: BLE001
                return []
            if not isinstance(cached, list):
                cached = []
            if len(self._llm_cache) >= self._llm_cache_max:
                self._llm_cache.clear()
            self._llm_cache[key] = cached
        return [
            EntityRef(
                name=str(it.get("name")),
                type=str(it.get("type")),
                standard_name=str(it.get("name")),   # 词典外实体：标准名即名字本身
                confidence="low",                    # 兜底命中给最低置信度（词典外实体，entity_id 为空）
                entity_id=None,
                dynasty=None,
            )
            for it in cached
            if it.get("name") and it.get("type")
        ]

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
