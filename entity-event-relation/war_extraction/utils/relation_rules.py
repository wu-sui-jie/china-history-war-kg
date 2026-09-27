"""
事件-事件关系仲裁。

抽取时（`relation_extractor`）与最终清理时（`main.cleanup_relation_conflicts`）都要仲裁，
规则只能有这一份——两处各一份会让"抽出来的关系"和"清理后的关系"对不上。

本模块管三件事：

1. **类型仲裁**：模型说"因果关系"、证据却只有顺承词时降级（`arbitrate_event_event_relation`）。
2. **方向判定**：`顺承/因果`是有方向的。原实现按**事件名字典序**固定方向
   （`EventName_A > EventName_B` 就交换两端），等于让汉字排序决定"谁先谁后"——
   这是数据正确性缺陷，不只是评估问题。现在改为按**证据出现顺序 → 事件起始年份**判定，
   两者都判不出来时**不合并**反向的两条。
3. **时间索引**：`build_event_start_years` 把事件表的起始年份整理成方向判定要用的索引。
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from war_extraction.utils.value_parsing import parse_year_for_order
from war_extraction.utils.vocabulary import relation_type_allowed

__all__ = [
    "SEQUENTIAL_KEYWORDS",
    "STRONG_CAUSAL_KEYWORDS",
    "PARALLEL_KEYWORDS",
    "DIRECTIONAL_RELATION_TYPES",
    "DEDUPE_BY_SYMMETRIC_PAIR",
    "TYPE_PRIORITY",
    "arbitrate_event_event_relation",
    "evidence_order",
    "resolve_event_event_direction",
    "build_event_start_years",
    "reduce_event_event_relations",
]

#: 顺承词。`此后`/`过后`严格说也是顺承词，原表漏了它们——而 `arbitrate` 现在只在
#: **确实命中顺承词**时才把"因果"降级为"顺承"，漏词会让本该降级的落到"并列"，
#: 所以这里补齐常见写法（这是关键词表，不是判定逻辑）。
SEQUENTIAL_KEYWORDS = (
    "之后", "此后", "然后", "过后", "以后", "随后", "其后",
    "后来", "次年", "继而", "接着", "遂",
)
STRONG_CAUSAL_KEYWORDS = ("因此", "于是", "导致", "引发", "致使", "使得", "造成", "因而", "从而", "迫使")
PARALLEL_KEYWORDS = ("同时", "并", "并且", "同年", "相继", "并发")

#: 带方向的关系类型：这两类的 A→B 语义不对称，"顺序"本身是信息。
DIRECTIONAL_RELATION_TYPES = frozenset({"顺承关系", "因果关系"})

#: 非方向类型里"**同一对事件的同一类型只应有一条**"的取值：按**对称对**去重，取证据最长的。
#:
#: 为什么把包含/条件从"其余"里拿出来：它们原先与枚举外类型同组，去重键里带证据——
#: 于是同一对事件被抽到两次时（证据分别是各自文本分段里的原文，**短的那条常常是长的
#: 那条的前缀**）会留下两条同向同类型的边。实测 published 里有 11 组这样的重复，
#: 全是包含关系；导入后表现为"库内 11 组冗余行 + 图比库少 11 条边"（图按同两端同类型合并）。
#: 并列关系一直就是这个口径（无向、按对称对去重）。
#:
#: 枚举外的类型仍走「对称对 + 证据」——它们的证据差异**可能代表两条不同的关系**，
#: 在类型被人判定之前不该擅自合并。
DEDUPE_BY_SYMMETRIC_PAIR = frozenset({"并列关系", "包含关系", "条件关系"})

#: 同一对事件出现多种类型时的取舍优先级：因果的信息量最大，顺承次之，并列最弱。
TYPE_PRIORITY = {"因果关系": 3, "顺承关系": 2, "并列关系": 1}


def arbitrate_event_event_relation(normalizer, rel):
    """
    按证据文本里的关键词，修正事件-事件关系的类型（就地改 ``rel.relation`` 并返回同一个对象）。

    规则：
      - 说是"因果关系"、证据里没有强因果词 → 有顺承词才是"顺承关系"，否则"并列关系"；
      - 说是"顺承关系"、证据里有并列词且没有顺承词 → 改判"并列关系"。

    **修掉的那一处**：原条件是 ``has_sequential_keyword or evidence``，而 `evidence`
    几乎恒成立（派生关系的证据直接取事件 `source_text`），于是"因果 → 顺承"变成了无条件降级。
    实测后果是类型分布严重偏斜：产物 `顺承关系 798 / 因果 40`，而标注是 `因果 80 / 顺承 59`。
    现在只有证据里**确实出现顺承词**才降级为顺承。
    """
    evidence = getattr(rel, "evidence", None) or ""
    relation = normalizer.normalize_relation(getattr(rel, "relation", None))

    has_sequential_keyword = any(token in evidence for token in SEQUENTIAL_KEYWORDS)
    has_strong_causal_keyword = any(token in evidence for token in STRONG_CAUSAL_KEYWORDS)
    has_parallel_keyword = any(token in evidence for token in PARALLEL_KEYWORDS)

    if relation == "因果关系" and not has_strong_causal_keyword:
        relation = "顺承关系" if has_sequential_keyword else "并列关系"
    elif relation == "顺承关系" and has_parallel_keyword and not has_sequential_keyword:
        relation = "并列关系"

    rel.relation = relation
    return rel


def evidence_order(evidence: str, name_a: str, name_b: str) -> Optional[str]:
    """
    按证据文本里两个事件名的出现顺序判断先后。

    Returns:
        ``"a_first"`` / ``"b_first"``；两个名字没同时出现（或位置相同）时返回 None。
    """
    if not evidence or not name_a or not name_b:
        return None
    idx_a = evidence.find(name_a)
    idx_b = evidence.find(name_b)
    if idx_a < 0 or idx_b < 0 or idx_a == idx_b:
        return None
    return "a_first" if idx_a < idx_b else "b_first"


def resolve_event_event_direction(normalizer, rel, event_start_years: Optional[Dict[str, int]] = None) -> bool:
    """
    判定并就地修正方向：把**更早的那个事件**放到 ``EventName_A``。

    判定顺序（先到先用）：
      1. 证据文本里两个事件名的出现顺序（原文怎么写，谁就先）；
      2. 两个事件的起始年份（`event_start_years` 里能查到且不等时，早者为 A）；
      3. 都判不出来 → 保持原样并返回 False。

    **为什么不再用字典序兜底。** 字典序与史实无关：它会让"泾原兵变"排在"奉天之围"前面
    仅仅因为"泾"的编码更大。原实现就是这么定方向的，而 `main.py` 只在证据同时含两个完整
    事件名时才换回来（实测 914 条事件-事件关系里只有 23 条满足），所以错误方向保留到了产物里。
    返回 False 时由调用方决定"不合并反向的两条"，而不是猜一个方向。
    """
    name_a = getattr(rel, "EventName_A", None) or ""
    name_b = getattr(rel, "EventName_B", None) or ""
    if not name_a or not name_b or name_a == name_b:
        return False

    order = evidence_order(getattr(rel, "evidence", None) or "", name_a, name_b)
    if order == "b_first":
        rel.EventName_A, rel.EventName_B = name_b, name_a
        return True
    if order == "a_first":
        return True

    if event_start_years:
        year_a = event_start_years.get(normalizer.normalize_event_name(name_a))
        year_b = event_start_years.get(normalizer.normalize_event_name(name_b))
        if year_a is not None and year_b is not None and year_a != year_b:
            if year_b < year_a:
                rel.EventName_A, rel.EventName_B = name_b, name_a
            return True
    return False


def build_event_start_years(normalizer, events) -> Dict[str, int]:
    """
    事件起始年份索引：``归一事件名 → 起始年份``（解析不出来的不入索引）。

    同名事件有多条时取**最早**的那一年：方向判定只是用来定先后，取最早不会把
    "同一场战争的后续阶段"判成更早。
    """
    years: Dict[str, int] = {}
    for event in events or []:
        name = normalizer.normalize_event_name(getattr(event, "EventName", None))
        if not name:
            continue
        year = parse_year_for_order(getattr(event, "StartDate", None))
        if year is None:
            continue
        if name not in years or year < years[name]:
            years[name] = year
    return years


def reduce_event_event_relations(
    normalizer,
    relations: List,
    event_start_years: Optional[Dict[str, int]] = None,
    name_allowed: Optional[Callable[[str], bool]] = None,
    quarantine: Optional[List] = None,
) -> List:
    """
    事件-事件关系的统一收敛：规范化 → 类型仲裁 → 方向判定 → 去重。**规则只有这一份。**

    抽取器（`relation_extractor`）、段间合并（`result_merger`）、最终清理
    （`main.cleanup_relation_conflicts`）三处原先各写一套，而三套的差异正好落在
    最要命的地方——方向怎么定、反向的两条要不要合并。收成一份之后，
    "抽出来的关系"与"清理后的关系"不可能再对不上。

    收敛规则：
      - 丢掉自环、空名、`E1` 这类事件 ID 占位名；
      - `name_allowed` 给出时，两端都必须通过（用于"事件名必须落在最终事件名单里"）；
      - 类型经仲裁后，**带方向的关系**按证据/时间定方向：判得出方向时同一对只留优先级最高的一条；
        **判不出方向时，A→B 与 B→A 都保留**（不猜、也不按字典序硬定一个方向）；
      - 并列关系是无向的，按对称对去重；其余非规范类型按（对称对 + 证据）去重。

    Args:
        normalizer: `Normalizer` 实例
        relations: `EventEventRelation` 列表（可含未标准化的名称）
        event_start_years: `build_event_start_years` 的结果，用于按时间定方向
        name_allowed: 事件名过滤谓词（入参是归一后的事件名）
        quarantine: 传入 list 时，把**关系类型不在枚举内**的条目也收进去（schema 校验层用）。
            注意这些条目**仍然出现在返回值里**——它们在产物中保留、由发布拆分阶段路由进候选区；
            `quarantine` 只提供一条"这批有多少条"的计数通道，不是"丢掉"的开关。
            没有这条通道时，枚举外的关系会与"自环/空名/事件 ID 占位名"混在一起，
            从计数上分不清"数据脏"还是"枚举该扩"。

    **计数口径：只在最后一道收敛（`main.cleanup_relation_conflicts`）计数。**
    四个调用点里只有它传 `quarantine`；抽取器（`relation_extractor`，两处）与段间合并
    （`result_merger`）**刻意不传**——同一条枚举外关系会先后出现在抽取器输出与合并输出里，
    中间环节再数一遍就是重复计数（`main` 那道是第三遍），总数会明显偏大。
    这不影响数据：不传 `quarantine` 只是"不数"，条目照旧留在返回值里（见上）。
    要看总数就看质量报告的 `event_event_relations_outside_enum`，它取的是最后一道的数。
    **看起来该传却没传，是因为这里数的是"最终产物里有多少条"，不是"每个环节各产出了多少条"。**

    Returns:
        收敛后的关系列表（含枚举外类型的条目）
    """
    grouped: Dict[tuple, List] = {}
    for rel in relations or []:
        rel.EventName_A = normalizer.standardize_event_name(getattr(rel, "EventName_A", None))
        rel.EventName_B = normalizer.standardize_event_name(getattr(rel, "EventName_B", None))
        rel.relation = normalizer.normalize_relation(getattr(rel, "relation", None))

        # `E1`/`E12` 这类是识别阶段的事件 ID，不是事件名：留着会让产物里出现悬空边
        if _looks_like_event_id(rel.EventName_A) or _looks_like_event_id(rel.EventName_B):
            continue

        name_a = normalizer.normalize_event_name(rel.EventName_A)
        name_b = normalizer.normalize_event_name(rel.EventName_B)
        if not name_a or not name_b or name_a == name_b:
            continue
        if name_allowed and not (name_allowed(name_a) and name_allowed(name_b)):
            continue
        if not rel.relation:
            continue
        if (rel.relation not in normalizer.CANONICAL_EVENT_RELATION_TYPES
                and not relation_type_allowed(rel.relation, "event-event")):
            # 枚举外类型：**保留**在产物里，交给发布拆分阶段路由进候选区
            # （`main.split_publishable_outputs` 的 `_relation_enum_ok(rel, "event-event")`），
            # 同时由质量报告的 `enum_out_values.event_event_relations` 与体检脚本计数。
            #
            # 这里原先直接 `continue`（丢掉），后果是"枚举外的事件-事件关系**无痕消失**"：
            # 既不在 raw 产物里、也不在候选区里，`moved_to_candidate_by_enum` 也看不到它们。
            # 与另外三类关系（事件-地点/组织/人物）的口径也不一致——那三类都是
            # "raw 保留 + 发布拆分时进候选区"。现在四类统一。
            #
            # 依据：阶段 3 的验收写的是"产物里不再出现枚举外的关系类型**（或它们按 candidate
            # 区规则安置）**"——保留在 raw 并进候选区就是后半句的那种安置方式。
            if quarantine is not None:
                quarantine.append(rel)

        rel = arbitrate_event_event_relation(normalizer, rel)
        grouped.setdefault(frozenset((name_a, name_b)), []).append(rel)

    reduced: List = []
    # 遍历顺序不必额外定序：`grouped` 是 **dict**，Python 3.7+ 的 dict 保插入序，
    # 而插入序就是下面 for 循环喂进来的顺序（= 抽取顺序，确定性）。frozenset 只是**键**，
    # 它的哈希只影响桶位置、不影响遍历顺序——别被"键里带 frozenset"误导去加排序
    # （2026-09-27 我就在这里误判过一次：以为它导致产物不可复现，实际那两次的差异来自
    # metadata 里的时间戳，见 `项目审查与修复历史.md` §26）。
    for entries in grouped.values():
        directional = [rel for rel in entries if rel.relation in DIRECTIONAL_RELATION_TYPES]
        others = [rel for rel in entries if rel.relation not in DIRECTIONAL_RELATION_TYPES]

        if directional:
            resolved_pairs = []
            unresolved = []
            for rel in directional:
                # 判定会就地改方向，所以先复制出方向键再比较
                rel = _copy_relation(rel)
                if resolve_event_event_direction(normalizer, rel, event_start_years):
                    resolved_pairs.append(rel)
                else:
                    unresolved.append(rel)
            if resolved_pairs:
                # 方向判得出来：这一对只留一条，取优先级最高（同优先级取证据更长）的
                # 平局取"先遇到的"——**上面那处定序遍历保证了这个"先"与进程哈希无关**，
                # 所以不必再加别的平局判据（加了反而会改掉既有语义，有用例钉着"取前一条"）。
                best = max(
                    resolved_pairs,
                    key=lambda r: (TYPE_PRIORITY.get(r.relation, 0), len(r.evidence or "")),
                )
                reduced.append(best)
                continue
            # 方向判不出来：同方向去重后**全部保留**，A→B 与 B→A 不合并
            by_direction = {}
            for rel in unresolved:
                key = (rel.EventName_A, rel.EventName_B, rel.relation)
                kept = by_direction.get(key)
                if kept is None or len(rel.evidence or "") > len(kept.evidence or ""):
                    by_direction[key] = rel
            reduced.extend(by_direction.values())
            continue

        # 非方向类型：并列 / 包含 / 条件按**对称对**去重（同一对事件的同一类型只留一条，
        # 取证据最长的）；其余（含枚举外类型）按（对称对 + 证据）去重
        by_key = {}
        for rel in others:
            if rel.relation in DEDUPE_BY_SYMMETRIC_PAIR:
                key = (rel.relation,)
            else:
                key = (rel.relation, rel.evidence or "")
            kept = by_key.get(key)
            if kept is None or len(rel.evidence or "") > len(kept.evidence or ""):
                by_key[key] = rel
        reduced.extend(by_key.values())

    # 输出定序：同一份输入换进程也要得到同一份结果（评估器对顺序敏感）
    reduced.sort(key=lambda r: (r.EventName_A, r.relation, r.EventName_B, r.evidence or ""))
    return reduced


def _looks_like_event_id(name: str) -> bool:
    text = (name or "").strip()
    return len(text) > 1 and text[0] == "E" and text[1:].isdigit()


def _copy_relation(rel):
    """浅拷贝同类型对象：方向判定会就地改字段，不能改到输入上的对象。"""
    return type(rel)(**rel.model_dump())
