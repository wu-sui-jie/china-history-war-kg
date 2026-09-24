"""
主流程控制模块
整合所有抽取器，支持单文件、长文本、批量处理三种模式
新增：文件级缓存机制，避免重复调用API
"""

import argparse
import json
import re
from pathlib import Path
from tqdm import tqdm
from src.core import DeepSeekClient, TextSplitter, CacheManager
from src.extractors import EntityExtractor, EventExtractor, RelationExtractor
from src.processors import ResultMerger, JsonToExcelConverter
from src.models import (
    EntityExtractionResult,
    EventExtractionResult,
    RelationExtractionResult,
    PlaceEntity,
    OrganizationEntity,
    PersonEntity,
)
from src.config import EXTRACTION_VERSION, PROMPT_VERSION, cache_context, current_timestamp
from src.utils import EntityClassifier, Normalizer


def dict_to_entities(data: dict):
    """将字典转换回 EntityExtractionResult 对象"""
    from src.models import EntityExtractionResult, PlaceEntity, OrganizationEntity, PersonEntity

    places = [PlaceEntity(**p) for p in data.get("places", [])]
    orgs = [OrganizationEntity(**o) for o in data.get("organizations", [])]
    persons = [PersonEntity(**p) for p in data.get("persons", [])]

    return EntityExtractionResult(places=places, organizations=orgs, persons=persons)


def dict_to_events(data: dict):
    """将字典转换回 EventExtractionResult 对象"""
    from src.models import EventExtractionResult, Event

    events = [Event(**e) for e in data.get("events", [])]

    return EventExtractionResult(events=events)


def dict_to_relations(data: dict):
    """将字典转换回 RelationExtractionResult 对象"""
    from src.models import (
        RelationExtractionResult,
        EventPlaceRelation,
        EventOrganizationRelation,
        EventPersonRelation,
        EventEventRelation
    )

    place_rels = [EventPlaceRelation(**r) for r in data.get("event_place_relations", [])]
    org_rels = [EventOrganizationRelation(**r) for r in data.get("event_organization_relations", [])]
    person_rels = [EventPersonRelation(**r) for r in data.get("event_person_relations", [])]
    event_rels = [EventEventRelation(**r) for r in data.get("event_event_relations", [])]

    return RelationExtractionResult(
        event_place_relations=place_rels,
        event_organization_relations=org_rels,
        event_person_relations=person_rels,
        event_event_relations=event_rels
    )


def _split_multi_value(value: str):
    if not value:
        return []
    normalized = str(value)
    for sep in ["、", "，", ",", "；", ";", "及", "与", "和", "/", " vs ", " VS ", "vs."]:
        normalized = normalized.replace(sep, "|")
    parts = [part.strip() for part in normalized.split("|")]
    return [part for part in parts if part and part != "不详" and part != "null"]


def _looks_like_person_name(value: str) -> bool:
    return EntityClassifier.looks_like_person_name(value)


def _looks_like_org_name(value: str) -> bool:
    return EntityClassifier.looks_like_org_name(value)


def _is_summary_only_event(event_obj) -> bool:
    """
    Added 2026-04-21 21:18:05 +08:00: Centralize summary-event detection so
    final event cleanup and quality diagnostics use the same rule.
    """
    event_name = getattr(event_obj, "EventName", "") or ""
    source_text = getattr(event_obj, "source_text", "") or ""
    remark = getattr(event_obj, "Remark", "") or ""
    text = f"{event_name} {source_text} {remark}"

    if "原文仅提及事件名称" in text:
        return True
    if "主要战争有" in source_text:
        return True
    if event_name in {"少康中兴", "商代之远征"}:
        return True
    if "北征南伐" in text and "东攻西进" in text:
        return True
    return False


def _parse_year_for_order(value: str):
    value = (value or "").strip()
    if not value or value in {"不详", "未知", "进行中"}:
        return None
    match = re.search(r"(公元前|前)\s*(\d{1,4})\s*年?", value)
    if match:
        return -int(match.group(2))
    match = re.search(r"(?<!前)(\d{1,4})\s*年", value)
    if match:
        return int(match.group(1))
    return None


def ensure_event_date_order(event_obj):
    """Ensure StartDate is not later than EndDate when both years are parseable."""
    start_year = _parse_year_for_order(getattr(event_obj, "StartDate", None))
    end_year = _parse_year_for_order(getattr(event_obj, "EndDate", None))
    if start_year is None or end_year is None or start_year <= end_year:
        return event_obj
    event_obj.StartDate, event_obj.EndDate = event_obj.EndDate, event_obj.StartDate
    remark = "已自动校正开始时间晚于结束时间的问题"
    event_obj.Remark = "\n".join([part for part in [getattr(event_obj, "Remark", None), remark] if part])
    return event_obj


