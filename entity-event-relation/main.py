"""
主流程控制模块
整合所有抽取器，支持单文件、长文本、批量处理三种模式
含文件级缓存机制，避免重复调用 API
"""

import argparse
import json
import re
from pathlib import Path
from tqdm import tqdm
from war_extraction.core import DeepSeekClient, TextSplitter, CacheManager
from war_extraction.core.text_cleaner import clean_text_with_mapping
from war_extraction.core.extraction_runner import (
    entities_from_dict as dict_to_entities,
    events_from_dict as dict_to_events,
    relations_from_dict as dict_to_relations,
    run_extraction,
)
from war_extraction.extractors import EntityExtractor, EventExtractor, RelationExtractor
from war_extraction.processors import ResultMerger, JsonToExcelConverter
from war_extraction.models import (
    EntityExtractionResult,
    EventExtractionResult,
    RelationExtractionResult,
    PlaceEntity,
    OrganizationEntity,
    PersonEntity,
)
from war_extraction.config import EXTRACTION_VERSION, PROMPT_VERSION, cache_context, current_timestamp
from war_extraction.utils import EntityClassifier, Normalizer
from war_extraction.utils.provenance import artifact_digest, generation_metadata
from war_extraction.utils.publish_rules import load_publish_rules
from war_extraction.utils.vocabulary import (
    normalize_event_type,
    normalize_org_type,
    normalize_role,
    relation_type_allowed,
)
from war_extraction.utils.relation_rules import (
    build_event_start_years,
    reduce_event_event_relations,
)
from war_extraction.utils.value_parsing import (
    ensure_event_date_order,
    event_identity_key,
    parse_year_for_order,
    split_multi_value,
)

# 注：dict_to_entities / dict_to_events / dict_to_relations 只有一处实现，在
# war_extraction.core.extraction_runner 里（抽取编排的唯一入口）。
# 这里保留同名别名，既有的调用点与用例不必改。


def _split_multi_value(value: str):
    # 多值拆分的实现只有 war_extraction/utils/value_parsing.py 一处；占位词排除集
    # 全模块统一为宽口径（含"未知/无/None"），所以这里不再显式传参。
    return split_multi_value(value)


def _looks_like_person_name(value: str) -> bool:
    return EntityClassifier.looks_like_person_name(value)


def _looks_like_org_name(value: str) -> bool:
    return EntityClassifier.looks_like_org_name(value)


#: 发布过滤用的书本特化规则（事件名名单、概括事件标记、弱结果词、上古标记词）。
#: 内容在 `config/publish_rules.json`，规则本身不再是代码——换语料只改配置。
PUBLISH_RULES = load_publish_rules()


def _is_summary_only_event(event_obj) -> bool:
    """判定"只有概括、没有具体战事"的事件；最终清理与质量诊断共用这一条规则。"""
    event_name = getattr(event_obj, "EventName", "") or ""
    source_text = getattr(event_obj, "source_text", "") or ""
    remark = getattr(event_obj, "Remark", "") or ""
    text = f"{event_name} {source_text} {remark}"

    if any(marker in text for marker in PUBLISH_RULES["summary_only_event_text_markers"]):
        return True
    if event_name in set(PUBLISH_RULES["summary_only_event_names"]):
        return True
    if any(token in text for token in ("北征南伐", "东攻西进")):
        return True
    return False


def _parse_year_for_order(value: str):
    # 年份解析的实现只有 war_extraction/utils/value_parsing.py 一处
    return parse_year_for_order(value)


# ensure_event_date_order 由 war_extraction.utils.value_parsing 直接提供，
# 本文件不再包一层——包一层会与 import 进来的同名函数互相遮蔽。


