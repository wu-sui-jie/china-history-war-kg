"""
预测结果与标注数据对齐工具
解决模型输出与人工标注在边界、表述上的差异问题
"""

import re
from typing import List, Dict, Tuple
from fuzzywuzzy import fuzz
from collections import defaultdict


class AlignmentTool:
    """
    对齐工具类
    支持精确匹配和部分匹配两种模式
    """

    def __init__(self, match_threshold: float = 0.85):
        """
        Args:
            match_threshold: 部分匹配的相似度阈值（0-1）
        """
        self.threshold = match_threshold

    def align_entities(
            self,
            preds: List[Dict],  # 模型预测实体
            golds: List[Dict],  # 人工标注实体
            match_mode: str = "exact"  # exact|partial
    ) -> Tuple[List[Tuple], List[Dict], List[Dict]]:
        """
        对齐实体预测与标注

        Returns:
            matched_pairs: 匹配的 (pred, gold) 对
            unmatched_preds: 未匹配的预测
            unmatched_golds: 未匹配的标注（即漏抽）
        """
        matched = []
        unmatched_preds = []
        unmatched_golds = golds.copy()

        for pred in preds:
            best_match = None
            best_score = 0

            for gold in unmatched_golds:
                if match_mode == "exact":
                    # 精确匹配：名称和类型必须完全相同
                    score = 1.0 if (pred.get("geo_name") == gold.get("geo_name") and
                                    pred.get("label") == gold.get("label")) else 0
                else:
                    # 部分匹配：计算文本相似度
                    pred_text = pred.get("geo_name") or pred.get("OrgName") or pred.get("PersonName")
                    gold_text = gold.get("geo_name") or gold.get("OrgName") or gold.get("PersonName")
                    # 名称相似度 + 类型相似度（如果存在）
                    name_score = fuzz.ratio(pred_text, gold_text) / 100.0
                    type_score = 1.0 if pred.get("label") == gold.get("label") else 0.7
                    score = name_score * type_score

                if score > best_score and score >= self.threshold:
                    best_match = gold
                    best_score = score

            if best_match:
                matched.append((pred, best_match))
                unmatched_golds.remove(best_match)
            else:
                unmatched_preds.append(pred)

        return matched, unmatched_preds, unmatched_golds

    def align_events(
            self,
            preds: List[Dict],
            golds: List[Dict],
            match_mode: str = "exact"
    ) -> Tuple[List[Tuple], List[Dict], List[Dict]]:
        """
        对齐事件预测与标注

        事件匹配策略：
        - 优先匹配 EventName
        - 其次匹配时间+地点+主体组合
        """
        matched = []
        unmatched_preds = []
        unmatched_golds = golds.copy()

        for pred in preds:
            best_match = None
            best_score = 0

            for gold in unmatched_golds:
                if match_mode == "exact":
                    # 事件名称完全匹配
                    score = 1.0 if pred.get("EventName") == gold.get("EventName") else 0
                else:
                    # 多维相似度：名称+时间+地点
                    name_score = fuzz.ratio(pred.get("EventName", ""), gold.get("EventName", "")) / 100.0
                    time_score = 1.0 if pred.get("StartDate") == gold.get("StartDate") else 0.5
                    place_score = 1.0 if pred.get("Place") == gold.get("Place") else 0.5

                    # 加权平均
                    score = (name_score * 0.6 + time_score * 0.2 + place_score * 0.2)

                if score > best_score and score >= self.threshold:
                    best_match = gold
                    best_score = score

            if best_match:
                matched.append((pred, best_match))
                unmatched_golds.remove(best_match)
            else:
                unmatched_preds.append(pred)

        return matched, unmatched_preds, unmatched_golds