def cleanup_entity_conflicts(entities: EntityExtractionResult) -> EntityExtractionResult:
    """
    Changed 2026-04-21 13:46:29 +08:00: Remove obvious person/org cross-type
    conflicts from final entity inventory.
    Changed 2026-04-21 16:12:05 +08:00: Canonicalize names, drop low-quality
    entity rows, and deduplicate cleaned people/organizations.
    """
    normalizer = Normalizer()

    def merge_source(existing_text, new_text):
        if not new_text:
            return existing_text
        if not existing_text:
            return new_text
        if new_text in existing_text:
            return existing_text
        return existing_text + "\n" + new_text

    cleaned_persons_map = {}
    for person in entities.persons:
        person.PersonName = normalizer.standardize_person_name(
            EntityClassifier.normalize_person_name(person.PersonName)
        )
        if not EntityClassifier.is_valid_person_name(person.PersonName):
            continue
        if person.PersonName and _looks_like_org_name(person.PersonName) and not _looks_like_person_name(person.PersonName):
            continue
        key = normalizer.normalize_entity_name(person.PersonName)
        if key not in cleaned_persons_map:
            cleaned_persons_map[key] = person
        else:
            existing = cleaned_persons_map[key]
            for field_name in ["DynastyName", "OrgName", "Role", "Note"]:
                if getattr(existing, field_name, None) in (None, "") and getattr(person, field_name, None) not in (None, ""):
                    setattr(existing, field_name, getattr(person, field_name))
            existing.source_text = merge_source(existing.source_text, person.source_text)

    cleaned_orgs_map = {}
    for org in entities.organizations:
        org.OrgName = normalizer.standardize_org_name(
            EntityClassifier.normalize_org_name(org.OrgName)
        )
        if not EntityClassifier.is_valid_org_name(org.OrgName):
            continue
        if org.OrgName and _looks_like_person_name(org.OrgName) and not _looks_like_org_name(org.OrgName):
            continue
        key = normalizer.normalize_entity_name(org.OrgName)
        if key not in cleaned_orgs_map:
            cleaned_orgs_map[key] = org
        else:
            existing = cleaned_orgs_map[key]
            for field_name in ["DynastyName", "OrgType"]:
                if getattr(existing, field_name, None) in (None, "") and getattr(org, field_name, None) not in (None, ""):
                    setattr(existing, field_name, getattr(org, field_name))
            existing.source_text = merge_source(existing.source_text, org.source_text)

    return EntityExtractionResult(
        places=entities.places,
        organizations=list(cleaned_orgs_map.values()),
        persons=list(cleaned_persons_map.values())
    )


def cleanup_events(events: EventExtractionResult) -> EventExtractionResult:
    """
    Changed 2026-04-21 16:12:05 +08:00: Canonicalize event display names and
    deduplicate near-duplicate final events before export.
    """
    normalizer = Normalizer()
    event_name_overrides = {
        "寒淀攻灭斟灌氏和斟寻氏": "寒浞攻灭斟灌氏和斟寻氏",
        "寒足攻灭斟灌氏和斟寻氏": "寒浞攻灭斟灌氏和斟寻氏",
    }
    def first_effective_place(value):
        for place_name in _split_multi_value(value):
            if not normalizer.is_noisy_place_name(place_name):
                return normalizer.normalize_entity_name(place_name)
        return ""

    def event_completeness_score(event_obj):
        score_fields = [
            "EventType", "StartDate", "EndDate", "DynastyName", "Place", "Aggressor",
            "Defender", "Allies", "Result", "Commanders", "KeyPersons", "Action",
            "TroopSize", "Duration", "GeographicScope", "Casualties", "Impact", "Remark"
        ]
        score = 0
        for field_name in score_fields:
            if not normalizer.is_placeholder_value(getattr(event_obj, field_name, None)):
                score += 1
        score += len(getattr(event_obj, "relations", []) or [])
        if getattr(event_obj, "source_text", None):
            score += 1
        return score

    event_map = {}
    for event in events.events:
        event = ensure_event_date_order(event)
        event.EventName = normalizer.standardize_event_name(event.EventName)
        event.EventName = event_name_overrides.get(event.EventName, event.EventName)
        key = (
            normalizer.normalize_event_name(event.EventName),
            event.DynastyName or "",
            event.StartDate or "",
            first_effective_place(event.Place),
        )
        if key not in event_map:
            event_map[key] = event
            continue
        existing = event_map[key]
        if event_completeness_score(event) > event_completeness_score(existing) or len(event.EventName or "") > len(existing.EventName or ""):
            previous_name = existing.EventName
            existing.EventName = event.EventName
            if previous_name and previous_name != event.EventName:
                existing.Remark = "\n".join([part for part in [existing.Remark, f"alias:{previous_name}"] if part])
        elif event.EventName and event.EventName != existing.EventName:
            existing.Remark = "\n".join([part for part in [existing.Remark, f"alias:{event.EventName}"] if part])
        for field_name in [
            "EventType", "StartDate", "EndDate", "Place", "Aggressor", "Defender",
            "Allies", "Result", "Commanders", "KeyPersons", "Action", "TroopSize",
            "Duration", "GeographicScope", "Casualties", "source", "Impact", "Remark"
        ]:
            if getattr(existing, field_name, None) in (None, "") and getattr(event, field_name, None) not in (None, ""):
                setattr(existing, field_name, getattr(event, field_name))
        if event.source_text:
            if not existing.source_text:
                existing.source_text = event.source_text
            elif event.source_text not in existing.source_text:
                existing.source_text += "\n" + event.source_text
        for rel in getattr(event, "relations", []) or []:
            if all(not (r.type == rel.type and r.to == rel.to) for r in getattr(existing, "relations", [])):
                existing.relations.append(rel)

    merged_events = list(event_map.values())

    # Changed 2026-04-21 20:00:18 +08:00: Drop broad summary events when a more
    # specific stage event for the same campaign already exists.
    normalized_names = [normalizer.normalize_event_name(event.EventName) for event in merged_events]
    summary_like_keys = set()
    for index, event in enumerate(merged_events):
        if _is_summary_only_event(event):
            summary_like_keys.add(normalized_names[index])
            continue
        event_name = event.EventName or ""
        if not event_name.endswith(("南征", "东征", "西征", "北伐", "征鬼方")):
            continue
        current_key = normalized_names[index]
        for other_index, other in enumerate(merged_events):
            if index == other_index:
                continue
            other_name = other.EventName or ""
            other_key = normalized_names[other_index]
            if other_name.startswith(event_name) and other_name != event_name and current_key != other_key:
                summary_like_keys.add(current_key)
                break

    filtered_events = [event for event in merged_events if normalizer.normalize_event_name(event.EventName) not in summary_like_keys]
    return EventExtractionResult(events=filtered_events)


