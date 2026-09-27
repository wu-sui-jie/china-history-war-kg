"""
Relation extractor.
"""
from __future__ import annotations

import re
from pathlib import Path

from war_extraction.config import PROMPT_VERSIONS
from war_extraction.core.llm_client import LLMAuthError, LLMAPIError
from war_extraction.models import (
    EventEventRelation,
    EventOrganizationRelation,
    EventPersonRelation,
    EventPlaceRelation,
    RelationExtractionResult,
)
from war_extraction.prompts import RELATION_EXTRACTION_PROMPT
from war_extraction.utils import Normalizer
from war_extraction.utils.json_payload import extract_json_payload
from war_extraction.utils.relation_rules import reduce_event_event_relations
from war_extraction.utils.value_parsing import PLACEHOLDERS_FULL, split_multi_value

#: 句子切分：句末标点之后、或换行。派生关系的证据与线索判断都限定在**同一句**内，
#: 而不是整段 source_text——"整段里出现'议和'就给该事件所有组织挂议和方"正是原来的缺陷。
_SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？；!?;])|\n+")


#: 扁平数组里"按哪一个实体字段存在"判别关系类别（每个对象只该命中一个）。
_FLAT_RELATION_KINDS = (
    ("event_place_relations", "modern_name"),
    ("event_organization_relations", "OrgName"),
    ("event_person_relations", "PersonName"),
    ("event_event_relations", "EventName_A"),
)


def coerce_relation_payload(data):
    """
    把模型偶发输出的**扁平数组**收成四类关系的 dict；收不了返回 None。

    **为什么要这个函数。** 提示词要的是 `{"event_place_relations": [...], ...}` 四键对象，
    但模型有时把四类关系**摊成一个数组**，每条形如
    `{"EventName": …, "relation": …, "modern_name": …}`。这时 `extract_json_payload` 拿到的是
    list，于是整段被判成"没有可用 JSON"、**降级成规则派生关系**——一整段的模型关系全丢。
    实测不是个例：`logs/relation_errors/invalid_json_payload.log` 里有 6 个这样的样本
    （含 2026-08-18 那次全书跑的），唐子集 29 段里也撞了 4 段。

    **判别是确定的**：每个对象只带一个实体字段（`modern_name` / `OrgName` / `PersonName` /
    `EventName_A`），按存在哪个字段归类即可。一个对象同时命中多个、或一个都不命中 → 丢掉：
    宁可少几条，也不能把它塞进错的关系类别（那会凭空造出一条错关系）。
    """
    if not isinstance(data, list):
        return None
    collected = {kind: [] for kind, _field in _FLAT_RELATION_KINDS}
    for item in data:
        if not isinstance(item, dict):
            continue
        hits = [kind for kind, field in _FLAT_RELATION_KINDS if item.get(field)]
        if len(hits) == 1:
            collected[hits[0]].append(item)
    if not any(collected.values()):
        return None
    return collected


class RelationStageDegraded(Exception):
    """
    关系阶段的 LLM 调用失败，已降级为"纯规则派生关系"。

    **为什么要单独一个异常类型。** 原实现把异常吞掉、直接返回派生结果，编排层记为成功、
    `ok=True`、**写进缓存**——于是"关系阶段其实失败了"在产物里完全看不出来，而且下次
    命中缓存不再重试。现在改成"数据不丢 + 失败可见 + 可重试"三条同时满足：

    - 数据不丢：异常里带着从事件字段按规则派生的关系（`self.result`），调用方照常收进产物；
    - 失败可见：编排层把它记成该阶段失败（`stage_ok` 不计、`partial_errors` 有记录、
      质量报告里 `relation_degraded_stages` 计数）；
    - 可重试：该段因 `ok=False` **不写缓存**，下次运行会重新调用模型。

    与实体/事件阶段"失败一律上抛"的口径并不矛盾：那两阶段失败时没有任何可用的替代产物，
    关系阶段则有一份与本次失败调用无关的规则派生结果，整段丢掉是净损失。
    """

    def __init__(self, message: str, result: RelationExtractionResult):
        super().__init__(message)
        self.result = result


