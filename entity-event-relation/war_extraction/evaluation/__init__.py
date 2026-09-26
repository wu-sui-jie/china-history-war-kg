"""
评估模块
提供实体、事件、关系抽取结果的评估功能

本模块只导出**一套**评估口径：`OptimalEvaluator`（最优模糊匹配）。两套口径并存、
而其中一套没有任何入口调用时，读者无从判断哪个才是官方指标——所以不要在这里再添第二套。
需要另一套参考实现（严格对齐、阈值 0.85）时从 git 历史取回：
`git show b26d3aa:entity-event-relation/war_extraction/evaluation/evaluator.py`。
"""

from war_extraction.evaluation.optimal_evaluator import OptimalEvaluator

__all__ = [
    'OptimalEvaluator',
]
