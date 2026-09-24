"""
Relation extractor.
"""
from __future__ import annotations

import json
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


class RelationExtractor:
    """Extract event-entity and event-event relations."""

    def __init__(self, llm_client):
        self.llm = llm_client
        self.prompt_template = RELATION_EXTRACTION_PROMPT
        self.error_dir = Path("logs") / "relation_errors"
        self.error_dir.mkdir(parents=True, exist_ok=True)
        self.normalizer = Normalizer()

    def _write_error_log(self, reason: str, response: str):
        """Changed 2026-04-21 13:57:14 +08:00: Persist lightweight relation error samples for prompt tuning."""
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

    def _extract_json_payload(self, response: str):
        """Changed 2026-04-21 13:46:29 +08:00: Parse dict or list JSON payloads from relation output."""
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass

        decoder = json.JSONDecoder()
        for start, char in enumerate(response):
            if char not in "[{":
                continue
            try:
                data, _ = decoder.raw_decode(response[start:])
                return data
            except json.JSONDecodeError:
                continue
        return None

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
        if not value:
            return []
        normalized = str(value)
        for sep in ["、", "，", ",", "；", ";", "及", "与", "和", "/", " vs ", " VS ", "vs."]:
            normalized = normalized.replace(sep, "|")
        parts = [part.strip() for part in normalized.split("|")]
        return [
            part for part in parts
            if part and part not in {"不详", "未知", "null", "None", "无"}
        ]

    def _allowed_name_set(self, value: str) -> set:
        return {
            self.normalizer.normalize_entity_name(item)
            for item in self._split_multi_value(value)
            if item
        }

    def _is_allowed_entity(self, name: str, allowed_names: set) -> bool:
        return not allowed_names or self.normalizer.normalize_entity_name(name) in allowed_names

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

    def _derive_place_relation_types(self, place_name: str, place_index: int, event) -> list:
        evidence = getattr(event, "source_text", None) or ""
        result = getattr(event, "Result", None) or ""
        action = getattr(event, "Action", None) or ""
        text = f"{evidence} {result} {action}"
        relation_types = []

        if place_index == 0:
            relation_types.append("主战场")
        else:
            relation_types.append("次要战场")

        if place_name and self._contains_any(text, [f"自{place_name}", f"从{place_name}", f"由{place_name}", f"{place_name}起兵", f"{place_name}发兵"]):
            relation_types.append("出发地")
        if place_name and self._contains_any(text, [f"攻{place_name}", f"伐{place_name}", f"至{place_name}", f"入{place_name}", f"围{place_name}", f"取{place_name}", f"趋{place_name}"]):
            relation_types.append("目的地")
        if place_name and self._contains_any(text, [f"经{place_name}", f"过{place_name}", f"途经{place_name}", f"道{place_name}"]):
            relation_types.append("途经地")
        if place_name and self._contains_any(text, [f"驻{place_name}", f"屯{place_name}", f"守{place_name}", f"据{place_name}", f"保{place_name}"]):
            relation_types.append("驻防地")
        if place_name and self._contains_any(text, [f"{place_name}关", f"{place_name}城", "战略", "要地", "关隘", "都城", "首都"]):
            relation_types.append("战略要地")
        if place_name and self._contains_any(text, [f"于{place_name}议和", f"在{place_name}议和", f"于{place_name}会盟", f"在{place_name}会盟", "议和", "和议", "会盟"]):
            relation_types.append("议和地点")
        if place_name and self._contains_any(text, [f"指挥所设于{place_name}", f"大营在{place_name}", f"营于{place_name}", f"驻跸{place_name}"]):
            relation_types.append("指挥所")
        if place_name and self._contains_any(text, [f"粮道{place_name}", f"{place_name}补给", "粮草", "后勤", "补给"]):
            relation_types.append("补给地")

        return list(dict.fromkeys(relation_types))

    def _derive_org_relation_types(self, base_relation: str, org_name: str, event) -> list:
        evidence = getattr(event, "source_text", None) or ""
        result = getattr(event, "Result", None) or ""
        text = f"{evidence} {result}"
        relation_types = [base_relation]

        if base_relation == "支援方" and self._contains_any(text, ["联盟", "同盟", "联合", "合兵", "会师", "联军"]):
            relation_types.append("同盟方")
        if org_name and self._contains_any(text, [f"{org_name}降", f"{org_name}投降", f"{org_name}请降", "投降", "归降", "降服"]):
            relation_types.append("投降方")
        if org_name and self._contains_any(text, [f"俘{org_name}", f"{org_name}被俘", "被俘", "俘获"]):
            relation_types.append("被俘方")
        if self._contains_any(text, ["议和", "和议", "讲和", "会盟", "盟约"]):
            relation_types.append("议和方")
        if self._contains_any(text, ["调停", "斡旋", "调解"]):
            relation_types.append("调停方")

        return list(dict.fromkeys(relation_types))

    def _derive_person_relation_types(self, base_relation: str, person_name: str, event) -> list:
        evidence = getattr(event, "source_text", None) or ""
        result = getattr(event, "Result", None) or ""
        text = f"{person_name} {evidence} {result}"
        relation_types = [base_relation]

        if base_relation == "统帅" and person_name and self._contains_any(person_name, ["王", "帝", "君", "公", "侯", "可汗"]):
            relation_types.append("君主")
        person_specific_text = f"{evidence} {result}"
        commander_patterns = [f"{person_name}为将", f"{person_name}将兵", f"{person_name}率兵", f"{person_name}领兵", f"{person_name}主将", f"将{person_name}"]
        strategist_patterns = [f"{person_name}献策", f"{person_name}献计", f"{person_name}谋", f"谋士{person_name}", f"军师{person_name}"]
        envoy_patterns = [f"{person_name}出使", f"{person_name}遣使", f"{person_name}议和", f"使者{person_name}", f"遣{person_name}使"]

        if base_relation == "统帅" and self._contains_any(person_specific_text, commander_patterns):
            relation_types.append("将领")
        if "谋" in person_name or "军师" in person_name or self._contains_any(person_specific_text, strategist_patterns):
            relation_types.append("谋士")
        if "使" in person_name or self._contains_any(person_specific_text, envoy_patterns):
            relation_types.append("使者")

        captured_patterns = [f"俘{person_name}", f"{person_name}被俘", f"{person_name}为俘", f"生擒{person_name}", f"擒{person_name}"]
        killed_patterns = [f"{person_name}阵亡", f"{person_name}战死", f"{person_name}死", f"杀{person_name}", f"{person_name}被杀"]
        surrendered_patterns = [f"{person_name}投降", f"{person_name}归降", f"{person_name}请降", f"{person_name}降于"]
        betrayed_patterns = [f"{person_name}叛", f"{person_name}叛变", f"{person_name}反叛", f"{person_name}倒戈"]

        if self._contains_any(person_specific_text, captured_patterns):
            relation_types.append("俘虏")
        if self._contains_any(person_specific_text, killed_patterns):
            relation_types.append("阵亡")
        if self._contains_any(person_specific_text, surrendered_patterns):
            relation_types.append("投降")
        if self._contains_any(person_specific_text, betrayed_patterns):
            relation_types.append("叛变")
        if "可汗" in person_name:
            relation_types.append("可汗")

        return list(dict.fromkeys(relation_types))

    def _derive_event_entity_relations_from_events(
        self,
        events: list,
        place_list: str = "",
        org_list: str = "",
        person_list: str = "",
    ) -> tuple[list, list, list]:
        """Backfill rich event-entity relations from structured event fields."""
        allowed_places = self._allowed_name_set(place_list)
        allowed_orgs = self._allowed_name_set(org_list)
        allowed_persons = self._allowed_name_set(person_list)
        place_rels = []
        org_rels = []
        person_rels = []

        for event in events:
            event_name = self.normalizer.standardize_event_name(getattr(event, "EventName", None))
            evidence = getattr(event, "source_text", None) or getattr(event, "Remark", None) or event_name
            if not event_name:
                continue

            for place_index, place_name in enumerate(self._split_multi_value(getattr(event, "Place", None))):
                if self._is_allowed_entity(place_name, allowed_places):
                    for relation in self._derive_place_relation_types(place_name, place_index, event):
                        self._append_place_relation(place_rels, event_name, relation, place_name, evidence)

            org_sources = [
                ("发起方", getattr(event, "Aggressor", None)),
                ("防守方", getattr(event, "Defender", None)),
                ("支援方", getattr(event, "Allies", None)),
            ]
            for relation, org_value in org_sources:
                for org_name in self._split_multi_value(org_value):
                    org_name = self.normalizer.standardize_org_name(org_name)
                    if org_name and self._is_allowed_entity(org_name, allowed_orgs):
                        for derived_relation in self._derive_org_relation_types(relation, org_name, event):
                            self._append_org_relation(org_rels, event_name, derived_relation, org_name, evidence)

            person_sources = [
                ("统帅", getattr(event, "Commanders", None)),
                ("参与者", getattr(event, "KeyPersons", None)),
            ]
            for relation, person_value in person_sources:
                for person_name in self._split_multi_value(person_value):
                    person_name = self.normalizer.standardize_person_name(person_name)
                    if person_name and self._is_allowed_entity(person_name, allowed_persons):
                        for derived_relation in self._derive_person_relation_types(relation, person_name, event):
                            self._append_person_relation(person_rels, event_name, derived_relation, person_name, evidence)
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
        event_rels = self._normalize_event_event_relations(
            self._derive_event_event_relations_from_events(events)
        )
        return RelationExtractionResult(
            event_place_relations=place_rels,
            event_organization_relations=org_rels,
            event_person_relations=person_rels,
            event_event_relations=event_rels,
        )

    def _derive_event_event_relations_from_events(self, events: list) -> list:
        """Changed 2026-04-21 14:45:21 +08:00: Backfill event-event relations from events[].relations."""
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

    def _arbitrate_event_event_relation(self, rel: EventEventRelation) -> EventEventRelation:
        evidence = rel.evidence or ""
        relation = self.normalizer.normalize_relation(rel.relation)
        sequential_keywords = ["之后", "以后", "随后", "其后", "后来", "次年", "继而", "接着", "然后", "遂"]
        strong_causal_keywords = ["因此", "于是", "导致", "引发", "致使", "使得", "造成", "因而", "从而", "迫使"]
        parallel_keywords = ["同时", "并", "并且", "同年", "相继", "并发"]

        has_sequential_keyword = any(token in evidence for token in sequential_keywords)
        has_strong_causal_keyword = any(token in evidence for token in strong_causal_keywords)
        has_parallel_keyword = any(token in evidence for token in parallel_keywords)

        if relation == "因果关系" and not has_strong_causal_keyword:
            relation = "顺承关系" if has_sequential_keyword or evidence else "并列关系"
        elif relation == "顺承关系" and has_parallel_keyword and not has_sequential_keyword:
            relation = "并列关系"

        rel.relation = relation
        return rel

    def _normalize_event_event_relations(self, relations: list[EventEventRelation]) -> list[EventEventRelation]:
        """Changed 2026-04-21 16:46:12 +08:00: Canonicalize event-event labels, resolve direction conflicts, and drop self-loops/duplicates."""
        normalized = []
        by_pair = {}
        relation_priority = {"因果关系": 3, "顺承关系": 2, "并列关系": 1}
        for rel in relations:
            rel.EventName_A = self.normalizer.standardize_event_name(rel.EventName_A)
            rel.EventName_B = self.normalizer.standardize_event_name(rel.EventName_B)
            rel.relation = self.normalizer.normalize_relation(rel.relation)
            rel = self._arbitrate_event_event_relation(rel)
            name_a = self.normalizer.normalize_event_name(rel.EventName_A)
            name_b = self.normalizer.normalize_event_name(rel.EventName_B)
            if not name_a or not name_b or name_a == name_b:
                continue
            pair_key = tuple(sorted([name_a, name_b])) + (rel.relation, rel.evidence or "")

            # Changed 2026-04-21 16:46:12 +08:00: Prefer one stable direction for
            # order-sensitive relations instead of keeping both A->B and B->A.
            if rel.relation in {"顺承关系", "因果关系"}:
                preferred = rel
                if rel.EventName_A > rel.EventName_B:
                    preferred = EventEventRelation(
                        EventName_A=rel.EventName_B,
                        relation=rel.relation,
                        EventName_B=rel.EventName_A,
                        evidence=rel.evidence,
                    )
                existing = by_pair.get(pair_key)
                existing_priority = relation_priority.get(existing.relation, 0) if existing else -1
                current_priority = relation_priority.get(preferred.relation, 0)
                if (
                    existing is None
                    or current_priority > existing_priority
                    or (
                        current_priority == existing_priority
                        and len(preferred.evidence or "") > len(existing.evidence or "")
                    )
                ):
                    by_pair[pair_key] = preferred
                continue

            directional_key = (name_a, rel.relation, name_b, rel.evidence or "")
            if directional_key not in by_pair:
                by_pair[directional_key] = rel

        normalized.extend(by_pair.values())
        return normalized

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
            data = self._extract_json_payload(response)
            if not isinstance(data, dict):
                print("关系抽取 JSON 解析失败：未找到可用 JSON")
                print(f"原始响应前 500 字符: {response[:500]}...")
                self._write_error_log("invalid_json_payload", response)
                return self._build_derived_relation_result(events, place_list, org_list, person_list)

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
            event_event_relations = self._normalize_event_event_relations(extracted_event_rels + derived_event_rels)

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

        except (LLMAuthError, LLMAPIError):
            raise
        except Exception as e:
            print(f"关系抽取失败: {e}")
            if "response" in locals():
                self._write_error_log(f"relation_extract_exception_{type(e).__name__}", response)
            return self._build_derived_relation_result(events, place_list, org_list, person_list)
