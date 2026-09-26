"""
实体抽取器
使用分离的模型和提示词模板
"""

from war_extraction.core.llm_client import LLMAuthError, LLMAPIError
from war_extraction.models import (
    PlaceEntity,
    OrganizationEntity,
    PersonEntity,
    EntityExtractionResult
)
from war_extraction.prompts import ENTITY_EXTRACTION_PROMPT
from war_extraction.config import PROMPT_VERSIONS
from war_extraction.utils import EntityClassifier
from war_extraction.utils.json_payload import extract_json_payload


class EntityExtractor:
    """实体抽取器"""

    def __init__(self, llm_client):
        self.llm = llm_client
        self.prompt_template = ENTITY_EXTRACTION_PROMPT

    def _flatten_entities(self, value) -> list:
        """把模型可能给出的嵌套列表压平成一维的实体字典列表。"""
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
        """粗分类之后解决人/组织/地点三类冲突（按覆盖表与判别规则把条目挪到正确的一类）。"""
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
        """把模型返回的 dict / list 两种实体 JSON 形态都规整成三键字典。"""
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
            # 优先请求 JSON 输出；API 不支持时 llm_client 内部会回退
            response = self.llm.call(prompt, json_mode=True)

            # 用 json_payload 的公共扫描逻辑，list / 根包装类载荷也能从模型输出里救回来
            data = extract_json_payload(response)
            if data is None:
                print("实体抽取 JSON 解析失败: 未找到可用 JSON")
                print(f"原始响应前500字符: {response[:500]}...")
                raise ValueError("Entity extraction response did not contain valid JSON")

            # 确保所有实体字段完整
            data = self._normalize_response_data(data)
            data = self._disambiguate_entities(data)

            # 只读规整后的字典：list 形态的响应不会再触发 `.get` 报错
            places = [self._ensure_complete_place(p) for p in data.get("places", [])]
            organizations = [self._ensure_complete_org(o) for o in data.get("organizations", [])]
            persons = [self._ensure_complete_person(p) for p in data.get("persons", [])]

            # 丢掉空行：模型输出里的包装对象不该变成无效实体
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
            # 必须上抛，不能吞掉异常返回空结果：调用方会把「调用失败」当成「本段确实无实体」
            # 写进缓存，失败片段就被永久污染（只能 --refresh-cache 手工救）。
            # 上抛后由编排层判失败、不落缓存、下次自动重试。
            print(f"实体抽取失败: {e}")
            raise