def finalize_outputs(entities, events, relations):
    """
    Changed 2026-04-21 16:12:05 +08:00: Run one shared cleanup path for both
    fresh extraction and cache reads so output quality stays consistent.
    """
    entities = cleanup_entity_conflicts(entities)
    events = cleanup_events(events)
    valid_event_names = {getattr(event, "EventName", None) for event in events.events if getattr(event, "EventName", None)}
    relations = cleanup_relation_conflicts(relations, valid_event_names)
    relations = enrich_relations_from_events(entities, events, relations)
    relations = cleanup_relation_conflicts(relations, valid_event_names)
    return entities, events, relations


def enrich_relations_from_events(entities, events, relations):
    """
    Add rich deterministic event-entity relations from final event fields.
    This runs after cache reads and chunk merges too, so old sparse relation
    caches do not keep the four graph pages under-connected.
    """
    place_list = "、".join([p.geo_name for p in entities.places if getattr(p, "geo_name", None)])
    org_list = "、".join([o.OrgName for o in entities.organizations if getattr(o, "OrgName", None)])
    person_list = "、".join([p.PersonName for p in entities.persons if getattr(p, "PersonName", None)])
    derived_relations = RelationExtractor(None)._build_derived_relation_result(
        events.events,
        place_list,
        org_list,
        person_list,
    )
    return ResultMerger.merge_relations([relations, derived_relations])


