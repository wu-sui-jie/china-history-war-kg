"""
实体抽取器
使用分离的模型和提示词模板
"""

import json

from src.core.llm_client import LLMAuthError, LLMAPIError
from src.models import (
    PlaceEntity,
    OrganizationEntity,
    PersonEntity,
    EntityExtractionResult
)
from src.prompts import ENTITY_EXTRACTION_PROMPT
from src.config import PROMPT_VERSIONS
from src.utils import EntityClassifier


class EntityExtractor:
    """实体抽取器"""

    def __init__(self, llm_client):
        self.llm = llm_client
        self.prompt_template = ENTITY_EXTRACTION_PROMPT

    def _extract_json_payload(self, response: str):
        """Changed 2026-04-21 12:48:22 +08:00: Parse dict or list JSON payloads from LLM output."""
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

    def _flatten_entities(self, value) -> list:
        """Changed 2026-04-20 23:06:12 +08:00: Flatten nested entity lists from LLM output."""
        if value is None:
            return []
        if isinstance(value, dict):
            return [value]
        if not isinstance(value, list):
            return []

        flattened = []
        for item in value:
            if isinstance(item, list):
                flattened.extend(self._flatten_entities(item))
            elif isinstance(item, dict):
                flattened.append(item)
        return flattened

    def _disambiguate_entities(self, data: dict) -> dict:
        """Changed 2026-04-21 13:46:29 +08:00: Resolve person/org/place conflicts after coarse classification."""
        places = []
        organizations = []
        persons = []
        place_names = set()
        org_names = set()
        person_names = set()

        for item in data.get("places", []):
            name = EntityClassifier.candidate_name(item, ["geo_name", "name", "entity_name", "text"])
            if name in EntityClassifier.PERSON_OVERRIDES:
                if name not in person_names:
                    persons.append(item)
                    person_names.add(name)
                continue
            if name in EntityClassifier.ORG_OVERRIDES:
                if name not in org_names:
                    organizations.append(item)
                    org_names.add(name)
                continue
            if name and name not in place_names:
                places.append(item)
                place_names.add(name)

        for item in data.get("organizations", []):
            name = EntityClassifier.candidate_name(item, ["OrgName", "name", "entity_name", "text"])
            if name in EntityClassifier.PERSON_OVERRIDES:
                if name not in person_names:
                    persons.append(item)
                    person_names.add(name)
                continue
            if name and name not in org_names:
                organizations.append(item)
                org_names.add(name)

        for item in data.get("persons", []):
            name = EntityClassifier.candidate_name(item, ["PersonName", "name", "entity_name", "text"])
            if name in EntityClassifier.ORG_OVERRIDES:
                if name not in org_names:
                    organizations.append(item)
                    org_names.add(name)
                continue
            if name and name not in person_names:
                persons.append(item)
                person_names.add(name)

        return {"places": places, "organizations": organizations, "persons": persons}

    def _looks_like_place(self, item: dict) -> bool:
        kind = EntityClassifier.normalize_kind(
            item.get("type") or item.get("entity_type") or item.get("category") or item.get("label")
        )
        if kind == "place":
            return True
        if kind in {"person", "organization"}:
            return False
        return any(key in item for key in [
            "geo_name", "modern_name", "Province", "City", "District_County", "District", "Specific_location"
        ])

    def _looks_like_org(self, item: dict) -> bool:
        kind = EntityClassifier.normalize_kind(
            item.get("type") or item.get("entity_type") or item.get("category") or item.get("label")
        )
        if kind == "organization":
            return True
        if kind in {"person", "place"}:
            return False
        if any(key in item for key in ["OrgName", "OrgType"]):
            return True
        return EntityClassifier.looks_like_org_name(
            item.get("name") or item.get("entity_name") or item.get("value") or item.get("text")
        )

    def _looks_like_person(self, item: dict) -> bool:
        kind = EntityClassifier.normalize_kind(
            item.get("type") or item.get("entity_type") or item.get("category") or item.get("label")
        )
        if kind == "person":
            return True
        if kind in {"organization", "place"}:
            return False
        if any(key in item for key in ["PersonName", "Role", "Note"]):
            return True
        return EntityClassifier.looks_like_person_name(
            item.get("name") or item.get("entity_name") or item.get("value") or item.get("text")
        )

    def _normalize_response_data(self, data) -> dict:
        """Changed 2026-04-20 23:06:12 +08:00: Accept dict/list entity JSON shapes from DeepSeek."""
        normalized = {
            "places": [],
            "organizations": [],
            "persons": []
        }

        if isinstance(data, dict):
            if isinstance(data.get("entities"), dict):
                data = data["entities"]
            elif isinstance(data.get("entities"), list):
                data = data["entities"]
            else:
                normalized["places"] = self._flatten_entities(
                    data.get("places", []) or data.get("location_entities", [])
                )
                normalized["organizations"] = self._flatten_entities(
                    data.get("organizations", []) or data.get("orgs", []) or data.get("forces", [])
                )
                normalized["persons"] = self._flatten_entities(
                    data.get("persons", []) or data.get("people", []) or data.get("person_entities", [])
                )
                if any(normalized.values()):
                    return normalized

        for item in self._flatten_entities(data):
            if self._looks_like_person(item):
                normalized["persons"].append(item)
            elif self._looks_like_org(item):
                normalized["organizations"].append(item)
            elif self._looks_like_place(item):
                normalized["places"].append(item)

        return normalized

    def _ensure_complete_place(self, place: dict) -> dict:
        """确保地点实体所有字段都存在"""
        return {
            "geo_name": place.get("geo_name") or place.get("name") or place.get("entity_name") or place.get("text"),
            "modern_name": place.get("modern_name"),
            "DynastyName": place.get("DynastyName") or place.get("dynasty"),
            "Province": place.get("Province"),
            "City": place.get("City"),
            "District_County": place.get("District_County") or place.get("District"),
            "Specific_location": place.get("Specific_location"),
            "source_text": place.get("source_text") or place.get("evidence")
        }

    def _ensure_complete_org(self, org: dict) -> dict:
        """确保组织实体所有字段都存在"""
        return {
            "OrgName": org.get("OrgName") or org.get("name") or org.get("entity_name") or org.get("text"),
            "OrgType": org.get("OrgType") or org.get("type_name") or org.get("subtype"),
            "DynastyName": org.get("DynastyName") or org.get("dynasty"),
            "source_text": org.get("source_text") or org.get("evidence")
        }

    def _ensure_complete_person(self, person: dict) -> dict:
        """确保人物实体所有字段都存在"""
        return {
            "PersonName": person.get("PersonName") or person.get("name") or person.get("entity_name") or person.get("text"),
            "DynastyName": person.get("DynastyName") or person.get("dynasty"),
            "OrgName": person.get("OrgName") or person.get("organization") or person.get("force"),
            "Role": person.get("Role"),
            "Note": person.get("Note"),
            "source_text": person.get("source_text") or person.get("evidence")
        }

    def extract(self, text: str) -> EntityExtractionResult:
        """执行实体抽取"""
        prompt = self.prompt_template.render(
            text=text,
            prompt_version=PROMPT_VERSIONS["entity_extraction"]
        )

        try:
            # Changed 2026-04-20 21:46:02 +08:00: Ask the model for JSON
            # where supported, while llm_client falls back if the API rejects it.
            response = self.llm.call(prompt, json_mode=True)

            # Changed 2026-04-21 12:48:22 +08:00: Use decoder scanning so
            # list/root-wrapper JSON payloads can be recovered from model output.
            data = self._extract_json_payload(response)
            if data is None:
                print("实体抽取 JSON 解析失败: 未找到可用 JSON")
                print(f"原始响应前500字符: {response[:500]}...")
                raise ValueError("Entity extraction response did not contain valid JSON")

            # 确保所有实体字段完整
            data = self._normalize_response_data(data)
            data = self._disambiguate_entities(data)

            # Changed 2026-04-20 23:06:12 +08:00: Read only normalized dict
            # data so list-shaped model responses no longer trigger `.get` errors.
            places = [self._ensure_complete_place(p) for p in data.get("places", [])]
            organizations = [self._ensure_complete_org(o) for o in data.get("organizations", [])]
            persons = [self._ensure_complete_person(p) for p in data.get("persons", [])]

            # Changed 2026-04-21 12:48:22 +08:00: Drop empty rows so stray
            # wrapper objects from LLM output do not become invalid entities.
            places = [p for p in places if p.get("geo_name")]
            organizations = [o for o in organizations if o.get("OrgName")]
            persons = [p for p in persons if p.get("PersonName")]

            return EntityExtractionResult(
                places=[PlaceEntity(**p) for p in places],
                organizations=[OrganizationEntity(**o) for o in organizations],
                persons=[PersonEntity(**p) for p in persons]
            )

        except (LLMAuthError, LLMAPIError):
            raise
        except Exception as e:
            print(f"实体抽取失败: {e}")
            return EntityExtractionResult()
