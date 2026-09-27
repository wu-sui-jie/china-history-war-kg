"""
最优评估器
历史战争文本知识抽取评估系统

评估策略是"最优模糊匹配"：事件名、实体名、关系名都按相似度阈值判定，再按相似度
降序做一对一贪心匹配。四个阈值由调用方从 `config/eval_config.json` 读入
（事件名 0.35、事件-事件尾实体 0.35、关系名 40%、实体 70%），另保留别名映射与
"包含关系满分"等规则。
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from fuzzywuzzy import fuzz
from typing import Dict, List, Set, Tuple
from war_extraction.utils.normalizer import Normalizer
from war_extraction.utils.value_parsing import (
    first_effective_place,
    parse_year_for_order,
    split_multi_value,
)

#: 朝代写法归一（**只收敛明显同义**，不改写内容）；用于事件配对的朝代相容判断。
#: 产物里 `清朝`/`清`、`明朝`/`明` 是同一朝代的两种写法，不该因为写法不同就被判成两场战争。
_DYNASTY_ALIASES = {
    "夏朝": "夏", "商朝": "商", "周朝": "周", "秦朝": "秦", "汉朝": "汉",
    "隋朝": "隋", "唐朝": "唐", "宋朝": "宋", "辽朝": "辽", "金朝": "金",
    "元朝": "元", "明朝": "明", "清朝": "清",
}

#: 朝代 → **时代档位**（按时间先后编号）。只用于事件配对的"朝代相容"判断，
#: 且**只收无歧义的写法**：认不出来的一律当"未知"→ 放行（见 `_dynasty_stage`）。
#:
#: 为什么需要这张表：两侧的朝代口径根本不同——预测侧写宏观朝代（`五代十国`、`东晋`），
#: 标注侧写政权（`后唐/后梁`、`十六国`），甚至同一场战争两侧分属相邻两朝（`松锦之战`
#: 预测`清`、标注明）。用"字符串相等"判相容会把这些**正确的配对**全部拒掉：
#: 实测 144 对被约束拆掉的配对里，84 对栽在朝代这一条上，其中大半是口径差异而非真错配。
_ERA_STAGES = {
    "上古": 0, "远古": 0, "原始社会": 0, "父系氏族社会": 0, "三皇五帝": 0,
    "夏": 1, "商": 2, "西周": 3, "周": 3,
    "春秋": 4, "战国": 5, "先秦": 5,
    "秦": 6, "西汉": 7, "新": 7, "东汉": 8, "汉": 7,
    "三国": 9, "魏": 9, "蜀": 9, "吴": 9,
    "西晋": 10, "晋": 10,
    "东晋": 11, "十六国": 11,
    "南北朝": 12, "南朝": 12, "北朝": 12,
    "隋": 13, "唐": 14, "五代十国": 15, "五代": 15,
    "北宋": 16, "辽": 16, "西夏": 16, "宋": 16,
    "南宋": 17, "金": 17,
    "元": 18, "明": 19, "清": 20, "民国": 21,
}

#: 相邻时代档位算相容（`abs(diff) <= 1`）：改朝换代的战争两侧常分属相邻两朝
#: （明末清初的 `松锦之战`、隋唐之际等），差 1 档不足以判定是两场战争。
_ERA_STAGE_TOLERANCE = 1

#: 残缺年份：1–3 位纯数字。标注里有 14 处这种残值（`天津保卫战` 的 `153`、
#: `北仓杨村阻击战` 的 `214`），它们不是"公元 153 年"而是被截断的年份。
_RESIDUAL_YEAR = re.compile(r"^\d{1,3}$")


def _is_residual_year(value) -> bool:
    """
    这个日期字段是不是**残缺年份**（1–3 位纯数字）。

    用途只有一个：让它**不参与**事件配对的年份比较。实测 `天津保卫战` 的标注
    `StartDate="153"`（截断残值）与预测 `"1900年6月"` 比出"相差 1747 年"，
    于是这对**同名且正确**的配对被年份约束拒掉。残值本身是标注缺陷（体检脚本会报出来、
    属阶段 1 的标注清理项），在它被清理之前，评估侧不能拿它做判据。
    """
    text = ("" if value is None else str(value)).strip()
    return bool(_RESIDUAL_YEAR.match(text))


#: 四类预测关系的固定顺序。三处口径（`filter_relations` / `filter_relations_by_category` /
#: `evaluate_relations` 的 `by_category`）都按这个顺序产出，结果才能逐项对照。
PRED_RELATION_CATEGORIES: Tuple[str, ...] = (
    "event-place", "event-person", "event-organization", "event-event",
)


class OptimalEvaluator:
    """
    最优模糊匹配评估器。
    """

    def __init__(self, annotation_dir: Path, relation_threshold: int = 40, event_sim_threshold: float = 0.35,
                 entity_fuzzy_threshold: int = 70, event_event_sim_threshold: float = 0.35,
                 event_year_tolerance: int = 30, enforce_semantic_constraints: bool = True):
        """
        初始化评估器
        Args:
            annotation_dir: 人工标注数据目录
            relation_threshold: 关系匹配模糊阈值(默认40%)
            event_sim_threshold: 事件名称匹配相似度阈值(默认0.35)
            entity_fuzzy_threshold: 实体模糊匹配阈值(默认70%)
            event_event_sim_threshold: 事件-事件关系中尾事件名称匹配阈值(默认0.35)
            event_year_tolerance: 事件配对的起始年份容差（年）；两侧年份都能解析时才生效
            enforce_semantic_constraints: 事件配对是否叠加朝代/时间/地点约束。
                关掉可以复现"只看名称相似度"的旧口径，用于对照说明约束带来的差异。
        """
        self.annotation_dir = Path(annotation_dir)
        self.relation_threshold = relation_threshold
        self.event_sim_threshold = event_sim_threshold
        self.entity_fuzzy_threshold = entity_fuzzy_threshold
        self.event_event_sim_threshold = event_event_sim_threshold
        self.event_year_tolerance = event_year_tolerance
        self.enforce_semantic_constraints = enforce_semantic_constraints
        #: 最近一次 `build_event_mapping` 的统计（配对约束拒绝了多少候选），供报告引用
        self.last_mapping_stats: Dict = {}
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
        #
        # 注意：下一行会用 `Normalizer.relation_map`（来自 `config/relation_types.json`）
        # **覆盖**上面这张硬编码表。被覆盖到的条目形同虚设——例如上面把"因果关系"列为
        # "顺承关系"的变体，而 config 把"因果关系"归给"因果关系"，最终以后者为准。
        # **改上面这张表可能完全看不到效果**；真想改关系归一，要先看
        # config/relation_types.json 覆盖了哪些键（`test_relation_types.py` 里有断言钉着）。
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

        # 这里原先还建了一份 `gold_rel_index`（head/relation/tail/category 四元组集合），
        # 供已删除的 `check_exists(gold)` 预过滤使用。预过滤删掉后它只写不读，
        # 已一并删除——留着会让人以为还有一条"先用 gold 筛一遍"的路径。

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

    def relation_types_match(self, pred_relation: str, gold_relation: str) -> bool:
        """
        关系类型是否算匹配。

        五个规范的事件-事件关系类型**两两之间的 fuzz.ratio 都是 50**——都带"关系"二字、
        4 个字里中 2 个（2*2/8=50），全部越过阈值 40。若对它们也走模糊比对，
        标注 `(E1, 顺承关系, E2)` 对上预测 `(E1, 因果关系, E2)` 与
        `(E1, 并列关系, E2)` 都会判 tp=1：**事件-事件关系类型判错照样满分**，
        关系指标对这一类错误完全不敏感。

        口径：
          - 两侧都在五个规范事件-事件关系类型里 → **要求精确相等**（`因果关系` ≠ `顺承关系`）；
          - 其余情况（自由文本关系名，如"主战场""统帅"）仍走模糊比对——写法不唯一，
            模糊比对本来就是对的。

        精确相等这条是评估口径的一部分：靠模糊比对把不同类型也算成立的话，
        关系指标会对"类型判错"完全不敏感，所以不能退回纯模糊比对。
        """
        if pred_relation == gold_relation:
            return True
        both_canonical = (pred_relation in self.normalizer.CANONICAL_EVENT_RELATION_TYPES
                          and gold_relation in self.normalizer.CANONICAL_EVENT_RELATION_TYPES)
        if both_canonical:
            return False
        return fuzz.ratio(pred_relation, gold_relation) / 100.0 >= self.relation_threshold / 100

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

    def _first_place(self, value) -> str:
        """事件字段里的首个有效地点（与抽取侧 `value_parsing.first_effective_place` 同口径）。"""
        return first_effective_place(self.normalizer, value)

    def _place_set(self, value) -> set:
        """
        事件 `Place` 字段的**全部**有效地点（归一后）。

        配对相容判断必须用集合而不是"首个地点"：`Place` 是多值字段，两侧列出的地点
        个数与顺序都不同——实测 `宁锦之战` 两侧是同一对集合、只是顺序相反（`锦州、宁远`
        vs `宁远、锦州`），只比首个就会被判成"地点不同"而拒掉这对**完全正确**的配对。
        """
        return {
            self.normalizer.normalize_entity_name(name)
            for name in split_multi_value(value)
            if name and not self.normalizer.is_noisy_place_name(name)
        }

    def _normalized_dynasty(self, value) -> str:
        """朝代归一（只收敛明显同义写法，不改写内容）；空值/占位词返回空串。"""
        text = ("" if value is None else str(value)).strip()
        if not text or self.normalizer.is_placeholder_value(text):
            return ""
        return _DYNASTY_ALIASES.get(text, text)
        return {
            self.normalizer.normalize_entity_name(name)
            for name in split_multi_value(value)
            if name and not self.normalizer.is_noisy_place_name(name)
        }

    def _dynasty_stage(self, value):
        """
        朝代 → **时代档位**；认不出来时返回 `None`（调用方按"未知"处理，即**放行**）。

        只认 `_ERA_STAGES` 里的无歧义写法：`后唐/后梁`、`十六国`、`元末明初` 这类
        多值或非枚举写法一律返回 `None`。这个方向是刻意的——**分类不出来就放行**，
        宁可放过一次跨朝代的错配，也不要因为朝代口径不同而拒掉正确配对。
        """
        text = self._normalized_dynasty(value)
        if not text:
            return None
        return _ERA_STAGES.get(text)

    def _semantic_pair_rejection(self, pred_event: Dict, gold_event: Dict,
                                 pred_name: str = "", gold_name: str = "") -> str:
        """
        名称相似度之外的语义约束：朝代、时间、地点。**返回拒绝原因**，允许配对时返回空串。

        **为什么必须加。** 只有 0.35 的全局相似度时，实测出现过
        `红巾军起义 → 英军挑起"亚罗船事件"`（相似度 0.25）、
        `床兀儿平定海都、笃哇之战 → 定都天京`（0.25）这类配对——那是**两场不同的战争**被判成同一场。
        后果有两层：虚增事件 TP；把 gold 关系挂到错误的预测事件上（错误报告里出现原文根本没有的关系）。

        **四条口径，其中前两条是"放行"规则**（2026-09-26 复核后补，证据见
        `tools/event_pairing_review.py` 导出的配对差异）：

        1. **同名（归一后相等）一律放行**。原来没有这条，实测 144 对被拆的配对里有
           22 对是**同名事件**（`官渡之战 → 官渡之战`、`七国之乱 → 七国之乱`），
           而事件 TP 本身就是按名称集合算的——拒绝同名配对等于自伤 TP、白送 FN。
        2. **两侧信息不足时放行**（任一侧缺值 / 认不出来）。缺值不等于不符。
        3. `dynasty` — 两侧都能归到时代档位（`_dynasty_stage`）且相差超过 1 档才拒。
           用档位而不是字符串相等：`清` 与 `明` 相邻（明末清初同一场战争）、
           `五代十国` 与 `后唐/后梁` 是口径粗细之差，都不该拒。
        4. `year` — 两侧起始年份都能解析、且**都不是残缺年份**、且相差超过
           `event_year_tolerance` 才拒。标注里有 14 处 1–3 位纯数字残值
           （`天津保卫战` 的 `153`），拿它跟预测的 `1900年6月` 比会得出"相差 1747 年"。
        5. `place` — 两侧地点**集合**都有值时，交集为空才算不符；单值包含也算相容。

        返回**原因串**而不是布尔值：约束是否过严只能用"被拒的是哪些对"来判，
        而只有布尔值的话，导出来的差异里分不清是哪一条在拦。
        阈值与开关都在 `config/eval_config.json`，改口径要连带记录。
        """
        if self.enforce_semantic_constraints is False:
            return ""

        if pred_name and gold_name and (
            self.normalizer.normalize_event_name(pred_name)
            == self.normalizer.normalize_event_name(gold_name)
        ):
            return ""

        pred_stage = self._dynasty_stage(pred_event.get("DynastyName"))
        gold_stage = self._dynasty_stage(gold_event.get("DynastyName"))
        if (pred_stage is not None and gold_stage is not None
                and abs(pred_stage - gold_stage) > _ERA_STAGE_TOLERANCE):
            return "dynasty"

        pred_start = pred_event.get("StartDate")
        gold_start = gold_event.get("StartDate")
        if not _is_residual_year(pred_start) and not _is_residual_year(gold_start):
            pred_year = parse_year_for_order(pred_start)
            gold_year = parse_year_for_order(gold_start)
            if (pred_year is not None and gold_year is not None
                    and abs(pred_year - gold_year) > self.event_year_tolerance):
                return "year"

        pred_places = self._place_set(pred_event.get("Place"))
        gold_places = self._place_set(gold_event.get("Place"))
        if pred_places and gold_places:
            if pred_places & gold_places:
                return ""
            # 单值包含也算相容（`官渡` vs `官渡（今河南中牟）` 这类写法差异）
            for pred_place in pred_places:
                for gold_place in gold_places:
                    if pred_place in gold_place or gold_place in pred_place:
                        return ""
            return "place"
        return ""

    def build_event_mapping(self, pred_events: List[Dict], enforce_constraints: bool = None) -> Dict[str, str]:
        """
        构建预测事件与标注事件的映射表（相似度降序贪心 + 语义约束）。

        语义约束（朝代/时间/地点）见 `_semantic_pair_rejection`；被约束拒掉的候选按**原因**
        分项计数，报告里能看出"相似度够但语义不符"被拦了多少条、被哪一条拦的——
        这既是配对质量的证据，也提醒口径变更的影响面。

        Args:
            enforce_constraints: 覆盖实例上的 `enforce_semantic_constraints`。
                只在做"约束该不该放宽"的复核时传 `False`（见 `event_mapping_diff`）。
        """
        pred_list = [(e.get('EventName', '').strip(), i) for i, e in enumerate(pred_events) if e.get('EventName')]
        gold_list = [(e.get('EventName', '').strip(), i) for i, e in enumerate(self.gold_events.get('events', [])) if e.get('EventName')]

        enforce = self.enforce_semantic_constraints if enforce_constraints is None else enforce_constraints
        print(f"\n【事件匹配】")
        print(f"  预测事件数: {len(pred_list)}")
        print(f"  标注事件数: {len(gold_list)}")
        print(f"  匹配阈值: {self.event_sim_threshold}"
              + (f"（另加语义约束：朝代 / 起始年份容差 {self.event_year_tolerance} 年 / 首个地点）"
                 if enforce else "（**语义约束已关闭**：只看名称相似度）"))

        gold_by_index = {index: event for index, event in enumerate(self.gold_events.get('events', []))}
        pred_by_index = {index: event for index, event in enumerate(pred_events)}

        scores = []
        candidate_pairs = 0          # 名称相似度达标的候选对
        rejected_by_reason = {"dynasty": 0, "year": 0, "place": 0}
        for pred_name, pred_idx in pred_list:
            for gold_name, gold_idx in gold_list:
                sim = self.calculate_similarity(pred_name, gold_name)
                if sim < self.event_sim_threshold:
                    continue
                candidate_pairs += 1
                if enforce:
                    reason = self._semantic_pair_rejection(
                        pred_by_index[pred_idx], gold_by_index[gold_idx], pred_name, gold_name)
                    if reason:
                        rejected_by_reason[reason] += 1
                        continue
                scores.append((sim, pred_name, gold_name, pred_idx, gold_idx))

        # 元组逐元素可比（相似度→预测名→标注名→下标）且下标唯一，故排序是全序、
        # 结果与进程哈希种子无关；不要改成只按相似度排序（并列时就会依赖输入顺序）。
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

        rejected_by_semantics = sum(rejected_by_reason.values())
        match_rate = len(mapping) / len(gold_list) if gold_list else 0.0
        print(f"  匹配成功: {len(mapping)} (匹配率: {match_rate:.1%})")
        print(f"  未匹配预测: {len(pred_list) - len(mapping)}")
        print(f"  未匹配标注: {len(gold_list) - len(mapping)}")
        if candidate_pairs and enforce:
            print(f"  候选对（相似度达标）: {candidate_pairs}，其中被语义约束拒绝: {rejected_by_semantics}"
                  f"（{rejected_by_semantics / candidate_pairs:.1%}；"
                  f"朝代 {rejected_by_reason['dynasty']} / 年份 {rejected_by_reason['year']}"
                  f" / 地点 {rejected_by_reason['place']}）")
        self.last_mapping_stats = {
            "pairs_matched": len(mapping),
            "candidate_pairs": candidate_pairs,
            "candidate_pairs_rejected_by_semantics": rejected_by_semantics,
            "rejected_ratio": (rejected_by_semantics / candidate_pairs) if candidate_pairs else 0.0,
            "rejected_by_reason": dict(rejected_by_reason),
            "semantic_constraints_enforced": enforce,
        }
        return mapping

    def event_mapping_diff(self, pred_events: List[Dict], limit: int = 300) -> Dict:
        """
        对照"开约束 / 关约束"两次事件配对，导出**被约束拆掉的配对**，供人工判定约束是否过严。

        **为什么要这个方法。** 约束一开，配对从 283 对掉到 176 对，而事件配对是三层指标的
        共同上游（事件 TP、实体的事件中心过滤、关系 gold 三元组）——"修正错配"与"砍掉正确配对"
        混在同一个数字里，只看 F1 分不开。这个方法把差额逐对导出来（含相似度与拒绝原因），
        人工判 20~30 对"该不该配"，再决定是否放宽 `event_year_tolerance` 或"地点互相包含"这一条。

        返回：`{"unconstrained_pairs": n, "constrained_pairs": m, "dropped": [...]}`。
        `dropped` 里每条含预测/标注事件名、相似度、拒绝原因，以及两侧的朝代/起始时间/地点字段
        （判定时手边就要有这些信息，否则还得回去翻产物）。
        """
        gold_by_index = {index: event for index, event in enumerate(self.gold_events.get('events', []))}
        pred_by_index = {index: event for index, event in enumerate(pred_events)}

        loose = self.build_event_mapping(pred_events, enforce_constraints=False)
        strict = self.build_event_mapping(pred_events, enforce_constraints=True)

        gold_name_by_index = {index: (event.get('EventName') or '').strip() for index, event in gold_by_index.items()}
        dropped = []
        for pred_name, gold_name in sorted(loose.items()):
            if strict.get(pred_name) == gold_name:
                continue
            pred_event = None
            gold_event = None
            for index, event in pred_by_index.items():
                if (event.get('EventName') or '').strip() == pred_name:
                    pred_event = event
                    break
            for index, event in gold_by_index.items():
                if gold_name_by_index[index] == gold_name:
                    gold_event = event
                    break
            reason = ""
            if pred_event is not None and gold_event is not None:
                reason = self._semantic_pair_rejection(pred_event, gold_event, pred_name, gold_name)
            dropped.append({
                "pred_event": pred_name,
                "gold_event": gold_name,
                "similarity": round(self.calculate_similarity(pred_name, gold_name), 4),
                "rejection_reason": reason or "（未被同一预测事件重新配对，属贪心竞争的结果）",
                "pred_dynasty": (pred_event or {}).get("DynastyName"),
                "gold_dynasty": (gold_event or {}).get("DynastyName"),
                "pred_start": (pred_event or {}).get("StartDate"),
                "gold_start": (gold_event or {}).get("StartDate"),
                "pred_place": (pred_event or {}).get("Place"),
                "gold_place": (gold_event or {}).get("Place"),
                "strict_mapped_to": strict.get(pred_name),
            })

        # 相似度高的排前面：它们最可能是被误杀的正确配对，最该先判
        dropped.sort(key=lambda item: (-item["similarity"], item["pred_event"]))
        return {
            "unconstrained_pairs": len(loose),
            "constrained_pairs": len(strict),
            "dropped_count": len(dropped),
            "dropped": dropped[:limit],
        }

    def evaluate_events_by_dynasty(self, pred_events: List[Dict], event_mapping: Dict) -> Dict:
        '''
        按**标注事件的朝代**分组统计事件 P/R/F1。

        分组依据取标注侧的朝代（评估的分母是标注），所以某一档的 F1 低说明
        "这个朝代的事件没配对上"，而不是"预测里这个朝代抽得多"。
        '''
        gold_by_name = {
            (event.get('EventName') or '').strip(): event
            for event in self.gold_events.get('events', [])
            if (event.get('EventName') or '').strip()
        }
        pred_names = {e.get('EventName', '').strip() for e in pred_events if e.get('EventName')}
        mapped_gold = set(event_mapping.values())
        mapped_pred = set(event_mapping.keys())

        groups: Dict[str, Dict[str, int]] = {}
        for gold_name in sorted(self.gold_event_names):
            dynasty = self._normalized_dynasty((gold_by_name.get(gold_name) or {}).get('DynastyName')) or "（朝代不详）"
            bucket = groups.setdefault(dynasty, {"gold": 0, "tp": 0, "unmatched_pred": 0})
            bucket["gold"] += 1
            if gold_name in mapped_gold:
                bucket["tp"] += 1

        # 未配上的预测事件没有对应的 gold 朝代，归到"（无法归组）"一档，
        # 免得它们从按朝代的报告里凭空消失（那会让各档 F1 看起来比整体好）。
        unmatched_pred = len(pred_names - mapped_pred)
        if unmatched_pred:
            groups.setdefault("（无法归组）", {"gold": 0, "tp": 0, "unmatched_pred": 0})
            groups["（无法归组）"]["unmatched_pred"] += unmatched_pred

        report = {}
        for dynasty, bucket in sorted(groups.items()):
            tp = bucket["tp"]
            fn = bucket["gold"] - tp
            fp = bucket["unmatched_pred"]
            p = tp / (tp + fp) if (tp + fp) else 0.0
            r = tp / (tp + fn) if (tp + fn) else 0.0
            f1 = 2 * p * r / (p + r) if (p + r) else 0.0
            report[dynasty] = {"gold": bucket["gold"], "tp": tp, "fp": fp, "fn": fn,
                               "precision": p, "recall": r, "f1": f1}
        return report

    def tagged_pred_triples(self, pred_relations: Dict, event_mapping: Dict) -> List[Tuple[str, Tuple[str, str, str]]]:
        """
        预测关系的**唯一构造入口**：返回 `[(分类, 三元组)]`，按条保留（不去重）。

        `filter_relations`（并集）与 `filter_relations_by_category`（分桶）都从这里派生，
        保证两者给出的条目集合逐项相同——原先两处各写一遍遍历，"改一处漏一处"就会让
        整体与分类的口径悄悄分叉。

        分类取自**源字段所在的容器**（事件-地点/人物/组织/事件），不是猜出来的：
        同一条三元组若在两个容器里各出现一次，这里会保留两条并各自带自己的类目。
        """
        matched_events = set(event_mapping.keys())
        tagged: List[Tuple[str, Tuple[str, str, str]]] = []

        for rel in pred_relations.get('event_place_relations', []):
            event = rel.get('EventName', '').strip()
            place = rel.get('modern_name', '').strip()
            if event in matched_events and place:
                tagged.append(("event-place", (event, self.normalize_relation(rel.get('relation', '')), place)))

        for rel in pred_relations.get('event_person_relations', []):
            event = rel.get('EventName', '').strip()
            person = rel.get('PersonName', '').strip()
            if event in matched_events and person:
                tagged.append(("event-person", (event, self.normalize_relation(rel.get('relation', '')), person)))

        for rel in pred_relations.get('event_organization_relations', []):
            event = rel.get('EventName', '').strip()
            org = rel.get('OrgName', '').strip()
            if event in matched_events and org:
                tagged.append(("event-organization", (event, self.normalize_relation(rel.get('relation', '')), org)))

        for rel in pred_relations.get('event_event_relations', []):
            event_a = rel.get('EventName_A', '').strip()
            event_b = rel.get('EventName_B', '').strip()
            if event_a in matched_events and event_b in matched_events:
                tagged.append(("event-event", (event_a, self.normalize_relation(rel.get('relation', '')), event_b)))

        return tagged

    def filter_relations_by_category(self, pred_relations: Dict, event_mapping: Dict) -> Dict[str, List]:
        """
        按**四类关系分别**返回"事件端已对齐"的预测三元组。

        整体指标仍由全部四类的并集算（`filter_relations`），这里额外给出分类结果，
        用于"按关系类型分别报告 P/R/F1"——只给一个宏观平均看不出是哪一类在拖后腿。

        与 `filter_relations` 同一口径：**按条保留**（`list`，不去重），且两者由
        `tagged_pred_triples` 同一份构造派生，条目集合必然一致。
        """
        buckets: Dict[str, List] = {category: [] for category in PRED_RELATION_CATEGORIES}
        for category, triple in self.tagged_pred_triples(pred_relations, event_mapping):
            buckets[category].append(triple)
        return buckets

    def filter_relations(self, pred_relations: Dict, event_mapping: Dict) -> List:
        """
        把预测关系收敛成"事件端已对齐"的三元组**列表**（按条，不去重），供 TP/FP 计算。

        **两处口径修正，缺一不可**：

        1. **删除"循环过滤"**。原实现里有个 `check_exists()`：先用 gold 关系去找相似的
           head/relation/tail，**只有"已经像某条 gold"的预测关系才进入评估**。实测后果是
           18,146 条原始预测里只有 888 条进入精确率计算，其余 17,258 条不计 FP。
           标准做法是：已对齐事件范围内的**全部**预测关系都参与 TP/FP。
        2. **不再用 `set` 去重**。原来返回 `set`，于是**完全重复的三元组只算一次**——
           重复项之间的 FP 被静默消掉。现在返回 `list`：同一三元组出现 k 次就计 k 条预测，
           其中 1 条有机会成为 TP，其余 k-1 条必然落进 FP（`match_relation_triples` 的一对一
           匹配天然给出这个结果：gold 在同一轮里只会被认领一次）。

        **实测影响（2026-09-26 复核）**：重复确实存在，来源是**关系名归一把别名写法收敛到同一
        规范名**——产物里同时有 `巨鹿之战 主战场 巨鹿` 与 `巨鹿之战 发生地点 巨鹿`，
        归一后就是同一条关系写了两遍。在已对齐事件范围内共 **747 条重复**
        （事件-地点 156 / 事件-人物 528 / 事件-组织 63 / 事件-事件 0），它们现在计入 FP。
        别按"产物里有没有重复行"来判断这件事：**原始行没有重复、归一之后才有**
        （按原始行去数是 0，这正是第一次复核时看漏它的原因）。

        与 `build_gold_triples` 的**不对称是有意的**：gold 侧仍然用 `set`——标注里重复的关系行
        （实测事件-事件有 21 条完全重复）是标注缺陷，不该变成"要求预测也多输出一条"。
        标注重复由体检脚本报出（`annotations.relations[*].duplicate_rows`），在标注侧清理。
        """
        return [triple for _category, triple in self.tagged_pred_triples(pred_relations, event_mapping)]

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
        """
        评估实体抽取：直接用 entities 数据，不从 events 反向提取实体。

        **两套身份口径一起报**（这是"实体精确率为什么只有 11.50%"的直接答案）：

        - `mention` 口径（默认，与历史指标可比）：预测侧**逐行**计数，参考侧压成名称集合。
          预测地点 5,316 行 vs 参考 378 个名称，于是"洛阳"40 行里 1 行算 TP、39 行算 FP。
        - `canonical` 口径：两侧都先做别名归一（`Normalizer` 的别名表 + `aliases.json`），
          预测侧按规范名去重。实测只把预测地点按名称去重（不动任何模型输出），
          实体 F1 就从 20.42% 升到 26.98%——**这部分差距是口径差异，不是模型错误**。

        两套都报的理由：单看任一套都会误判。mention 口径惩罚"同一实体的多次提及"，
        canonical 口径掩盖"同一个名字抽了 40 遍"的低效。要判断抽取质量得两者一起看。
        """
        raw_places = pred_data.get('entities', {}).get('places', [])
        raw_persons = pred_data.get('entities', {}).get('persons', [])
        raw_orgs = pred_data.get('entities', {}).get('organizations', [])

        gold_place_rows = self.gold_entities.get('places', [])
        gold_person_rows = self.gold_entities.get('persons', [])
        gold_org_rows = self.gold_entities.get('organizations', [])

        gold_places = {p.get('geo_name', '').strip() for p in gold_place_rows if p.get('geo_name')}
        gold_persons = {p.get('PersonName', '').strip() for p in gold_person_rows if p.get('PersonName')}
        gold_orgs = {o.get('OrgName', '').strip() for o in gold_org_rows if o.get('OrgName')}

        def match_entities(pred_list, gold_names, key, canonicalize=None):
            """
            匹配实体，返回 (matched, unmatched, unmatched_gold)。

            `canonicalize` 为 None 时用原字符串（mention 口径）；给了函数就把两侧都先
            归一到规范名，并把预测侧按规范名去重（canonical 口径）。
            """
            if canonicalize is not None:
                deduped = {}
                for item in pred_list:
                    name = (item.get(key) or '').strip()
                    if not name:
                        continue
                    canonical = canonicalize(name)
                    if canonical and canonical not in deduped:
                        deduped[canonical] = item
                pred_list = list(deduped.values())
                gold_names = {canonicalize(name) for name in gold_names}

            matched = []
            unmatched = []
            unmatched_gold = set(gold_names)
            for item in pred_list:
                name = (item.get(key) or '').strip()
                if not name:
                    continue
                comparable = canonicalize(name) if canonicalize is not None else name

                def valid_match(pred_name, gold_name):
                    if pred_name == gold_name:
                        return True
                    if len(pred_name) >= 2 and pred_name in gold_name:
                        return True
                    if len(gold_name) >= 2 and gold_name in pred_name:
                        return True
                    return fuzz.ratio(pred_name, gold_name) >= self.entity_fuzzy_threshold

                # 包含关系匹配要求长度 ≥2，否则 "汉" 会匹配上 "汉武帝"
                # 遍历前必须定序：unmatched_gold 是集合，字符串哈希每进程随机化；
                # 相似度并列时"谁先被取走"会随进程变化，错误样例随之漂移。
                best_gold = None
                best_score = -1
                for gold_name in sorted(unmatched_gold):
                    if valid_match(comparable, gold_name):
                        score = fuzz.ratio(comparable, gold_name)
                        if score > best_score:
                            best_score = score
                            best_gold = gold_name

                # 实体匹配是一对一，所以召回率不可能超过 100%
                if best_gold:
                    matched.append(item)
                    unmatched_gold.remove(best_gold)
                else:
                    unmatched.append(item)
            return matched, unmatched, unmatched_gold

        def calc_metrics(matched, unmatched, gold_names):
            """按 TP/FP/FN 算 P/R/F1 与计数"""
            tp = len(matched)
            fp = len(unmatched)
            fn = len(gold_names) - tp
            p = tp / (tp + fp) if (tp + fp) > 0 else 0
            r = tp / (tp + fn) if (tp + fn) > 0 else 0
            f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0
            return p, r, f1, tp, fp, fn, len(matched) + len(unmatched), len(gold_names)

        def canonicalize(name: str) -> str:
            # 真正调用别名归一：原先 `evaluate_entities` 只做"原字符串相等/双向包含/模糊"，
            # 没调 `Normalizer`，于是 `aliases.json` 里 `孙滨 → 孙膑` 这类归一
            # 对实体指标完全不生效——标注文档里"别名归一后可以匹配"的表述与实现不符。
            return self.normalizer.normalize_entity_name(name) or (name or '').strip()

        groups = [
            ("places", raw_places, gold_places, 'geo_name', gold_place_rows),
            ("persons", raw_persons, gold_persons, 'PersonName', gold_person_rows),
            ("organizations", raw_orgs, gold_orgs, 'OrgName', gold_org_rows),
        ]

        details = {}
        canonical_details = {}
        identity = {}
        unmatched_names = {}
        mention_totals = {'tp': 0, 'fp': 0, 'fn': 0}
        canonical_totals = {'tp': 0, 'fp': 0, 'fn': 0}

        for label, pred_list, gold_names, key, gold_rows in groups:
            matched, unmatched, _ = match_entities(pred_list, gold_names, key)
            p, r, f1, tp, fp, fn, pred_cnt, gold_cnt = calc_metrics(matched, unmatched, gold_names)
            details[label] = {'p': p, 'r': r, 'f1': f1, 'tp': tp, 'fp': fp, 'fn': fn,
                              'pred': pred_cnt, 'gold': gold_cnt}
            mention_totals['tp'] += tp
            mention_totals['fp'] += fp
            mention_totals['fn'] += fn
            unmatched_names[label] = sorted((item.get(key) or '').strip() for item in unmatched)

            c_matched, c_unmatched, _ = match_entities(pred_list, gold_names, key, canonicalize=canonicalize)
            cp, cr, cf1, ctp, cfp, cfn, cpred_cnt, cgold_cnt = calc_metrics(c_matched, c_unmatched, gold_names)
            canonical_details[label] = {'p': cp, 'r': cr, 'f1': cf1, 'tp': ctp, 'fp': cfp, 'fn': cfn,
                                        'pred': cpred_cnt, 'gold': cgold_cnt}
            canonical_totals['tp'] += ctp
            canonical_totals['fp'] += cfp
            canonical_totals['fn'] += cfn

            # 同名多行是"身份口径不一致"的量化面：预测侧逐行计数、参考侧按名称集合，
            # 多出来的行必然记 FP。这里把两边的粒度都摆出来，省得再靠人猜。
            named_rows = [(item.get(key) or '').strip() for item in pred_list]
            named_rows = [name for name in named_rows if name]
            unique_names = set(named_rows)
            identity[label] = {
                'pred_rows': len(named_rows),
                'pred_unique_names': len(unique_names),
                'pred_unique_canonical': cpred_cnt,
                'gold_rows': len(gold_rows),
                'gold_unique_names': gold_cnt,
                'gold_unique_canonical': cgold_cnt,
                'duplicate_rows': len(named_rows) - len(unique_names),
            }

        def micro(totals):
            tp, fp, fn = totals['tp'], totals['fp'], totals['fn']
            p = tp / (tp + fp) if (tp + fp) > 0 else 0
            r = tp / (tp + fn) if (tp + fn) > 0 else 0
            f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0
            return p, r, f1

        # 三层合计用标准 micro 口径（P=TP/(TP+FP)、R=TP/(TP+FN)）。
        # 注意不要退回"按预测数加权"（Σr·pred/Σpred）：那既非 micro 也非 macro，
        # 数值没有标准含义（其中 P 在数学上恒等于 micro-P，R 则是错的）。
        avg_p, avg_r, avg_f1 = micro(mention_totals)
        can_p, can_r, can_f1 = micro(canonical_totals)

        print(f"\n【实体评估】")
        print(f"  原始预测: 地点{details['places']['pred']} + 人物{details['persons']['pred']}"
              f" + 组织{details['organizations']['pred']} = {sum(d['pred'] for d in details.values())}行")
        print(f"  标注名称: 地点{details['places']['gold']} + 人物{details['persons']['gold']}"
              f" + 组织{details['organizations']['gold']} = {sum(d['gold'] for d in details.values())}个")
        for label, title in (('places', '地点'), ('persons', '人物'), ('organizations', '组织')):
            d = details[label]
            c = canonical_details[label]
            print(f"  {title}: mention P={d['p']:.2%} R={d['r']:.2%} F1={d['f1']:.2%} (TP={d['tp']}) | "
                  f"canonical P={c['p']:.2%} R={c['r']:.2%} F1={c['f1']:.2%} (TP={c['tp']})")
        print(f"  总体: mention P={avg_p:.2%} R={avg_r:.2%} F1={avg_f1:.2%}"
              f" | canonical P={can_p:.2%} R={can_r:.2%} F1={can_f1:.2%}")

        return {
            'precision': avg_p,
            'recall': avg_r,
            'f1': avg_f1,
            'details': details,
            'canonical': {
                'precision': can_p,
                'recall': can_r,
                'f1': can_f1,
                'details': canonical_details,
            },
            'identity': identity,
            'counts': {
                'raw_pred': sum(d['pred'] for d in details.values()),
                'filtered_pred': sum(d['pred'] for d in details.values()),
                'gold': sum(d['gold'] for d in details.values()),
                'tp': mention_totals['tp'],
                'fp': mention_totals['fp'],
                'fn': mention_totals['fn'],
                'canonical_tp': canonical_totals['tp'],
                'canonical_fp': canonical_totals['fp'],
                'canonical_fn': canonical_totals['fn'],
            },
            'error_samples': {
                # 定序后再截前 20：错误分析报告要能逐条比对，不能随取样顺序变化
                'unmatched_places': unmatched_names['places'][:20],
                'unmatched_persons': unmatched_names['persons'][:20],
                'unmatched_organizations': unmatched_names['organizations'][:20],
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

    def match_relation_triples(self, pred_triples, gold_triples, event_mapping: Dict) -> Dict:
        """
        关系三元组一对一贪心匹配。

        输入是**按条**的预测三元组（`filter_relations` 的返回值，允许重复）与 gold 三元组集合。
        一对一匹配天然把重复项罚成 FP：`pred` 里同一三元组出现 k 次时，gold 只被认领一次，
        其余 k-1 次找不到未认领的 gold → 计入 `unmatched_predictions`（FP）。

        输入是**集合或列表**，而 Python 的字符串哈希每进程随机化，集合迭代顺序随
        `PYTHONHASHSEED` 变化。因此这里先把两侧都按内容排序再遍历——否则同一份 pred + gold
        换个进程就会得到不同的 tp/fp/fn（实测全量数据上 tp 在 739~743 之间漂移、
        F1 在 0.7438~0.7479 之间漂移）。排序对含重复项的列表同样成立（`sorted` 稳定且全序），
        所以"按条计数"与"结果可复现"不冲突。

        注意 `gold_list` 必须唯一（`build_gold_triples` 返回 `set`）：`matched_gold` 是按
        `gold_list` 下标打标的，gold 侧若带重复项，同一 gold 会被重复认领、FN 反而算少了。
        """
        reverse_mapping = {v: k for k, v in event_mapping.items()}
        gold_list = sorted(gold_triples)
        matched_gold = [False] * len(gold_list)
        tp = 0
        unmatched_pred_triples = []

        for pred in sorted(pred_triples):
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

                # 关系类型匹配：规范事件-事件关系类型要求精确相等，其余走模糊
                # （见 relation_types_match 的说明——纯模糊时五个规范类型两两都过阈值）
                if not self.relation_types_match(rel_pred, rel_gold):
                    continue
                # rel_score 只用于下面的排序（精确相等时为 1.0，自由文本关系名按相似度）
                rel_score = fuzz.ratio(rel_pred, rel_gold) / 100.0

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
                    if tail_score < self.relation_threshold / 100:
                        continue

                # 并列时取 gold_list 中靠前者；gold_list 已定序，所以并列结果也可复现
                combined = (head_sim + rel_score + tail_score) / 3
                if combined > best_score:
                    best_score = combined
                    best_match_idx = i

            if best_match_idx != -1:
                tp += 1
                matched_gold[best_match_idx] = True
            else:
                unmatched_pred_triples.append(pred)

        return {
            'tp': tp,
            'matched_gold': matched_gold,
            'gold_list': gold_list,
            # pred 已按定序遍历，故错误样例的成员与顺序都是确定的
            'unmatched_predictions': unmatched_pred_triples,
        }

    def evaluate_relations(self, pred_relations: Dict, event_mapping: Dict) -> Dict:
        """
        评估关系抽取：头事件按事件名相似度、尾实体按关系阈值模糊匹配。
        """
        print(f"\n【关系评估】阈值={self.relation_threshold}%")

        raw_place_rels = len(pred_relations.get('event_place_relations', []))
        raw_person_rels = len(pred_relations.get('event_person_relations', []))
        raw_org_rels = len(pred_relations.get('event_organization_relations', []))
        raw_event_rels = len(pred_relations.get('event_event_relations', []))
        raw_total = raw_place_rels + raw_person_rels + raw_org_rels + raw_event_rels

        tagged_pred = self.tagged_pred_triples(pred_relations, event_mapping)
        pred_triples = [triple for _category, triple in tagged_pred]
        gold_triples = self.build_gold_triples(event_mapping)
        gold_total = len(gold_triples)
        # 按条计数的同时给出唯一三元组数：两者的差额就是"重复项"，它们是必然的 FP，
        # 报出来才不会让"过滤后预测 N 条"这个数字看起来像凭空多出来的。
        pred_unique_total = len(set(pred_triples))

        print(f"  原始预测: 地点{raw_place_rels} + 人物{raw_person_rels} + 组织{raw_org_rels} + 事件{raw_event_rels} = {raw_total}个")
        print(f"  过滤后预测: {len(pred_triples)} 条（仅与已映射事件相关；其中唯一三元组 {pred_unique_total} 条，"
              f"重复 {len(pred_triples) - pred_unique_total} 条）")
        print(f"  标注总数(事件相关): {gold_total}个")

        match = self.match_relation_triples(pred_triples, gold_triples, event_mapping)
        tp = match['tp']
        gold_list = match['gold_list']
        matched_gold = match['matched_gold']
        unmatched_pred_triples = match['unmatched_predictions']
        fp = len(unmatched_pred_triples)
        fn = matched_gold.count(False)

        p = tp / (tp + fp) if (tp + fp) > 0 else 0
        r = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0

        print(f"  匹配详情: TP={tp}, FP={fp}, FN={fn}")
        print(f"  指标: P={p:.2%}, R={r:.2%}, F1={f1:.2%}")

        # 按四类关系分别报 P/R/F1：宏观平均盖住的是"哪一类在拖后腿"，
        # 只给一个数没法决定先修哪一类。
        #
        # **口径：一次匹配、按类目切分**（不再对四类各跑一遍匹配）。原先整体用
        # `filter_relations` 的并集匹配、分类用分桶各自匹配，两次结果对不上：
        # 并集匹配时不同类目的预测会**竞争同一条 gold**（先到先得），分桶匹配时各自
        # 独立认领。实测 `evaluation/run_20260926_after3/results.json`：整体 TP=806 /
        # FP=4557，四类之和 TP=803 / FP=4560，差 3 条——同一份产物、同一个评估器，
        # 两套数互不相等。切分之后"四类 TP 之和 == 整体 TP、四类 FP 之和 == 整体 FP"
        # 恒成立，`summary.relation_f1` 与 `by_category` 可以互相校验。
        by_category = self.split_match_by_category(match, tagged_pred, event_mapping)
        print("  分类明细:")
        for category, item in by_category.items():
            print(f"    {category}: P={item['precision']:.2%} R={item['recall']:.2%} "
                  f"F1={item['f1']:.2%} (预测{item['pred']}条/唯一{item['pred_unique']} "
                  f"标注{item['gold']} TP={item['tp']})")

        return {
            'precision': p,
            'recall': r,
            'f1': f1,
            'by_category': by_category,
            'counts': {
                'raw_pred': raw_total,
                'filtered_pred': len(pred_triples),
                'filtered_pred_unique': pred_unique_total,
                'filtered_pred_duplicates': len(pred_triples) - pred_unique_total,
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

    def split_match_by_category(self, match: Dict, tagged_pred: List, event_mapping: Dict) -> Dict:
        """
        把**并集那一次匹配**的结果按类目切分，产出 `by_category`（不再各类目各跑一遍匹配）。

        **为什么必须切分。** 两次独立匹配的结果对不上：并集匹配时不同类目的预测会竞争同一条
        gold（先到先得），分桶匹配时各自独立认领；而分桶匹配的 FN 又是"只在本类目的 gold 里找"，
        会把"本类目的预测认领了别类目的 gold"这类交叉情形算成两个 FN。切分之后
        `sum(tp) == match['tp']`、`sum(fp) == len(match['unmatched_predictions'])`
        恒成立，`by_category` 与整体的 `counts` 可以互相校验。

        切分口径：

        - `tp` / `fp` 按**预测条目**的类目归属（`tagged_pred` 带标签，重复项按条计数：
          未匹配条数用 `Counter` 逐条消去，与并集的按条计数一致）；
        - `fn` 按**gold 条目**的类目归属。gold 侧的一条三元组只归一个类目（先到先得），
          所以各档 FN 之和恒等于并集的 FN——否则同一个 gold 在两类目里各算一次 FN，
          和就比整体大。
        """
        pred_total: Counter = Counter()
        pred_triples_by_category = {category: [] for category in PRED_RELATION_CATEGORIES}
        for category, triple in tagged_pred:
            pred_total[category] += 1
            pred_triples_by_category[category].append(triple)

        unmatched_remaining: Counter = Counter(match['unmatched_predictions'])
        pred_unmatched: Counter = Counter()
        for category, triple in tagged_pred:
            if unmatched_remaining[triple] > 0:
                unmatched_remaining[triple] -= 1
                pred_unmatched[category] += 1

        gold_by_category = self.build_gold_triples_by_category(event_mapping)
        gold_owner: Dict[Tuple[str, str, str], str] = {}
        for category in PRED_RELATION_CATEGORIES:
            for triple in sorted(gold_by_category[category]):
                gold_owner.setdefault(triple, category)

        gold_unmatched: Counter = Counter()
        for index, gold in enumerate(match['gold_list']):
            if match['matched_gold'][index]:
                continue
            owner = gold_owner.get(gold)
            if owner:
                gold_unmatched[owner] += 1

        report = {}
        for category in PRED_RELATION_CATEGORIES:
            category_tp = pred_total[category] - pred_unmatched[category]
            category_fp = pred_unmatched[category]
            category_fn = gold_unmatched[category]
            category_p = category_tp / (category_tp + category_fp) if (category_tp + category_fp) else 0
            category_r = category_tp / (category_tp + category_fn) if (category_tp + category_fn) else 0
            category_f1 = (2 * category_p * category_r / (category_p + category_r)
                           if (category_p + category_r) else 0)
            report[category] = {
                'precision': category_p, 'recall': category_r, 'f1': category_f1,
                'pred': pred_total[category],
                'pred_unique': len(set(pred_triples_by_category[category])),
                'gold': len(gold_by_category[category]),
                'tp': category_tp, 'fp': category_fp, 'fn': category_fn,
            }
        return report

    def build_gold_triples_by_category(self, event_mapping: Dict) -> Dict[str, Set]:
        """按四类关系分别构建标注三元组（口径与 `build_gold_triples` 一致，只是不混在一起）。"""
        reverse_mapping = {v: k for k, v in event_mapping.items()}
        buckets: Dict[str, Set] = {
            "event-place": set(),
            "event-person": set(),
            "event-organization": set(),
            "event-event": set(),
        }
        for category, rels in self.gold_relations.items():
            normalized_category = self.normalize_category(category)
            if normalized_category not in buckets:
                continue
            for rel in rels:
                head = rel.get('head', '').strip()
                tail = rel.get('tail', '').strip()
                relation = self.normalize_relation(rel.get('relation', ''))
                if not head or not relation or not tail or head not in reverse_mapping:
                    continue
                mapped_head = reverse_mapping[head]
                if normalized_category == 'event-event':
                    if tail in reverse_mapping:
                        buckets['event-event'].add((mapped_head, relation, reverse_mapping[tail]))
                else:
                    buckets[normalized_category].add((mapped_head, relation, tail))
        return buckets

    def evaluate_event_field_accuracy(self, pred_events: List[Dict], event_mapping: Dict) -> Dict:
        """
        事件字段的**值准确率**（相对填充率）：在已配对的事件上，逐字段与标注比对填得对不对。

        **为什么要从"填充率"升级到"值准确率"。** `evaluate_event_fields` 只看"填没填"，
        字段填错既不出现在那里、也不出现在事件 F1 里（事件 F1 只比名称）。
        实测 `Casualties` 报告 99.6% 填充，但实质取值只有 37.0%——一个指标同时漏掉了
        "填了但错"和"把不详当已填"两件事。

        比对口径（每个字段单独报 comparable/correct/accuracy，"两侧都有可比值"才算得进 accuracy）：

        - `StartDate`：两侧都能解析年份 → 年份相等或相差 ≤ 1 年算对；
        - `DynastyName`：归一后相等；
        - `Place`：首个有效地点互相包含即算对；
        - `Aggressor`/`Defender`：多值集合有交集即算对；
        - `Person`（标注）对 `Commanders`+`KeyPersons`（产物）：集合有交集即算对
          ——这两组的字段名历史上不一致（标注用 `Person`，产物用 `KeyPersons`），
          评估器只能按语义对齐，这也正是标注 README 里记着的遗留差异；
        - `Result`：归一后相等，或胜负方向词（胜/败/和/降/退）一致。

        **不比对** `TroopSize`/`Casualties` 这类数值字段的"准不准"：标注里的对应字段
        （`Scale`）覆盖率太低，比出来的数字没有解释力；它们仍由填充率与实质取值率监控。
        """
        gold_by_name = {
            (event.get('EventName') or '').strip(): event
            for event in self.gold_events.get('events', [])
            if (event.get('EventName') or '').strip()
        }
        pred_by_name = {}
        for event in pred_events:
            name = (event.get('EventName') or '').strip()
            if name:
                pred_by_name[name] = event

        result_direction_words = ("胜", "败", "和", "降", "退", "克", "陷")

        def values(value) -> set:
            return {item.strip() for item in split_multi_value(value) if item and item.strip()}

        def result_direction(value) -> str:
            text = (value or "").strip()
            for word in result_direction_words:
                if word in text:
                    return word
            return ""

        def compare(field: str, pred_event: Dict, gold_event: Dict) -> Tuple[bool, bool]:
            """返回 (是否可比, 是否算对)。"""
            if field == 'StartDate':
                pred_year = parse_year_for_order(pred_event.get('StartDate'))
                gold_year = parse_year_for_order(gold_event.get('StartDate'))
                if pred_year is None or gold_year is None:
                    return False, False
                return True, abs(pred_year - gold_year) <= 1
            if field == 'DynastyName':
                pred_value = self._normalized_dynasty(pred_event.get('DynastyName'))
                gold_value = self._normalized_dynasty(gold_event.get('DynastyName'))
                if not pred_value or not gold_value:
                    return False, False
                return True, pred_value == gold_value
            if field == 'Place':
                # 多值字段按**集合有交集**判定，与 Aggressor/Defender/Person 同一口径：
                # 原先只比"首个有效地点"，而两侧列出的地点个数与顺序都不同——
                # `宁锦之战` 两侧是同一对集合、只是顺序相反，按首个比就判成"不同"。
                pred_places = self._place_set(pred_event.get('Place'))
                gold_places = self._place_set(gold_event.get('Place'))
                if not pred_places or not gold_places:
                    return False, False
                if pred_places & gold_places:
                    return True, True
                for pred_place in pred_places:
                    for gold_place in gold_places:
                        if pred_place in gold_place or gold_place in pred_place:
                            return True, True
                return True, False
            if field in ('Aggressor', 'Defender'):
                pred_names = values(pred_event.get(field))
                gold_names = values(gold_event.get(field))
                if not pred_names or not gold_names:
                    return False, False
                return True, bool(pred_names & gold_names)
            if field == 'Person':
                pred_names = values(pred_event.get('Commanders')) | values(pred_event.get('KeyPersons'))
                gold_names = values(gold_event.get('Person')) | values(gold_event.get('KeyPersons'))
                if not pred_names or not gold_names:
                    return False, False
                return True, bool(pred_names & gold_names)
            if field == 'Result':
                pred_text = (pred_event.get('Result') or '').strip()
                gold_text = (gold_event.get('Result') or '').strip()
                if not pred_text or not gold_text:
                    return False, False
                if pred_text == gold_text:
                    return True, True
                pred_word = result_direction(pred_text)
                gold_word = result_direction(gold_text)
                if pred_word and gold_word:
                    return True, pred_word == gold_word
                return True, False
            raise ValueError(f"未定义的比对字段: {field}")

        fields = ['StartDate', 'DynastyName', 'Place', 'Aggressor', 'Defender', 'Person', 'Result']
        counters = {field: {'comparable': 0, 'correct': 0} for field in fields}
        matched_pairs = 0

        for pred_name, gold_name in sorted(event_mapping.items()):
            pred_event = pred_by_name.get(pred_name)
            gold_event = gold_by_name.get(gold_name)
            if not pred_event or not gold_event:
                continue
            matched_pairs += 1
            for field in fields:
                comparable, correct = compare(field, pred_event, gold_event)
                if comparable:
                    counters[field]['comparable'] += 1
                if correct:
                    counters[field]['correct'] += 1

        report = {}
        for field in fields:
            comparable = counters[field]['comparable']
            correct = counters[field]['correct']
            report[field] = {
                'comparable': comparable,
                'correct': correct,
                'accuracy': correct / comparable if comparable else 0.0,
                'coverage': comparable / matched_pairs if matched_pairs else 0.0,
            }

        print(f"\n【事件字段值准确率】（{matched_pairs} 对已配对事件；accuracy 只在两侧都有可比值时计算）")
        for field in fields:
            item = report[field]
            print(f"  {field}: {item['correct']}/{item['comparable']} = {item['accuracy']:.1%}"
                  f"（可比覆盖率 {item['coverage']:.1%}）")

        return {'matched_pairs': matched_pairs, 'fields': report}

    def evaluate_event_fields(self, pred_events: List[Dict]) -> Dict:
        """
        统计事件各字段的填充率，并**同时**给出"实质取值率"。

        `field_fill_rates`（旧口径）只判断"非空且不是字符串 `null`"，于是把"不详"算成已填：
        实测 `Casualties` 报 99.6%，而实质取值只有 37.0%（657 个事件如实写了"不详"）。
        `field_effective_rates` 用 `Normalizer.is_placeholder_value` 判定——
        占位词（"不详/未知/无/null/…"）一律算**没有值**。

        **为什么必须这样改。** 模型在原文没有数字时如实写"不详"是**正确行为**，不能被当成
        "没填"而扣分；反过来，把它算成"已填"会让报告里的 99.6% 成为假象。
        发布过滤（`main.py` 的弱结果词表）与 `is_placeholder_value` 早就是"不详 = 没有值"
        这个口径，只有这一个指标例外——口径必须统一到"不详 = 未填"。
        推论（不能违反的原则）：不要要求"所有属性都不能留空"，那会把如实的"不详"变成编造的数字。

        统计字段：Allies, Commanders, KeyPersons, TroopSize, Duration,
        GeographicScope, Casualties, relations。
        """
        if not pred_events:
            return {}

        total = len(pred_events)

        def is_filled(value) -> bool:
            return bool(value) and value != 'null'

        def is_effective(value) -> bool:
            # 多值字段**逐项**判定。原实现把整串交给 `is_placeholder_value`，而那是个
            # 精确匹配、切不开多值：`"李广、不详"` 整串既不是 `不详` 也不是空，于是被判成
            # "有实质取值"——与"不详 = 没有值"这条口径不符；反过来只看整串也会把
            # `"不详、李广"` 误判。拆开逐项判，只要有一项是实质取值，这一格才算有值。
            if not is_filled(value):
                return False
            if not isinstance(value, str):
                return True
            items = split_multi_value(value)
            if not items:
                return False
            return any(not self.normalizer.is_placeholder_value(item) for item in items)

        fields = ['Allies', 'Commanders', 'KeyPersons', 'TroopSize', 'Duration',
                  'GeographicScope', 'Casualties']
        filled = {field: sum(1 for e in pred_events if is_filled(e.get(field))) for field in fields}
        effective = {field: sum(1 for e in pred_events if is_effective(e.get(field))) for field in fields}
        # relations 是列表，判"有没有关系"而不是"值是不是占位词"
        filled['relations'] = sum(1 for e in pred_events if e.get('relations') and len(e.get('relations', [])) > 0)
        effective['relations'] = filled['relations']

        print(f"\n【事件字段填充率 / 实质取值率】（共{total}个事件）")
        for field in list(fields) + ['relations']:
            fill_rate = filled[field] / total if total > 0 else 0
            effective_rate = effective[field] / total if total > 0 else 0
            print(f"  {field}: 填充 {filled[field]}/{total} ({fill_rate:.1%})，"
                  f"实质取值 {effective[field]}/{total} ({effective_rate:.1%})")

        def rate_of(counts) -> Dict[str, float]:
            return {k: v / total if total > 0 else 0 for k, v in counts.items()}

        def avg_of(counts) -> float:
            return sum(counts.values()) / (len(counts) * total) if total > 0 else 0

        return {
            'total_events': total,
            # 旧键保留（口径 = 非空即算已填），便于与历史报告逐项对照
            'field_fill_rates': rate_of(filled),
            'avg_fill_rate': avg_of(filled),
            # 新口径：占位词也算没有值——这才是"模型有没有真的给出这个要素"
            'field_effective_rates': rate_of(effective),
            'avg_effective_rate': avg_of(effective),
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

        # 事件字段填充率（不进 F1，只报"填没填"）与**值准确率**（填得对不对）分开报：
        # 填充率会把"不详"算成已填，值准确率才是"这个字段能不能直接展示"的依据。
        field_metrics = self.evaluate_event_fields(all_pred_events)
        field_accuracy = self.evaluate_event_field_accuracy(all_pred_events, event_mapping)

        # 事件按朝代分组：只看宏观 F1 无法判断"某一朝代整体抽得差"（上古/先秦的实体
        # 与现代差异最大，往往是最差的一档）。
        by_dynasty = self.evaluate_events_by_dynasty(all_pred_events, event_mapping)

        macro_f1 = (entity_metrics['f1'] + event_metrics['f1'] + relation_metrics['f1']) / 3

        results = {
            'entity_extraction': entity_metrics,
            'event_extraction': {
                'full_events': event_metrics,
                'field_metrics': field_metrics,        # 填充率 / 实质取值率
                'field_accuracy': field_accuracy,      # 值准确率（与标注逐字段比对）
                'by_dynasty': by_dynasty,              # 按朝代分组的事件指标
                'mapping_stats': self.last_mapping_stats,
            },
            'relation_extraction': relation_metrics,
            'summary': {
                'entity_f1': entity_metrics['f1'],
                'entity_f1_canonical': entity_metrics['canonical']['f1'],
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