def split_publishable_outputs(entities, events, relations):
    """
    Added 2026-04-22 10:20:00 +08:00: Split final extraction output into
    publishable data and candidate data so weak records stop blocking
    downstream storage and graph usage.
    """
    normalizer = Normalizer()

    def is_effective_value(value):
        return not normalizer.is_placeholder_value(value)

    def has_publishable_place(value):
        return any(
            not normalizer.is_noisy_place_name(place_name)
            for place_name in _split_multi_value(value)
        )

    def sanitize_publishable_place(value):
        cleaned_places = [
            place_name for place_name in _split_multi_value(value)
            if not normalizer.is_noisy_place_name(place_name)
        ]
        return "、".join(cleaned_places)

    def has_publishable_org(value):
        names = _split_multi_value(value)
        if not names:
            return False
        valid_names = [
            name for name in names
            if EntityClassifier.is_valid_org_name(name)
            and not normalizer.is_placeholder_value(name)
            and len(name.strip()) >= 2
        ]
        return bool(valid_names)

    def has_publishable_result(event_obj):
        result_value = getattr(event_obj, "Result", None)
        action_value = getattr(event_obj, "Action", None)
        return (
            is_effective_value(result_value)
            or is_effective_value(action_value)
        )

    def has_strong_result(event_obj):
        result_value = (getattr(event_obj, "Result", None) or "").strip()
        weak_result_tokens = ["获得一些胜利", "暂时控制", "势力南至", "不详", "未知"]
        if not is_effective_value(result_value):
            return False
        return not any(token in result_value for token in weak_result_tokens)

    def is_high_confidence_event_event_relation(rel):
        evidence = getattr(rel, "evidence", None) or ""
        relation = normalizer.normalize_relation(getattr(rel, "relation", None))
        if relation == "因果关系":
            return any(token in evidence for token in ["因此", "于是", "导致", "引发", "致使", "造成"])
        if relation == "顺承关系":
            return all(
                name and name in evidence
                for name in [getattr(rel, "EventName_A", None), getattr(rel, "EventName_B", None)]
            )
        return relation == "并列关系"

    def published_event_key(event_obj):
        normalized_name = normalizer.normalize_event_name(getattr(event_obj, "EventName", None))
        place_key = sanitize_publishable_place(getattr(event_obj, "Place", None))
        return (
            normalized_name,
            getattr(event_obj, "StartDate", None) or "",
            place_key,
        )

    def has_credible_dynasty(event_obj):
        event_name = (getattr(event_obj, "EventName", "") or "").strip()
        dynasty_name = (getattr(event_obj, "DynastyName", "") or "").strip()
        if not dynasty_name or normalizer.is_placeholder_value(dynasty_name):
            return False
        ancient_markers = ["神农", "黄帝", "炎帝", "尧", "舜", "禹", "蚩尤"]
        if any(marker in event_name for marker in ancient_markers):
            return dynasty_name in {"上古", "远古"}
        return True

    def event_publish_score(event_obj):
        score_fields = [
            "EventType", "StartDate", "EndDate", "DynastyName", "Place", "Aggressor",
            "Defender", "Allies", "Result", "Commanders", "KeyPersons", "Action", "Impact"
        ]
        score = sum(
            1 for field_name in score_fields
            if is_effective_value(getattr(event_obj, field_name, None))
        )
        score += len(getattr(event_obj, "relations", []) or [])
        if getattr(event_obj, "source_text", None):
            score += 1
        return score

    def has_required_event_fields(event_obj):
        return (
            is_effective_value(getattr(event_obj, "EventType", None))
            and has_credible_dynasty(event_obj)
            and has_publishable_place(getattr(event_obj, "Place", None))
            and has_publishable_org(getattr(event_obj, "Aggressor", None))
            and has_required_opponent(event_obj)
            and has_publishable_result(event_obj)
            and has_strong_result(event_obj)
        )

    def has_required_opponent(event_obj):
        return (
            has_publishable_org(getattr(event_obj, "Defender", None))
            or is_effective_value(getattr(event_obj, "Result", None))
        )

    def is_publishable_event(event_obj):
        event_name = (getattr(event_obj, "EventName", "") or "").strip()
        if not event_name or _is_summary_only_event(event_obj):
            return False

        populated_core_fields = 0
        for field_name in ["EventType", "Place", "Aggressor", "Defender", "Result", "Action"]:
            if is_effective_value(getattr(event_obj, field_name, None)):
                populated_core_fields += 1

        return (
            populated_core_fields >= 4
            and has_required_event_fields(event_obj)
        )

    published_places = []
    candidate_places = []
    for place in entities.places:
        if normalizer.is_noisy_place_name(getattr(place, "geo_name", None)):
            candidate_places.append(place)
        else:
            published_places.append(place)

    published_events = []
    candidate_events = []
    published_event_names = set()
    for event in events.events:
        event.Place = sanitize_publishable_place(getattr(event, "Place", None)) or getattr(event, "Place", None)
        if is_publishable_event(event):
            published_events.append(event)
            published_event_names.add(getattr(event, "EventName", None))
        else:
            candidate_events.append(event)

    deduped_published_events = {}
    remaining_candidate_events = list(candidate_events)
    for event in published_events:
        key = published_event_key(event)
        existing = deduped_published_events.get(key)
        if existing is None:
            deduped_published_events[key] = event
            continue
        if event_publish_score(event) > event_publish_score(existing):
            remaining_candidate_events.append(existing)
            deduped_published_events[key] = event
        else:
            remaining_candidate_events.append(event)
    published_events = list(deduped_published_events.values())
    candidate_events = remaining_candidate_events
    published_event_names = {getattr(event, "EventName", None) for event in published_events}

    published_org_names = {
        org.OrgName for org in entities.organizations
        if EntityClassifier.is_valid_org_name(getattr(org, "OrgName", None))
    }

    published_relations = RelationExtractionResult(
        event_place_relations=[],
        event_organization_relations=[],
        event_person_relations=[],
        event_event_relations=[],
    )
    candidate_relations = RelationExtractionResult(
        event_place_relations=[],
        event_organization_relations=[],
        event_person_relations=[],
        event_event_relations=[],
    )

    for rel in relations.event_place_relations:
        if getattr(rel, "EventName", None) not in published_event_names:
            candidate_relations.event_place_relations.append(rel)
            continue
        place_name = getattr(rel, "modern_name", None)
        if normalizer.is_noisy_place_name(place_name):
            candidate_relations.event_place_relations.append(rel)
            continue
        published_relations.event_place_relations.append(rel)

    for rel in relations.event_organization_relations:
        if (
            getattr(rel, "EventName", None) in published_event_names
            and EntityClassifier.is_valid_org_name(getattr(rel, "OrgName", None))
            and getattr(rel, "OrgName", None) in published_org_names
        ):
            published_relations.event_organization_relations.append(rel)
        else:
            candidate_relations.event_organization_relations.append(rel)

    for rel in relations.event_person_relations:
        if getattr(rel, "EventName", None) in published_event_names:
            published_relations.event_person_relations.append(rel)
        else:
            candidate_relations.event_person_relations.append(rel)

    for rel in relations.event_event_relations:
        if (
            getattr(rel, "EventName_A", None) in published_event_names
            and getattr(rel, "EventName_B", None) in published_event_names
            and is_high_confidence_event_event_relation(rel)
        ):
            published_relations.event_event_relations.append(rel)
        else:
            candidate_relations.event_event_relations.append(rel)

    referenced_published_org_names = set()
    referenced_published_person_names = set()
    for event in published_events:
        referenced_published_org_names.update(_split_multi_value(getattr(event, "Aggressor", None)))
        referenced_published_org_names.update(_split_multi_value(getattr(event, "Defender", None)))
        referenced_published_org_names.update(_split_multi_value(getattr(event, "Allies", None)))
        referenced_published_person_names.update(_split_multi_value(getattr(event, "Commanders", None)))
        referenced_published_person_names.update(_split_multi_value(getattr(event, "KeyPersons", None)))
    referenced_published_org_names.update(getattr(rel, "OrgName", None) for rel in published_relations.event_organization_relations)
    referenced_published_person_names.update(getattr(rel, "PersonName", None) for rel in published_relations.event_person_relations)
    referenced_published_org_names = {name for name in referenced_published_org_names if name}
    referenced_published_person_names = {name for name in referenced_published_person_names if name}

    published_entities = EntityExtractionResult(
        places=published_places,
        organizations=[
            org for org in entities.organizations
            if EntityClassifier.is_valid_org_name(getattr(org, "OrgName", None))
            and getattr(org, "OrgName", None) in referenced_published_org_names
        ],
        persons=[
            person for person in entities.persons
            if getattr(person, "PersonName", None) in referenced_published_person_names
        ],
    )
    candidate_entities = EntityExtractionResult(
        places=candidate_places,
        organizations=[
            org for org in entities.organizations
            if not EntityClassifier.is_valid_org_name(getattr(org, "OrgName", None))
            or getattr(org, "OrgName", None) not in referenced_published_org_names
        ],
        persons=[
            person for person in entities.persons
            if getattr(person, "PersonName", None) not in referenced_published_person_names
        ],
    )
    published_event_result = EventExtractionResult(
        events=published_events,
        metadata={**(events.metadata or {}), "publish_stage": "published"},
    )
    candidate_event_result = EventExtractionResult(
        events=candidate_events,
        metadata={**(events.metadata or {}), "publish_stage": "candidate"},
    )
    return (
        (published_entities, published_event_result, published_relations),
        (candidate_entities, candidate_event_result, candidate_relations),
    )


