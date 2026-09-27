# -*- coding: utf-8 -*-
"""
三条抽取口径的回归保护：人名怎么处理、占位词怎么算、哪些孤儿模块不许存在，
外加「过宽概括」判定只有一份表（第三阶段 D5）。

1. **人名不做"整条丢弃"**。`EntityClassifier` 上不存在"低质量人名"名单：`秦始皇` / `吴起`
   都是合法人物（人工标注里各出现 1 次）；整条丢掉会让预测压根不产生这个名字，
   连配对的机会都没有，等于白送 FN。人名只做归一与结构过滤。
2. **占位词排除集只有一套宽口径**（含"未知/无/None"）。若一边用窄集（只排除"不详/null"），
   `"甲、未知、乙"` 里的"未知"就会被当成真名字留在字段里。
3. **`utils/alignment.py` 不存在**。`AlignmentTool` 零引用，留着就是一份会被误认成
   "官方对齐逻辑"的死代码。
4. **「过宽概括」的特征表只有一份**（`config/publish_rules.json` 的 `overbroad_event_markers`）：
   原先 `main` 与抽取器各写一张，口径还不同。

**注意第 1、2、4 项是抽取阶段的口径**：只在下一次抽取的产物里见效，当前产物与评估指标不受影响。
"""
import importlib
import json
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


# ---------------------------------------------- 4. 「过宽概括」判定只有一份表（第三阶段 D5）

def test_合并后的特征表是两张表的并集():
    """
    合并**不是**"挑一张留下"：原先 `main._is_summary_only_event` 有 4 个文本标记 + 2 个事件名，
    `EventExtractor._is_summary_style_event` 有 4 个名称标记 + 6 个文本标记，两边各有独占项
    （`原文仅提及事件名称` 只有前者有；`几次大决战`/`此后`/`继后`/`远征` 只有后者有）。
    合并后这些必须**都还在**——漏掉任何一项都等于悄悄放宽/收紧了过滤，
    而两条链路的产物形态差异要等重跑才看得出来。
    """
    from war_extraction.utils.publish_rules import load_publish_rules

    markers = load_publish_rules()["overbroad_event_markers"]
    # main 侧原有
    assert "原文仅提及事件名称" in markers["text_markers"]
    assert set(markers["event_names"]) == {"少康中兴", "商代之远征"}
    # 抽取器侧原有
    assert set(markers["event_name_markers"]) == {"时期", "系列", "多路征伐", "远征"}
    for marker in ("几次大决战", "此后", "继后", "曾北征南伐"):
        assert marker in markers["text_markers"], marker


def test_过宽概括判定读同一份配置(tmp_path, monkeypatch):
    """
    **"config 改一处、main 与抽取器同时变化"**（工作单 D5 的验收口径）。

    做法是把两边指向同一份临时配置：`publish_rules.DEFAULT_CONFIG_PATH` 换成它
    （抽取器不传 `rules`，走默认加载），`main.PUBLISH_RULES` 也换成从它加载的结果
    （那是模块级常量，import 时就定了）。两边都翻转才说明它们读的是同一份表。
    """
    import main as main_module
    from war_extraction.extractors.event_extractor import EventExtractor
    from war_extraction.models import Event
    from war_extraction.utils import publish_rules

    config = tmp_path / "publish_rules.json"
    config.write_text(json.dumps({
        "overbroad_event_markers": {
            "event_name_markers": ["临时名称标记"],
            "text_markers": ["临时文本标记"],
            "event_names": ["临时事件名"],
        }
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(publish_rules, "DEFAULT_CONFIG_PATH", config)
    monkeypatch.setattr(main_module, "PUBLISH_RULES", publish_rules.load_publish_rules(config))

    extractor = EventExtractor(None)  # 只调判定，不碰模型

    # 名称标记
    by_name = Event(EventName="某临时名称标记之战")
    assert main_module._is_summary_only_event(by_name) is True
    assert extractor._is_summary_style_event(by_name.EventName, "") is True

    # 文本标记（main 看的是事件名 + source_text + Remark，抽取器看的是事件名 + evidence）
    evidence = "这里出现临时文本标记"
    by_text = Event(EventName="乙战", source_text=evidence)
    assert main_module._is_summary_only_event(by_text) is True
    assert extractor._is_summary_style_event("乙战", evidence) is True

    # 名单精确相等
    listed = Event(EventName="临时事件名")
    assert main_module._is_summary_only_event(listed) is True
    assert extractor._is_summary_style_event("临时事件名", "") is True

    # 不含任何标记的事件两边都不认（不然"改一处生效两处"可能只是两边都恒 True）
    plain = Event(EventName="牧野之战", source_text="周武王率诸侯之师与商军战于牧野")
    assert main_module._is_summary_only_event(plain) is False
    assert extractor._is_summary_style_event("牧野之战", plain.source_text) is False
