# -*- coding: utf-8 -*-
"""
决策项收口的回归保护（2026-09-25）。

三项裁定都来自第 11 轮留下的"待口径确认"，依据均为实测
（完整证据见 `docs/修复实施记录-决策项收口-20260925.md`）：

1. **`秦始皇` / `吴起` 不再作"低质量人名"整条丢弃**。它们原先挂在
   `EntityClassifier.LOW_QUALITY_PERSON_NAMES` 里，而实测两者都在人工标注里各出现 1 次
   （`data/annotations/sample_entities.json`）、预测 persons 里各 0 次——实体评估只比对
   `PersonName`，所以这份名单保证它们永远配不上，直接贡献 2 个 FN。模型其实抽出来了，
   只是散在别处：关系里 `吴起` 作「统帅」、`秦始皇` 作「君主」各 2 条，
   事件的 `Commanders` / `KeyPersons` 里也都有。
2. **占位词排除集统一为宽口径**。`main.py` 原先用窄集（只排除"不详/null"），于是
   `"甲、未知、乙"` 里的"未知"会被当成真名字留下来；窄集现已删除。
3. **`utils/alignment.py` 已删除**。`AlignmentTool` 零引用（此前只被第 10 轮删掉的
   `evaluator.py` 用过），`utils/__init__.py` 也不导出它——留着就是一份会被误认成
   "官方对齐逻辑"的死代码。

**注意这是抽取产物的口径变更**：第 1、2 项都在抽取阶段生效，只在下一次抽取的产物里可见；
当前产物与评估指标**不受影响**（改前/改后对同一份产物各跑一次评估，除
`metadata.evaluated_at` 外逐字段相同——对照见实施记录）。
"""
import importlib
import os

import pytest

from war_extraction.utils import EntityClassifier, value_parsing


# ---------------------------------------------- 1. 人名过滤名单已删除

def test_low_quality_person_names_filter_is_gone():
    """`LOW_QUALITY_PERSON_NAMES` 已删除，不能再悄悄长回来。"""
    assert not hasattr(EntityClassifier, "LOW_QUALITY_PERSON_NAMES")


def test_qin_shihuang_and_wu_qi_are_valid_person_names():
    """这两条是合法人物（关系里作「君主」「统帅」），不该被整条丢掉。"""
    assert EntityClassifier.is_valid_person_name("秦始皇") is True
    assert EntityClassifier.is_valid_person_name("吴起") is True


def test_other_person_filters_still_work():
    """删掉名单不等于取消过滤：空值、超长、以及"像组织不像人"的仍要拦住。"""
    assert EntityClassifier.is_valid_person_name("") is False
    assert EntityClassifier.is_valid_person_name("一" * 13) is False
    assert EntityClassifier.is_valid_person_name("商军") is False


# ---------------------------------------------- 2. 占位词排除集已统一

def test_placeholder_set_unified_to_wide():
    """窄集已删除；默认（不传参）即宽口径，占位词一律不算值。"""
    assert not hasattr(value_parsing, "PLACEHOLDERS_MINIMAL")
    for text in ("甲、未知、乙", "甲、无、乙", "甲、None、乙", "甲、null、乙", "甲、不详、乙"):
        assert value_parsing.split_multi_value(text) == ["甲", "乙"], text


def test_main_pipeline_uses_the_unified_wide_set():
    """main.py 的多值拆分包装函数必须与统一口径一致（不再显式传窄集）。"""
    import main as main_module

    assert main_module._split_multi_value("甲、未知、乙") == ["甲", "乙"]
    assert main_module._split_multi_value("甲、无、乙") == ["甲", "乙"]


# ---------------------------------------------- 3. AlignmentTool 已删除

def test_alignment_module_is_removed():
    """零引用的孤儿模块已删除；留着会让人误以为它是官方对齐逻辑。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert not os.path.exists(os.path.join(root, "war_extraction", "utils", "alignment.py"))
    with pytest.raises(ImportError):
        importlib.import_module("war_extraction.utils.alignment")
