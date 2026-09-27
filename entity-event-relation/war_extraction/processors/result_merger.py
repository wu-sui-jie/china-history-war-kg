"""
多段抽取结果合并模块
解决长文本分段处理后的结果整合与去重问题
含事件关系合并与 source_text 去重
"""

from war_extraction.models import (
    EntityExtractionResult,
    EventExtractionResult,
    RelationExtractionResult,
    EventRelation
)
from war_extraction.utils.normalizer import Normalizer
from war_extraction.utils.relation_rules import reduce_event_event_relations
from war_extraction.utils.value_parsing import event_identity_key


class ResultMerger:
    """
    结果合并器
    合并多个文本片段的抽取结果，智能去重并补充缺失属性
    """

    @staticmethod
    def _fill_missing_fields(existing_obj, new_obj, field_names: list):
        """用新对象的非空字段补齐旧对象的空字段（互补合并，不整行替换）。"""
        for field_name in field_names:
            existing_value = getattr(existing_obj, field_name, None)
            new_value = getattr(new_obj, field_name, None)
            if (existing_value is None or existing_value == "") and new_value not in (None, ""):
                setattr(existing_obj, field_name, new_value)

    @staticmethod
    def _merge_evidence_text(existing: str, new: str) -> str:
        if not existing:
            return new
        if not new or new in existing:
            return existing
        if existing in new:
            return new
        return existing + "\n" + new

    @staticmethod
    def _event_merge_key(normalizer: Normalizer, event_obj):
        """
        事件合并键：走统一的事件身份定义（归一名称 + 朝代 + 起始时间 + 首个地点）。

        原先这里是"名称 + 朝代"两项，而清理期与发布期用的是另外两套键——同名不同年代的
        战争在段间合并时被并成一条、在清理期又分不开。定义收在
        `value_parsing.event_identity_key`，四处共用。
        """
        return event_identity_key(
            normalizer,
            getattr(event_obj, "EventName", None),
            getattr(event_obj, "DynastyName", None),
            getattr(event_obj, "StartDate", None),
            getattr(event_obj, "Place", None),
        )

    @staticmethod
    def merge_entities(results: list) -> EntityExtractionResult:
        """
        合并多个片段的实体抽取结果

        策略：以实体名称为键，保留信息最完整的版本
        对于 source_text，合并所有片段的原文（去重后拼接）

        Args:
            results: 多个 EntityExtractionResult 对象列表

        Returns:
            合并去重后的实体结果
        """
        normalizer = Normalizer()
        all_places = {}
        all_orgs = {}
        all_persons = {}

        for result in results:
            # 合并地点实体，以 geo_name 为唯一键
            for place in result.places:
                # 合并键含朝代与现代地名，减少同名不同地点的误合并
                key = (
                    normalizer.normalize_entity_name(place.geo_name),
                    place.DynastyName or "",
                    place.modern_name or ""
                )
                if key not in all_places:
                    all_places[key] = place
                else:
                    # 保留信息更完整的版本
                    existing = all_places[key]
                    ResultMerger._fill_missing_fields(
                        existing,
                        place,
                        ["modern_name", "DynastyName", "Province", "City", "District_County", "Specific_location"]
                    )
                    # 合并 source_text（去重）
                    if place.source_text:
                        existing.source_text = ResultMerger._merge_source_text(
                            existing.source_text, place.source_text
                        )

            # 合并组织实体，以 OrgName 为唯一键
            for org in result.organizations:
                key = (
                    normalizer.normalize_entity_name(org.OrgName),
                    org.DynastyName or "",
                    org.OrgType or ""
                )
                if key not in all_orgs:
                    all_orgs[key] = org
                else:
                    existing = all_orgs[key]
                    ResultMerger._fill_missing_fields(existing, org, ["OrgType", "DynastyName"])
                    # 合并 source_text（去重）
                    if org.source_text:
                        existing.source_text = ResultMerger._merge_source_text(
                            existing.source_text, org.source_text
                        )

            # 合并人物实体，以 PersonName 为唯一键
            for person in result.persons:
                key = (
                    normalizer.normalize_entity_name(person.PersonName),
                    person.DynastyName or "",
                    person.OrgName or ""
                )
                if key not in all_persons:
                    all_persons[key] = person
                else:
                    existing = all_persons[key]
                    ResultMerger._fill_missing_fields(
                        existing,
                        person,
                        ["DynastyName", "OrgName", "Role", "Note"]
                    )
                    # 合并 source_text（去重）
                    if person.source_text:
                        existing.source_text = ResultMerger._merge_source_text(
                            existing.source_text, person.source_text
                        )

        return EntityExtractionResult(
            places=list(all_places.values()),
            organizations=list(all_orgs.values()),
            persons=list(all_persons.values())
        )

    @staticmethod
    def _merge_source_text(existing: str, new: str) -> str:
        """
        合并source_text，去除重复内容
        
        Args:
            existing: 已有的source_text
            new: 新的source_text
            
        Returns:
            合并后的source_text
        """
        if not existing:
            return new
        if not new:
            return existing
        
        # 如果新文本已存在于旧文本中，直接返回旧文本
        if new in existing:
            return existing
        
        # 合并，用换行分隔
        return existing + "\n" + new

    @staticmethod
    def _merge_event_metadata(results: list) -> dict:
        """
        汇总各段事件诊断 metadata（识别数 / 最终数 / 后处理过滤与归并数）。

        **为什么要显式带上它。** `merge_events` 原先把 `events.metadata` 直接丢掉，
        而质量报告的事件诊断项（`identified_event_count`、`postprocess_filtered_count` …）
        正是从这份 metadata 读的——丢了之后它们恒等于"最终事件数/0"，
        等于这几项诊断完全失效（现存 `published/quality_report.json` 就是 881/881/0/0）。
        计数类字段按段求和，其余字段不猜。
        """
        counters = (
            "identified_event_count",
            "final_event_count",
            "event_count_expansion",
            "missing_event_count",
            "postprocess_filtered_count",
            "postprocess_merged_count",
        )
        merged = {}
        chunks_with_metadata = 0
        for result in results:
            metadata = getattr(result, "metadata", None) or {}
            if not metadata:
                continue
            chunks_with_metadata += 1
            for key in counters:
                value = metadata.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    merged[key] = merged.get(key, 0) + value
        if chunks_with_metadata:
            merged["merged_from_chunks"] = chunks_with_metadata
        return merged

    @staticmethod
    def merge_events(results: list) -> EventExtractionResult:
        """
        合并多个片段的事件抽取结果

        策略：完整事件按名称去重，source_text合并，relations合并

        Args:
            results: 多个 EventExtractionResult 对象列表

        Returns:
            合并后的事件结果
        """
        normalizer = Normalizer()
        all_events = {}

        # 收集所有事件，按名称去重
        for result in results:
            for evt in result.events:
                key = ResultMerger._event_merge_key(normalizer, evt)
                if key not in all_events:
                    evt.EventName = normalizer.standardize_event_name(evt.EventName)
                    all_events[key] = evt
                else:
                    existing = all_events[key]
                    if len(evt.EventName or "") > len(existing.EventName or ""):
                        existing.EventName = normalizer.standardize_event_name(evt.EventName)
                    ResultMerger._fill_missing_fields(
                        existing,
                        evt,
                        [
                            "EventType", "EndDate", "DynastyName", "Place", "Aggressor", "Defender",
                            "Allies", "Result", "Commanders", "KeyPersons", "Action", "TroopSize",
                            "Duration", "GeographicScope", "Casualties", "source", "Impact", "Remark"
                        ]
                    )
                    
                    # 合并 source_text（去重）
                    if evt.source_text:
                        existing.source_text = ResultMerger._merge_source_text(
                            existing.source_text, evt.source_text
                        )
                    
                    # 合并 relations
                    if evt.relations:
                        existing.relations = ResultMerger._merge_relations(
                            existing.relations, evt.relations
                        )

        return EventExtractionResult(
            events=list(all_events.values()),
            metadata=ResultMerger._merge_event_metadata(results),
        )

    @staticmethod
    def _merge_relations(existing: list, new: list) -> list:
        """
        合并事件关系列表，去重
        
        Args:
            existing: 已有的关系列表
            new: 新的关系列表
            
        Returns:
            合并去重后的关系列表
        """
        if not existing:
            return new
        if not new:
            return existing
        
        # 使用集合去重，以(type, to)为键
        relation_dict = {}
        
        for rel in existing:
            if isinstance(rel, EventRelation):
                key = (rel.type, rel.to)
                relation_dict[key] = rel
            elif isinstance(rel, dict):
                key = (rel.get('type'), rel.get('to'))
                relation_dict[key] = EventRelation(**rel)
        
        for rel in new:
            if isinstance(rel, EventRelation):
                key = (rel.type, rel.to)
                # 如果关系已存在，保留证据更充分的
                existing_evidence = relation_dict[key].evidence or "" if key in relation_dict else ""
                current_evidence = rel.evidence or ""
                # evidence 是可选字段：先判空再比长度，避免在 None 上取 len
                if key not in relation_dict or len(current_evidence) > len(existing_evidence):
                    relation_dict[key] = rel
            elif isinstance(rel, dict):
                key = (rel.get('type'), rel.get('to'))
                if key not in relation_dict:
                    relation_dict[key] = EventRelation(**rel)
        
        return list(relation_dict.values())

    @staticmethod
    def merge_relations(results: list, event_start_years: dict = None) -> RelationExtractionResult:
        """
        合并多个片段的关系抽取结果

        策略：使用元组 (主体, 关系, 客体) 作为唯一键进行去重；
        事件-事件关系走 `relation_rules.reduce_event_event_relations`（方向与去重规则只那一份）。

        Args:
            results: 多个 RelationExtractionResult 对象列表
            event_start_years: 事件起始年份索引（`build_event_start_years`），
                用于给"顺承/因果"定方向。不传时只按证据定方向，判不出来就不合并反向的两条。

        Returns:
            合并去重后的关系结果
        """
        merged = RelationExtractionResult()

        # 收集所有关系（不同片段可能抽取到相同关系）
        for result in results:
            merged.event_place_relations.extend(result.event_place_relations)
            merged.event_organization_relations.extend(result.event_organization_relations)
            merged.event_person_relations.extend(result.event_person_relations)
            merged.event_event_relations.extend(result.event_event_relations)

        # 去重：转换为不可变元组作为字典键，再转回对象
        def deduplicate(relations, key_func):
            relation_map = {}
            for rel in relations:
                key = key_func(rel)
                existing = relation_map.get(key)
                if existing is None:
                    relation_map[key] = rel
                    continue
                merged_evidence = ResultMerger._merge_evidence_text(
                    getattr(existing, "evidence", None),
                    getattr(rel, "evidence", None),
                )
                keep_rel = rel if len(getattr(rel, "evidence", None) or "") > len(getattr(existing, "evidence", None) or "") else existing
                keep_rel.evidence = merged_evidence
                relation_map[key] = keep_rel
            return list(relation_map.values())

        # 为每类关系定义唯一键生成函数
        normalizer = Normalizer()

        merged.event_place_relations = deduplicate(
            merged.event_place_relations,
            lambda r: (
                normalizer.normalize_event_name(r.EventName),
                normalizer.normalize_relation(r.relation),
                normalizer.normalize_entity_name(r.modern_name),
            )
        )

        merged.event_organization_relations = deduplicate(
            merged.event_organization_relations,
            lambda r: (
                normalizer.normalize_event_name(r.EventName),
                normalizer.normalize_relation(r.relation),
                normalizer.normalize_entity_name(r.OrgName),
            )
        )

        merged.event_person_relations = deduplicate(
            merged.event_person_relations,
            lambda r: (
                normalizer.normalize_event_name(r.EventName),
                normalizer.normalize_relation(r.relation),
                normalizer.normalize_entity_name(r.PersonName),
            )
        )

        merged.event_event_relations = reduce_event_event_relations(
            normalizer,
            merged.event_event_relations,
            event_start_years=event_start_years,
            # 不传 `quarantine`：段间合并不是最后一道收敛，枚举外的条目在这里计数会与
            # `main.cleanup_relation_conflicts` 重复。计数口径见
            # `war_extraction/utils/relation_rules.py::reduce_event_event_relations` 的 docstring。
        )

        return merged
