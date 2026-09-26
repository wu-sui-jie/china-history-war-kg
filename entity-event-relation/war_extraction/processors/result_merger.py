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
        """事件合并键：规范化名称 + 朝代，让重叠分段里的同一事件能归并到一起。"""
        normalized_name = normalizer.normalize_event_name(event_obj.EventName)
        if normalized_name:
            return (normalized_name, event_obj.DynastyName or "")
        return (
            normalized_name,
            event_obj.DynastyName or "",
            event_obj.StartDate or "",
            normalizer.normalize_entity_name(event_obj.Place or "")
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

        return EventExtractionResult(events=list(all_events.values()))

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
    def merge_relations(results: list) -> RelationExtractionResult:
        """
        合并多个片段的关系抽取结果

        策略：使用元组 (主体, 关系, 客体) 作为唯一键进行去重

        Args:
            results: 多个 RelationExtractionResult 对象列表

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

        merged.event_event_relations = deduplicate(
            [
                r for r in merged.event_event_relations
                if normalizer.normalize_event_name(r.EventName_A) != normalizer.normalize_event_name(r.EventName_B)
            ],
            lambda r: (
                normalizer.normalize_event_name(r.EventName_A),
                normalizer.normalize_relation(r.relation),
                normalizer.normalize_event_name(r.EventName_B),
            )
        )

        for rel in merged.event_event_relations:
            rel.relation = normalizer.normalize_relation(rel.relation)

        # 事件-事件关系里"顺承/因果"是有方向的：分段合并后把 A→B 与 B→A 收敛成一条，
        # 方向按事件名字典序固定（`EventName_A > EventName_B` 就交换两端）——注意这是
        # 已有的取舍，方向由字典序而非历史时间决定，后续由 evidence 顺序再校正一次。
        reduced_event_rels = {}
        for rel in merged.event_event_relations:
            name_a = normalizer.normalize_event_name(rel.EventName_A)
            name_b = normalizer.normalize_event_name(rel.EventName_B)
            if rel.relation in {"顺承关系", "因果关系"}:
                pair_key = tuple(sorted([name_a, name_b])) + (rel.relation,)
                preferred = rel
                if rel.EventName_A > rel.EventName_B:
                    preferred = type(rel)(
                        EventName_A=rel.EventName_B,
                        relation=rel.relation,
                        EventName_B=rel.EventName_A,
                        evidence=rel.evidence,
                    )
                existing = reduced_event_rels.get(pair_key)
                if existing is None:
                    reduced_event_rels[pair_key] = preferred
                else:
                    preferred.evidence = ResultMerger._merge_evidence_text(existing.evidence, preferred.evidence)
                    if len(preferred.evidence or "") > len(existing.evidence or ""):
                        reduced_event_rels[pair_key] = preferred
            else:
                pair_key = (name_a, rel.relation, name_b)
                existing = reduced_event_rels.get(pair_key)
                if existing is None:
                    reduced_event_rels[pair_key] = rel
                else:
                    existing.evidence = ResultMerger._merge_evidence_text(existing.evidence, rel.evidence)
        merged.event_event_relations = list(reduced_event_rels.values())

        return merged
