"""
Shared entity classification helpers for extraction, backfill, and cleanup.
"""

from typing import Optional


class EntityClassifier:
    """
    Changed 2026-04-21 13:57:14 +08:00: Centralize person/org/place
    disambiguation so extraction and backfill no longer maintain two rule sets.
    Changed 2026-04-21 16:46:12 +08:00: Add stricter low-quality filters and
    canonical cleanup for final export.
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
    LOW_QUALITY_PERSON_NAMES = {"秦始皇", "吴起", "孙滨"}

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
        if not value:
            return ""
        stripped = str(value).strip()
        if stripped == "神农氏":
            return "神农"
        if stripped == "商纣王":
            return "纣王"
        if stripped == "夏桀":
            return "桀"
        return stripped

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
        if stripped in cls.LOW_QUALITY_PERSON_NAMES:
            return False
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
