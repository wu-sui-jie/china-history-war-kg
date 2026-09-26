"""
Shared entity classification helpers for extraction, backfill, and cleanup.
"""
from __future__ import annotations

from typing import Optional

from war_extraction.utils.normalizer import Normalizer


class EntityClassifier:
    """
    人/组织/地点三类的判别与清洗规则集中在这里，抽取与回填不再各维护一套。

    除标记词（`PERSON_MARKERS` / `ORG_MARKERS`）外，还有两张显式覆盖表：
    `PERSON_OVERRIDES`（不带任何称号词、但确是人名的上古人物）与
    `ORG_OVERRIDES`（容易被误判成人名的部族/方国/军队）。

    人名侧没有"整条丢弃"的名单：`秦始皇` / `吴起` 这类合法人物、以及 `孙滨` 这类原书错字
    变体都不该被丢掉——后者走别名归一（见 `Normalizer.ENTITY_ALIASES`），前者只做结构过滤
    （见 `is_valid_person_name`）。
    """

    PERSON_OVERRIDES = {
        "神农", "黄帝", "炎帝", "蚩尤", "尧", "舜", "禹", "启", "益",
        "桀", "商汤", "武丁", "妇好", "帝乙", "纣王", "周文王", "周武王",
        "周公", "成王", "康王", "昭王", "穆王", "秦仲", "尹吉甫", "方叔", "申侯",
        "后羿", "少康", "伯益", "相", "太康", "靡", "吕望", "驩兜",
    }

    ORG_OVERRIDES = {
        "鬼方", "羌方", "东夷", "犬戎", "淮夷", "徐夷", "西戎", "荆楚", "荆蛮",
        "九黎", "三苗", "商军", "周军", "秦军", "赵军", "炎帝族", "黄帝族",
        "有扈氏", "有穷氏", "斟寻氏", "斟灌氏", "崇国", "有仍氏", "有虞氏",
    }

    PERSON_MARKERS = ["王", "公", "侯", "帝", "后", "将军", "太后", "太子", "单于"]
    ORG_MARKERS = ["军", "军队", "部", "部落", "部族", "国", "氏", "政权", "王朝", "联军", "联盟", "夷", "戎", "羌", "狄", "蛮", "族"]
    LOW_QUALITY_ORG_NAMES = {
        "下旨", "南征", "东征", "西征", "北伐", "战争", "之战", "作战",
        "三个宗族集团", "夷族", "诸侯国", "方国", "四周方国", "夷",
    }

    KIND_MAPPING = {
        "place": "place",
        "location": "place",
        "geo": "place",
        "地点": "place",
        "地名": "place",
        "person": "person",
        "人物": "person",
        "人名": "person",
        "organization": "organization",
        "organisation": "organization",
        "org": "organization",
        "group": "organization",
        "force": "organization",
        "faction": "organization",
        "组织": "organization",
        "势力": "organization",
        "军队": "organization",
        "国家": "organization",
        "政权": "organization",
        "部族": "organization",
        "方国": "organization",
        "族群": "organization",
    }

    @classmethod
    def normalize_kind(cls, value: Optional[str]) -> str:
        if not value:
            return ""
        return cls.KIND_MAPPING.get(str(value).strip().lower(), "")

    @classmethod
    def looks_like_person_name(cls, value: Optional[str]) -> bool:
        if not value:
            return False
        stripped = str(value).strip()
        if stripped in cls.ORG_OVERRIDES or len(stripped) > 8:
            return False
        if stripped in cls.PERSON_OVERRIDES:
            return True
        return any(marker in stripped for marker in cls.PERSON_MARKERS)

    @classmethod
    def looks_like_org_name(cls, value: Optional[str]) -> bool:
        if not value:
            return False
        stripped = str(value).strip()
        if stripped in cls.PERSON_OVERRIDES:
            return False
        if stripped in cls.ORG_OVERRIDES:
            return True
        if stripped in cls.LOW_QUALITY_ORG_NAMES:
            return False
        return any(marker in stripped for marker in cls.ORG_MARKERS)

    @classmethod
    def normalize_person_name(cls, value: Optional[str]) -> str:
        # 人名别名表只有 Normalizer.ENTITY_ALIASES 一份，本模块不另存副本
        if not value:
            return ""
        stripped = str(value).strip()
        return Normalizer.ENTITY_ALIASES.get(stripped, stripped)

    @classmethod
    def normalize_org_name(cls, value: Optional[str]) -> str:
        if not value:
            return ""
        stripped = str(value).strip()
        if stripped in {"东夷的军队", "东夷偃姓"}:
            return "东夷"
        if stripped.startswith("三苗"):
            return "三苗"
        if stripped.startswith("九黎"):
            return "九黎"
        if stripped in {"舜的部落", "舜部落联盟"}:
            return "舜部落"
        if stripped in {"尧部落联盟"}:
            return "尧部落"
        return stripped

    @classmethod
    def is_valid_person_name(cls, value: Optional[str]) -> bool:
        stripped = cls.normalize_person_name(value)
        if not stripped or len(stripped) > 12:
            return False
        # 结构过滤：既像组织又不像人的一律拦掉（"商军"这类）
        if cls.looks_like_org_name(stripped) and not cls.looks_like_person_name(stripped):
            return False
        return True

    @classmethod
    def is_valid_org_name(cls, value: Optional[str]) -> bool:
        stripped = cls.normalize_org_name(value)
        if not stripped or len(stripped) > 20:
            return False
        if stripped in cls.LOW_QUALITY_ORG_NAMES:
            return False
        if stripped.endswith(("集团", "势力")) and len(stripped) <= 6:
            return False
        return True

    @classmethod
    def candidate_name(cls, item: dict, keys: list[str]) -> str:
        for key in keys:
            value = item.get(key)
            if value not in (None, ""):
                return str(value).strip()
        return ""
