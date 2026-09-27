#!/usr/bin/env python
"""
给抽样核验表补**原文上下文**：把"模型给的证据"换成"原文里那一段（含前后段）"。

**为什么需要它。** `sample_for_review.py` 导出的表里，`原文上下文` 一列装的其实是
**模型自己写的证据**（事件的 `source_text` / 关系的 `evidence`），截断到 400 字。
用它判对错有两个坑：

1. **证据可能是模型改写的句子**，原文里根本没有（实测 `event_event_relations` 的
   evidence 大量是"…随后发生…"这类总结句），而"这条证据本身可不可信"正是要判的东西之一；
2. **判时间时证据不够**：事件的时间常写在**前一段**。实测 `崤底之战` 的证据段通篇没有年份，
   而上一段写着"公元27年，邓禹率领车将军邓弘等由河西回到湖县"——只按证据段判，
   会把产物写对的 `27年` 判成"原文没写"。

所以本脚本不替换原表，而是**另出一份**：上下文换成清洗后原文里定位到的段落（前 700 字、
后 300 字，按换行对齐），产物证据原样保留在独立列里，另给"定位方式"与"原文出处"。

**定位键一列逐行照抄冻结样本**，所以判定结果仍能映射回 `sample_<seed>.json` 那批基准定位
（跨版本对比靠的就是它）。

**四级标注**（每级都记在 `定位方式` 列里，供人核对；每次运行都会打印这一档的分布）：

1. `记录证据`：证据文本（去空白后）分四处（开头、1/5、2/5、3/5）各取一段当样例，
   先长（40 字）后短（16 字）地往原文里试，并用"命中点附近落着证据的几段"（覆盖率）
   判断这条证据是不是真来自这里。覆盖率不足 2 段时降级成下一档，而不是当成功。
2. `记录证据（部分）`：证据是**多段原文拼起来的**（模型写了过渡句，或证据自己重复了一遍），
   上下文只覆盖其中一段。给出上下文但**明确标注要人自己补看**。
3. `所属事件证据`：关系行的证据定位不到时（模型改写了），改用**该事件的 `source_text`**。
4. `名称定位`：还不行就按端名 / 事件名找，并标出该名在原文出现几次；出现多次时取首次出现，
   人必须自己核对是否找对了地方。

四级都命不中时**不编造**：上下文列留空，`定位方式` 写明"未能定位"，
`产物证据` 列里原有的证据照旧保留，人按事件名自己回原文查。
（当前产物上这一档是 0 行，但保留它——定位失败时给一段错的原文比留空更糟。）

用法：

    python tools/expand_review_context.py                    # 默认读 evaluation/review/sample_20260926.json
    python tools/expand_review_context.py --sample <别的样本.json> --output <csv 路径>

产物：CSV（utf-8-sig，Excel 直接打开）+ 同名 JSON（含来源指纹与定位统计，供复核）。
**不修改** `sample_<seed>.csv` / `.json` 那两份冻结基准。
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from war_extraction.core.text_cleaner import clean_text_with_mapping, load_cleaning_rules  # noqa: E402
from war_extraction.utils.provenance import file_sha256  # noqa: E402

MODULE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SAMPLE = MODULE_ROOT / "evaluation" / "review" / "sample_20260926.json"
DEFAULT_BOOK = MODULE_ROOT / "data" / "中国历代战争简史.txt"
DEFAULT_PRED = MODULE_ROOT / "output" / "中国历代战争简史" / "9_final_all.json"

#: 核验表的列。前四列是被判的对象与依据，中间两列是这次新增的定位信息，
#: 最后三列留给人填（与冻疆样本同名同义，便于把判定结果映射回去）。
REVIEW_COLUMNS = [
    "分层", "定位键", "记录摘要", "原文上下文", "产物证据", "定位方式", "原文出处",
    "判定（对/错/无法判断）", "判据", "备注",
]

#: 证据在原文里的对应位置用这两个括号标出（原文里不出现这两个字符，实测过）。
_EVIDENCE_OPEN = "〈"
_EVIDENCE_CLOSE = "〉"

#: 上下文窗口：定位点前 900 字、后 200 字（按换行边界对齐），整段超过 1600 字就从前端裁。
#:
#: 窗口大小是量出来的，不是拍的：判事件时最需要的是**时间**，而时间常写在证据段的前面。
#: 实测 330 个定位成功的行，从证据起点往回找最近年份的距离中位数 **246 字**、p80 **589 字**；
#: 按"前 900 字"取窗口，71 个事件行里有 **69** 个能在窗口里看到年份（前 600 字只有 65 个）。
#: 再宽（1200 字）不增加覆盖、只是更长的墙——所以停在 900。
_BEFORE = 900
_AFTER = 200
_MAX_WINDOW = 1600

#: 证据前缀逐级退让的长度（去空白后计）。
_PREFIX_LENGTHS = (40, 30, 24, 16)


def collapse(text: str):
    """
    去掉全部空白并记录"折叠后下标 → 原下标"的映射。

    为什么要折叠：原文按 30 来字硬折行，而产物里的证据是被清洗过的连续文本，
    字符级查找会跨不过换行。折叠空白后两边才可比；映射用来把命中的位置换回真实坐标。
    """
    kept = []
    index = []
    for position, char in enumerate(text):
        if char.isspace():
            continue
        kept.append(char)
        index.append(position)
    return "".join(kept), index


def _sample_chunks(collapsed: str, length: int = 24) -> list:
    """把证据文本分四处（开头、1/5、2/5、3/5）各切一段，用来数覆盖率。"""
    starts = [0, len(collapsed) // 5, 2 * len(collapsed) // 5, 3 * len(collapsed) // 5]
    chunks = []
    for start in dict.fromkeys(starts):
        chunk = collapsed[start:start + length]
        if len(chunk) >= 12:
            chunks.append(chunk)
    return chunks


def _coverage(haystack: str, position: int, length: int, chunks: list) -> int:
    """
    命中位置附近落着证据的几段（0~4）。用来区分"整段来自这里"与"只有一句撞上了"。

    **窗口边界必须与最终给出的上下文一致**（前 `_BEFORE`、后 `_AFTER`）：覆盖率是给
    `定位方式` 打标签用的，如果它比上下文窗口宽，就会出现"标着覆盖率 2、人却在上下文里
    找不到那一段"——标签说的和给人的东西对不上。
    """
    window = haystack[max(0, position - _BEFORE):position + length + _AFTER]
    return sum(1 for chunk in chunks if chunk in window)


def locate_prefix(haystack: str, text: str, lengths=_PREFIX_LENGTHS):
    """
    在原文里找这段文本的位置；返回 ((起, 止), 覆盖率)，找不到返回 (None, 0)。

    **为什么要从"只比开头"改成"分几处抽样例比"**：模型写证据时常常**删掉括号里的内容**
    （实测 `辽西渔阳之战` 的证据是"袭掠辽西，杀太守"，原文是"袭掠辽西（治所辽宁义县西），杀太守"），
    只比开头就会在第 13 个字上断掉，整行被误判成"未能定位"。所以把这段文本分四处
    （开头、1/5、2/5、3/5）各取一段当样例，先长后短地试。

    **覆盖率是防误判的核心**：样例必须唯一（短样例容易在原文里撞上别处），而且要数一数
    命中位置附近到底落着证据的几段——

    - 覆盖率 ≥ 2：证据确实来自这一段，标 `记录证据`；
    - 覆盖率 = 1：只有一句撞上了。实测 `刘裕北伐灭后秦` 的"证据"是**两段不相邻原文拼起来的**
      （中间还有模型自己加的过渡句），这类**不能**当成功——标成"部分命中"交给人工核对；
    - 覆盖率 = 0：当作没找到，退回下一级策略。

    不这么区分的话，"定位成功"就会把这类拼接句也盖进去，而人拿着一段只对上一句的原文
    去判对错，判出来的错会直接进精确率。
    """
    if not text:
        return None, 0
    collapsed = re.sub(r"\s+", "", text)
    chunks = _sample_chunks(collapsed)
    starts = list(dict.fromkeys([0] + [len(collapsed) * step // 5 for step in (1, 2, 3)]))
    best = (None, 0)
    for length in lengths:
        for start in starts:
            needle = collapsed[start:start + length]
            if len(needle) < 8:
                continue
            if haystack.count(needle) != 1:
                continue
            position = haystack.find(needle)
            coverage = _coverage(haystack, position, len(needle), chunks)
            if coverage > best[1]:
                best = ((position, position + len(needle)), coverage)
            if coverage >= 2:
                return best[0], best[1]
    return best[0], best[1]


def locate_name(haystack: str, name: str):
    """按名称找位置；返回 (命中区间或 None, 该名在原文出现的次数)。"""
    collapsed = re.sub(r"\s+", "", name or "")
    if len(collapsed) < 2:
        return None, 0
    count = haystack.count(collapsed)
    position = haystack.find(collapsed)
    if position < 0:
        return None, 0
    return (position, position + len(collapsed)), count


def _snap_window(cleaned: str, index_map, start: int, end: int):
    """把折叠坐标的命中区间换成"按换行对齐的清洗后区间"（前 `_BEFORE`、后 `_AFTER`）。"""
    cleaned_start = index_map[min(start, len(index_map) - 1)]
    cleaned_end = index_map[min(end, len(index_map) - 1)] + 1
    low = max(0, cleaned_start - _BEFORE)
    high = min(len(cleaned), cleaned_end + _AFTER)
    newline = cleaned.rfind("\n", 0, low)
    low = newline + 1 if newline >= 0 else 0
    newline = cleaned.find("\n", high)
    high = newline if newline >= 0 else len(cleaned)
    # 同名的段可能很长（一段上千字），这时从**前端**按段裁，保住紧邻证据的上文
    while high - low > _MAX_WINDOW:
        newline = cleaned.find("\n", low)
        if newline < 0 or newline >= cleaned_start:
            break
        low = newline + 1
    return low, high, cleaned_start, cleaned_end


def build_context(cleaned: str, index_map, mapping, hit) -> str:
    """把命中区间扩成"含前后段的原文"，并把证据那一段用 〈〉 标出来。"""
    low, high, evidence_start, evidence_end = _snap_window(cleaned, index_map, hit[0], hit[1])
    evidence_start = max(low, evidence_start)
    evidence_end = min(high, evidence_end)
    text = (cleaned[low:evidence_start] + _EVIDENCE_OPEN
            + cleaned[evidence_start:evidence_end] + _EVIDENCE_CLOSE
            + cleaned[evidence_end:high])
    # 行内换行在原文里是硬折行，显示出来只会干扰阅读；段落之间才保留换行
    return text.replace("\n", "")


def locate_record(record: dict, haystack: str, event_source: dict):
    """三级退让定位，返回 (命中区间或 None, 定位方式说明)。"""
    hit, coverage = locate_prefix(haystack, record.get("原文上下文") or "")
    if hit and coverage >= 2:
        return hit, "记录证据"
    if hit:
        partial = hit
    else:
        partial = None

    segments = (record.get("定位键") or "").split("|")
    layer = record.get("分层") or ""
    event_name = segments[1] if len(segments) > 1 else ""
    end_name = segments[3] if len(segments) > 3 else ""

    if layer.startswith("关系:"):
        hit, coverage = locate_prefix(haystack, event_source.get(event_name, ""))
        if hit and coverage >= 2:
            return hit, "所属事件证据"
        if partial:
            return partial, "记录证据（部分：证据是多段拼接，上下文只覆盖其中一段，请核对）"
        hit, count = locate_name(haystack, end_name)
        if hit:
            return hit, f"名称定位（端名「{end_name}」在原文出现 {count} 次，取首次出现，请核对）"
    else:
        if partial:
            return partial, "记录证据（部分：证据是多段拼接，上下文只覆盖其中一段，请核对）"
        hit, count = locate_name(haystack, event_name)
        if hit:
            return hit, f"名称定位（「{event_name}」在原文出现 {count} 次，取首次出现，请核对）"
    return None, "未能定位（证据是模型改写过的句子，请按事件名自行回原文查）"


def build_rows(records: list, book_path: Path, pred_path: Path) -> tuple:
    raw = book_path.read_text(encoding="utf-8")
    cleaned, cleaning_stats, mapping = clean_text_with_mapping(raw, load_cleaning_rules())
    haystack, index_map = collapse(cleaned)

    event_source = {}
    if pred_path.is_file():
        payload = json.loads(pred_path.read_text(encoding="utf-8"))
        block = payload.get("events") or {}
        events = block if isinstance(block, list) else (block.get("events") or [])
        event_source = {event.get("EventName"): (event.get("source_text") or "")
                        for event in events if event.get("EventName")}

    rows = []
    ways = Counter()
    for record in records:
        hit, way = locate_record(record, haystack, event_source)
        # 统计按**人类看到的那一档**归并：`记录证据（部分…）` 是独立一档，
        # 不能并进"记录证据"里——那样统计会把"只对上一句"的行说成"定位成功"
        if "部分" in way:
            ways["记录证据（部分）"] += 1
        elif way.startswith("名称定位"):
            ways["名称定位"] += 1
        else:
            ways[way.split("（")[0]] += 1
        if hit:
            context = build_context(cleaned, index_map, mapping, hit)
            origin = (f"原文 [{mapping.to_original(index_map[min(hit[0], len(index_map) - 1)])}, "
                      f"{mapping.to_original(index_map[min(hit[1] - 1, len(index_map) - 1)]) + 1}]")
            # 「名称定位」的提醒**只放在 `定位方式` 列**，不往上下文里插标记：
            # 上下文那一格要保持是纯原文，人才好直接读；提醒在紧邻的一列，看得出来。
        else:
            context = ""
            origin = "—"
        rows.append({
            "分层": record.get("分层", ""),
            "定位键": record.get("定位键", ""),
            "记录摘要": record.get("记录摘要", ""),
            "原文上下文": context,
            "产物证据": record.get("原文上下文", ""),
            "定位方式": way,
            "原文出处": origin,
            "判定（对/错/无法判断）": record.get("判定（对/错/无法判断）", ""),
            "判据": record.get("判据", ""),
            "备注": record.get("备注", ""),
        })

    provenance = {
        "book": str(book_path),
        "book_sha256": file_sha256(book_path),
        "cleaning_stats": cleaning_stats,
        "locate_stats": dict(ways.most_common()),
        "located": sum(1 for row in rows if row["原文上下文"]),
        "total": len(rows),
    }
    return rows, provenance


def main() -> int:
    parser = argparse.ArgumentParser(description="给抽样核验表补原文上下文（含前后段）")
    parser.add_argument("--sample", default=str(DEFAULT_SAMPLE), help="冻结的抽样 JSON")
    parser.add_argument("--book", default=str(DEFAULT_BOOK), help="原文（全书 txt）")
    parser.add_argument("--pred", default=str(DEFAULT_PRED), help="产物 JSON（取事件 source_text 用）")
    parser.add_argument("--output", default=None, help="CSV 输出路径")
    args = parser.parse_args()

    sample_path = Path(args.sample)
    book_path = Path(args.book)
    if not sample_path.is_file():
        print(f"抽样 JSON 不存在: {sample_path}")
        return 2
    if not book_path.is_file():
        print(f"原文不存在: {book_path}")
        return 2

    frozen = json.loads(sample_path.read_text(encoding="utf-8"))
    records = frozen.get("records", frozen if isinstance(frozen, list) else [])
    rows, provenance = build_rows(records, book_path, Path(args.pred))

    output = Path(args.output or sample_path.with_name(sample_path.stem + "_with_source.csv"))
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    json_path = output.with_suffix(".json")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump({
            "note": ("本表由 tools/expand_review_context.py 从冻结样本生成："
                     "`原文上下文` = 原文里定位到的段落（前后各扩一段，〈〉内是产物证据在原文里的起头），"
                     "`产物证据` = 模型自己写的证据（原表那一列的内容，一字未改），"
                     "`定位方式` 三级退让的哪一级，`原文出处` 是原文坐标。"
                     "`定位键` 逐行照抄冻结样本，所以判定结果可映射回 sample_<seed>.json。"
                     "判对错的方法见 docs/抽样判定规范.md。"),
            "source_sample": str(sample_path),
            "source_sample_sha256": file_sha256(sample_path),
            "provenance": provenance,
            "records": rows,
        }, handle, ensure_ascii=False, indent=2)

    print(f"共 {len(rows)} 行，定位成功 {provenance['located']} 行")
    for way, count in provenance["locate_stats"].items():
        print(f"  {way}: {count}")
    print(f"CSV: {output}")
    print(f"JSON: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
