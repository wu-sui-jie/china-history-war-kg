"""
实体数据模型
定义地点、组织、人物三类实体的Pydantic模型
"""

from pydantic import BaseModel, Field
from typing import List, Optional


class PlaceEntity(BaseModel):
    """地点实体模型"""
    geo_name: str
    modern_name: Optional[str] = None
    DynastyName: Optional[str] = None
    Province: Optional[str] = None
    City: Optional[str] = None
    District_County: Optional[str] = None
    Specific_location: Optional[str] = None
    source_text: Optional[str] = None  # 原文片段


class OrganizationEntity(BaseModel):
    """组织实体模型"""
    OrgName: str
    OrgType: Optional[str] = None
    DynastyName: Optional[str] = None
    source_text: Optional[str] = None  # 原文片段


class PersonEntity(BaseModel):
    """人物实体模型"""
    PersonName: str
    DynastyName: Optional[str] = None
    OrgName: Optional[str] = None
    Role: Optional[str] = None
    Note: Optional[str] = None
    source_text: Optional[str] = None  # 原文片段


class EntityExtractionResult(BaseModel):
    """实体抽取结果容器"""
    places: List[PlaceEntity] = Field(default_factory=list)
    organizations: List[OrganizationEntity] = Field(default_factory=list)
    persons: List[PersonEntity] = Field(default_factory=list)

    def model_dump(self, **kwargs):
        """自定义序列化，确保 None 值输出为 null"""
        data = super().model_dump(**kwargs)

        for place in data.get("places", []):
            place.setdefault("geo_name", None)
            place.setdefault("modern_name", None)
            place.setdefault("DynastyName", None)
            place.setdefault("Province", None)
            place.setdefault("City", None)
            place.setdefault("District_County", None)
            place.setdefault("Specific_location", None)
            place.setdefault("source_text", None)

        for org in data.get("organizations", []):
            org.setdefault("OrgName", None)
            org.setdefault("OrgType", None)
            org.setdefault("DynastyName", None)
            org.setdefault("source_text", None)

        for person in data.get("persons", []):
            person.setdefault("PersonName", None)
            person.setdefault("DynastyName", None)
            person.setdefault("OrgName", None)
            person.setdefault("Role", None)
            person.setdefault("Note", None)
            person.setdefault("source_text", None)

        return data
