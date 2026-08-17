"""
评估器主模块
计算准确率(P)、召回率(R)、F1值，支持多层次评估
"""

import json
from pathlib import Path
from typing import Dict, List, Any
from fuzzywuzzy import fuzz
from src.utils.alignment import AlignmentTool
from src.models import (
    EntityExtractionResult,
    EventExtractionResult,
    RelationExtractionResult
)
from src.evaluation.metrics_calculator import MetricsCalculator, MetricsResult


class Evaluator:
    """
    综合评估器
    支持实体、事件、关系三个层次的评估
    """

    def __init__(self, annotation_dir: Path, match_threshold: float = 0.85):
        """
        Args:
            annotation_dir: 标注数据目录
            match_threshold: 对齐阈值
        """
        self.annotation_dir = Path(annotation_dir)
        self.aligner = AlignmentTool(match_threshold)
        self.calculator = MetricsCalculator()
        self.results = {}

        # 加载标注数据
        self.load_annotations()

    def load_annotations(self):
        """加载人工标注的 ground truth 数据"""
        self.gold_entities = {}
        self.gold_events = {}
        self.gold_relations = {}

        if (self.annotation_dir / "sample_entities.json").exists():
            with open(self.annotation_dir / "sample_entities.json", "r", encoding="utf-8") as f:
                self.gold_entities = json.load(f)

        if (self.annotation_dir / "sample_events.json").exists():
            with open(self.annotation_dir / "sample_events.json", "r", encoding="utf-8") as f:
                self.gold_events = json.load(f)

        if (self.annotation_dir / "sample_relations.json").exists():
            with open(self.annotation_dir / "sample_relations.json", "r", encoding="utf-8") as f:
                self.gold_relations = json.load(f)

    def calculate_metrics(self, matched: List, preds: List, golds: List) -> Dict[str, Any]:
        """
        计算评估指标（使用MetricsCalculator）

        Args:
            matched: 匹配的 (pred, gold) 对
            preds: 所有预测
            golds: 所有标注

        Returns:
            指标字典：precision, recall, f1, support, tp, fp, fn
        """
        result = self.calculator.calculate_basic_metrics(matched, preds, golds)
        return result.to_dict()

    def evaluate_entities(
            self,
            pred_result: EntityExtractionResult,
            match_mode: str = "exact"
    ) -> Dict[str, Any]:
        """
        评估实体抽取效果

        Args:
            pred_result: 模型预测结果
            match_mode: 匹配模式 exact|partial

        Returns:
            分类评估结果和总体结果
        """
        # 转换预测结果为对齐格式
        pred_places = [{"geo_name": p.geo_name, "label": "PLACE"} for p in pred_result.places]
        pred_orgs = [{"OrgName": o.OrgName, "label": "ORG"} for o in pred_result.organizations]
        pred_persons = [{"PersonName": p.PersonName, "label": "PERSON"} for p in pred_result.persons]

        all_preds = pred_places + pred_orgs + pred_persons

        # 转换标注数据
        gold_places = [{"geo_name": g["geo_name"], "label": "PLACE"} for g in self.gold_entities.get("places", [])]
        gold_orgs = [{"OrgName": g["OrgName"], "label": "ORG"} for g in self.gold_entities.get("organizations", [])]
        gold_persons = [{"PersonName": g["PersonName"], "label": "PERSON"} for g in
                        self.gold_entities.get("persons", [])]

        all_golds = gold_places + gold_orgs + gold_persons

        # 对齐预测与标注
        matched, unmatched_preds, unmatched_golds = self.aligner.align_entities(
            all_preds, all_golds, match_mode
        )

        # 计算指标
        metrics = self.calculate_metrics(matched, all_preds, all_golds)

        # 分类别评估
        category_metrics = {}

        # 地点评估
        if pred_places and gold_places:
            place_matched, _, _ = self.aligner.align_entities(pred_places, gold_places, match_mode)
            category_metrics["places"] = self.calculate_metrics(place_matched, pred_places, gold_places)

        # 组织评估
        if pred_orgs and gold_orgs:
            org_matched, _, _ = self.aligner.align_entities(pred_orgs, gold_orgs, match_mode)
            category_metrics["organizations"] = self.calculate_metrics(org_matched, pred_orgs, gold_orgs)

        # 人物评估
        if pred_persons and gold_persons:
            person_matched, _, _ = self.aligner.align_entities(pred_persons, gold_persons, match_mode)
            category_metrics["persons"] = self.calculate_metrics(person_matched, pred_persons, gold_persons)

        return {
            "overall": metrics,
            "by_category": category_metrics,
            "match_mode": match_mode
        }

    def evaluate_events(
            self,
            pred_result: EventExtractionResult,
            match_mode: str = "exact"
    ) -> Dict[str, Any]:
        """
        评估事件抽取效果

        Args:
            pred_result: 包含子事件和完整事件的结果
            match_mode: 匹配模式

        Returns:
            子事件和完整事件的评估指标
        """
        # 评估完整事件
        pred_events = [{"EventName": e.EventName, "EventType": e.EventType,
                        "StartDate": e.StartDate, "Place": e.Place} for e in pred_result.events]
        gold_events = self.gold_events.get("events", [])

        event_matched, _, _ = self.aligner.align_events(pred_events, gold_events, match_mode)
        event_metrics = self.calculate_metrics(event_matched, pred_events, gold_events)

        return {
            "full_events": event_metrics,
            "match_mode": match_mode
        }

    def evaluate_relations(
            self,
            pred_result: RelationExtractionResult,
            match_mode: str = "exact"
    ) -> Dict[str, Any]:
        """
        评估关系抽取效果

        Args:
            pred_result: 四类关系的预测结果
            match_mode: 匹配模式

        Returns:
            分类别和总体的评估指标
        """
        # 转换预测为统一格式
        pred_triples = []

        for rel in pred_result.event_place_relations:
            pred_triples.append({
                "head": rel.EventName, "relation": rel.relation, "tail": rel.modern_name, "type": "event-place"
            })

        for rel in pred_result.event_organization_relations:
            pred_triples.append({
                "head": rel.EventName, "relation": rel.relation, "tail": rel.OrgName, "type": "event-org"
            })

        for rel in pred_result.event_person_relations:
            pred_triples.append({
                "head": rel.EventName, "relation": rel.relation, "tail": rel.PersonName, "type": "event-person"
            })

        for rel in pred_result.event_event_relations:
            pred_triples.append({
                "head": rel.EventName_A, "relation": rel.relation, "tail": rel.EventName_B, "type": "event-event"
            })

        # 转换标注数据
        gold_triples = []
        for category, relations in self.gold_relations.items():
            for rel in relations:
                gold_triples.append({
                    "head": rel["head"], "relation": rel["relation"], "tail": rel["tail"], "type": category
                })

        # 对齐（关系匹配需头尾实体和关系类型都匹配）
        matched = []
        unmatched_preds = []
        unmatched_golds = gold_triples.copy()

        for pred in pred_triples:
            best_match = None
            best_score = 0

            for gold in unmatched_golds:
                if match_mode == "exact":
                    # 精确匹配：头、关系、尾完全相同
                    score = 1.0 if (pred["head"] == gold["head"] and
                                    pred["relation"] == gold["relation"] and
                                    pred["tail"] == gold["tail"]) else 0
                else:
                    # 部分匹配：三者相似度加权
                    head_score = fuzz.ratio(pred["head"], gold["head"]) / 100.0
                    rel_score = fuzz.ratio(pred["relation"], gold["relation"]) / 100.0
                    tail_score = fuzz.ratio(pred["tail"], gold["tail"]) / 100.0
                    score = (head_score + rel_score + tail_score) / 3.0

                if score > best_score and score >= self.aligner.threshold:
                    best_match = gold
                    best_score = score

            if best_match:
                matched.append((pred, best_match))
                unmatched_golds.remove(best_match)
            else:
                unmatched_preds.append(pred)

        # 计算总体指标
        metrics = self.calculate_metrics(matched, pred_triples, gold_triples)

        # 分类别评估
        category_metrics = {}
        for rel_type in ["event-place", "event-org", "event-person", "event-event"]:
            pred_type = [t for t in pred_triples if t["type"] == rel_type]
            gold_type = [t for t in gold_triples if t["type"] == rel_type]

            if pred_type and gold_type:
                type_matched = [m for m in matched if m[0]["type"] == rel_type]
                category_metrics[rel_type] = self.calculate_metrics(type_matched, pred_type, gold_type)

        return {
            "overall": metrics,
            "by_category": category_metrics,
            "match_mode": match_mode
        }

    def run_full_evaluation(
            self,
            pred_entities: EntityExtractionResult,
            pred_events: EventExtractionResult,
            pred_relations: RelationExtractionResult
    ) -> Dict[str, Any]:
        """
        执行完整评估流程（实体+事件+关系）

        Args:
            pred_entities: 实体预测结果
            pred_events: 事件预测结果
            pred_relations: 关系预测结果

        Returns:
            完整评估报告
        """
        print("\n" + "=" * 60)
        print("开始评估...")
        print("=" * 60)

        # 评估三个层次
        entity_metrics = self.evaluate_entities(pred_entities, match_mode="partial")
        event_metrics = self.evaluate_events(pred_events, match_mode="partial")
        relation_metrics = self.evaluate_relations(pred_relations, match_mode="exact")

        # 汇总结果
        self.results = {
            "entity_extraction": entity_metrics,
            "event_extraction": event_metrics,
            "relation_extraction": relation_metrics,
            "summary": {
                "entity_f1": entity_metrics["overall"]["f1"],
                "event_f1": event_metrics["full_events"]["f1"],
                "relation_f1": relation_metrics["overall"]["f1"],
                "macro_avg_f1": round((entity_metrics["overall"]["f1"] +
                                       event_metrics["full_events"]["f1"] +
                                       relation_metrics["overall"]["f1"]) / 3, 4)
            }
        }

        return self.results

    def save_report(self, output_path: Path):
        """
        保存评估报告

        Args:
            output_path: 报告保存路径
        """
        if not self.results:
            raise ValueError("请先运行评估")

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # 保存 JSON 报告
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(self.results, f, ensure_ascii=False, indent=2)

        # 打印摘要
        print("\n" + "=" * 60)
        print("评估完成！摘要如下：")
        print(f"实体抽取 F1: {self.results['summary']['entity_f1']:.4f}")
        print(f"事件抽取 F1: {self.results['summary']['event_f1']:.4f}")
        print(f"关系抽取 F1: {self.results['summary']['relation_f1']:.4f}")
        print(f"宏观平均 F1: {self.results['summary']['macro_avg_f1']:.4f}")
        print(f"详细报告已保存至: {output_path}")
        print("=" * 60)