def _arbitrate_event_event_relation(normalizer: Normalizer, rel):
    evidence = getattr(rel, "evidence", None) or ""
    relation = normalizer.normalize_relation(getattr(rel, "relation", None))
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


def cleanup_relation_conflicts(relations: RelationExtractionResult, valid_event_names=None) -> RelationExtractionResult:
    """
    Changed 2026-04-21 17:20:11 +08:00: Apply one final relation pass to
    remove reverse duplicates and prefer stronger relation labels.
    Changed 2026-04-21 20:46:18 +08:00: Use evidence order to stabilize
    顺承/因果 direction without changing the existing relation schema.
    Changed 2026-04-21 21:18:05 +08:00: Drop event-event relations that
    reference filtered events so summary-event cleanup cannot leave stale edges.
    """
    normalizer = Normalizer()
    relation_priority = {"因果关系": 3, "顺承关系": 2, "并列关系": 1}
    valid_event_keys = {
        normalizer.normalize_event_name(name)
        for name in (valid_event_names or set())
        if name
    }

    cleaned_event_rels = {}
    for rel in relations.event_event_relations:
        rel.EventName_A = normalizer.standardize_event_name(rel.EventName_A)
        rel.EventName_B = normalizer.standardize_event_name(rel.EventName_B)
        rel.relation = normalizer.normalize_relation(rel.relation)
        if (rel.EventName_A or "").startswith("E") and (rel.EventName_A or "")[1:].isdigit():
            continue
        if (rel.EventName_B or "").startswith("E") and (rel.EventName_B or "")[1:].isdigit():
            continue

        name_a = normalizer.normalize_event_name(rel.EventName_A)
        name_b = normalizer.normalize_event_name(rel.EventName_B)
        if not name_a or not name_b or name_a == name_b:
            continue
        if valid_event_keys and (name_a not in valid_event_keys or name_b not in valid_event_keys):
            continue
        rel = _arbitrate_event_event_relation(normalizer, rel)

        if rel.relation in {"顺承关系", "因果关系"} and rel.evidence:
            idx_a = rel.evidence.find(rel.EventName_A)
            idx_b = rel.evidence.find(rel.EventName_B)
            if idx_a >= 0 and idx_b >= 0 and idx_a > idx_b:
                rel.EventName_A, rel.EventName_B = rel.EventName_B, rel.EventName_A
                name_a, name_b = name_b, name_a

        if rel.relation in {"顺承关系", "因果关系", "并列关系"}:
            pair_key = tuple(sorted([name_a, name_b]))
        else:
            pair_key = tuple(sorted([name_a, name_b])) + (rel.evidence or "",)

        existing = cleaned_event_rels.get(pair_key)
        if existing is None:
            cleaned_event_rels[pair_key] = rel
            continue

        existing_priority = relation_priority.get(existing.relation, 0)
        current_priority = relation_priority.get(rel.relation, 0)
        if current_priority > existing_priority:
            cleaned_event_rels[pair_key] = rel
        elif current_priority == existing_priority and len(rel.evidence or "") > len(existing.evidence or ""):
            cleaned_event_rels[pair_key] = rel

    relations.event_event_relations = list(cleaned_event_rels.values())
    return relations


def enrich_entities_from_events(entities: EntityExtractionResult, events: EventExtractionResult) -> EntityExtractionResult:
    """
    Changed 2026-04-21 12:48:22 +08:00: Backfill places, organizations, and
    persons from event fields so missed first-pass entities can still enter
    the final graph.
    """
    place_map = {(p.geo_name, p.DynastyName or ""): p for p in entities.places}
    org_map = {(o.OrgName, o.DynastyName or ""): o for o in entities.organizations}
    person_map = {(p.PersonName, p.DynastyName or ""): p for p in entities.persons}

    for event in events.events:
        dynasty = event.DynastyName or None
        source_text = event.source_text or None

        for place_name in _split_multi_value(event.Place):
            key = (place_name, dynasty or "")
            if key not in place_map:
                place_map[key] = PlaceEntity(
                    geo_name=place_name,
                    DynastyName=dynasty,
                    source_text=source_text
                )

        org_sources = [
            ("Aggressor", event.Aggressor),
            ("Defender", event.Defender),
            ("Allies", event.Allies),
        ]
        for org_role, org_value in org_sources:
            for org_name in _split_multi_value(org_value):
                if not _looks_like_org_name(org_name):
                    continue
                key = (org_name, dynasty or "")
                if key not in org_map:
                    org_map[key] = OrganizationEntity(
                        OrgName=org_name,
                        OrgType="军事势力",
                        DynastyName=dynasty,
                        source_text=source_text
                    )

        person_sources = [
            ("统帅", event.Commanders),
            ("关键人物", event.KeyPersons),
        ]
        for role_name, person_value in person_sources:
            for person_name in _split_multi_value(person_value):
                if not _looks_like_person_name(person_name):
                    continue
                key = (person_name, dynasty or "")
                if key not in person_map:
                    person_map[key] = PersonEntity(
                        PersonName=person_name,
                        DynastyName=dynasty,
                        Role=role_name,
                        source_text=source_text
                    )

    return EntityExtractionResult(
        places=list(place_map.values()),
        organizations=list(org_map.values()),
        persons=list(person_map.values())
    )