def cleanup_entity_conflicts(entities: EntityExtractionResult) -> EntityExtractionResult:
    """
    清理最终实体清单：规范化名称并剔除明显的人/组织跨类型冲突与低质量行；
    人物与组织按规范化名称归并（重复行的缺失字段互补、source_text 合并），地点原样保留。
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


def _add_alias(event_obj, alias: str) -> None:
    '''
    把事件名别名记进独立的 `AliasNames` 字段。

    **不再写进 `Remark`。** `Remark` 是正文备注，会经 `import_json_to_sqlite` 的
    `events.remark` 列、前端属性面板、Excel 三处展示；把 `alias:旧名` 拼进去等于把
    流程元信息当成内容给用户看。新字段对下游零影响（未映射的键被忽略）。
    '''
    alias = (alias or "").strip()
    if not alias or alias == (getattr(event_obj, "EventName", None) or ""):
        return
    aliases = list(getattr(event_obj, "AliasNames", None) or [])
    if alias not in aliases:
        aliases.append(alias)
    event_obj.AliasNames = aliases


def cleanup_events(events: EventExtractionResult) -> EventExtractionResult:
    """
    导出前规范化事件显示名，并按"规范化名称 + 朝代 + 开始时间 + 首个有效地点"归并近似重复事件：
    重复行按完整度取优、缺失字段互补，最后丢掉只有概括的事件。

    事件的诊断 metadata 要**带着一起走**并就地更新（最终事件数、后处理过滤数），
    否则合并阶段刚补回来的诊断项会在这一步再丢一次。
    """
    normalizer = Normalizer()
    # 事件名别名表只有 war_extraction/utils/normalizer.py 的 EVENT_NAME_ALIASES 一份，
    # 这里直接引用它。standardize_event_name 已经套过别名表，这里再套一次是为了让
    # "已标准化的名字"也能再收敛一次（幂等，不改变行为）。
    event_name_overrides = normalizer.EVENT_NAME_ALIASES

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
        # 事件身份键的唯一实现在 value_parsing.event_identity_key：四处（抽取器/合并期/
        # 清理期/发布期）必须用同一套，否则同名不同年代的事件在一处被合并、在另一处不被合并。
        key = event_identity_key(
            normalizer, event.EventName, event.DynastyName, event.StartDate, event.Place
        )
        if key not in event_map:
            event_map[key] = event
            continue
        existing = event_map[key]
        if event_completeness_score(event) > event_completeness_score(existing) or len(event.EventName or "") > len(existing.EventName or ""):
            previous_name = existing.EventName
            existing.EventName = event.EventName
            if previous_name and previous_name != event.EventName:
                _add_alias(existing, previous_name)
        elif event.EventName and event.EventName != existing.EventName:
            _add_alias(existing, event.EventName)
        for alias in getattr(event, "AliasNames", None) or []:
            _add_alias(existing, alias)
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

    # 同一场战役已经有更具体的阶段事件时，丢掉那条近似战役名的概括事件
    # （含"原文仅提及事件名称"这类只有名字、没有战事的事件）。
    normalized_names = [normalizer.normalize_event_name(event.EventName) for event in merged_events]
    summary_like_keys = set()
    for index, event in enumerate(merged_events):
        if _is_summary_only_event(event):
            summary_like_keys.add(normalized_names[index])
            continue
        event_name = event.EventName or ""
        if not event_name.endswith(tuple(PUBLISH_RULES["campaign_summary_suffixes"])):
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
    metadata = dict(getattr(events, "metadata", None) or {})
    if metadata:
        # 有诊断 metadata 才更新：没有时保持为空，免得凭空造出"看起来正常"的数字。
        # 这一步丢掉的（概括事件、被更具体阶段事件取代的战役）计入后处理过滤数。
        dropped = max(len(merged_events) - len(filtered_events), 0)
        metadata["postprocess_filtered_count"] = int(metadata.get("postprocess_filtered_count", 0)) + dropped
        metadata["final_event_count"] = len(filtered_events)
        metadata["missing_event_count"] = max(
            int(metadata.get("identified_event_count", len(filtered_events))) - len(filtered_events), 0
        )
    return EventExtractionResult(events=filtered_events, metadata=metadata)


def finalize_outputs(entities, events, relations):
    """抽取与缓存读取共用的收尾清理链：实体清洗 → 事件归并 → 关系清洗 → 从事件回填关系 → 再清洗。"""
    entities = cleanup_entity_conflicts(entities)
    events = cleanup_events(events)
    valid_event_names = {getattr(event, "EventName", None) for event in events.events if getattr(event, "EventName", None)}
    # 事件起始年份索引：关系清理要用它给"顺承/因果"定方向（字典序定方向是错的）
    event_start_years = build_event_start_years(Normalizer(), events.events)
    relations, first_pass = cleanup_relation_conflicts(relations, valid_event_names, event_start_years)
    relations = enrich_relations_from_events(entities, events, relations)
    relations, second_pass = cleanup_relation_conflicts(relations, valid_event_names, event_start_years)
    # 两次清理丢掉的悬空边合计写进事件 metadata：质量报告与体检脚本从这里读，
    # 不能让"丢掉了几条边"这个事实只存在于内存里。
    cleanup_diagnostics = {
        "dangling_relations_dropped": {
            key: first_pass["dangling_relations_dropped"].get(key, 0)
            + second_pass["dangling_relations_dropped"].get(key, 0)
            for key in second_pass["dangling_relations_dropped"]
        },
        # 枚举外的事件-事件关系条数：它们**保留在产物里**、由发布拆分阶段进候选区，
        # 这里单独计数是为了让"这批有多少条"在质量报告里有通道（原先既不留也不数）。
        "event_event_relations_outside_enum": (
            first_pass.get("event_event_relations_outside_enum", 0)
            + second_pass.get("event_event_relations_outside_enum", 0)
        ),
    }
    events.metadata = {**(events.metadata or {}), "cleanup_diagnostics": cleanup_diagnostics}
    return entities, events, relations


def enrich_relations_from_events(entities, events, relations):
    """
    从最终事件字段确定性派生事件-实体关系。缓存读取与分段合并之后都要再跑一次，
    否则旧的稀疏关系缓存会让四个图谱页面一直连不上。
    """
    normalizer = Normalizer()
    place_list = "、".join([p.geo_name for p in entities.places if getattr(p, "geo_name", None)])
    org_list = "、".join([o.OrgName for o in entities.organizations if getattr(o, "OrgName", None)])
    person_list = "、".join([p.PersonName for p in entities.persons if getattr(p, "PersonName", None)])
    derived_relations = RelationExtractor(None)._build_derived_relation_result(
        events.events,
        place_list,
        org_list,
        person_list,
    )
    return ResultMerger.merge_relations(
        [relations, derived_relations],
        event_start_years=build_event_start_years(normalizer, events.events),
    )


def _enum_is_legal(value, kind: str) -> bool:
    """
    schema 校验层：这个取值是否落在枚举权威表（`utils/vocabulary.py`）里。

    这是**防漂移的闸门**，不是装饰——`normalize_relation` 对不认识的关系名是原样返回的，
    于是 20 种提示词枚举外的地点关系、`关键人物`这类枚举外角色能一路落进产物，
    而下游（前端下拉、RAG 白名单、导入白名单）看不到这些取值，只表现为"点不到""被静默改写"。
    校验不通过的记录进候选区（`candidate/`），不进发布子集。

    取值本身已经在决策里扩散进权威表的（`军事势力`/`关键人物`等）算通过；
    只有表外的**新**取值会被拦下来——这时要做的是决定"扩表还是改数据"，而不是让它悄悄流下去。
    """
    checkers = {
        "OrgType": normalize_org_type,
        "Role": normalize_role,
        "EventType": normalize_event_type,
    }
    checker = checkers[kind]
    _, hit = checker(value)
    return hit


def split_publishable_outputs(entities, events, relations):
    """
    把最终抽取结果拆成"可发布"与"候选"两套：只有要素齐全、结果可信、且枚举合法的记录，
    以及它们引用到的实体与关系进入 published；其余留在 candidate，
    避免它们阻塞下游入库与图谱使用。

    两处相对原实现的口径变化：

    1. **地点不再按"有噪声/没噪声"二分**（已定决策 10.1 第 8 项）：`published` 保留全量地点，
       另加 `referenced_by_published_event` 标记区分"主数据"与"候选地点"。理由是裁掉会
       与地理编码链和地图选择器断链——地图页能画出多少点直接取决于地点是否在 published 里。
    2. **枚举校验进候选区**：`OrgType`/`Role`/`EventType`/关系类型不在权威表里的记录
       走 candidate，不进发布子集（见 `_enum_is_legal`）。
    """
    normalizer = Normalizer()
    enum_out = {
        "places": 0,
        "organizations": 0,
        "persons": 0,
        "events": 0,
        "event_place_relations": 0,
        "event_organization_relations": 0,
        "event_person_relations": 0,
        "event_event_relations": 0,
    }

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
        weak_result_tokens = PUBLISH_RULES["weak_result_tokens"]
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
        # 发布期的身份键也走统一实现（原先是"名称 + 时间 + 地点"，**少了朝代**——
        # 与清理期不一致，同名不同朝代的事件在这一步又会被并掉）
        return event_identity_key(
            normalizer,
            getattr(event_obj, "EventName", None),
            getattr(event_obj, "DynastyName", None),
            getattr(event_obj, "StartDate", None),
            sanitize_publishable_place(getattr(event_obj, "Place", None)),
        )

    def has_credible_dynasty(event_obj):
        event_name = (getattr(event_obj, "EventName", "") or "").strip()
        dynasty_name = (getattr(event_obj, "DynastyName", "") or "").strip()
        if not dynasty_name or normalizer.is_placeholder_value(dynasty_name):
            return False
        ancient_markers = PUBLISH_RULES["ancient_event_markers"]
        if any(marker in event_name for marker in ancient_markers):
            return dynasty_name in set(PUBLISH_RULES["ancient_credible_dynasties"])
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

    # 地点：**全量进 published**，用 `referenced_by_published_event` 标记是否被发布事件引用。
    # 裁掉噪声地点会与地理编码链、地图选择器断链（已定决策 10.1 第 8 项）。
    published_places = list(entities.places)
    candidate_places = []

    published_events = []
    candidate_events = []
    published_event_names = set()
    for event in events.events:
        event.Place = sanitize_publishable_place(getattr(event, "Place", None)) or getattr(event, "Place", None)
        if not _enum_is_legal(getattr(event, "EventType", None), "EventType"):
            # 事件类型不在权威表里：进候选区，不进发布子集（下游按固定词典统计，未知类型会被兜底）
            enum_out["events"] += 1
            candidate_events.append(event)
            continue
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

    def _relation_enum_ok(rel, category: str) -> bool:
        if relation_type_allowed(normalizer.normalize_relation(getattr(rel, "relation", None)), category):
            return True
        enum_out[category_map[category]] += 1
        return False

    category_map = {
        "event-place": "event_place_relations",
        "event-organization": "event_organization_relations",
        "event-person": "event_person_relations",
        "event-event": "event_event_relations",
    }

    for rel in relations.event_place_relations:
        if getattr(rel, "EventName", None) not in published_event_names:
            candidate_relations.event_place_relations.append(rel)
            continue
        place_name = getattr(rel, "modern_name", None)
        if normalizer.is_noisy_place_name(place_name):
            candidate_relations.event_place_relations.append(rel)
            continue
        if not _relation_enum_ok(rel, "event-place"):
            candidate_relations.event_place_relations.append(rel)
            continue
        published_relations.event_place_relations.append(rel)

    for rel in relations.event_organization_relations:
        if (
            getattr(rel, "EventName", None) in published_event_names
            and EntityClassifier.is_valid_org_name(getattr(rel, "OrgName", None))
            and getattr(rel, "OrgName", None) in published_org_names
            and _relation_enum_ok(rel, "event-organization")
        ):
            published_relations.event_organization_relations.append(rel)
        else:
            candidate_relations.event_organization_relations.append(rel)

    for rel in relations.event_person_relations:
        if (
            getattr(rel, "EventName", None) in published_event_names
            and _relation_enum_ok(rel, "event-person")
        ):
            published_relations.event_person_relations.append(rel)
        else:
            candidate_relations.event_person_relations.append(rel)

    for rel in relations.event_event_relations:
        # **取值合法性检查必须排在 `is_high_confidence...` 之前**：后者只认"因果/顺承/并列"，
        # 枚举外的类型（如 `主战场`）一律返回 False，于是 `and` 短路后
        # `_relation_enum_ok` 根本不会被执行——计数恒为 0，这就是"枚举外的条目没有计数通道"
        # 的具体成因（核验 P0-2 的另一半）。顺序换成"事件在名单里 → 取值合法 → 置信度"。
        if (
            getattr(rel, "EventName_A", None) in published_event_names
            and getattr(rel, "EventName_B", None) in published_event_names
            and _relation_enum_ok(rel, "event-event")
            and is_high_confidence_event_event_relation(rel)
        ):
            published_relations.event_event_relations.append(rel)
        else:
            candidate_relations.event_event_relations.append(rel)

    # 地点是否被发布事件引用：给全量地点一个"主数据 / 候选"的区分标记（软依赖，
    # 下游不认识的键会忽略；前端可用它区分主数据与候选地点）。
    referenced_published_place_names = set()
    for event in published_events:
        for place_name in _split_multi_value(getattr(event, "Place", None)):
            referenced_published_place_names.add(normalizer.normalize_entity_name(place_name))
    for rel in published_relations.event_place_relations:
        referenced_published_place_names.add(
            normalizer.normalize_entity_name(getattr(rel, "modern_name", None))
        )
    for place in published_places:
        referenced = normalizer.normalize_entity_name(getattr(place, "geo_name", None)) in referenced_published_place_names
        try:
            place.referenced_by_published_event = referenced
        except (ValueError, TypeError):
            # 模型没声明该字段时忽略：标记只是软依赖，不该让落盘失败
            pass

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

    def _org_publishable(org) -> bool:
        name = getattr(org, "OrgName", None)
        if not EntityClassifier.is_valid_org_name(name):
            return False
        if name not in referenced_published_org_names:
            return False
        # 组织类型不在权威表里的组织进候选区（原先只有导入脚本会静默改写它）
        if not _enum_is_legal(getattr(org, "OrgType", None), "OrgType"):
            enum_out["organizations"] += 1
            return False
        return True

    def _person_publishable(person) -> bool:
        if getattr(person, "PersonName", None) not in referenced_published_person_names:
            return False
        if not _enum_is_legal(getattr(person, "Role", None), "Role"):
            enum_out["persons"] += 1
            return False
        return True

    published_entities = EntityExtractionResult(
        places=published_places,
        organizations=[org for org in entities.organizations if _org_publishable(org)],
        persons=[person for person in entities.persons if _person_publishable(person)],
    )
    candidate_entities = EntityExtractionResult(
        places=candidate_places,
        organizations=[org for org in entities.organizations if not _org_publishable(org)],
        persons=[person for person in entities.persons if not _person_publishable(person)],
    )
    published_event_result = EventExtractionResult(
        events=published_events,
        metadata={**(events.metadata or {}), "publish_stage": "published"},
    )
    candidate_event_result = EventExtractionResult(
        events=candidate_events,
        metadata={**(events.metadata or {}), "publish_stage": "candidate"},
    )
    # 第三个返回值是"因枚举不合法而进候选区"的条数：写进质量报告，别让这一步静默发生
    return (
        (published_entities, published_event_result, published_relations),
        (candidate_entities, candidate_event_result, candidate_relations),
        {"enum_out": enum_out},
    )


def cleanup_relation_conflicts(relations: RelationExtractionResult, valid_event_names=None,
                             event_start_years=None) -> RelationExtractionResult:
    """
    关系的最后一道清理：规范化名称与关系类型、丢掉自环与指向已过滤事件的边、
    按证据/时间校正"顺承/因果"方向，再按对称对去重。

    传入 `valid_event_names` 时，**四类关系**的事件端都要在最终事件名单里对得上——
    原来只有事件-事件关系做了这道过滤，于是 raw 产物里事件-地点 27 条、事件-组织 17 条、
    事件-人物 10 条关系的 `EventName` 在事件表里根本找不到（导入时靠精确名单丢掉，
    只有 `published` 侧是干净的）。现在四类一起过滤，悬空边数为 0。

    事件-事件关系的方向判定与去重收在 `war_extraction/utils/relation_rules.py`，
    抽取器与本函数共用同一份——两处各一份会让"抽出来的关系"和"清理后的关系"对不上。
    """
    normalizer = Normalizer()
    valid_event_keys = {
        normalizer.normalize_event_name(name)
        for name in (valid_event_names or set())
        if name
    }

    def _event_allowed(name) -> bool:
        if not valid_event_keys:
            return True
        return normalizer.normalize_event_name(name) in valid_event_keys

    dropped_dangling = {
        "event_place_relations": 0,
        "event_organization_relations": 0,
        "event_person_relations": 0,
        "event_event_relations": 0,
    }

    def _drop_dangling(relations, attribute):
        """丢掉事件端对不上最终事件名单的边，并计数——悬空边数要能被体检脚本与质量报告看见。"""
        kept = []
        for rel in relations:
            rel.EventName = normalizer.standardize_event_name(getattr(rel, "EventName", None))
            rel.relation = normalizer.normalize_relation(getattr(rel, "relation", None))
            if not rel.EventName or not rel.relation:
                continue
            if not _event_allowed(rel.EventName):
                dropped_dangling[attribute] += 1
                continue
            kept.append(rel)
        return kept

    relations.event_place_relations = _drop_dangling(
        relations.event_place_relations, "event_place_relations")
    relations.event_organization_relations = _drop_dangling(
        relations.event_organization_relations, "event_organization_relations")
    relations.event_person_relations = _drop_dangling(
        relations.event_person_relations, "event_person_relations")

    # 事件-事件：端点过滤在收敛函数内部做（它要先标准化再判），所以**在这里先数一遍**——
    # 原先把 `name_allowed` 传进去就完事，"被它丢掉了多少条悬空边"既没计数也没返回通道，
    # 于是四类关系里只有这一类在报告里是 0（不是真的没有，是没数）。
    if valid_event_keys:
        surviving_event_rels = []
        for rel in relations.event_event_relations:
            name_a = normalizer.normalize_event_name(getattr(rel, "EventName_A", None))
            name_b = normalizer.normalize_event_name(getattr(rel, "EventName_B", None))
            if _event_allowed(name_a) and _event_allowed(name_b):
                surviving_event_rels.append(rel)
            else:
                dropped_dangling["event_event_relations"] += 1
        relations.event_event_relations = surviving_event_rels

    # 枚举外类型的关系**不丢**，只计数（它们留在产物里、由发布拆分阶段进候选区）：
    # 见 `war_extraction/utils/relation_rules.py::reduce_event_event_relations` 的说明。
    quarantined_event_relations: list = []
    relations.event_event_relations = reduce_event_event_relations(
        normalizer,
        relations.event_event_relations,
        event_start_years=event_start_years,
        quarantine=quarantined_event_relations,
    )

    # 本次的丢弃/隔离统计随返回值一起出去：质量报告与体检脚本都要报这些数，不能静默丢。
    # （不用函数属性当返回值——那种隐式通道会让调用方忘记读取，数字就悄悄消失了。）
    return relations, {
        "dangling_relations_dropped": dropped_dangling,
        "event_event_relations_outside_enum": len(quarantined_event_relations),
    }


def enrich_entities_from_events(entities: EntityExtractionResult, events: EventExtractionResult) -> EntityExtractionResult:
    """用事件字段回填地点/组织/人物，让第一遍没抽到的实体仍能进入最终图谱。"""
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
                      read_cache: bool = True, write_cache: bool = True,
                      text_meta: dict = None, source_mapping=None):
    """
    处理长文本：分段抽取→合并结果

    分段循环与三阶段调用在 `war_extraction.core.extraction_runner`（与 backend 共用同一份
    编排）。本函数只提供本链路自己的后处理：每段的实体回填/清洗与 finalize_outputs 走钩子，
    段间合并仍走 ResultMerger。
    """
    cache = CacheManager() if (read_cache or write_cache) else None
    cache_meta = cache_context(llm.model, "long_text_chunk",
                               splitter.chunk_size, splitter.overlap)

    def _entities_ready(_chunk_text, entities, events):
        # 事件字段回填实体清单，再清洗冲突：关系阶段要用的实体名列表随之变长
        return cleanup_entity_conflicts(enrich_entities_from_events(entities, events))

    progress = {"bar": None}

    def _chunk_start(index, total, start, end):
        if progress["bar"] is None:
            progress["bar"] = tqdm(total=total, desc="处理文本片段")
        print(f"\n--- 处理片段 {index}/{total} (位置: {start}-{end}) ---")

    def _chunk_finish(chunk):
        progress["bar"].update(1)
        print(f"  实体: {len(chunk.entities.places)}地点, "
              f"{len(chunk.entities.organizations)}组织, {len(chunk.entities.persons)}人物")
        print(f"  事件: {len(chunk.events.events)}个")

    run = run_extraction(
        llm, text, splitter=splitter,
        cache=cache, cache_context_meta=cache_meta,
        read_cache=read_cache, write_cache=write_cache,
        source_mapping=source_mapping,
        on_entities_ready=_entities_ready,
        on_chunk_done=lambda _t, e, ev, r: finalize_outputs(e, ev, r),
        on_chunk_start=_chunk_start,
        on_chunk_finish=_chunk_finish,
    )
    if progress["bar"] is not None:
        progress["bar"].close()

    if run.partial_errors:
        print(f"\n注意：本次有 {len(run.partial_errors)} 处阶段失败，相关分段的结果可能不完整")

    # 合并结果
    print("\n合并各片段结果...")
    merger = ResultMerger()

    final_entities = merger.merge_entities(run.entities)
    final_events = merger.merge_events(run.events)
    # 合并期就要有事件起始年份：跨段出现的同一条"顺承/因果"关系需要据此定方向，
    # 只靠事件名字典序定方向是错的（详见 utils/relation_rules.py 的说明）。
    final_relations = merger.merge_relations(
        run.relations,
        event_start_years=build_event_start_years(Normalizer(), final_events.events),
    )
    final_entities, final_events, final_relations = finalize_outputs(final_entities, final_events, final_relations)
    _attach_extraction_diagnostics(final_events, run, text_meta)
    return final_entities, final_events, final_relations


def _attach_extraction_diagnostics(final_events, run, text_meta: dict = None) -> None:
    """
    把本次分段运行的诊断挂到事件 metadata 上，供质量报告读取。

    **为什么要这么挂。** 质量报告只拿得到 entities/events/relations 三样东西，
    而"关系阶段降级了几段"这类诊断在 `run.diagnostics` 里。挂在事件 metadata 上是
    唯一不需要改 `build_quality_report` / `save_results` / `process_*` 三层签名的通道，
    且与既有的"事件诊断 metadata"用的是同一处（`events.metadata`）。
    """
    diagnostics = dict(getattr(run, "diagnostics", None) or {})
    if not diagnostics:
        return
    final_events.metadata = {
        **(final_events.metadata or {}),
        **(text_meta or {}),
        "extraction_diagnostics": {
            "stage_ok": diagnostics.get("stage_ok"),
            "chunks": diagnostics.get("chunks"),
            "cache_hits": diagnostics.get("cache_hits"),
            "failed_chunks": diagnostics.get("failed_chunks"),
            "relation_degraded_stages": diagnostics.get("relation_degraded_stages", 0),
        },
    }


def process_single_file(file_path: Path, llm, enable_split: bool = True,
                        read_cache: bool = True, write_cache: bool = True,
                        clean: bool = True):
    """
    处理单个文件（支持缓存）

    Args:
        file_path: 文件路径
        llm: LLM客户端
        enable_split: 是否启用长文本分段
        read_cache: 是否读取缓存
        write_cache: 是否保存缓存
        clean: 是否做输入文本清洗（OCR 错字、行内硬换行、不可见字符）。
            清洗统计随产物 metadata 记录在 `text_cleaning` 下，所以"这次洗了没有、影响多大"可查。
            默认开启：书籍转换文本的行内硬换行会把句子折断，不清洗等于把输入噪声记成模型错误。
    """
    print(f"使用模型: {llm.model}")
    print(f"开始处理文件: {file_path}")

    raw_text = file_path.read_text(encoding="utf-8")
    source_mapping = None
    if clean:
        # 带位置映射的清洗：分段算出来的 `source_offset` 才能换算回**原文**坐标
        text, cleaning_stats, source_mapping = clean_text_with_mapping(raw_text)
        cleaning_stats["enabled"] = True
        if cleaning_stats["soft_line_breaks_merged"] or cleaning_stats["ocr_fixes_applied"]:
            print(f"文本清洗: 合并行内换行 {cleaning_stats['soft_line_breaks_merged']} 处，"
                  f"错字订正 {cleaning_stats['ocr_fixes_applied']} 处，"
                  f"长度 {cleaning_stats['input_length']} → {cleaning_stats['output_length']}")
    else:
        text, cleaning_stats = raw_text, {"enabled": False, "input_length": len(raw_text),
                                          "output_length": len(raw_text)}
    text_meta = {"text_cleaning": cleaning_stats}
    print(f"文本长度: {len(text)} 字符")

    # 短文本直接整体缓存，长文本使用分段缓存
    if not enable_split or len(text) <= 2000:
        # 短文本：整篇一段、整体缓存。三阶段调用交给共享编排，
        # 这里只提供本链路的钩子（实体回填/清洗 + finalize_outputs）。
        return _process_one_shot(text, llm, "single_file", read_cache, write_cache, text_meta,
                                 source_mapping)

    else:
        # 长文本：使用分段缓存（在 process_long_text 内部处理）
        return process_long_text(text, llm, TextSplitter(), read_cache, write_cache, text_meta,
                                 source_mapping)


def _process_one_shot(text: str, llm, cache_stage: str,
                      read_cache: bool = True, write_cache: bool = True,
                      text_meta: dict = None, source_mapping=None):
    """
    整篇一次跑完（不分段）：短文本路径用，缓存按整篇文本一个条目。

    与 `process_long_text` 共用同一份编排（`run_extraction`），差别只在
    "一段 vs 多段"与"缓存上下文用 single_file 还是 long_text_chunk"。

    "不分段"是这一支的原口径，所以这里把切分器撑到整篇长度（`chunk_size=max(len, 默认)`）：
    1800~2000 字的短文本不会被切成两段。
    """
    cache = CacheManager() if (read_cache or write_cache) else None
    cache_meta = cache_context(llm.model, cache_stage)

    run = run_extraction(
        llm, text,
        splitter=TextSplitter(chunk_size=max(len(text), 1), overlap=0),
        cache=cache, cache_context_meta=cache_meta,
        read_cache=read_cache, write_cache=write_cache,
        source_mapping=source_mapping,
        on_entities_ready=lambda _t, entities, events: cleanup_entity_conflicts(
            enrich_entities_from_events(entities, events)),
        on_chunk_done=lambda _t, e, ev, r: finalize_outputs(e, ev, r),
    )

    chunk = run.chunks[0]
    if chunk.from_cache:
        print("\n" + "=" * 60)
        print("缓存命中！直接返回结果，无需API调用")
        print("=" * 60)
    _attach_extraction_diagnostics(chunk.events, run, text_meta)
    return chunk.entities, chunk.events, chunk.relations


def _enum_out_counts(entities, events, relations) -> dict:
    """
    数一遍产物里"不在枚举权威表内"的取值。

    这批数字是 `阶段 3 / schema 校验层` 的观测面：校验层只把不合法的记录挪进候选区，
    而"到底有多少、是哪一类"必须能在质量报告里看到——否则所谓"防漂移"就只是一句话。
    关系类型按类别分别数，因为四类各有各的允许集合。
    """
    counter = {
        "OrgType": 0, "Role": 0, "EventType": 0,
        "event_place_relations": 0, "event_organization_relations": 0,
        "event_person_relations": 0, "event_event_relations": 0,
    }
    for org in entities.organizations:
        if not normalize_org_type(getattr(org, "OrgType", None))[1]:
            counter["OrgType"] += 1
    for person in entities.persons:
        if not normalize_role(getattr(person, "Role", None))[1]:
            counter["Role"] += 1
    for event in events.events:
        if not normalize_event_type(getattr(event, "EventType", None))[1]:
            counter["EventType"] += 1
    for attribute, category in (
        ("event_place_relations", "event-place"),
        ("event_organization_relations", "event-organization"),
        ("event_person_relations", "event-person"),
        ("event_event_relations", "event-event"),
    ):
        for rel in getattr(relations, attribute):
            if not relation_type_allowed(getattr(rel, "relation", None), category):
                counter[attribute] += 1
    return counter


def build_quality_report(entities, events, relations, split_stats: dict = None) -> dict:
    """汇总抽取完整度（各类计数、source_text/evidence 缺失数、事件计数诊断），便于重跑后快速判断。"""
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
            # 枚举外取值数（体检脚本与它同口径）。收敛到权威表之后这里应当是 0；
            # 变成非 0 说明"提示词枚举 / 权威表 / 产物"三者漂移了，要先决定扩表还是改数据。
            "enum_out_values": _enum_out_counts(entities, events, relations),
            # 悬空边（事件端对不上最终事件名单）被丢掉的前后口径：丢掉多少必须可见
            "dangling_relations_dropped": (event_metadata.get("cleanup_diagnostics") or {})
                .get("dangling_relations_dropped", {}),
            # 枚举外的事件-事件关系条数（保留在产物里、进候选区，见 cleanup_relation_conflicts）
            "event_event_relations_outside_enum": (event_metadata.get("cleanup_diagnostics") or {})
                .get("event_event_relations_outside_enum", 0),
            # 走了"降级"路径的分段数（关系阶段 LLM 失败但保留了规则派生关系）
            "relation_degraded_stages": (event_metadata.get("extraction_diagnostics") or {})
                .get("relation_degraded_stages", 0),
            # 因枚举不合法被挪进候选区的条数。**raw 产物这一项恒为 null**，因为发布拆分
            # 发生在 raw 产物写盘之后（拆分会把事件的 `Place` 就地清洗，不能提前跑）——
            # 这里如实写 null 表示"不适用"，不要写成空字典（那看起来像"统计到了 0 条"）。
            # 要看这个数就去看 `published/` 与 `candidate/` 两份报告，或看本报告的
            # `enum_out_values`（raw 侧各容器的枚举外计数）。
            "moved_to_candidate_by_enum": (split_stats or {}).get("enum_out") if split_stats else None,
        }
    }


def _write_artifact(path: Path, payload: dict) -> None:
    """
    落盘产物：先算出内容哈希写进 `metadata.artifact_sha256`，再写文件。

    哈希覆盖"除该字段自身以外的全部内容"，所以读回来抠掉它就能自校验——
    "指标变了"时至少能确认被评估的确实是哪一份文件。
    """
    payload.setdefault("metadata", {})["artifact_sha256"] = artifact_digest(payload)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _artifact_body(entities, events, relations) -> dict:
    """
    把三个结果对象转成落盘用的三段，并把 `text_cleaning` 从 `events.metadata` **提到顶层**。

    为什么要提出来：清洗统计描述的是**输入文本**，不是事件属性；文档（指南 §2.4）承诺它在
    顶层 `metadata.text_cleaning` 下，而它实际是被 `_attach_extraction_diagnostics` 塞进
    `events.metadata` 的（那条通道是为了让分段诊断能走到质量报告）。这里在落盘前搬一次，
    两处都保留会变成"同一件事两处口径"。

    注意 `events.model_dump()` 返回的是新字典，所以 pop 只影响落盘内容，不改内存里的对象。
    """
    events_payload = events.model_dump()
    return {
        "entities": entities.model_dump(),
        "events": events_payload,
        "relations": relations.model_dump(),
        "_text_cleaning": (events_payload.get("metadata") or {}).pop("text_cleaning", None),
    }


def save_results(name: str, entities, events, relations, input_file: Path = None,
                 text_length: int = None, output_base: Path = None, llm_meta: dict = None):
    """
    保存抽取结果到 output/ 目录
    输出9个JSON文件和对应的Excel文件

    llm_meta: 由调用方传入 {"model": ..., "model_served": ..., "api_base": ...}，随 metadata 落盘，
    使产物可自证由哪个模型/端点产出（缺了它，换模型重跑后产物就分不出来源）。
    除此之外，`generation_metadata` 还会补上温度、seed、`git_commit` 与产物哈希。
    """
    # 默认输出目录按 __file__ 锚定到模块根，不随当前工作目录变
    output_dir = output_base or (Path(__file__).resolve().parent / "output")
    output_dir.mkdir(exist_ok=True)

    result_dir = output_dir / name
    result_dir.mkdir(exist_ok=True)

    # 产物自证：模型 / 端点 / 温度 / seed / 提交号，与内容哈希一起写进 metadata
    provenance = generation_metadata(llm_meta=llm_meta)

    # 分步中间产物（1_places.json … 8_events.json）不再写出：它们与 9_final_all.json 的
    # 对应字段逐字节一致，属纯重复（约 20 MB/次），而且 json_to_excel 已改为只读聚合文件。
    # 批次目录里唯一的权威产物是 9_final_all.json（published/ 另算一套粒度）。

    # 9. 全部整合 JSON
    quality_report = build_quality_report(entities, events, relations)
    body = _artifact_body(entities, events, relations)
    text_cleaning = body.pop("_text_cleaning")
    final_data = {
        "metadata": {
            "extracted_at": current_timestamp(),
            "extraction_version": EXTRACTION_VERSION,
            "prompt_version": PROMPT_VERSION,
            "input_file": str(input_file) if input_file else name,
            "text_length": text_length,
            # 输入清洗统计：顶层 metadata 下（`events.metadata` 里那份已在 _artifact_body 里搬走）
            "text_cleaning": text_cleaning,
            **provenance,
        },
        **body,
        "quality_report": quality_report
    }
    _write_artifact(result_dir / "9_final_all.json", final_data)

    with open(result_dir / "10_quality_report.json", "w", encoding="utf-8") as f:
        json.dump(quality_report, f, ensure_ascii=False, indent=2)

    published_output, candidate_output, split_stats = split_publishable_outputs(entities, events, relations)
    stage_entities, stage_events, stage_relations = published_output
    candidate_entities, candidate_events, candidate_relations = candidate_output
    stage_dir = result_dir / "published"
    stage_dir.mkdir(exist_ok=True)
    stage_quality_report = build_quality_report(
        stage_entities, stage_events, stage_relations, split_stats=split_stats)
    stage_body = _artifact_body(stage_entities, stage_events, stage_relations)
    stage_final_data = {
        "metadata": {
            "extracted_at": current_timestamp(),
            "extraction_version": EXTRACTION_VERSION,
            "prompt_version": PROMPT_VERSION,
            "input_file": str(input_file) if input_file else name,
            "text_length": text_length,
            "text_cleaning": stage_body.pop("_text_cleaning"),
            "publish_stage": "published",
            **provenance,
        },
        **stage_body,
        "quality_report": stage_quality_report
    }
    _write_artifact(stage_dir / "final.json", stage_final_data)
    with open(stage_dir / "quality_report.json", "w", encoding="utf-8") as f:
        json.dump(stage_quality_report, f, ensure_ascii=False, indent=2)

    # 候选区落盘：原先 candidate 只在内存里算出来就被丢掉，于是"哪些记录没进发布子集、
    # 为什么"完全不可查（下游只导 9_final_all.json 与 published/final.json，不读这里）。
    # 落盘只是让这一半结果可见，对下游零影响。
    candidate_dir = result_dir / "candidate"
    candidate_dir.mkdir(exist_ok=True)
    candidate_quality_report = build_quality_report(
        candidate_entities, candidate_events, candidate_relations, split_stats=split_stats)
    candidate_body = _artifact_body(candidate_entities, candidate_events, candidate_relations)
    candidate_final_data = {
        "metadata": {
            "extracted_at": current_timestamp(),
            "extraction_version": EXTRACTION_VERSION,
            "prompt_version": PROMPT_VERSION,
            "input_file": str(input_file) if input_file else name,
            "text_length": text_length,
            "text_cleaning": candidate_body.pop("_text_cleaning"),
            "publish_stage": "candidate",
            "publish_split_stats": split_stats,
            **provenance,
        },
        **candidate_body,
        "quality_report": candidate_quality_report,
    }
    _write_artifact(candidate_dir / "final.json", candidate_final_data)
    with open(candidate_dir / "quality_report.json", "w", encoding="utf-8") as f:
        json.dump(candidate_quality_report, f, ensure_ascii=False, indent=2)

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
    parser.add_argument("--no-clean", action="store_true",
                        help="跳过输入文本清洗（OCR 错字、行内硬换行、不可见字符）；"
                             "默认清洗，清洗统计会记进产物 metadata")
    parser.add_argument("--output", default=str(Path(__file__).resolve().parent / "output"),
                        help="输出目录，默认 <模块根>/output")
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
                path, llm, enable_split, read_cache, write_cache, clean=not args.no_clean
            )
            text_length = len(path.read_text(encoding="utf-8"))
            save_results(
                path.stem, entities, events, relations, path, text_length, Path(args.output),
                llm_meta={
                    # 请求名与实际服务名分开记：服务端把 deepseek-chat 别名路由到
                    # deepseek-flash，只记请求名等于自证错信息（见 llm_client 的说明）
                    "model": llm.model,
                    "model_served": llm.model_served,
                    "thinking_mode": llm.thinking_mode,
                    "api_base": llm.base_url,
                },
            )
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
