#!/usr/bin/env python
"""
给参考集回填**原文证据与字符偏移**（原文坐标），并报出定位不上的条数。

**为什么需要它。** 旧参考集的最致命缺陷是**没有证据字段**（`data/annotations/README.md` 局限 7）：
事件的 `source` 只有书名、关系只有 `head/relation/tail`。于是"这条标注来自原文何处"无法回答，
改标注、复现指标、判"这条到底对不对"全都无从下手。这份工具把**候选锚点**先批量填出来，
人工只需要核验与修正，而不是从零翻原文。

**为什么坐标必须是原文坐标。** 清洗会改变文本长度（删不可见字符、并硬折行、订正错字），
所以"清洗后坐标"与"原文坐标"是两回事（指南 §1.16）。这里走的是与抽取链同一套映射
（`clean_text_with_mapping` → `collapse`），**不另写一套换算**——两套坐标必然会漂移。

**它只产候选，不产真值**：`locate_status` 三档（唯一命中 / 多处命中(取首次) / 未命中），
未命中的那些就是要人工补的行。报告按类别给出三档条数，C 组据此知道工作量在哪。

用法：

    python tools/backfill_annotation_evidence.py                        # 只报数
    python tools/backfill_annotation_evidence.py --output-dir /tmp/ann  # 另存带证据的副本
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1]
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from tools.annotation_io import GOLD_FILES  # noqa: E402
from tools.expand_review_context import collapse, locate_name, locate_prefix  # noqa: E402
from war_extraction.core.text_cleaner import clean_text_with_mapping, load_cleaning_rules  # noqa: E402
from war_extraction.extractors.relation_extractor import _SENTENCE_BOUNDARY  # noqa: E402
from war_extraction.utils.normalizer import Normalizer  # noqa: E402

DEFAULT_DIR = MODULE_ROOT / "data" / "annotations"
DEFAULT_BOOK = MODULE_ROOT / "data" / "中国历代战争简史.txt"
#: 关系定位时在头事件附近找目标的窗口（清洗后坐标）。太窄会漏（同一战事的叙述常跨几段），
#: 太宽会把别处的同名实体认进来——四百字大致是一段到两段。
_TAIL_WINDOW = 400


class Locator:
    """把"归一名"定位到**原文坐标**的定位器（与 `expand_review_context` 同一套折叠与映射）。"""

    def __init__(self, book_path: Path):
        raw = book_path.read_text(encoding="utf-8")
        self.cleaned, self.stats, self.mapping = clean_text_with_mapping(raw, load_cleaning_rules())
        self.haystack, self.index_map = collapse(self.cleaned)
        self.normalizer = Normalizer()

    def locate(self, name: str) -> dict:
        span, count = locate_name(self.haystack, self.normalizer.normalize_entity_name(name))
        if span is None:
            # 两种"找不到"要分清：文里没有这个串（多半是概括名，不是原文串），与名字太短（<2 字）。
            status = "未命中(文中无此串)" if len((name or "").strip()) >= 2 else "未命中(名称太短)"
            return {"status": status, "hits": 0, "evidence": "", "start": None, "end": None,
                    "cleaned_span": None}
        return self._from_span(span, count)

    def _from_span(self, span, count, note: str = "") -> dict:
        """命中区间 → 候选锚点（证据扩到所在句、坐标折回原文）。`note` 说明是哪条路找到的。"""
        cleaned_start, cleaned_end = self._to_cleaned(span)
        evidence, low, high = self._sentence(cleaned_start, cleaned_end)
        return {
            "status": ("唯一命中" if count == 1 else "多处命中(取首次)") + note,
            "hits": count,
            "evidence": evidence,
            "start": self.mapping.to_original(low),
            "end": self.mapping.to_original(high - 1) + 1,
            "cleaned_span": (low, high),
        }

    def locate_text(self, text: str):
        """
        按**一段文本**定位（覆盖率 ≥2 才算成功）——产物的 `source_text` / 关系的 `evidence`
        就是这种：它们是书里的原句，比"事件名"可靠得多（事件名多是模型合成的；
        实测旧 gold 的事件名只有 26% 是原文串）。定位器复用 `expand_review_context` 那一套
        （同样分四处抽样例、同样要求样例唯一），所以两处的"定位成功"含义一致。
        """
        span, coverage = locate_prefix(self.haystack, text or "")
        if span is None or coverage < 2:
            return None
        cleaned_start, cleaned_end = self._to_cleaned(span)
        evidence, low, high = self._sentence(cleaned_start, cleaned_end)
        return {
            "status": f"证据定位（覆盖率 {coverage}）",
            "hits": coverage,
            "evidence": evidence,
            "start": self.mapping.to_original(low),
            "end": self.mapping.to_original(high - 1) + 1,
            "cleaned_span": (low, high),
        }

    def locate_best(self, name: str, text: str = "") -> dict:
        """**证据优先、名称兜底**：有原句就用原句定位，没有才退到名称。"""
        found = self.locate_text(text)
        if found is not None:
            return found
        return self.locate(name)

    def locate_event(self, name: str, hint: str = "") -> dict:
        """
        事件用：**线索证据优先 → 事件名去后缀 → 事件名原样**。

        第三档为什么要"去后缀"：`normalize_event_name` 会把「攻破建康之战」压成「攻破建康」，
        而后者常常正是原文串（原文写「攻破建康（南京）」）——靠这一档，**人工补漏的事件**
        （草稿里没有、没有产物证据可借）也能锚到原文句。命中后 status 带「（事件名去后缀）」
        后缀，一眼能看出它是哪条路找来的。
        """
        found = self.locate_text(hint)
        if found is not None:
            return found
        span, count = locate_name(self.haystack, self.normalizer.normalize_event_name(name))
        if span is not None:
            return self._from_span(span, count, note="（事件名去后缀）")
        return self.locate(name)

    def locate_entity(self, name: str, hint: str = "") -> dict:
        """
        实体用：**名称优先、证据兜底**（与事件相反）。

        为什么反过来：实体的名字**本来就是原文串**（人物、地名、组织名多是书里写着的），
        按名称定位得到的证据句里**必然含这个名字**——这正是"这条实体在原文里成立"的直接证据。
        先用产物给的证据句反而可能落在不含该名字的句子上。
        名称定位不到时才退到证据句（多为人工改过名的行，如「叶赫联军」→「叶赫军」）。
        """
        found = self.locate(name)
        if found["status"].startswith("未命中") and hint:
            return self.locate_text(hint) or found
        return found

    def locate_near(self, name: str, cleaned_span) -> dict:
        """
        在锚点（**清洗后区间**）附近找目标名——关系用：目标实体应当出现在头事件那段叙述里。

        锚点由调用方给：**优先复用头事件已经定位好的区间**（`locate_event(...)["cleaned_span"]`），
        而不是拿头事件的名再定位一次——产物的事件名多是合成的，按名称定位会大片失败
        （实测：按名称时关系定位率 11~16%，复用事件坐标后大幅上升）。
        """
        normalized = self.normalizer.normalize_entity_name(name)
        if not normalized or not cleaned_span:
            return {"status": "未命中", "hits": 0, "evidence": "", "start": None, "end": None,
                    "cleaned_span": None}
        low = max(0, cleaned_span[0] - _TAIL_WINDOW)
        high = min(len(self.cleaned), cleaned_span[1] + _TAIL_WINDOW)
        window = self.cleaned[low:high]
        count = window.count(normalized)
        if not count:
            return {"status": "未命中", "hits": 0, "evidence": "", "start": None, "end": None,
                    "cleaned_span": None}
        offset = window.find(normalized)
        cleaned_start, cleaned_end = low + offset, low + offset + len(normalized)
        evidence, ev_low, ev_high = self._sentence(cleaned_start, cleaned_end)
        return {
            "status": "唯一命中" if count == 1 else "多处命中(取首次)",
            "hits": count,
            "evidence": evidence,
            "start": self.mapping.to_original(ev_low),
            "end": self.mapping.to_original(ev_high - 1) + 1,
            "cleaned_span": (ev_low, ev_high),
        }


    def _to_cleaned(self, collapsed_span):
        start, end = collapsed_span
        last = len(self.index_map) - 1
        return self.index_map[min(start, last)], self.index_map[min(end - 1, last)] + 1

    def _sentence(self, cleaned_start: int, cleaned_end: int):
        """把命中区间扩到**所在句**（句末标点/换行切分，与抽取器同一套边界）。"""
        bounded = [m.end() for m in _SENTENCE_BOUNDARY.finditer(self.cleaned[:cleaned_start])]
        low = bounded[-1] if bounded else 0
        next_match = _SENTENCE_BOUNDARY.search(self.cleaned, cleaned_end)
        high = next_match.end() if next_match else len(self.cleaned)
        return self.cleaned[low:high].strip(), low, high


def backfill(gold: dict, locator: Locator) -> tuple:
    """
    给三类记录各算一份候选锚点；返回 (结果, 统计)。

    **三层各用各的策略**（依据是"哪条线索在这层更可靠"，不是图省事）：

    - **事件**：`evidence_hint` 优先、事件名兜底。事件名多是模型合成的概括名，
      按名称定位实测只有约四分之一能命中；产物给的证据句是书里的原句，可靠得多。
    - **实体**：名称优先、`evidence_hint` 兜底。实体名本来就是原文串，按名称定位得到的
      证据句里必然含这个名字——这才叫"这条实体在原文里成立"。
    - **关系**：先定位**头事件**（同样证据优先），再在它前后一个窗口里找目标实体
      （`locate_near`）——关系的证据必须是"头尾同时出现的那段"；窗口里找不到时才退到
      该关系自己的 `evidence_hint`。
    """
    results = {"events": [], "entities": [], "relations": []}
    stats = Counter()

    #: 头事件名 → 它的证据线索。关系定位要借头事件的锚点，所以先备好这张表。
    event_hints = {str(row.get("EventName") or ""): (row.get("evidence_hint") or "")
                   for row in gold["events"]}

    for row in gold["events"]:
        found = locator.locate_event(row.get("EventName"), row.get("evidence_hint") or "")
        stats[f"events/{found['status']}"] += 1
        results["events"].append({"source": row, **found})

    for kind, key in (("places", "geo_name"), ("persons", "PersonName"), ("organizations", "OrgName")):
        for row in gold[kind]:
            found = locator.locate_entity(row.get(key), row.get("evidence_hint") or "")
            stats[f"{kind}/{found['status']}"] += 1
            results["entities"].append({"source": row, "kind": kind, **found})

    for label, rows in gold["relations"].items():
        for row in rows:
            head = str(row.get("head") or "")
            head_report = locator.locate_event(head, event_hints.get(head, ""))
            anchor = head_report.get("cleaned_span")
            if anchor is None:
                found = {"status": "未命中(头事件)", "hits": 0, "evidence": "",
                         "start": None, "end": None, "cleaned_span": None}
            else:
                found = locator.locate_near(row.get("tail"), anchor)
                if found["status"] != "未命中":
                    found["status"] = f"头事件{head_report['status']}/{found['status']}"
                elif row.get("evidence_hint"):
                    # 头事件段里找不到目标：退到该关系自己的证据句（仍是原文串，可审计）
                    fallback = locator.locate_text(row.get("evidence_hint"))
                    if fallback is not None:
                        fallback["status"] = f"证据定位（头事件窗内未命中）"
                        found = fallback
            stats[f"relations:{label}/{found['status']}"] += 1
            results["relations"].append({"source": row, "attribute": label, **found})

    return results, stats


def _load_raw(source_dir: Path) -> dict:
    """
    读三份原始 JSON（**保留文件结构**，因为写回时要原样写出去）。

    不直接用 `load_annotations` 的扁平结果来写：那个函数返回的是"重排过的视图"，
    拿它写回会把文件结构改成工具自己的形状（`sample_entities.json` 的
    `places/persons/organizations` 三个键、关系的连字符键名都要保持原样）。
    """
    raw = {}
    for name in GOLD_FILES.values():
        payload = json.loads((source_dir / name).read_text(encoding="utf-8"))
        raw[name] = payload
    return raw


def _flatten(raw: dict) -> dict:
    """把原始结构摊成三类行列表（**行对象是引用**，所以回填能直接改到原对象）。"""
    entities = raw[GOLD_FILES["entities"]]
    relations = raw[GOLD_FILES["relations"]]
    return {
        "events": raw[GOLD_FILES["events"]].get("events") or [],
        "places": entities.get("places") or [],
        "persons": entities.get("persons") or [],
        "organizations": entities.get("organizations") or [],
        "relations": {label: (relations.get(label) or [])
                      for label in ("event-place", "event-org", "event-person", "event-event")},
    }


def write_back(results: dict, raw: dict, output_dir: Path) -> None:
    """
    把候选锚点写进副本（`evidence` / `evidence_start` / `evidence_end` / `locate_status`）。

    按**对象引用**回填：`results` 里的 `source` 就是 `raw` 里那一行本身，所以这里只负责
    把字段写上去、再把原始结构原样 dump 出来——不重新读文件（重读会换出一批新对象，
    按身份查表必然查不到，字段就静默一个都写不上）。
    """
    for item in results["events"] + results["entities"] + results["relations"]:
        row = item["source"]
        row["evidence"] = item["evidence"]
        row["evidence_start"] = item["start"]
        row["evidence_end"] = item["end"]
        row["locate_status"] = item["status"]

    output_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in raw.items():
        (output_dir / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                       encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="参考集的原文证据与字符偏移回填（只产候选）")
    parser.add_argument("--annotations", default=str(DEFAULT_DIR))
    parser.add_argument("--book", default=str(DEFAULT_BOOK))
    parser.add_argument("--output-dir", default=None, help="另存带证据的副本（不给则只报数）")
    args = parser.parse_args()

    book = Path(args.book)
    if not book.is_file():
        print(f"原文不存在: {book}（它受版权约束、不入库，需要在本地准备）")
        return 2
    raw = _load_raw(Path(args.annotations))
    gold = _flatten(raw)
    locator = Locator(book)
    results, stats = backfill(gold, locator)

    print("=" * 74)
    print("参考集证据回填（候选锚点）")
    print("=" * 74)
    print(f"原文: {book}（{len(locator.cleaned)} 字符清洗后 / {len(locator.haystack)} 字符折叠后）")
    for key in sorted(stats):
        print(f"  {key}: {stats[key]}")
    miss = [(k, v) for k, v in stats.items() if "未命中" in k]
    if miss:
        print(f"\n未命中合计 {sum(v for _k, v in miss)} 条——这些就是要人工回原文补的行")
    for label in ("events", "places", "persons", "organizations"):
        by_evidence = sum(v for k, v in stats.items()
                          if k.startswith(f"{label}/证据定位"))
        total = sum(v for k, v in stats.items() if k.startswith(f"{label}/"))
        if total:
            print(f"  其中 {label} 靠「产物证据句」定位上的: {by_evidence}/{total}"
                  f"（{by_evidence / total:.0%}）")
    event_hits = sum(v for k, v in stats.items()
                     if k.startswith("events/") and "未命中" not in k)
    event_rows = sum(v for k, v in stats.items() if k.startswith("events/"))
    if event_rows:
        print(f"\n**诊断**：事件定位中的 {event_hits}/{event_rows}"
              f"（{event_hits / event_rows:.0%}）落到了原文坐标。\n"
              "  **注意这不是「事件名在原文里找得到」的比例**——事件名多是标注方/模型写的概括名，"
              "按名称定位实测只有约四分之一能命中；这一层绝大多数是靠 `evidence_hint`（产物"
              "引用的原句）锚定的。所以事件层的证据是「该战事在原文哪一段叙述」，"
              "而不是「事件名这个串出现过」。")
    print("\n口径：`locate_status`（唯一命中 / 多处命中(取首次) / 未命中(文中无此串) / "
          "证据定位（覆盖率 N））；坐标是**原文**坐标（走抽取链同一套映射，不另写换算）")

    if args.output_dir:
        write_back(results, raw, Path(args.output_dir))
        print(f"\n已另存带证据的副本: {Path(args.output_dir)}（原目录未动）")
    else:
        print("\n（只读模式：没有写字。要拿到带证据的副本，加 --output-dir）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
