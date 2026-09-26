# -*- coding: utf-8 -*-
"""
三条抽取口径的回归保护：人名怎么处理、占位词怎么算、哪些孤儿模块不许存在。

1. **人名不做"整条丢弃"**。`EntityClassifier` 上不存在"低质量人名"名单：`秦始皇` / `吴起`
   都是合法人物（人工标注里各出现 1 次）；整条丢掉会让预测压根不产生这个名字，
   连配对的机会都没有，等于白送 FN。人名只做归一与结构过滤。
2. **占位词排除集只有一套宽口径**（含"未知/无/None"）。若一边用窄集（只排除"不详/null"），
   `"甲、未知、乙"` 里的"未知"就会被当成真名字留在字段里。
3. **`utils/alignment.py` 不存在**。`AlignmentTool` 零引用，留着就是一份会被误认成
   "官方对齐逻辑"的死代码。

**注意第 1、2 项是抽取阶段的口径**：只在下一次抽取的产物里见效，当前产物与评估指标不受影响。
"""
import importlib
import os

import pytest

from war_extraction.utils import EntityClassifier, value_parsing


# ---------------------------------------------- 1. 不存在"低质量人名"名单

def test_low_quality_person_names_filter_is_gone():
    """`EntityClassifier` 上不许再长出"低质量人名"名单。"""
    assert not hasattr(EntityClassifier, "LOW_QUALITY_PERSON_NAMES")


def test_qin_shihuang_and_wu_qi_are_valid_person_names():
    """这两条是合法人物（关系里作「君主」「统帅」），不该被整条丢掉。"""
    assert EntityClassifier.is_valid_person_name("秦始皇") is True
    assert EntityClassifier.is_valid_person_name("吴起") is True


def test_other_person_filters_still_work():
    """没有名单不等于没有过滤：空值、超长、以及"像组织不像人"的仍要拦住。"""
    assert EntityClassifier.is_valid_person_name("") is False
    assert EntityClassifier.is_valid_person_name("一" * 13) is False
    assert EntityClassifier.is_valid_person_name("商军") is False


# ---------------------------------------------- 2. 占位词排除集只有宽口径一套

def test_placeholder_set_unified_to_wide():
    """默认（不传参）即宽口径：占位词一律不算值，不存在"窄集"可选值。"""
    assert not hasattr(value_parsing, "PLACEHOLDERS_MINIMAL")
    for text in ("甲、未知、乙", "甲、无、乙", "甲、None、乙", "甲、null、乙", "甲、不详、乙"):
        assert value_parsing.split_multi_value(text) == ["甲", "乙"], text


def test_main_pipeline_uses_the_unified_wide_set():
    """main.py 的多值拆分包装函数必须与统一口径一致。"""
    import main as main_module

    assert main_module._split_multi_value("甲、未知、乙") == ["甲", "乙"]
    assert main_module._split_multi_value("甲、无、乙") == ["甲", "乙"]


# ---------------------------------------------- 3. AlignmentTool 不许存在

def test_alignment_module_is_removed():
    """零引用的孤儿模块不许出现；留着会让人误以为它是官方对齐逻辑。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert not os.path.exists(os.path.join(root, "war_extraction", "utils", "alignment.py"))
    with pytest.raises(ImportError):
        importlib.import_module("war_extraction.utils.alignment")