def process_long_text(text: str, llm, splitter: TextSplitter,
                      read_cache: bool = True, write_cache: bool = True):
    """
    处理长文本：分段抽取→合并结果
    注意：长文本的缓存处理较复杂，这里采用分段缓存策略
    """
    chunks = splitter.split(text)
    print(f"文本过长，已切分为 {len(chunks)} 个片段")

    entity_results = []
    event_results = []
    relation_results = []

    # 为每个片段单独使用缓存
    cache = CacheManager()
    cache_meta = cache_context(
        llm.model,
        "long_text_chunk",
        splitter.chunk_size,
        splitter.overlap
    )
    # Changed 2026-04-20 16:33:36 +08:00: Reuse extractors per long-text run
    # to avoid repeated initialization and keep one consistent prompt version.
    entity_extractor = EntityExtractor(llm)
    event_extractor = EventExtractor(llm)
    relation_extractor = RelationExtractor(llm)

    for i, (start_idx, end_idx, chunk) in enumerate(tqdm(chunks, desc="处理文本片段")):
        print(f"\n--- 处理片段 {i + 1}/{len(chunks)} (位置: {start_idx}-{end_idx}) ---")

        # 初始化变量，确保即使失败也有默认值
        entities = None
        events = None
        relations = None

        # 检查片段缓存
        cached = cache.get(chunk, cache_meta) if read_cache else None
        if cached:
            entities = dict_to_entities(cached["entities"])
            events = dict_to_events(cached["events"])
            relations = dict_to_relations(cached["relations"])
            entities, events, relations = finalize_outputs(entities, events, relations)
        else:
            # 无缓存，调用API
            chunk_processed_ok = False
            try:
                entities = entity_extractor.extract(chunk)

                # 准备实体列表字符串，传递给事件抽取
                place_list = "、".join([p.geo_name for p in entities.places])
                org_list = "、".join([o.OrgName for o in entities.organizations])
                person_list = "、".join([p.PersonName for p in entities.persons])

                events = event_extractor.extract(chunk, place_list, org_list, person_list)

                # Changed 2026-04-21 12:48:22 +08:00: Feed event fields back
                # into entity inventory so missed people/orgs/places are recovered.
                entities = enrich_entities_from_events(entities, events)
                entities = cleanup_entity_conflicts(entities)
                place_list = "、".join([p.geo_name for p in entities.places])
                org_list = "、".join([o.OrgName for o in entities.organizations])
                person_list = "、".join([p.PersonName for p in entities.persons])

                relations = relation_extractor.extract(chunk, events.events, place_list, org_list, person_list)
                entities, events, relations = finalize_outputs(entities, events, relations)
                chunk_processed_ok = True

            except Exception as e:
                print(f"  片段 {i + 1} 处理失败: {e}")
                # 使用空结果；不写缓存，避免把 API 失败/空结果缓存成“永久识别不到数据”
                entities = EntityExtractionResult()
                events = EventExtractionResult()
                relations = RelationExtractionResult()

            # 只有片段真正处理成功才写缓存，失败片段下次运行会自动重试
            if write_cache and chunk_processed_ok:
                try:
                    # 使用 model_dump 确保完整字段
                    cache.set(chunk, {
                        "entities": entities.model_dump(),
                        "events": events.model_dump(),
                        "relations": relations.model_dump()
                    }, cache_meta)
                except Exception as e:
                    print(f"  缓存保存失败: {e}")

        # 确保变量已定义（双重保险）
        if entities is None:
            entities = EntityExtractionResult()
        if events is None:
            events = EventExtractionResult()
        if relations is None:
            relations = RelationExtractionResult()

        entity_results.append(entities)
        event_results.append(events)
        relation_results.append(relations)

        print(f"  实体: {len(entities.places)}地点, {len(entities.organizations)}组织, {len(entities.persons)}人物")
        print(f"  事件: {len(events.events)}个")

    # 合并结果
    print("\n合并各片段结果...")
    merger = ResultMerger()

    final_entities = merger.merge_entities(entity_results)
    final_events = merger.merge_events(event_results)
    final_relations = merger.merge_relations(relation_results)
    final_entities, final_events, final_relations = finalize_outputs(final_entities, final_events, final_relations)

    return final_entities, final_events, final_relations


def process_single_file(file_path: Path, llm, enable_split: bool = True,
                        read_cache: bool = True, write_cache: bool = True):
    """
    处理单个文件（支持缓存）

    Args:
        file_path: 文件路径
        llm: LLM客户端
        enable_split: 是否启用长文本分段
        read_cache: 是否读取缓存
        write_cache: 是否保存缓存
    """
    print(f"使用模型: {llm.model}")
    print(f"开始处理文件: {file_path}")

    text = file_path.read_text(encoding="utf-8")
    print(f"文本长度: {len(text)} 字符")

    # 短文本直接整体缓存，长文本使用分段缓存
    if not enable_split or len(text) <= 2000:
        # 短文本：整体缓存
        if read_cache:
            cache = CacheManager()
            cached = cache.get(text, cache_context(llm.model, "single_file"))
            if cached:
                print("\n" + "=" * 60)
                print("缓存命中！直接返回结果，无需API调用")
                print("=" * 60)
                return finalize_outputs(
                    dict_to_entities(cached["entities"]),
                    dict_to_events(cached["events"]),
                    dict_to_relations(cached["relations"])
                )

        # 无缓存，正常处理
        # 第1轮：实体抽取
        entity_extractor = EntityExtractor(llm)
        entities = entity_extractor.extract(text)

        # 准备实体列表字符串
        place_list = "、".join([p.geo_name for p in entities.places])
        org_list = "、".join([o.OrgName for o in entities.organizations])
        person_list = "、".join([p.PersonName for p in entities.persons])

        # 第2轮：事件抽取（传入实体列表）
        event_extractor = EventExtractor(llm)
        event_result = event_extractor.extract(text, place_list, org_list, person_list)

        # Changed 2026-04-21 12:48:22 +08:00: Backfill entity inventory from
        # extracted event fields before relation extraction and final output.
        entities = enrich_entities_from_events(entities, event_result)
        entities = cleanup_entity_conflicts(entities)
        place_list = "、".join([p.geo_name for p in entities.places])
        org_list = "、".join([o.OrgName for o in entities.organizations])
        person_list = "、".join([p.PersonName for p in entities.persons])

        # 第3轮：关系抽取（传入实体列表和事件列表）
        relation_extractor = RelationExtractor(llm)
        relations = relation_extractor.extract(text, event_result.events, place_list, org_list, person_list)
        entities, event_result, relations = finalize_outputs(entities, event_result, relations)

        # 保存缓存（使用 model_dump 确保完整字段）
        if write_cache:
            cache = CacheManager()
            cache.set(text, {
                "entities": entities.model_dump(),
                "events": event_result.model_dump(),
                "relations": relations.model_dump()
            }, cache_context(llm.model, "single_file"))

        return entities, event_result, relations

    else:
        # 长文本：使用分段缓存（在 process_long_text 内部处理）
        return process_long_text(text, llm, TextSplitter(), read_cache, write_cache)


