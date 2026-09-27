#!/usr/bin/env python
"""
给参考集（`data/annotations/`）分配**稳定 ID**，并报出身份冲突。

**为什么需要它。** 参考集重建之后，"这条记录"要能在文档、判定表、diff、IAA 记录里被**指认**。
用名称指认不行（同名不同年代各记一条，整改方案 10.1 第 4 项）；用行号更不行（插一条就全位移）。
所以每条记录要有一个跟内容绑定的短 ID。

**两种身份，报告里分开算**（口径见 `tools/annotation_io.py`）：

- **稳定 ID**（`evt-…` / `plc-…` / `per-…` / `org-…` / `rel-…`）：核心是 `名称 + 朝代`
  （关系的核心是三元组），**不含时间/地点**——把某条事件的地点改对了，ID 不该变。
- **归一键**（`Normalizer` 归一后 + 朝代 [+ 现代名]）：用于**冲突检测**——
  同一个归一键出现多行就是"同一条写了两遍"（要删），
  同一个稳定 ID 出现多行但归一键不同，就是"同名不同年代/不同地点"（合法，但要人看一眼确认）。

**只读是默认行为**。加 `--output-dir` 才写出带 `annotation_id` 的副本——原目录不动，
因为改参考集等于改指标分母，要走 `data/annotations/README.md` §四 的留档流程。

用法：

    python tools/assign_annotation_ids.py                       # 只体检、只报数
    python tools/assign_annotation_ids.py --output-dir /tmp/ann # 另存带 ID 的副本
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1]
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from tools.annotation_io import (  # noqa: E402
    GOLD_FILES,
    ID_PREFIXES,
    RELATION_CATEGORY_LABELS,
    entity_core,
    event_core,
    gold_counts,
    load_annotations,
    relation_core,
    stable_id,
)
from war_extraction.utils.normalizer import Normalizer  # noqa: E402

DEFAULT_DIR = MODULE_ROOT / "data" / "annotations"


def _event_records(gold: dict):
    """产出 (类别, 稳定 ID, 归一键, 行) —— 三个类别（事件/实体/关系）都走这一条路。"""
    normalizer = Normalizer()
    for row in gold["events"]:
        core = event_core(row, normalizer)
        norm_key = "|".join([
            normalizer.normalize_event_name(row.get("EventName")),
            str(row.get("DynastyName") or ""),
            str(row.get("StartDate") or ""),
            normalizer.normalize_entity_name(row.get("Place")),
        ])
        yield "events", stable_id(ID_PREFIXES["events"], core), norm_key, row
    for kind in ("places", "persons", "organizations"):
        for row in gold[kind]:
            core = entity_core(kind, row, normalizer)
            yield kind, stable_id(ID_PREFIXES[kind], core), core, row
    for attribute, (label, _layer) in RELATION_CATEGORY_LABELS.items():
        for row in gold["relations"][label]:
            core = relation_core(attribute, row, normalizer)
            yield "relations", stable_id(ID_PREFIXES["relations"], core), core, row


def build_index(gold: dict) -> dict:
    """按类别收集：稳定 ID → 行列表、归一键 → 行列表。"""
    index = {key: {"by_id": defaultdict(list), "by_norm": defaultdict(list), "rows": 0}
             for key in ("events", "places", "persons", "organizations", "relations")}
    for category, annotation_id, norm_key, row in _event_records(gold):
        bucket = index[category]
        bucket["rows"] += 1
        bucket["by_id"][annotation_id].append(row)
        bucket["by_norm"][norm_key].append(annotation_id)
    return index


def report(index: dict) -> dict:
    """三类问题分开报：同 ID 多行（同名不同年代，合法但要人看）、同归一键多行（重复行，要删）。"""
    result = {}
    for category, bucket in index.items():
        same_id = {aid: rows for aid, rows in bucket["by_id"].items() if len(rows) > 1}
        duplicated = {key: ids for key, ids in bucket["by_norm"].items() if len(ids) > 1}
        result[category] = {
            "rows": bucket["rows"],
            "unique_ids": len(bucket["by_id"]),
            "same_id_multiple_rows": len(same_id),
            "duplicate_rows": sum(len(ids) - 1 for ids in duplicated.values()),
            "duplicate_samples": [
                {"归一键": key, "条数": len(ids), "ID": sorted(set(ids))[:3]}
                for key, ids in sorted(duplicated.items(), key=lambda item: -len(item[1]))[:5]
            ],
            "same_id_samples": [
                {"ID": aid, "条数": len(rows),
                 "名称": (rows[0].get("EventName") or rows[0].get("geo_name")
                          or rows[0].get("PersonName") or rows[0].get("OrgName")
                          or rows[0].get("head"))}
                for aid, rows in sorted(same_id.items())[:5]
            ],
        }
    return result


def write_with_ids(source_dir: Path, output_dir: Path, gold: dict) -> list:
    """把 `annotation_id` 写进副本（结构与其余字段一字不动）。"""
    normalizer = Normalizer()
    written = []
    events = json.loads((source_dir / GOLD_FILES["events"]).read_text(encoding="utf-8"))
    for row in events.get("events") or []:
        row["annotation_id"] = stable_id(ID_PREFIXES["events"], event_core(row, normalizer))
    _dump(output_dir / GOLD_FILES["events"], events)
    written.append(GOLD_FILES["events"])

    entities = json.loads((source_dir / GOLD_FILES["entities"]).read_text(encoding="utf-8"))
    for kind in ("places", "persons", "organizations"):
        for row in entities.get(kind) or []:
            row["annotation_id"] = stable_id(ID_PREFIXES[kind], entity_core(kind, row, normalizer))
    _dump(output_dir / GOLD_FILES["entities"], entities)
    written.append(GOLD_FILES["entities"])

    relations = json.loads((source_dir / GOLD_FILES["relations"]).read_text(encoding="utf-8"))
    for attribute, (label, _layer) in RELATION_CATEGORY_LABELS.items():
        for row in relations.get(label) or []:
            row["annotation_id"] = stable_id(ID_PREFIXES["relations"],
                                             relation_core(attribute, row, normalizer))
    _dump(output_dir / GOLD_FILES["relations"], relations)
    written.append(GOLD_FILES["relations"])
    return written


def _dump(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="参考集稳定 ID 分配与身份冲突检测")
    parser.add_argument("--annotations", default=str(DEFAULT_DIR), help="参考集目录")
    parser.add_argument("--output-dir", default=None,
                        help="另存带 annotation_id 的副本（不给则只报数、不写文件）")
    parser.add_argument("--json", default=None, help="把报告写到这个路径")
    args = parser.parse_args()

    source = Path(args.annotations)
    gold = load_annotations(source)
    if not any(gold[key] for key in ("events", "places", "persons", "organizations")):
        print(f"没有读到参考集内容: {source}")
        return 2

    index = build_index(gold)
    result = report(index)

    print("=" * 74)
    print(f"参考集稳定 ID: {source}")
    print("=" * 74)
    print(f"条数: {gold_counts(gold)}")
    total_dup = 0
    for category, item in result.items():
        total_dup += item["duplicate_rows"]
        print(f"\n[{category}] 行 {item['rows']}，唯一 ID {item['unique_ids']}"
              f"；同 ID 多行 {item['same_id_multiple_rows']}，重复行 {item['duplicate_rows']}")
        for sample in item["same_id_samples"]:
            print(f"    同 ID 多行（同名不同年代？看一眼）: {sample['ID']} × {sample['条数']}"
                  f"  例: {sample['名称']}")
        for sample in item["duplicate_samples"]:
            print(f"    重复行（同一条写了两遍？要删）: {sample['归一键']} × {sample['条数']}")
    print(f"\n合计重复行 {total_dup}（旧参考集的结构问题之一，见 docs/参考集重建规范.md §2）")

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"source": str(source), "counts": gold_counts(gold),
                                   "index": result}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"报告已写入: {out}")

    if args.output_dir:
        written = write_with_ids(source, Path(args.output_dir), gold)
        print(f"\n已另存带 annotation_id 的副本: {Path(args.output_dir)}"
              f"（{len(written)} 个文件，原目录未动）")
    else:
        print("\n（只读模式：没有写字。要拿到带 ID 的副本，加 --output-dir）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
