"""
Shared entity classification helpers for extraction, backfill, and cleanup.
"""
from __future__ import annotations

from typing import Optional

from war_extraction.utils.normalizer import Normalizer


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
    #: 人名过滤名单**已删除**（2026-09-25 决策项收口，用户裁定"删掉"）。
    #: 这里原先有 `LOW_QUALITY_PERSON_NAMES = {"秦始皇", "吴起"}`，把这两条整条丢弃。删除依据（实测）：
    #: 两者都在人工标注里各出现 1 次（data/annotations/sample_entities.json），而预测 persons 里各 0 次
    #: ——实体评估只比对 PersonName，所以那份名单保证它们永远配不上，直接贡献 2 个 FN。模型其实抽出来了，
    #: 只是散在别处：`relations.event_person_relations[].PersonName` 里 `吴起` 作「统帅」、
    #: `秦始皇` 作「君主」各 2 条，事件的 `Commanders` / `KeyPersons` 里也都有。
    #: **注意**：过滤发生在抽取阶段（main.py 建 persons 列表时），删掉它只在下一次抽取的产物里见效；
    #: 当前产物与评估指标不受影响（改前/改后对同一份产物各跑一次评估，除时间戳外逐字段相同）。
    #: 另注："孙滨"更早也在这份名单里，那本来就是错的——它是"孙膑"的原书错字变体，
    #: 已改为别名归一（见 Normalizer.ENTITY_ALIASES），不该整条丢弃。

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
        # Changed 2026-09-25（第 11 轮 C-5）：原先这里硬编码三条人名别名
        # （神农氏/商纣王/夏桀），与 Normalizer.ENTITY_ALIASES 是同内容的第二份——
        # 现在只保留 normalizer 那一份，别名表在本模块里不再重复。
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
        # 原先这里还有一条"在 LOW_QUALITY_PERSON_NAMES 里就丢弃"——该名单已于 2026-09-25 删除，
        # 理由见类顶部注释。
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