def build_quality_report(entities, events, relations) -> dict:
    """
    Added 2026-04-20 21:46:02 +08:00: Summarize extraction completeness so
    reruns can quickly judge whether source_text/evidence requirements held.
    """
    places = entities.places
    persons = entities.persons
    orgs = entities.organizations
    event_list = events.events
    relation_groups = [
        relations.event_place_relations,
        relations.event_person_relations,
        relations.event_organization_relations,
        relations.event_event_relations,
    ]
    flat_relations = [rel for group in relation_groups for rel in group]
    embedded_event_relations = sum(len(getattr(event, "relations", []) or []) for event in event_list)

    def missing_source(items):
        return sum(1 for item in items if not getattr(item, "source_text", None))

    def missing_evidence(items):
        return sum(1 for item in items if not getattr(item, "evidence", None))

    event_names = [getattr(event, "EventName", None) for event in event_list if getattr(event, "EventName", None)]
    org_names = [getattr(org, "OrgName", None) for org in orgs if getattr(org, "OrgName", None)]
    person_names = [getattr(person, "PersonName", None) for person in persons if getattr(person, "PersonName", None)]
    duplicate_event_names = len(event_names) - len(set(event_names))
    duplicate_org_names = len(org_names) - len(set(org_names))
    duplicate_person_names = len(person_names) - len(set(person_names))
    summary_like_events = sum(1 for event in event_list if _is_summary_only_event(event))
    event_metadata = getattr(events, "metadata", {}) or {}
    identified_event_count = int(event_metadata.get("identified_event_count", len(event_list)))
    final_event_count = int(event_metadata.get("final_event_count", len(event_list)))
    event_count_expansion = int(event_metadata.get("event_count_expansion", final_event_count - identified_event_count))
    missing_event_count = int(event_metadata.get("missing_event_count", max(identified_event_count - final_event_count, 0)))
    postprocess_filtered_count = int(event_metadata.get("postprocess_filtered_count", 0))
    postprocess_merged_count = int(event_metadata.get("postprocess_merged_count", 0))

    return {
        "generated_at": current_timestamp(),
        "extraction_version": EXTRACTION_VERSION,
        "prompt_version": PROMPT_VERSION,
        "counts": {
            "places": len(places),
            "persons": len(persons),
            "organizations": len(orgs),
            "events": len(event_list),
            "relations": len(flat_relations),
            "embedded_event_relations": embedded_event_relations,
        },
        "missing_source_text": {
            "places": missing_source(places),
            "persons": missing_source(persons),
            "organizations": missing_source(orgs),
            "events": missing_source(event_list),
        },
        "missing_evidence": {
            "relations": missing_evidence(flat_relations)
        },
        "diagnostics": {
            "event_event_relations": len(relations.event_event_relations),
            "events_with_embedded_relations": sum(1 for event in event_list if getattr(event, "relations", [])),
            "duplicate_event_names": duplicate_event_names,
            "duplicate_org_names": duplicate_org_names,
            "duplicate_person_names": duplicate_person_names,
            "summary_like_events": summary_like_events,
            "identified_event_count": identified_event_count,
            "final_event_count": final_event_count,
            "event_count_expansion": event_count_expansion,
            "missing_event_count": missing_event_count,
            "postprocess_filtered_count": postprocess_filtered_count,
            "postprocess_merged_count": postprocess_merged_count,
        }
    }


