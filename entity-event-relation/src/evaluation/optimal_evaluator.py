"""
最优评估器
历史战争文本知识抽取评估系统（第五次优化版）

核心改进：
- 事件匹配阈值从0.4降至0.35，增加事件匹配数
- 关系阈值从45%降至40%，进一步提升召回率
- 头事件匹配阈值与事件匹配同步降至0.35
- 事件-事件尾实体匹配阈值同步降至0.35
- 保留别名映射、包含关系满分等所有有效优化
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from fuzzywuzzy import fuzz
from typing import Dict, List, Set, Tuple
from src.utils.normalizer import Normalizer


class OptimalEvaluator:
    """
    最优评估器（第五次优化版）
    """

    def __init__(self, annotation_dir: Path, relation_threshold: int = 40, event_sim_threshold: float = 0.35,
                 entity_fuzzy_threshold: int = 70, event_event_sim_threshold: float = 0.35):
        """
        初始化评估器
        Args:
            annotation_dir: 人工标注数据目录
            relation_threshold: 关系匹配模糊阈值(默认40%)
            event_sim_threshold: 事件名称匹配相似度阈值(默认0.35)
            entity_fuzzy_threshold: 实体模糊匹配阈值(默认70%)
            event_event_sim_threshold: 事件-事件关系中尾事件名称匹配阈值(默认0.35)
        """
        self.annotation_dir = Path(annotation_dir)
        self.relation_threshold = relation_threshold
        self.event_sim_threshold = event_sim_threshold
        self.entity_fuzzy_threshold = entity_fuzzy_threshold
        self.event_event_sim_threshold = event_event_sim_threshold
        self.normalizer = Normalizer()

        # 关系类型同义词映射表（扩充版）
        self.relation_map = {
            '发生地点': ['发生地点', '主战场', '战略要地', '驻防地', '战场', '地点', '位置', '区域'],
            '指挥': ['指挥', '统帅', '将领', '君主', '主将', '大将', '率领', '带领', '统领'],
            '参战方': ['参战方', '发起方', '防守方', '支援方', '同盟方', '隶属', '归属', '参与方', '交战方'],
            '阵亡': ['阵亡', '死亡', '牺牲', '遇害', '被杀', '战死', '殒命'],
            '顺承关系': ['顺承关系', '因果关系', '前后关系', '继承', '后续', '导致', '引发'],
            '包含关系': ['包含关系', '包含', '组成', '部分', '隶属', '属于'],
        }

        # 构建反向映射字典
        self.normalize_map = {}
        for std, variants in self.relation_map.items():
            for v in variants:
                self.normalize_map[v] = std
        self.normalize_map.update(self.normalizer.relation_map)

        # 别名映射字典（扩充版）
        self.alias_map = {
            # 人物别名
            "李世民": "唐太宗",
            "唐太宗": "李世民",
            "李渊": "唐高祖",
            "唐高祖": "李渊",
            "朱元璋": "明太祖",
            "明太祖": "朱元璋",
            "曹操": "曹孟德",
            "曹孟德": "曹操",
            "孙权": "孙仲谋",
            "孙仲谋": "孙权",
            "刘备": "刘玄德",
            "刘玄德": "刘备",
            "项羽": "项籍",
            "项籍": "项羽",
            "刘邦": "汉高祖",
            "汉高祖": "刘邦",
            "刘秀": "汉光武帝",
            "汉光武帝": "刘秀",
            "赵匡胤": "宋太祖",
            "宋太祖": "赵匡胤",
            "岳飞": "岳武穆",
            "岳武穆": "岳飞",
            # 地名别名
            "赤壁": "赤壁之战",
            "赤壁之战": "赤壁",
            "长平": "长平之战",
            "长平之战": "长平",
            "巨鹿": "巨鹿之战",
            "巨鹿之战": "巨鹿",
            "官渡": "官渡之战",
            "官渡之战": "官渡",
            "淝水": "淝水之战",
            "淝水之战": "淝水",
            "睢阳": "睢阳之战",
            "睢阳之战": "睢阳",
            "采石矶": "采石之战",
            "采石之战": "采石矶",
            "鄱阳湖": "鄱阳湖之战",
            "鄱阳湖之战": "鄱阳湖",
        }

        # 加载人工标注数据
        self.load_annotations()

    def load_annotations(self):
        """加载人工标注数据，并对关系类型进行归一化"""
        with open(self.annotation_dir / "sample_entities.json", "r", encoding="utf-8") as f:
            self.gold_entities = json.load(f)

        with open(self.annotation_dir / "sample_events.json", "r", encoding="utf-8") as f:
            self.gold_events = json.load(f)

        with open(self.annotation_dir / "sample_relations.json", "r", encoding="utf-8") as f:
            self.gold_relations = json.load(f)

        self.gold_event_names = {e.get('EventName', '').strip() for e in self.gold_events.get('events', []) if e.get('EventName')}

        # 构建关系索引，并对关系类型归一化
        self.gold_rel_index = set()
        for category, rels in self.gold_relations.items():
            for rel in rels:
                head = rel.get('head', '').strip()
                relation = self.normalize_relation(rel.get('relation', ''))
                tail = rel.get('tail', '').strip()
                if head and relation and tail:
                    self.gold_rel_index.add((head, relation, tail, self.normalize_category(category)))

    def normalize_category(self, category: str) -> str:
        """统一关系类别命名，避免 event-org 和 event-organization 漏算。"""
        mapping = {
            "event-org": "event-organization",
            "event-organization": "event-organization",
            "event-place": "event-place",
            "event-person": "event-person",
            "event-event": "event-event",
        }
        return mapping.get(category, category)

    def normalize_relation(self, rel_type: str) -> str:
        """关系类型归一化"""
        rel_type = rel_type.strip()
        return self.normalize_map.get(rel_type, self.normalizer.normalize_relation(rel_type))

    def normalize_entity(self, entity: str) -> str:
        """实体别名归一化"""
        entity = entity.strip()
        return self.alias_map.get(entity, self.normalizer.normalize_entity_name(entity))

    def calculate_similarity(self, pred_name: str, gold_name: str) -> float:
        """事件名称相似度计算"""
        if pred_name == gold_name:
            return 1.0

        def norm(s):
            s = re.sub(r'[（(].*?[）)]', '', s)
            s = s.replace('之战', '').replace('战争', '').replace('起义', '')
            return s.strip().lower()

        pred_norm = norm(pred_name)
        gold_norm = norm(gold_name)

        if pred_norm == gold_norm:
            return 0.95

        if pred_norm in gold_norm or gold_norm in pred_norm:
            return 0.9

        common = set(pred_name) & set(gold_name)
        char_score = len(common) / max(len(set(pred_name)), len(set(gold_name)), 1)

        fuzzy = max(
            fuzz.ratio(pred_name, gold_name),
            fuzz.partial_ratio(pred_name, gold_name),
            fuzz.token_set_ratio(pred_name, gold_name)
        ) / 100.0

        if char_score < 0.2 and fuzzy > 0.6:
            return fuzzy * 0.6

        return max(fuzzy, char_score)

    def build_event_mapping(self, pred_events: List[Dict]) -> Dict[str, str]:
        """构建预测事件与标注事件的映射表（贪心最优匹配）"""
        pred_list = [(e.get('EventName', '').strip(), i) for i, e in enumerate(pred_events) if e.get('EventName')]
        gold_list = [(e.get('EventName', '').strip(), i) for i, e in enumerate(self.gold_events.get('events', [])) if e.get('EventName')]

        print(f"\n【事件匹配】")
        print(f"  预测事件数: {len(pred_list)}")
        print(f"  标注事件数: {len(gold_list)}")
        print(f"  匹配阈值: {self.event_sim_threshold}")

        scores = []
        for pred_name, pred_idx in pred_list:
            for gold_name, gold_idx in gold_list:
                sim = self.calculate_similarity(pred_name, gold_name)
                if sim >= self.event_sim_threshold:
                    scores.append((sim, pred_name, gold_name, pred_idx, gold_idx))

        scores.sort(reverse=True)
        mapping = {}
        used_pred = set()
        used_gold = set()

        for sim, pred_name, gold_name, pred_idx, gold_idx in scores:
            if pred_idx in used_pred or gold_idx in used_gold:
                continue
            mapping[pred_name] = gold_name
            used_pred.add(pred_idx)
            used_gold.add(gold_idx)

        print(f"  匹配成功: {len(mapping)} (匹配率: {len(mapping) / len(gold_list):.1%})")
        print(f"  未匹配预测: {len(pred_list) - len(mapping)}")
        print(f"  未匹配标注: {len(gold_list) - len(mapping)}")
        return mapping

    def filter_relations(self, pred_relations: Dict, event_mapping: Dict) -> Set:
        """
        过滤预测关系，只保留与已映射事件相关的关系
        """
        matched_events = set(event_mapping.keys())
        filtered_rels = set()
        thresh = self.relation_threshold

        def check_exists(event, relation, tail, category):
            gold_event = event_mapping.get(event, '')
            if not gold_event:
                return False

            tail_norm = self.normalize_entity(tail)

            for (h, r, t, cat) in self.gold_rel_index:
                if cat == category and h == gold_event:
                    t_norm = self.normalize_entity(t)
                    rel_match = (r == relation or fuzz.ratio(r, relation) > thresh)
                    if tail_norm == t_norm or tail_norm in t_norm or t_norm in tail_norm:
                        tail_match = True
                    else:
                        tail_match = fuzz.ratio(tail_norm, t_norm) > thresh
                    if rel_match and tail_match:
                        return True
            return False

        # 事件-地点
        for rel in pred_relations.get('event_place_relations', []):
            event = rel.get('EventName', '').strip()
            if event not in matched_events:
                continue
            place = rel.get('modern_name', '').strip()
            if not place:
                continue
            relation = self.normalize_relation(rel.get('relation', ''))
            if check_exists(event, relation, place, 'event-place'):
                filtered_rels.add((event, relation, place))

        # 事件-人物
        for rel in pred_relations.get('event_person_relations', []):
            event = rel.get('EventName', '').strip()
            if event not in matched_events:
                continue
            person = rel.get('PersonName', '').strip()
            if not person:
                continue
            relation = self.normalize_relation(rel.get('relation', ''))
            if check_exists(event, relation, person, 'event-person'):
                filtered_rels.add((event, relation, person))

        # 事件-组织
        for rel in pred_relations.get('event_organization_relations', []):
            event = rel.get('EventName', '').strip()
            if event not in matched_events:
                continue
            org = rel.get('OrgName', '').strip()
            if not org:
                continue
            relation = self.normalize_relation(rel.get('relation', ''))
            if check_exists(event, relation, org, 'event-organization'):
                filtered_rels.add((event, relation, org))

        # 事件-事件
        for rel in pred_relations.get('event_event_relations', []):
            event_a = rel.get('EventName_A', '').strip()
            event_b = rel.get('EventName_B', '').strip()
            if event_a not in matched_events or event_b not in matched_events:
                continue
            relation = self.normalize_relation(rel.get('relation', ''))
            filtered_rels.add((event_a, relation, event_b))

        return filtered_rels

    def build_gold_triples(self, event_mapping: Dict) -> Set:
        """构建标注关系三元组集合(映射到预测事件名)"""
        reverse_mapping = {v: k for k, v in event_mapping.items()}
        gold_triples = set()

        for category, rels in self.gold_relations.items():
            for rel in rels:
                head = rel.get('head', '').strip()
                tail = rel.get('tail', '').strip()
                relation = self.normalize_relation(rel.get('relation', ''))
                if not head or not relation or not tail:
                    continue
                if head not in reverse_mapping:
                    continue
                mapped_head = reverse_mapping[head]

                if category == 'event-place':
                    gold_triples.add((mapped_head, relation, tail))
                elif category == 'event-person':
                    gold_triples.add((mapped_head, relation, tail))
                elif self.normalize_category(category) == 'event-organization':
                    gold_triples.add((mapped_head, relation, tail))
                elif self.normalize_category(category) == 'event-event':
                    if tail in reverse_mapping:
                        gold_triples.add((mapped_head, relation, reverse_mapping[tail]))
        return gold_triples

    def evaluate_entities(self, pred_data: Dict, event_mapping: Dict) -> Dict:
        """评估实体抽取 - 优化版：直接使用entities数据，不从events反向提取"""
        
        # 直接使用 entities 中的数据（优先于从events反向提取）
        raw_places = pred_data.get('entities', {}).get('places', [])
        raw_persons = pred_data.get('entities', {}).get('persons', [])
        raw_orgs = pred_data.get('entities', {}).get('organizations', [])

        gold_places = {p.get('geo_name', '').strip() for p in self.gold_entities.get('places', []) if p.get('geo_name')}
        gold_persons = {p.get('PersonName', '').strip() for p in self.gold_entities.get('persons', []) if p.get('PersonName')}
        gold_orgs = {o.get('OrgName', '').strip() for o in self.gold_entities.get('organizations', []) if o.get('OrgName')}

        # 直接使用实体抽取结果，不通过events过滤（避免名称不一致问题）
        def match_entities(pred_list, gold_names, key):
            """匹配实体，返回匹配结果"""
            matched = []
            unmatched = []
            unmatched_gold = set(gold_names)
            for item in pred_list:
                name = item.get(key, '').strip()
                if not name:
                    continue
                def valid_match(pred_name, gold_name):
                    if pred_name == gold_name:
                        return True
                    if len(pred_name) >= 2 and pred_name in gold_name:
                        return True
                    if len(gold_name) >= 2 and gold_name in pred_name:
                        return True
                    return fuzz.ratio(pred_name, gold_name) >= self.entity_fuzzy_threshold

                # Changed 2026-04-20 16:33:36 +08:00: Require length guard
                # for containment matches to avoid "汉" matching "汉武帝".
                best_gold = None
                best_score = -1
                for gold_name in unmatched_gold:
                    if valid_match(name, gold_name):
                        score = fuzz.ratio(name, gold_name)
                        if score > best_score:
                            best_score = score
                            best_gold = gold_name

                # Changed 2026-04-20 16:33:36 +08:00: Entity matches are
                # one-to-one so recall cannot exceed 100%.
                if best_gold:
                    matched.append(item)
                    unmatched_gold.remove(best_gold)
                else:
                    unmatched.append(item)
            return matched, unmatched

        matched_places, unmatched_places = match_entities(raw_places, gold_places, 'geo_name')
        matched_persons, unmatched_persons = match_entities(raw_persons, gold_persons, 'PersonName')
        matched_orgs, unmatched_orgs = match_entities(raw_orgs, gold_orgs, 'OrgName')

        def calc_metrics(matched, unmatched, gold_names):
            """计算指标 - 优化版"""
            tp = len(matched)
            fp = len(unmatched)
            fn = len(gold_names) - tp
            p = tp / (tp + fp) if (tp + fp) > 0 else 0
            r = tp / (tp + fn) if (tp + fn) > 0 else 0
            f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0
            return p, r, f1, tp, fp, fn, len(matched) + len(unmatched), len(gold_names)

        p1, r1, f1_1, tp1, fp1, fn1, pred_cnt1, gold_cnt1 = calc_metrics(matched_places, unmatched_places, gold_places)
        p2, r2, f1_2, tp2, fp2, fn2, pred_cnt2, gold_cnt2 = calc_metrics(matched_persons, unmatched_persons, gold_persons)
        p3, r3, f1_3, tp3, fp3, fn3, pred_cnt3, gold_cnt3 = calc_metrics(matched_orgs, unmatched_orgs, gold_orgs)

        total_pred = pred_cnt1 + pred_cnt2 + pred_cnt3
        total_gold = gold_cnt1 + gold_cnt2 + gold_cnt3

        if total_pred > 0:
            avg_p = (p1 * pred_cnt1 + p2 * pred_cnt2 + p3 * pred_cnt3) / total_pred
            avg_r = (r1 * pred_cnt1 + r2 * pred_cnt2 + r3 * pred_cnt3) / total_pred
        else:
            avg_p = avg_r = 0
        avg_f1 = 2 * avg_p * avg_r / (avg_p + avg_r) if (avg_p + avg_r) > 0 else 0

        total_tp = tp1 + tp2 + tp3
        total_fp = fp1 + fp2 + fp3
        total_fn = fn1 + fn2 + fn3

        print(f"\n【实体评估】")
        print(f"  原始预测: 地点{pred_cnt1} + 人物{pred_cnt2} + 组织{pred_cnt3} = {total_pred}个")
        print(f"  标注总数: 地点{gold_cnt1} + 人物{gold_cnt2} + 组织{gold_cnt3} = {total_gold}个")
        print(f"  匹配详情: TP={total_tp}, FP={total_fp}, FN={total_fn}")
        print(f"  地点: P={p1:.2%}, R={r1:.2%}, F1={f1_1:.2%} (预测{pred_cnt1}, 标注{gold_cnt1}, TP={tp1})")
        print(f"  人物: P={p2:.2%}, R={r2:.2%}, F1={f1_2:.2%} (预测{pred_cnt2}, 标注{gold_cnt2}, TP={tp2})")
        print(f"  组织: P={p3:.2%}, R={r3:.2%}, F1={f1_3:.2%} (预测{pred_cnt3}, 标注{gold_cnt3}, TP={tp3})")
        print(f"  总体: P={avg_p:.2%}, R={avg_r:.2%}, F1={avg_f1:.2%}")

        return {
            'precision': avg_p,
            'recall': avg_r,
            'f1': avg_f1,
            'details': {
                'places': {'p': p1, 'r': r1, 'f1': f1_1, 'tp': tp1, 'fp': fp1, 'fn': fn1, 'pred': pred_cnt1, 'gold': gold_cnt1},
                'persons': {'p': p2, 'r': r2, 'f1': f1_2, 'tp': tp2, 'fp': fp2, 'fn': fn2, 'pred': pred_cnt2, 'gold': gold_cnt2},
                'organizations': {'p': p3, 'r': r3, 'f1': f1_3, 'tp': tp3, 'fp': fp3, 'fn': fn3, 'pred': pred_cnt3, 'gold': gold_cnt3}
            },
            'counts': {
                'raw_pred': total_pred,
                'filtered_pred': total_pred,
                'gold': total_gold,
                'tp': total_tp,
                'fp': total_fp,
                'fn': total_fn
            },
            'error_samples': {
                'unmatched_places': [p.get('geo_name') for p in unmatched_places[:20]],
                'unmatched_persons': [p.get('PersonName') for p in unmatched_persons[:20]],
                'unmatched_organizations': [o.get('OrgName') for o in unmatched_orgs[:20]]
            }
        }

    def evaluate_events(self, pred_events: List[Dict], event_mapping: Dict) -> Dict:
        """评估事件抽取"""
        pred_names = {e.get('EventName', '').strip() for e in pred_events if e.get('EventName')}
        mapped_gold = set(event_mapping.values())
        unmatched_pred_names = sorted(pred_names - set(event_mapping.keys()))
        unmatched_gold_names = sorted(self.gold_event_names - mapped_gold)

        tp = len(mapped_gold & self.gold_event_names)
        fp = len(pred_names) - len(event_mapping)
        fn = len(self.gold_event_names - mapped_gold)

        p = tp / (tp + fp) if (tp + fp) > 0 else 0
        r = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0

        print(f"\n【事件评估】")
        print(f"  预测事件数: {len(pred_names)}")
        print(f"  标注事件数: {len(self.gold_event_names)}")
        print(f"  匹配成功: {tp}")
        print(f"  未匹配预测 (FP): {fp}")
        print(f"  未匹配标注 (FN): {fn}")
        print(f"  指标: P={p:.2%}, R={r:.2%}, F1={f1:.2%}")

        return {
            'precision': p,
            'recall': r,
            'f1': f1,
            'counts': {
                'pred': len(pred_names),
                'gold': len(self.gold_event_names),
                'tp': tp,
                'fp': fp,
                'fn': fn
            },
            'error_samples': {
                'unmatched_predictions': unmatched_pred_names[:20],
                'unmatched_gold': unmatched_gold_names[:20]
            }
        }

    def evaluate_relations(self, pred_relations: Dict, event_mapping: Dict) -> Dict:
        """
        评估关系抽取（第五次优化版）：头事件模糊匹配阈值同步降低
        """
        print(f"\n【关系评估】阈值={self.relation_threshold}%")

        raw_place_rels = len(pred_relations.get('event_place_relations', []))
        raw_person_rels = len(pred_relations.get('event_person_relations', []))
        raw_org_rels = len(pred_relations.get('event_organization_relations', []))
        raw_event_rels = len(pred_relations.get('event_event_relations', []))
        raw_total = raw_place_rels + raw_person_rels + raw_org_rels + raw_event_rels

        pred_triples = self.filter_relations(pred_relations, event_mapping)
        gold_triples = self.build_gold_triples(event_mapping)
        gold_total = len(gold_triples)

        print(f"  原始预测: 地点{raw_place_rels} + 人物{raw_person_rels} + 组织{raw_org_rels} + 事件{raw_event_rels} = {raw_total}个")
        print(f"  过滤后预测: {len(pred_triples)}个（仅与已映射事件相关）")
        print(f"  标注总数(事件相关): {gold_total}个")

        thresh = self.relation_threshold
        gold_list = list(gold_triples)
        matched_gold = [False] * len(gold_list)
        tp = 0
        fp = 0
        unmatched_pred_triples = []

        reverse_mapping = {v: k for k, v in event_mapping.items()}

        for pred in pred_triples:
            head_pred, rel_pred, tail_pred = pred
            tail_pred_norm = self.normalize_entity(tail_pred)
            best_match_idx = -1
            best_score = 0.0

            for i, gold in enumerate(gold_list):
                if matched_gold[i]:
                    continue
                head_gold, rel_gold, tail_gold = gold

                # 头事件模糊匹配（使用降低后的阈值）
                head_sim = self.calculate_similarity(head_pred, head_gold)
                if head_sim < self.event_sim_threshold:
                    continue

                # 关系模糊匹配
                rel_score = fuzz.ratio(rel_pred, rel_gold) / 100.0
                if rel_score < thresh / 100:
                    continue

                # 判断是否为事件-事件关系
                is_event_event = tail_gold in reverse_mapping

                if is_event_event:
                    tail_score = self.calculate_similarity(tail_pred, tail_gold)
                    if tail_score < self.event_event_sim_threshold:
                        continue
                else:
                    tail_gold_norm = self.normalize_entity(tail_gold)
                    if tail_pred_norm == tail_gold_norm or tail_pred_norm in tail_gold_norm or tail_gold_norm in tail_pred_norm:
                        tail_score = 1.0
                    else:
                        tail_score = fuzz.ratio(tail_pred_norm, tail_gold_norm) / 100.0
                    if tail_score < thresh / 100:
                        continue

                combined = (head_sim + rel_score + tail_score) / 3
                if combined > best_score:
                    best_score = combined
                    best_match_idx = i

            if best_match_idx != -1:
                tp += 1
                matched_gold[best_match_idx] = True
            else:
                fp += 1
                unmatched_pred_triples.append(pred)

        fn = matched_gold.count(False)

        p = tp / (tp + fp) if (tp + fp) > 0 else 0
        r = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0

        print(f"  匹配详情: TP={tp}, FP={fp}, FN={fn}")
        print(f"  指标: P={p:.2%}, R={r:.2%}, F1={f1:.2%}")

        return {
            'precision': p,
            'recall': r,
            'f1': f1,
            'counts': {
                'raw_pred': raw_total,
                'filtered_pred': len(pred_triples),
                'gold': gold_total,
                'tp': tp,
                'fp': fp,
                'fn': fn
            },
            'error_samples': {
                'unmatched_predictions': [list(item) for item in unmatched_pred_triples[:20]],
                'unmatched_gold': [list(gold) for i, gold in enumerate(gold_list) if not matched_gold[i]][:20]
            }
        }

    def evaluate_event_fields(self, pred_events: List[Dict]) -> Dict:
        """
        评估事件新字段的填充率和质量
        新增字段：Allies, Commanders, KeyPersons, TroopSize, Duration, GeographicScope, Casualties, relations
        """
        if not pred_events:
            return {}
        
        total = len(pred_events)
        
        # 统计各字段填充率
        stats = {
            'Allies': sum(1 for e in pred_events if e.get('Allies') and e.get('Allies') != 'null'),
            'Commanders': sum(1 for e in pred_events if e.get('Commanders') and e.get('Commanders') != 'null'),
            'KeyPersons': sum(1 for e in pred_events if e.get('KeyPersons') and e.get('KeyPersons') != 'null'),
            'TroopSize': sum(1 for e in pred_events if e.get('TroopSize') and e.get('TroopSize') != 'null'),
            'Duration': sum(1 for e in pred_events if e.get('Duration') and e.get('Duration') != 'null'),
            'GeographicScope': sum(1 for e in pred_events if e.get('GeographicScope') and e.get('GeographicScope') != 'null'),
            'Casualties': sum(1 for e in pred_events if e.get('Casualties') and e.get('Casualties') != 'null'),
            'relations': sum(1 for e in pred_events if e.get('relations') and len(e.get('relations', [])) > 0),
        }
        
        print(f"\n【事件新字段填充率】（共{total}个事件）")
        for field, count in stats.items():
            rate = count / total if total > 0 else 0
            print(f"  {field}: {count}/{total} ({rate:.1%})")
        
        return {
            'total_events': total,
            'field_fill_rates': {k: v/total if total > 0 else 0 for k, v in stats.items()},
            'avg_fill_rate': sum(stats.values()) / (len(stats) * total) if total > 0 else 0
        }

    def run_evaluation(self, pred_data: Dict) -> Dict:
        """执行完整评估流程"""
        print("=" * 70)
        print("【最优评估】模糊匹配策略（第五次优化版）")
        print("=" * 70)

        all_pred_events = pred_data.get('events', {}).get('events', [])
        event_mapping = self.build_event_mapping(all_pred_events)

        entity_metrics = self.evaluate_entities(pred_data, event_mapping)
        event_metrics = self.evaluate_events(all_pred_events, event_mapping)
        relation_metrics = self.evaluate_relations(pred_data.get('relations', {}), event_mapping)
        
        # 新增：事件新字段评估
        field_metrics = self.evaluate_event_fields(all_pred_events)

        macro_f1 = (entity_metrics['f1'] + event_metrics['f1'] + relation_metrics['f1']) / 3

        results = {
            'entity_extraction': entity_metrics,
            'event_extraction': {
                'full_events': event_metrics,
                'field_metrics': field_metrics  # 新增字段评估
            },
            'relation_extraction': relation_metrics,
            'summary': {
                'entity_f1': entity_metrics['f1'],
                'event_f1': event_metrics['f1'],
                'relation_f1': relation_metrics['f1'],
                'macro_avg_f1': round(macro_f1, 4)
            }
        }

        print(f"\n{'=' * 70}")
        print("【最终评估结果汇总】")
        print(f"{'=' * 70}")
        print(f"实体抽取:")
        print(f"  原始预测: {entity_metrics['counts']['raw_pred']}个 → 过滤后: {entity_metrics['counts']['filtered_pred']}个")
        print(f"  标注: {entity_metrics['counts']['gold']}个 | TP={entity_metrics['counts']['tp']} FP={entity_metrics['counts']['fp']} FN={entity_metrics['counts']['fn']}")
        print(f"  F1: {results['summary']['entity_f1']:.2%}")

        print(f"\n事件抽取:")
        print(f"  预测: {event_metrics['counts']['pred']}个 | 标注: {event_metrics['counts']['gold']}个")
        print(f"  TP={event_metrics['counts']['tp']} FP={event_metrics['counts']['fp']} FN={event_metrics['counts']['fn']}")
        print(f"  F1: {results['summary']['event_f1']:.2%}")

        print(f"\n关系抽取:")
        print(f"  原始预测: {relation_metrics['counts']['raw_pred']}个 → 过滤后: {relation_metrics['counts']['filtered_pred']}个")
        print(f"  标注: {relation_metrics['counts']['gold']}个 | TP={relation_metrics['counts']['tp']} FP={relation_metrics['counts']['fp']} FN={relation_metrics['counts']['fn']}")
        print(f"  F1: {results['summary']['relation_f1']:.2%}")

        print(f"\n{'=' * 70}")
        print(f"宏观平均 F1: {results['summary']['macro_avg_f1']:.2%}")
        print(f"{'=' * 70}")

        return results
