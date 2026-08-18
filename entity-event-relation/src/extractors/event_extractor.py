"""
Event extractor.
"""
from __future__ import annotations

import json
import re
from typing import List, Tuple

from src.config import PROMPT_VERSIONS
from src.core.llm_client import LLMAuthError, LLMAPIError
from src.models import Event, EventExtractionResult, EventRelation
from src.prompts import EVENT_IDENTIFICATION_PROMPT, EVENT_TYPE_PROMPT, FULL_EVENT_PROMPT
from src.utils import Normalizer


class EventExtractor:
    """Simplified two-stage event extractor."""

    def __init__(self, llm_client):
        self.llm = llm_client
        self.q1_template = EVENT_TYPE_PROMPT
        self.identify_template = EVENT_IDENTIFICATION_PROMPT
        self.q3_template = FULL_EVENT_PROMPT
        # Changed 2026-04-21 12:42:17 +08:00: Split large event lists into
        # smaller batches so one oversized prompt does not zero out a chunk.
        self.full_event_batch_size = 3
        self.normalizer = Normalizer()

    def _extract_json_payload(self, response: str):
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

    def _flatten_list(self, value) -> list:
        if value is None:
            return []
        if isinstance(value, dict):
            return [value]
        if not isinstance(value, list):
            return []
        flattened = []
        for item in value:
            if isinstance(item, list):
                flattened.extend(self._flatten_list(item))
            elif isinstance(item, dict):
                flattened.append(item)
        return flattened

    def _normalize_events_payload(self, data) -> list:
        if isinstance(data, list):
            return self._flatten_list(data)
        if not isinstance(data, dict):
            return []
        for key in ["events", "event_list", "data", "result", "items"]:
            if key in data:
                return self._flatten_list(data.get(key))
        if "EventName" in data or "name" in data:
            return [data]
        return []

    def _ensure_relation_item(self, relation: dict) -> dict:
        return {
            "type": self.normalizer.normalize_relation(relation.get("type") or relation.get("relation") or ""),
            "to": relation.get("to") or relation.get("target") or relation.get("EventName") or "",
            "evidence": relation.get("evidence") or relation.get("source_text") or "",
        }

    def _build_event_name_map(self, event_list: List[Tuple[str, str, str, str, str, str]]) -> dict:
        return {
            event_id: self.normalizer.standardize_event_name(event_name)
            for event_id, event_name, *_ in event_list
            if event_id and event_name
        }

    def _pick_first(self, item: dict, keys: list, default=None):
        for key in keys:
            value = item.get(key)
            if value not in (None, "", []):
                return value
        return default

    def _parse_year_for_order(self, value: str):
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

    def _ensure_chronological_dates(self, event: Event) -> Event:
        """Keep extracted event dates in historical order when both years are parseable."""
        start_year = self._parse_year_for_order(event.StartDate)
        end_year = self._parse_year_for_order(event.EndDate)
        if start_year is None or end_year is None:
            return event
        if start_year > end_year:
            event.StartDate, event.EndDate = event.EndDate, event.StartDate
            remark = "已自动校正开始时间晚于结束时间的问题"
            event.Remark = "\n".join([part for part in [event.Remark, remark] if part])
        return event

    def _build_minimal_event(self, event_tuple: Tuple[str, str, str, str, str, str]) -> Event:
        _, event_name, time_text, location, parties, evidence = event_tuple
        return Event(
            EventName=self.normalizer.standardize_event_name(event_name) or "Unnamed Event",
            StartDate=time_text or None,
            Place=location or None,
            Aggressor=parties or None,
            source_text=evidence or "",
        )

    def _chunk_event_tuples(self, event_list: List[Tuple]) -> List[List[Tuple]]:
        if len(event_list) <= self.full_event_batch_size:
            return [event_list]
        return [event_list[index:index + self.full_event_batch_size] for index in range(0, len(event_list), self.full_event_batch_size)]

    def _deduplicate_identified_events(self, event_list: List[Tuple[str, str, str, str, str, str]]) -> List[Tuple[str, str, str, str, str, str]]:
        """
        Changed 2026-04-21 16:46:12 +08:00: Deduplicate identified events using
        canonical name first, then fall back to time/place context.
        """
        deduplicated = []
        seen = set()
        for item in event_list:
            _, event_name, time_text, location, parties, evidence = item
            canonical_name = self.normalizer.standardize_event_name(event_name)
            key = (
                self.normalizer.normalize_event_name(canonical_name),
                (time_text or "").strip(),
                self.normalizer.normalize_entity_name(location or ""),
            )
            fallback_key = (self.normalizer.normalize_event_name(canonical_name), "", "")
            if key in seen or fallback_key in seen:
                continue
            seen.add(key)
            seen.add(fallback_key)
            deduplicated.append((f"E{len(deduplicated) + 1}", canonical_name, time_text, location, parties, evidence))
        return deduplicated

    def _is_summary_style_event(self, event_name: str, evidence: str) -> bool:
        text = f"{event_name or ''} {evidence or ''}"
        summary_name_markers = ["时期", "系列", "多路征伐", "远征"]
        summary_text_markers = ["主要战争有", "曾北征南伐", "东攻西进", "几次大决战", "此后", "继后"]
        if any(marker in (event_name or "") for marker in summary_name_markers):
            return True
        if any(marker in text for marker in summary_text_markers):
            return True
        return False

    def _looks_like_war_candidate(self, event_name: str, evidence: str) -> bool:
        text = f"{event_name or ''} {evidence or ''}"
        war_markers = ["战", "攻", "伐", "征", "围", "讨", "平叛", "灭", "败", "复国"]
        weak_only_markers = ["建国", "失国", "暴动", "迁都", "会盟"]
        if any(marker in text for marker in war_markers):
            return True
        if any(marker in text for marker in weak_only_markers):
            return False
        return False

    def _is_local_event_candidate(self, event_name: str, evidence: str) -> bool:
        """
        Changed 2026-04-21 17:20:11 +08:00: Drop summary-only future events
        that appear in overview sentences instead of the chunk's local narrative.
        """
        text = f"{event_name or ''} {evidence or ''}"
        summary_markers = ["最后", "尔后的", "春秋战国", "战国七雄", "第一次大统一", "此时期又称"]
        future_event_markers = ["秦始皇", "灭六国"]
        if any(marker in text for marker in future_event_markers) and any(marker in text for marker in summary_markers):
            return False
        if self._is_summary_style_event(event_name, evidence):
            return False
        return True

    def _filter_identified_events(self, event_list: List[Tuple[str, str, str, str, str, str]]) -> List[Tuple[str, str, str, str, str, str]]:
        filtered = []
        for item in event_list:
            if not self._looks_like_war_candidate(item[1], item[5]):
                continue
            if not self._is_local_event_candidate(item[1], item[5]):
                continue
            filtered.append(item)
        return filtered

    def _build_metadata(
        self,
        identified_count: int,
        final_events: List[Event],
        postprocess_filtered_count: int = 0,
        postprocess_merged_count: int = 0,
    ) -> dict:
        summary_like_count = sum(1 for event in final_events if self._is_summary_style_event(event.EventName, event.source_text))
        return {
            "identified_event_count": identified_count,
            "final_event_count": len(final_events),
            "event_count_expansion": len(final_events) - identified_count,
            "missing_event_count": max(identified_count - len(final_events), 0),
            "summary_like_events": summary_like_count,
            "postprocess_filtered_count": postprocess_filtered_count,
            "postprocess_merged_count": postprocess_merged_count,
        }

    def _fill_missing_batch_events(self, event_batch: List[Tuple], batch_events: List[Event]) -> List[Event]:
        completed_keys = {
            self.normalizer.normalize_event_name(event.EventName)
            for event in batch_events
            if event.EventName
        }
        filled_events = list(batch_events)
        for item in event_batch:
            normalized_name = self.normalizer.normalize_event_name(item[1])
            if normalized_name in completed_keys:
                continue
            filled_events.append(self._build_minimal_event(item))
            completed_keys.add(normalized_name)
        return filled_events

    def _postprocess_events(self, events: List[Event]) -> tuple[List[Event], int, int]:
        """
        Changed 2026-04-21 16:46:12 +08:00: Canonicalize names and merge
        near-duplicates using canonical name + dynasty, with place as a tiebreaker.
        """
        filtered = []
        event_map = {}
        filtered_out_count = 0
        merged_count = 0
        for event in events:
            event.EventName = self.normalizer.standardize_event_name(event.EventName)
            if not self._looks_like_war_candidate(event.EventName, event.source_text):
                filtered_out_count += 1
                continue
            if not self._is_local_event_candidate(event.EventName, event.source_text):
                filtered_out_count += 1
                continue
            key = (
                self.normalizer.normalize_event_name(event.EventName),
                # Changed 2026-04-21 17:20:11 +08:00: Collapse final duplicates
                # by canonical event name first; dynasty differences are merged
                # later as complementary metadata.
            )
            if key not in event_map:
                event_map[key] = event
                continue
            merged_count += 1
            existing = event_map[key]
            if len(event.EventName or "") > len(existing.EventName or ""):
                existing.EventName = event.EventName
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
            for rel in event.relations:
                if all(not (r.type == rel.type and r.to == rel.to) for r in existing.relations):
                    existing.relations.append(rel)
        for event in event_map.values():
            filtered.append(self._ensure_chronological_dates(event))
        return filtered, filtered_out_count, merged_count

    def extract_event_type(self, text: str) -> str:
        prompt = self.q1_template.render(text=text, prompt_version=PROMPT_VERSIONS["event_type"])
        return self.llm.call(prompt, temperature=0.1)

    def identify_events(self, text: str) -> List[Tuple[str, str, str, str, str, str]]:
        prompt = self.identify_template.render(text=text, prompt_version=PROMPT_VERSIONS["event_identification"])
        response = self.llm.call(prompt, temperature=0.2, json_mode=True)
        parsed_json = self._parse_identification_json(response)
        if parsed_json is not None:
            return parsed_json
        return self._parse_identification_legacy(response)

    def _parse_identification_json(self, response: str):
        data = self._extract_json_payload(response)
        if data is None:
            return None
        events = []
        for index, event in enumerate(self._normalize_events_payload(data), start=1):
            events.append((
                self._pick_first(event, ["id", "event_id"], f"E{index}"),
                self.normalizer.standardize_event_name(self._pick_first(event, ["name", "EventName", "title"], "")),
                self._pick_first(event, ["time", "StartDate", "date"], ""),
                self._pick_first(event, ["location", "Place", "place"], ""),
                self._pick_first(event, ["parties", "participants", "sides"], ""),
                self._pick_first(event, ["evidence", "source_text", "context"], ""),
            ))
        return events

    def _parse_identification_legacy(self, response: str) -> List[Tuple[str, str, str, str, str, str]]:
        events = []
        current_event = {}
        for line in response.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("事件ID："):
                if current_event:
                    events.append(self._parse_event_dict(current_event))
                current_event = {"id": line.replace("事件ID：", "").strip()}
            elif line.startswith("事件名称："):
                current_event["name"] = line.replace("事件名称：", "").strip()
            elif line.startswith("时间："):
                current_event["time"] = line.replace("时间：", "").strip()
            elif line.startswith("地点："):
                current_event["location"] = line.replace("地点：", "").strip()
            elif line.startswith("交战方："):
                current_event["parties"] = line.replace("交战方：", "").strip()
            elif line.startswith("触发证据："):
                current_event["evidence"] = line.replace("触发证据：", "").strip()
        if current_event:
            events.append(self._parse_event_dict(current_event))
        return events

    def _parse_event_dict(self, event_dict: dict) -> Tuple[str, str, str, str, str, str]:
        return (
            event_dict.get("id", ""),
            self.normalizer.standardize_event_name(event_dict.get("name", "")),
            event_dict.get("time", ""),
            event_dict.get("location", ""),
            event_dict.get("parties", ""),
            event_dict.get("evidence", ""),
        )

    def _ensure_complete_event(self, event: dict) -> dict:
        return {
            "EventName": self.normalizer.standardize_event_name(self._pick_first(event, ["EventName", "name", "title"])),
            "EventType": self._pick_first(event, ["EventType", "type", "event_type"]),
            "StartDate": self._pick_first(event, ["StartDate", "start_date", "time", "date"]),
            "EndDate": self._pick_first(event, ["EndDate", "end_date"]),
            "DynastyName": self._pick_first(event, ["DynastyName", "Dynasty", "dynasty"]),
            "Place": self._pick_first(event, ["Place", "place", "location"]),
            "Aggressor": self._pick_first(event, ["Aggressor", "aggressor", "attacker", "initiator", "parties"]),
            "Defender": self._pick_first(event, ["Defender", "defender", "target"]),
            "Allies": self._pick_first(event, ["Allies", "allies", "supporters"]),
            "Result": self._pick_first(event, ["Result", "result", "outcome"]),
            "Commanders": self._pick_first(event, ["Commanders", "commanders", "leaders"]),
            "KeyPersons": self._pick_first(event, ["KeyPersons", "key_persons", "important_people"]),
            "Action": self._pick_first(event, ["Action", "action"]),
            "TroopSize": self._pick_first(event, ["TroopSize", "troop_size", "forces"]),
            "Duration": self._pick_first(event, ["Duration", "duration"]),
            "GeographicScope": self._pick_first(event, ["GeographicScope", "geographic_scope", "scope"]),
            "Casualties": self._pick_first(event, ["Casualties", "casualties"]),
            "source": self._pick_first(event, ["source", "Source", "reference"]),
            "Impact": self._pick_first(event, ["Impact", "impact"]),
            "Remark": event.get("Remark") or event.get("mem", ""),
            "relations": self._pick_first(event, ["relations", "Relations"], []),
            "source_text": self._pick_first(event, ["source_text", "evidence", "context"], ""),
        }

    def extract_full_events(self, text: str, event_list: List[Tuple], place_list: str = "", org_list: str = "", person_list: str = "") -> List[Event]:
        event_list_str = "\n".join([
            f"{e[0]}: {e[1]} (时间: {e[2]}, 地点: {e[3]}, 交战方: {e[4]})"
            for e in event_list
        ])
        prompt = self.q3_template.render(
            event_list=event_list_str,
            text=text,
            place_list=place_list,
            org_list=org_list,
            person_list=person_list,
            prompt_version=PROMPT_VERSIONS["full_event"],
        )

        try:
            response = self.llm.call(prompt, temperature=0.1, json_mode=True)
            event_name_map = self._build_event_name_map(event_list)
            allowed_event_name_keys = {
                self.normalizer.normalize_event_name(event_name)
                for _, event_name, *_ in event_list
                if event_name
            }
            data = self._extract_json_payload(response)
            if data is None:
                print("事件抽取 JSON 解析失败: 未找到可用 JSON")
                print(f"原始响应前 500 字符: {response[:500]}...")
                return []

            events = []
            for event_data in self._normalize_events_payload(data):
                try:
                    event_identifier = self._pick_first(event_data, ["id", "event_id"], "")
                    complete_event = self._ensure_complete_event(event_data)
                    if event_identifier in event_name_map:
                        complete_event["EventName"] = event_name_map[event_identifier]
                    normalized_name = self.normalizer.normalize_event_name(complete_event.get("EventName"))
                    if not normalized_name or normalized_name not in allowed_event_name_keys:
                        continue
                    relations_data = complete_event.pop("relations", [])
                    relations = []
                    for relation_item in self._flatten_list(relations_data):
                        normalized_relation = self._ensure_relation_item(relation_item)
                        target = normalized_relation.get("to")
                        normalized_relation["to"] = event_name_map.get(target, self.normalizer.standardize_event_name(target))
                        if not normalized_relation.get("type") or not normalized_relation.get("to"):
                            continue
                        if self.normalizer.normalize_event_name(normalized_relation["to"]) == self.normalizer.normalize_event_name(complete_event.get("EventName")):
                            continue
                        relations.append(EventRelation(**normalized_relation))

                    complete_event["relations"] = relations
                    if not complete_event.get("EventName"):
                        complete_event["EventName"] = event_data.get("name") or event_data.get("id") or "Unnamed Event"
                    complete_event["EventName"] = self.normalizer.standardize_event_name(complete_event["EventName"])
                    events.append(self._ensure_chronological_dates(Event(**complete_event)))
                except Exception as item_error:
                    print(f"  警告：单条事件解析失败，已跳过: {item_error}")
            return events
        except (LLMAuthError, LLMAPIError):
            raise
        except Exception as e:
            print(f"事件抽取失败: {e}")
            return []

    def extract(self, text: str, place_list: str = "", org_list: str = "", person_list: str = "") -> EventExtractionResult:
        identified_count = 0
        try:
            identified_events = self.identify_events(text)
            identified_events = self._deduplicate_identified_events(identified_events)
            identified_events = self._filter_identified_events(identified_events)
            identified_count = len(identified_events)
            print(f"识别到 {len(identified_events)} 个战争事件")
            if identified_events:
                for display_index, event in enumerate(identified_events, start=1):
                    print(f"  - E{display_index}: {event[1]}")
        except (LLMAuthError, LLMAPIError):
            raise
        except Exception as e:
            print(f"事件识别失败: {e}")
            identified_events = []

        if not identified_events:
            print("警告：未识别到战争事件")
            return EventExtractionResult(events=[], metadata=self._build_metadata(0, []))

        events = []
        event_batches = self._chunk_event_tuples(identified_events)
        for batch_index, event_batch in enumerate(event_batches, start=1):
            try:
                if len(identified_events) > self.full_event_batch_size:
                    print(f"  完整事件抽取批次 {batch_index}/{len(event_batches)}（本批 {len(event_batch)} 个事件）")
                batch_events = self.extract_full_events(text, event_batch, place_list, org_list, person_list)
            except (LLMAuthError, LLMAPIError):
                raise
            except Exception as e:
                print(f"完整事件抽取失败: {e}")
                batch_events = []

            if batch_events:
                completed_batch_events = self._fill_missing_batch_events(event_batch, batch_events)
                missing_count = len(completed_batch_events) - len(batch_events)
                if missing_count > 0:
                    print(f"  警告：批次 {batch_index} 缺失 {missing_count} 个事件，已用最小事件补回")
                events.extend(completed_batch_events)
            else:
                print(f"  警告：批次 {batch_index} 完整事件为空，改用识别结果生成最小事件")
                events.extend([self._build_minimal_event(item) for item in event_batch])

        final_events, postprocess_filtered_count, postprocess_merged_count = self._postprocess_events(events)
        return EventExtractionResult(
            events=final_events,
            metadata=self._build_metadata(
                identified_count,
                final_events,
                postprocess_filtered_count,
                postprocess_merged_count,
            )
        )
