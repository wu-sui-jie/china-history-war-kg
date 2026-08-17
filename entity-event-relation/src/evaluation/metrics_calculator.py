"""
指标计算模块
提供准确率(P)、召回率(R)、F1值的标准化计算
支持多层次、多类别的评估指标计算
"""

from typing import Dict, List, Tuple, Any, Optional
from dataclasses import dataclass
from collections import defaultdict
import json


@dataclass
class MetricsResult:
    """指标计算结果数据类"""
    precision: float
    recall: float
    f1: float
    support: int  # 标注样本数
    tp: int  # 真正例
    fp: int  # 假正例
    fn: int  # 假负例

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "support": self.support,
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn
        }

    @classmethod
    def from_counts(cls, tp: int, fp: int, fn: int, support: Optional[int] = None):
        """从计数创建指标对象"""
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

        return cls(
            precision=precision,
            recall=recall,
            f1=f1,
            support=support if support is not None else (tp + fn),
            tp=tp,
            fp=fp,
            fn=fn
        )


class MetricsCalculator:
    """
    指标计算器
    支持二分类和多分类场景的P/R/F1计算
    """

    @staticmethod
    def calculate_basic_metrics(matched: List, preds: List, golds: List) -> MetricsResult:
        """
        计算基础指标（二分类场景）

        Args:
            matched: 正确匹配的样本列表
            preds: 预测结果列表
            golds: 标注结果列表

        Returns:
            MetricsResult对象
        """
        tp = len(matched)
        fp = len(preds) - tp
        fn = len(golds) - tp

        return MetricsResult.from_counts(tp, fp, fn, len(golds))

    @staticmethod
    def calculate_classification_metrics(
        predictions: List[str],
        labels: List[str],
        average: str = "macro"
    ) -> Dict[str, float]:
        """
        计算多分类指标

        Args:
            predictions: 预测标签列表
            labels: 真实标签列表
            average: 平均方法 ("macro", "micro", "weighted")

        Returns:
            包含precision, recall, f1的字典
        """
        # 获取所有类别
        classes = set(predictions) | set(labels)

        # 计算每个类别的指标
        class_metrics = {}
        for cls in classes:
            tp = sum(1 for p, l in zip(predictions, labels) if p == cls and l == cls)
            fp = sum(1 for p, l in zip(predictions, labels) if p == cls and l != cls)
            fn = sum(1 for p, l in zip(predictions, labels) if p != cls and l == cls)

            precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
            recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
            f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
            support = sum(1 for l in labels if l == cls)

            class_metrics[cls] = {
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "support": support
            }

        # 根据average参数计算总体指标
        if average == "macro":
            precision = sum(m["precision"] for m in class_metrics.values()) / len(class_metrics) if class_metrics else 0.0
            recall = sum(m["recall"] for m in class_metrics.values()) / len(class_metrics) if class_metrics else 0.0
            f1 = sum(m["f1"] for m in class_metrics.values()) / len(class_metrics) if class_metrics else 0.0

        elif average == "micro":
            total_tp = sum(sum(1 for p, l in zip(predictions, labels) if p == cls and l == cls) for cls in classes)
            total_fp = sum(sum(1 for p, l in zip(predictions, labels) if p == cls and l != cls) for cls in classes)
            total_fn = sum(sum(1 for p, l in zip(predictions, labels) if p != cls and l == cls) for cls in classes)

            precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0.0
            recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0.0
            f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

        elif average == "weighted":
            total_support = sum(m["support"] for m in class_metrics.values())
            if total_support > 0:
                precision = sum(m["precision"] * m["support"] for m in class_metrics.values()) / total_support
                recall = sum(m["recall"] * m["support"] for m in class_metrics.values()) / total_support
                f1 = sum(m["f1"] * m["support"] for m in class_metrics.values()) / total_support
            else:
                precision = recall = f1 = 0.0

        return {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "by_class": class_metrics
        }

    @staticmethod
    def calculate_hierarchical_metrics(
        entity_metrics: MetricsResult,
        event_metrics: MetricsResult,
        relation_metrics: MetricsResult,
        weights: Optional[Dict[str, float]] = None
    ) -> Dict[str, float]:
        """
        计算分层加权指标（实体+事件+关系）

        Args:
            entity_metrics: 实体抽取指标
            event_metrics: 事件抽取指标
            relation_metrics: 关系抽取指标
            weights: 各层权重，默认等权重

        Returns:
            加权后的综合指标
        """
        if weights is None:
            weights = {"entity": 1/3, "event": 1/3, "relation": 1/3}

        # 计算加权平均
        precision = (
            entity_metrics.precision * weights["entity"] +
            event_metrics.precision * weights["event"] +
            relation_metrics.precision * weights["relation"]
        )

        recall = (
            entity_metrics.recall * weights["entity"] +
            event_metrics.recall * weights["event"] +
            relation_metrics.recall * weights["relation"]
        )

        f1 = (
            entity_metrics.f1 * weights["entity"] +
            event_metrics.f1 * weights["event"] +
            relation_metrics.f1 * weights["relation"]
        )

        return {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "macro_avg_f1": round(f1, 4),  # 兼容原有字段
            "weights": weights
        }


class DetailedReportGenerator:
    """详细评估报告生成器"""

    @staticmethod
    def generate_report(
        entity_metrics: Dict,
        event_metrics: Dict,
        relation_metrics: Dict,
        output_path: str
    ):
        """
        生成详细评估报告（JSON格式）

        Args:
            entity_metrics: 实体评估结果
            event_metrics: 事件评估结果
            relation_metrics: 关系评估结果
            output_path: 输出文件路径
        """
        report = {
            "entity_extraction": entity_metrics,
            "event_extraction": event_metrics,
            "relation_extraction": relation_metrics,
            "summary": {
                "entity_f1": entity_metrics.get("overall", {}).get("f1", 0),
                "event_f1": event_metrics.get("full_events", {}).get("f1", 0),
                "relation_f1": relation_metrics.get("overall", {}).get("f1", 0),
                "macro_avg_f1": round(
                    (entity_metrics.get("overall", {}).get("f1", 0) +
                     event_metrics.get("full_events", {}).get("f1", 0) +
                     relation_metrics.get("overall", {}).get("f1", 0)) / 3, 4
                )
            }
        }

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        return report