class RelationExtractor:
    """Extract event-entity and event-event relations."""

    def __init__(self, llm_client):
        self.llm = llm_client
        self.prompt_template = RELATION_EXTRACTION_PROMPT
        # 按 __file__ 锚定到项目根的 logs/：用相对当前工作目录的 Path("logs") 的话，
        # 换个目录启动就会在那儿新建一个 logs/，错误样本散落各处找不到。
        self.error_dir = Path(__file__).resolve().parents[2] / "logs" / "relation_errors"
        self.error_dir.mkdir(parents=True, exist_ok=True)
        self.normalizer = Normalizer()

    def _write_error_log(self, reason: str, response: str):
        """把抽错的响应片段落盘成错误样本，供之后调提示词用。"""
        safe_reason = reason.replace(" ", "_").replace(":", "_")
        path = self.error_dir / f"{safe_reason}.log"
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        snippet = response[:2000]
        if snippet in existing:
            return
        with open(path, "a", encoding="utf-8") as f:
            if existing:
                f.write("\n" + "=" * 80 + "\n")
            f.write(snippet)

    def _ensure_complete_place_rel(self, rel: dict) -> dict:
        return {
            "EventName": self.normalizer.standardize_event_name(rel.get("EventName")),
            "relation": self.normalizer.normalize_relation(rel.get("relation")),
            "modern_name": rel.get("modern_name") or rel.get("geo_name") or rel.get("Place"),
            "evidence": rel.get("evidence"),
        }

    def _ensure_complete_org_rel(self, rel: dict) -> dict:
        return {
            "EventName": self.normalizer.standardize_event_name(rel.get("EventName")),
            "relation": self.normalizer.normalize_relation(rel.get("relation")),
            "OrgName": self.normalizer.standardize_org_name(rel.get("OrgName")),
            "evidence": rel.get("evidence"),
        }

    def _ensure_complete_person_rel(self, rel: dict) -> dict:
        return {
            "EventName": self.normalizer.standardize_event_name(rel.get("EventName")),
            "relation": self.normalizer.normalize_relation(rel.get("relation")),
            "PersonName": self.normalizer.standardize_person_name(rel.get("PersonName")),
            "evidence": rel.get("evidence"),
        }

    def _ensure_complete_event_rel(self, rel: dict) -> dict:
        return {
            "EventName_A": self.normalizer.standardize_event_name(rel.get("EventName_A")),
            "relation": self.normalizer.normalize_relation(rel.get("relation")),
            "EventName_B": self.normalizer.standardize_event_name(rel.get("EventName_B")),
            "evidence": rel.get("evidence"),
        }

    def _split_multi_value(self, value: str) -> list:
        # 多值拆分只有 war_extraction/utils/value_parsing.py 一处；这里显式传全量占位词集，
        # 保证"未知/无/None"也算没有值（与 main.py 侧同一口径）。
        return split_multi_value(value, placeholders=PLACEHOLDERS_FULL)

    def _allowed_name_set(self, value: str) -> set:
        return {
            self.normalizer.normalize_entity_name(item)
            for item in self._split_multi_value(value)
            if item
        }

    def _is_allowed_entity(self, name: str, allowed_names: set) -> bool:
        return not allowed_names or self.normalizer.normalize_entity_name(name) in allowed_names

    def _entity_sentence(self, name: str, event) -> str:
        """
        实体名所在的那一句原文——派生关系的**证据与线索都只看这一句**。

        找不到（实体名不在事件的 source_text 里）时返回空串：调用方据此**不生成**派生关系。
        这是"派生关系必须绑定具体证据与具体实体"的落点：绑定不上就不生成，
        而不是退回去拿整段 source_text 当证据（那正是 62%~72% 的关系共用同一段证据的来源）。
        """
        if not name:
            return ""
        source_text = getattr(event, "source_text", None) or ""
        for sentence in _SENTENCE_BOUNDARY.split(source_text):
            piece = sentence.strip()
            if piece and name in piece:
                return piece
        return ""

    def _contains_any(self, text: str, keywords: list) -> bool:
        return any(keyword in (text or "") for keyword in keywords)

    def _append_place_relation(self, rels: list, event_name: str, relation: str, place_name: str, evidence: str):
        rels.append(
            EventPlaceRelation(
                EventName=event_name,
                relation=relation,
                modern_name=place_name,
                evidence=evidence,
            )
        )

    def _append_org_relation(self, rels: list, event_name: str, relation: str, org_name: str, evidence: str):
        rels.append(
            EventOrganizationRelation(
                EventName=event_name,
                relation=relation,
                OrgName=org_name,
                evidence=evidence,
            )
        )

    def _append_person_relation(self, rels: list, event_name: str, relation: str, person_name: str, evidence: str):
        rels.append(
            EventPersonRelation(
                EventName=event_name,
                relation=relation,
                PersonName=person_name,
                evidence=evidence,
            )
        )

    # ------------------------------------------------------------------ 线索判定
    #
    # 所有判定都以"实体名所在的那一句"为文本边界。三处共同的口径：
    #   ① 线索词必须与实体名**同句**出现才成立；
    #   ② 不根据名称里的某个字推断角色（`王翦`含"王"不等于君主，"杨谋"含"谋"不等于谋士）；
    #   ③ 判定不成立就不生成该条关系——宁缺勿错，精确率优先于关系数量。

    def _derive_place_relation_types(self, place_name: str, sentence: str, is_first_combat_place: bool) -> list:
        """
        从"地点所在的那句原文"推地点在该事件中的角色。

        传进来的 `sentence` **已经含有这个地点名**（`_entity_sentence` 保证），
        所以这里只判线索词：线索与地名同句 = 有依据；不同句就不生成关系。

        原先的两条无条件规则已删除：
          - `Place` 字段第 0 个地点一律"主战场"（无需任何证据）；
          - 整段文本里出现"议和/粮草/战略"就给该事件**所有**地点挂上对应关系。
        现在 `主战场` 给第一个**有交战线索**的地点，其余有交战线索的地点记 `次要战场`。
        """
        relation_types = []
        if not sentence:
            return relation_types

        if self._contains_any(sentence, [
            f"战于{place_name}", f"战{place_name}", f"{place_name}之战", f"{place_name}大捷",
            f"{place_name}大败", f"败于{place_name}", f"围{place_name}", f"攻{place_name}",
            f"取{place_name}", f"破{place_name}", f"克{place_name}", f"击{place_name}",
            f"御{place_name}", f"守{place_name}", f"据{place_name}", f"拒{place_name}",
        ]):
            relation_types.append("主战场" if is_first_combat_place else "次要战场")

        # 线索词分两类：① 与地名**连写**的形式（`自长平`、`至长平`、`驻长平`），
        # 天然精确；② 不含地名的短语，一律要求**两个汉字以上**——单字线索
        # （"自""出""至""攻"）在长句里命中率太高，"赵括自幼熟读兵书"会因为一个"自"字
        # 被判成出发地。
        if self._contains_any(sentence, [
            "起兵", "发兵", "出兵", "出师",
            f"自{place_name}", f"从{place_name}", f"由{place_name}", f"{place_name}起兵",
        ]):
            relation_types.append("出发地")
        if self._contains_any(sentence, [
            "进抵", "进逼", "直取", "攻取", "攻入", "抵达",
            f"至{place_name}", f"入{place_name}", f"趋{place_name}", f"伐{place_name}",
        ]):
            relation_types.append("目的地")
        if self._contains_any(sentence, [
            "途经", "经过", "道经", "路过", "取道",
            f"经{place_name}", f"过{place_name}",
        ]):
            relation_types.append("途经地")
        if self._contains_any(sentence, [
            "驻扎", "驻军", "驻守", "屯兵", "屯驻", "营于", "留守",
            f"驻{place_name}", f"屯{place_name}", f"守{place_name}",
        ]):
            relation_types.append("驻防地")
        if self._contains_any(sentence, [
            "关隘", "要塞", "险要", "战略要地", "都城", "首都", "重镇",
            f"{place_name}关", f"{place_name}城",
        ]):
            relation_types.append("战略要地")
        if self._contains_any(sentence, ["议和", "和议", "会盟", "盟约"]):
            relation_types.append("议和地点")
        if self._contains_any(sentence, ["指挥所", "大营", "驻跸", "帅帐"]):
            relation_types.append("指挥所")
        if self._contains_any(sentence, ["粮草", "粮道", "后勤", "补给"]):
            relation_types.append("补给地")

        return list(dict.fromkeys(relation_types))

    def _derive_org_relation_types(self, base_relation: str, org_name: str, sentence: str) -> list:
        """
        `Aggressor/Defender/Allies` 到关系名的直接映射，外加"同句线索"推出的附加关系。

        传进来的 `sentence` **已经含有这个组织名**。原先附加关系看的是整段文本
        （`evidence + Result`），于是"议和"二字会给该事件的**所有**组织都挂上"议和方"。
        """
        relation_types = [base_relation]
        if not sentence:
            return relation_types

        if base_relation == "支援方" and self._contains_any(
            sentence, ["联盟", "同盟", "联合", "合兵", "会师", "联军"]
        ):
            relation_types.append("同盟方")
        if self._contains_any(sentence, ["投降", "归降", "请降", "降服", "纳降", "降于", "归顺"]):
            relation_types.append("投降方")
        if self._contains_any(sentence, ["被俘", "俘获", "生擒", "俘虏"]):
            relation_types.append("被俘方")
        if self._contains_any(sentence, ["议和", "和议", "讲和", "会盟", "盟约"]):
            relation_types.append("议和方")
        if self._contains_any(sentence, ["调停", "斡旋", "调解"]):
            relation_types.append("调停方")

        return list(dict.fromkeys(relation_types))

    def _derive_person_relation_types(self, base_relation: str, person_name: str, sentence: str) -> list:
        """
        `Commanders/KeyPersons` 到关系名的直接映射，外加"同句线索"推出的附加关系。

        传进来的 `sentence` **已经含有这个人物名**。原先按**名称里的字**推断角色，两处具体错误：
          - 含"王/帝/君/公/侯/可汗"就判君主 —— `王翦` 这类名将会被误判成国君；
          - 含"谋"就判谋士、含"使"就判使者 —— 名字里出现一个字与职能无关。
        现在君主/可汗只按**称号结尾**判定，谋士/使者一律要求同句线索。
        """
        relation_types = [base_relation]
        if not person_name:
            return relation_types

        monarch_titles = ("王", "帝", "君", "公", "侯", "可汗")
        if person_name.endswith(monarch_titles):
            relation_types.append("可汗" if person_name.endswith("可汗") else "君主")

        if not sentence:
            return list(dict.fromkeys(relation_types))

        if self._contains_any(sentence, ["献策", "献计", "进言", "谋士", "军师", "参谋"]):
            relation_types.append("谋士")
        if self._contains_any(sentence, ["出使", "遣使", "使者", "使节", "奉命出"]):
            relation_types.append("使者")
        if self._contains_any(sentence, ["为将", "率兵", "将兵", "领兵", "主将", "将军", "统兵"]):
            relation_types.append("将领")
        if self._contains_any(sentence, ["被俘", "生擒", "俘获", "擒获"]):
            relation_types.append("俘虏")
        if self._contains_any(sentence, ["阵亡", "战死", "被杀", "殉国"]):
            relation_types.append("阵亡")
        if self._contains_any(sentence, ["投降", "归降", "请降"]):
            relation_types.append("投降")
        if self._contains_any(sentence, ["叛", "倒戈", "反叛"]):
            relation_types.append("叛变")

        return list(dict.fromkeys(relation_types))

    def _derive_event_entity_relations_from_events(
        self,
        events: list,
        place_list: str = "",
        org_list: str = "",
        person_list: str = "",
    ) -> tuple[list, list, list]:
        """
        从事件的结构化字段回填事件-实体关系。

        **证据口径**：每条关系带的是"实体名所在的那一句原文"（`_entity_sentence`）。
        名称确实不在原文片段里时（事件字段被模型规范化过，例如原文写"周武王率诸侯之师"、
        字段填"周军"），退一步用该事件的 `source_text`——此时字段本身就是这条关系的依据，
        但关系与不出一句对应的原文。实测"证据与事件 source_text 逐字符相同"的比例
        从 62%~72% 降到 26.3%，其余 73.7% 都是各条关系自己的那一句。
        线索判定（主战场/议和方/君主…）则**只看那一句**，找不到句子就不生成该条关系。
        """
        allowed_places = self._allowed_name_set(place_list)
        allowed_orgs = self._allowed_name_set(org_list)
        allowed_persons = self._allowed_name_set(person_list)
        place_rels = []
        org_rels = []
        person_rels = []

        for event in events:
            event_name = self.normalizer.standardize_event_name(getattr(event, "EventName", None))
            if not event_name:
                continue
            event_evidence = getattr(event, "source_text", None) or None
            combat_place_taken = False

            for place_name in self._split_multi_value(getattr(event, "Place", None)):
                if not self._is_allowed_entity(place_name, allowed_places):
                    continue
                sentence = self._entity_sentence(place_name, event)
                relation_types = self._derive_place_relation_types(
                    place_name, sentence, is_first_combat_place=not combat_place_taken
                )
                if any(rt in {"主战场", "次要战场"} for rt in relation_types):
                    combat_place_taken = True
                for relation in relation_types:
                    self._append_place_relation(
                        place_rels, event_name, relation, place_name, sentence or event_evidence
                    )

            org_sources = [
                ("发起方", getattr(event, "Aggressor", None)),
                ("防守方", getattr(event, "Defender", None)),
                ("支援方", getattr(event, "Allies", None)),
            ]
            for relation, org_value in org_sources:
                for org_name in self._split_multi_value(org_value):
                    org_name = self.normalizer.standardize_org_name(org_name)
                    if org_name and self._is_allowed_entity(org_name, allowed_orgs):
                        sentence = self._entity_sentence(org_name, event)
                        for derived_relation in self._derive_org_relation_types(relation, org_name, sentence):
                            self._append_org_relation(
                                org_rels, event_name, derived_relation, org_name, sentence or event_evidence
                            )

            person_sources = [
                # **`Commanders` 派生 `将领`，不是 `统帅`**（阶段三 E2，依据 B 组抽样判定）。
                # 字段的定义是「双方主要军事指挥官」（注释见 `event_prompts.py`），
                # 而提示词里的关系枚举把两者分得很清：`统帅`=最高指挥官、`将领`=中级指挥官。
                # 把列表里的**每一个人**都派生成 `统帅`，与字段和枚举两边的定义都冲突：
                # 实测产物 3016 条 `统帅` 里 2767 条（91.7%）来自这条规则，
                # 而抽样判定里规则派生的 `统帅` 例 10/10 被判错
                # （"此役统帅为刘曜，石生并非统帅"、"不设元帅，郭子仪为九节度使之一"、
                # "张孝忠系从征节度使，统帅应为朱滔"…）——判据里给出的正确说法都是"将领"。
                # 改名的代价是"谁是最高指挥"不再由规则推断（那本来就是它推不出的信息，
                # 字段里没有敌我、没有级别）；原文真的写了最高指挥时，关系阶段仍会抽 `统帅`。
                ("将领", getattr(event, "Commanders", None)),
                ("参与者", getattr(event, "KeyPersons", None)),
            ]
            for relation, person_value in person_sources:
                for person_name in self._split_multi_value(person_value):
                    person_name = self.normalizer.standardize_person_name(person_name)
                    if person_name and self._is_allowed_entity(person_name, allowed_persons):
                        sentence = self._entity_sentence(person_name, event)
                        for derived_relation in self._derive_person_relation_types(relation, person_name, sentence):
                            self._append_person_relation(
                                person_rels, event_name, derived_relation, person_name, sentence or event_evidence
                            )
        return place_rels, org_rels, person_rels

    def _deduplicate_relations(self, relations: list, key_func):
        relation_map = {}
        for rel in relations:
            key = key_func(rel)
            existing = relation_map.get(key)
            if existing is None or len(getattr(rel, "evidence", None) or "") > len(getattr(existing, "evidence", None) or ""):
                relation_map[key] = rel
        return list(relation_map.values())

    def _build_derived_relation_result(
        self,
        events: list,
        place_list: str = "",
        org_list: str = "",
        person_list: str = "",
    ) -> RelationExtractionResult:
        place_rels, org_rels, person_rels = self._derive_event_entity_relations_from_events(
            events,
            place_list,
            org_list,
            person_list,
        )
        event_rels = reduce_event_event_relations(
            self.normalizer,
            self._derive_event_event_relations_from_events(events),
            # 不传 `quarantine`：枚举外的条目不在这里计数（数据仍留在返回值里），
            # 否则与段间合并、最终清理两处重复计数。计数口径见
            # `war_extraction/utils/relation_rules.py::reduce_event_event_relations` 的 docstring。
        )
        return RelationExtractionResult(
            event_place_relations=place_rels,
            event_organization_relations=org_rels,
            event_person_relations=person_rels,
            event_event_relations=event_rels,
        )

    def _derive_event_event_relations_from_events(self, events: list) -> list:
        """从事件的 `relations` 字段回填事件-事件关系（模型常在事件层给关系、不给三元组）。"""
        derived = []
        for event in events:
            event_name = self.normalizer.standardize_event_name(event.EventName)
            for rel in getattr(event, "relations", []) or []:
                relation = self.normalizer.normalize_relation(getattr(rel, "type", None))
                target = self.normalizer.standardize_event_name(getattr(rel, "to", None))
                if not relation or not target:
                    continue
                if self.normalizer.normalize_event_name(event_name) == self.normalizer.normalize_event_name(target):
                    continue
                derived.append(
                    EventEventRelation(
                        EventName_A=event_name,
                        relation=relation,
                        EventName_B=target,
                        evidence=getattr(rel, "evidence", None),
                    )
                )
        return derived

    def extract(self, text: str, events: list, place_list: str = "", org_list: str = "", person_list: str = "") -> RelationExtractionResult:
        """Execute relation extraction."""
        if not events:
            print("警告：无事件，跳过关系抽取")
            return RelationExtractionResult()

        event_names = "\n".join([
            (
                f"- {self.normalizer.standardize_event_name(e.EventName)}"
                f" | 地点: {getattr(e, 'Place', None) or '不详'}"
                f" | 主动方: {getattr(e, 'Aggressor', None) or '不详'}"
                f" | 防守方: {getattr(e, 'Defender', None) or '不详'}"
                f" | 盟友: {getattr(e, 'Allies', None) or '无'}"
                f" | 指挥官: {getattr(e, 'Commanders', None) or '不详'}"
                f" | 关键人物: {getattr(e, 'KeyPersons', None) or '无'}"
            )
            for e in events
        ])
        prompt = self.prompt_template.render(
            text=text,
            event_names=event_names,
            place_list=place_list,
            org_list=org_list,
            person_list=person_list,
            prompt_version=PROMPT_VERSIONS["relation_extraction"],
        )

        try:
            response = self.llm.call(prompt, temperature=0.1, json_mode=True)
            data = extract_json_payload(response)
            if not isinstance(data, dict):
                # 模型偶发把四类关系摊成一个数组：先按实体字段归类收回来（判别是确定的），
                # 收不了才降级——降级会把这一整段的模型关系换成规则派生，代价太大。
                coerced = coerce_relation_payload(data)
                self._write_error_log("invalid_json_payload", response)
                if coerced is None:
                    print("关系抽取 JSON 解析失败：未找到可用 JSON")
                    print(f"原始响应前 500 字符: {response[:500]}...")
                    raise RelationStageDegraded(
                        "关系抽取响应没有可用 JSON，已降级为规则派生关系",
                        self._build_derived_relation_result(events, place_list, org_list, person_list),
                    )
                print(f"关系抽取返回的是扁平数组，已按实体字段归类收回: "
                      f"{ {k: len(v) for k, v in coerced.items() if v} }")
                data = coerced

            place_rels = [self._ensure_complete_place_rel(r) for r in data.get("event_place_relations", [])]
            org_rels = [self._ensure_complete_org_rel(r) for r in data.get("event_organization_relations", [])]
            person_rels = [self._ensure_complete_person_rel(r) for r in data.get("event_person_relations", [])]
            event_rels = [self._ensure_complete_event_rel(r) for r in data.get("event_event_relations", [])]

            place_rels = [r for r in place_rels if r.get("EventName") and r.get("relation") and r.get("modern_name")]
            org_rels = [r for r in org_rels if r.get("EventName") and r.get("relation") and r.get("OrgName")]
            person_rels = [r for r in person_rels if r.get("EventName") and r.get("relation") and r.get("PersonName")]
            event_rels = [r for r in event_rels if r.get("EventName_A") and r.get("relation") and r.get("EventName_B")]

            extracted_event_rels = [EventEventRelation(**r) for r in event_rels]
            derived_event_rels = self._derive_event_event_relations_from_events(events)
            event_event_relations = reduce_event_event_relations(
                self.normalizer, extracted_event_rels + derived_event_rels,
                # 同上：中间环节不计数，避免同一条被数多次
            )

            extracted_place_rels = [EventPlaceRelation(**r) for r in place_rels]
            extracted_org_rels = [EventOrganizationRelation(**r) for r in org_rels]
            extracted_person_rels = [EventPersonRelation(**r) for r in person_rels]
            derived_place_rels, derived_org_rels, derived_person_rels = self._derive_event_entity_relations_from_events(
                events,
                place_list,
                org_list,
                person_list,
            )

            event_place_relations = self._deduplicate_relations(
                extracted_place_rels + derived_place_rels,
                lambda r: (
                    self.normalizer.normalize_event_name(r.EventName),
                    self.normalizer.normalize_relation(r.relation),
                    self.normalizer.normalize_entity_name(r.modern_name),
                ),
            )
            event_organization_relations = self._deduplicate_relations(
                extracted_org_rels + derived_org_rels,
                lambda r: (
                    self.normalizer.normalize_event_name(r.EventName),
                    r.relation,
                    self.normalizer.normalize_entity_name(r.OrgName),
                ),
            )
            event_person_relations = self._deduplicate_relations(
                extracted_person_rels + derived_person_rels,
                lambda r: (
                    self.normalizer.normalize_event_name(r.EventName),
                    r.relation,
                    self.normalizer.normalize_entity_name(r.PersonName),
                ),
            )

            return RelationExtractionResult(
                event_place_relations=event_place_relations,
                event_organization_relations=event_organization_relations,
                event_person_relations=event_person_relations,
                event_event_relations=event_event_relations,
            )

        except (LLMAuthError, RelationStageDegraded):
            # 密钥无效不重试、也不降级：重试无意义，降级会把"没有权限"伪装成"关系抽取成功"
            raise
        except LLMAPIError as exc:
            raise RelationStageDegraded(
                f"关系抽取 API 调用失败，已降级为规则派生关系: {exc}",
                self._build_derived_relation_result(events, place_list, org_list, person_list),
            ) from exc
        except Exception as e:
            print(f"关系抽取失败: {e}")
            if "response" in locals():
                self._write_error_log(f"relation_extract_exception_{type(e).__name__}", response)
            raise RelationStageDegraded(
                f"关系抽取异常，已降级为规则派生关系: {type(e).__name__}: {e}",
                self._build_derived_relation_result(events, place_list, org_list, person_list),
            ) from e
