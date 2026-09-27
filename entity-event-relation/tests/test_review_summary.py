# -*- coding: utf-8 -*-
"""抽样核验汇总（B 组）的口径保护。

`docs/抽样判定规范.md` §8 把 B 组的最后一步写成"按分层与整体算精确率、'无法判断'的比例，
并附 Wilson 置信区间"，并如实记着"汇总脚本还没有"。这个工具一建起来，"口径"就成了
**能写错且看不出来**的东西：分母算不算"无法判断"、小样本用什么区间、错误类型怎么归。
这篇用例钉的就是这几条，免得下一个人顺手改一个口径、数字全变而没人发现。

三条口径（规范 §3/§6/§8）：

1. 精确率的分母是"对 + 错"，**不含"无法判断"**；它的比例单独报；
2. 区间用 **Wilson**（分层只有 8 条时，正态近似会给出超过 100% 的上限）；
3. 错误类型按判据的**固定前缀**统计，且**只统计判"错"的行**。
"""

import sys
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1]
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from tools.summarize_review import (  # noqa: E402
    categorize,
    extract_reason_prefixes,
    summarize,
    wilson_interval,
)

VERDICT = "判定（对/错/无法判断）"


def _row(layer, verdict, reason="对：无需判据"):
    return {"分层": layer, VERDICT: verdict, "判据": reason}


# ------------------------------------------------------------ Wilson 区间

def test_wilson区间与小样本的点估计一致():
    """270/326 的 Wilson 95% 区间应落在 [78%, 87%]（与实测输出一致）。"""
    low, high = wilson_interval(270, 326)
    assert 0.775 < low < 0.795, low
    assert 0.855 < high < 0.875, high


def test_wilson区间不会越界():
    """
    正态近似在极端比例上会给出 `> 1` 的上限或 `< 0` 的下限——那种数字一旦写进文档
    就会被当成真的。分层样本只有 8 条，所以这条是必须的。
    """
    assert wilson_interval(8, 8) == (wilson_interval(8, 8)[0], 1.0)
    assert wilson_interval(0, 8)[0] == 0.0
    low, high = wilson_interval(0, 8)
    assert 0.0 <= low <= high <= 1.0
    low, high = wilson_interval(8, 8)
    assert 0.0 <= low < 1.0 and high == 1.0
    # 分母为 0 不算错，返回空区间（"一条都没判"不是"精确率 0%"）
    assert wilson_interval(0, 0) == (0.0, 0.0)


# ------------------------------------------------------------ 判据前缀

def test_判据前缀按规范提取():
    """规范 §3 的前缀要能从常见的复合判据里全部取出。"""
    text = ("时间：原文未给年份；地点：原文作「崤底（即崤山）」，同一处；"
            "主动方：原文是「商」，产物写「商汤」，同一方")
    assert extract_reason_prefixes(text) == ["时间", "地点", "主动方"]


def test_判据前缀只认带冒号的前缀():
    """
    正文里出现"结果""时间"这些词是常态——只认"前缀 + 冒号"才不会把解释当成错误类型。
    """
    prose = "原文写的结果与产物一致，时间上没有矛盾，这次判定为对"
    assert extract_reason_prefixes(prose) == []
    # 冒号在，但冒号前不是规范前缀（"备注"不在表里）
    assert extract_reason_prefixes("备注：这一条的表本身有问题") == []


def test_判据前缀不误吃长句():
    """冒号前的串太长就不是前缀而是句子（如"原文说时间："这种不能算 `时间`）。"""
    assert extract_reason_prefixes("原文说时间：公元前1046年") == []
    assert extract_reason_prefixes("依原文结果：一致") == []


# ------------------------------------------------------------ 汇总口径

def test_精确率分母不含无法判断():
    rows = [_row("事件:东汉", "对"), _row("事件:东汉", "错", "结果：原文未给结果"),
            _row("事件:东汉", "无法判断", "上下文不足：名称定位到的是另一段")]
    report = summarize(rows)
    assert (report["ok"], report["bad"], report["unknown"]) == (1, 1, 1)
    assert report["judged"] == 2, "分母是 对+错"
    assert report["precision"] == 0.5
    assert abs(report["unknown_ratio"] - 1 / 3) < 1e-9, "无法判断的比例要单独报"


def test_分类按冒号前那级合并():
    """事件按朝代切了 8 层，类别那一级才是"这类问题有多大"的读数。"""
    rows = [_row("事件:东汉", "对"), _row("事件:元", "对"), _row("事件:明", "错", "时间：原文未给年份"),
            _row("实体:地点", "对"), _row("关系:事件-人物", "对"), _row("关系:事件-事件", "错", "关系方向：与 A→B 相反")]
    report = summarize(rows)
    by_category = {item["category"]: item for item in report["categories"]}
    assert set(by_category) == {"事件", "实体", "关系"}
    assert by_category["事件"]["rows"] == 3
    assert by_category["事件"]["precision"] == 2 / 3
    assert by_category["关系"]["precision"] == 0.5
    assert by_category["实体"]["precision"] == 1.0
    # 分层那一级仍然逐层给出（事件三个朝代各一层）
    assert {item["layer"] for item in report["layers"]} >= {"事件:东汉", "事件:元", "事件:明"}


def test_错误类型只统计判错的行():
    """
    判"对"和"无法判断"的行里也可能提到字段名，但错误类型统计只该看判错的行——
    否则"无法判断"的原因会混进"错在哪"。
    """
    rows = [_row("事件:东汉", "对", "时间：一致"),
            _row("事件:东汉", "无法判断", "上下文不足：未能定位"),
            _row("事件:东汉", "错", "时间：原文未给年份；结果：胜负颠倒")]
    report = summarize(rows)
    assert report["error_types"] == {"时间": 1, "结果": 1}
    assert report["unknown_reasons"] == {"上下文不足": 1}


def test_未填判定的行不计入任何一档():
    """没填的行不能被悄悄吞掉——它既不是对也不是错，`summarize` 只如实分类。"""
    rows = [_row("事件:唐", ""), _row("事件:唐", "对")]
    report = summarize(rows)
    assert report["table_rows"] == 2
    assert (report["ok"], report["bad"], report["unknown"]) == (1, 0, 0)
    assert report["precision"] == 1.0


def test_categorize_对无冒号的分层名不报错():
    """分层名没有冒号时（历史表或手工表）不该抛异常，类别就取整名。"""
    assert categorize("事件:东汉") == "事件"
    assert categorize("其它") == "其它"
    assert categorize("") == ""
