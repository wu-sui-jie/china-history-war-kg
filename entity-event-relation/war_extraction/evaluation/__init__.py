"""
评估模块
提供实体、事件、关系抽取结果的评估功能

只暴露**一套**评估口径：`OptimalEvaluator`（最优模糊匹配）。EER-10 之前这里还导出
`Evaluator` + `MetricsCalculator` 另一套（严格对齐、阈值 0.85），两套口径并存、而那一套
没有任何入口调用——读者无从判断哪个才是官方指标，第 10 轮移除。
需要时从 git 历史取回：
`git show b26d3aa:entity-event-relation/war_extraction/evaluation/evaluator.py`。
"""

from war_extraction.evaluation.optimal_evaluator import OptimalEvaluator

__all__ = [
    'OptimalEvaluator',
]