def save_results(name: str, entities, events, relations, input_file: Path = None,
                 text_length: int = None, output_base: Path = None, llm_meta: dict = None):
    """
    保存抽取结果到 output/ 目录
    输出9个JSON文件和对应的Excel文件

    llm_meta: 由调用方传入 {"model": ..., "api_base": ...}，随 metadata 落盘，
    使产物可自证由哪个模型/端点产出（修正 2026-09-25：原先缺失，换模型重跑后
    产物无法区分来源）。
    """
    output_dir = output_base or Path("output")
    output_dir.mkdir(exist_ok=True)

    result_dir = output_dir / name
    result_dir.mkdir(exist_ok=True)

    # 使用 model_dump 确保所有字段完整输出
    # 1. 地点实体 JSON
    places_data = {"places": [place.model_dump() for place in entities.places]}
    with open(result_dir / "1_places.json", "w", encoding="utf-8") as f:
        json.dump(places_data, f, ensure_ascii=False, indent=2)

    # 2. 人物实体 JSON
    persons_data = {"persons": [person.model_dump() for person in entities.persons]}
    with open(result_dir / "2_persons.json", "w", encoding="utf-8") as f:
        json.dump(persons_data, f, ensure_ascii=False, indent=2)

    # 3. 组织实体 JSON
    organizations_data = {"organizations": [org.model_dump() for org in entities.organizations]}
    with open(result_dir / "3_organizations.json", "w", encoding="utf-8") as f:
        json.dump(organizations_data, f, ensure_ascii=False, indent=2)

    # 4. 事件-地点关系 JSON
    event_place_data = {"event_place_relations": [rel.model_dump() for rel in relations.event_place_relations]}
    with open(result_dir / "4_event_place_relations.json", "w", encoding="utf-8") as f:
        json.dump(event_place_data, f, ensure_ascii=False, indent=2)

    # 5. 事件-人物关系 JSON
    event_person_data = {"event_person_relations": [rel.model_dump() for rel in relations.event_person_relations]}
    with open(result_dir / "5_event_person_relations.json", "w", encoding="utf-8") as f:
        json.dump(event_person_data, f, ensure_ascii=False, indent=2)

    # 6. 事件-组织关系 JSON
    event_org_data = {
        "event_organization_relations": [rel.model_dump() for rel in relations.event_organization_relations]}
    with open(result_dir / "6_event_organization_relations.json", "w", encoding="utf-8") as f:
        json.dump(event_org_data, f, ensure_ascii=False, indent=2)

    # 7. 事件-事件关系 JSON
    event_event_data = {"event_event_relations": [rel.model_dump() for rel in relations.event_event_relations]}
    with open(result_dir / "7_event_event_relations.json", "w", encoding="utf-8") as f:
        json.dump(event_event_data, f, ensure_ascii=False, indent=2)

    # 8. 事件 JSON
    events_data = {"events": [event.model_dump() for event in events.events]}
    with open(result_dir / "8_events.json", "w", encoding="utf-8") as f:
        json.dump(events_data, f, ensure_ascii=False, indent=2)

    # 9. 全部整合 JSON
    quality_report = build_quality_report(entities, events, relations)
    final_data = {
        "metadata": {
            "extracted_at": current_timestamp(),
            "extraction_version": EXTRACTION_VERSION,
            "prompt_version": PROMPT_VERSION,
            "input_file": str(input_file) if input_file else name,
            "text_length": text_length,
            **(llm_meta or {}),
        },
        "entities": entities.model_dump(),
        "events": events.model_dump(),
        "relations": relations.model_dump(),
        "quality_report": quality_report
    }
    with open(result_dir / "9_final_all.json", "w", encoding="utf-8") as f:
        json.dump(final_data, f, ensure_ascii=False, indent=2)

    with open(result_dir / "10_quality_report.json", "w", encoding="utf-8") as f:
        json.dump(quality_report, f, ensure_ascii=False, indent=2)

    published_output, _ = split_publishable_outputs(entities, events, relations)
    stage_entities, stage_events, stage_relations = published_output
    stage_dir = result_dir / "published"
    stage_dir.mkdir(exist_ok=True)
    stage_quality_report = build_quality_report(stage_entities, stage_events, stage_relations)
    stage_final_data = {
        "metadata": {
            "extracted_at": current_timestamp(),
            "extraction_version": EXTRACTION_VERSION,
            "prompt_version": PROMPT_VERSION,
            "input_file": str(input_file) if input_file else name,
            "text_length": text_length,
            "publish_stage": "published",
            **(llm_meta or {}),
        },
        "entities": stage_entities.model_dump(),
        "events": stage_events.model_dump(),
        "relations": stage_relations.model_dump(),
        "quality_report": stage_quality_report
    }
    with open(stage_dir / "final.json", "w", encoding="utf-8") as f:
        json.dump(stage_final_data, f, ensure_ascii=False, indent=2)
    with open(stage_dir / "quality_report.json", "w", encoding="utf-8") as f:
        json.dump(stage_quality_report, f, ensure_ascii=False, indent=2)

    print(f"\nJSON文件已保存至: {result_dir}")

    # 转换为Excel
    print("正在转换为Excel格式...")
    converter = JsonToExcelConverter()
    converter.convert_all(result_dir, name)
    print(f"Excel文件已保存至: {result_dir / 'excel'}")

    return result_dir


def main():
    """主入口函数"""
    parser = argparse.ArgumentParser(description="历史战争文本实体-事件-关系抽取")
    parser.add_argument("path", help="待抽取的文本文件路径")
    parser.add_argument("--no-split", action="store_true", help="禁用长文本分段")
    parser.add_argument("--no-cache", action="store_true", help="完全禁用缓存：不读取也不保存")
    parser.add_argument("--refresh-cache", action="store_true", help="跳过读取缓存，但保存本次新结果")
    parser.add_argument("--output", default="output", help="输出目录，默认 output")
    args = parser.parse_args()

    llm = DeepSeekClient()
    path = Path(args.path)
    enable_split = not args.no_split
    read_cache = not args.no_cache and not args.refresh_cache
    write_cache = not args.no_cache

    if args.no_cache:
        print("\n[注意] 已完全禁用缓存，将强制重新调用API且不保存缓存")
    elif args.refresh_cache:
        print("\n[注意] 已启用刷新缓存，将跳过旧缓存并保存新缓存")

    try:
        if path.is_file():
            entities, events, relations = process_single_file(
                path, llm, enable_split, read_cache, write_cache
            )
            text_length = len(path.read_text(encoding="utf-8"))
            save_results(path.stem, entities, events, relations, path, text_length, Path(args.output),
                         llm_meta={"model": llm.model, "api_base": llm.base_url})
        else:
            print(f"错误: 文件不存在: {path}")
            raise SystemExit(1)
    except Exception as e:
        print(f"处理失败: {e}")
        import traceback
        traceback.print_exc()
        raise SystemExit(1)


if __name__ == "__main__":
    main()
