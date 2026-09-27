#!/usr/bin/env python
"""
把**同一批抽取**在多个文本切片上跑出的产物合并成一份——用来把**预测范围对齐到参考集的 dev/test**。

**为什么需要它。** 参考集是按朝代块切的（当前 dev = 唐 + 秦汉、test = 明），而抽取是在
**文本文件**上跑的：一个子集一份产物。dev 横跨两个子集，评估却要一份与 dev 同范围的预测，
所以把两份并起来。

**为什么不能拿整本产物切一刀代替。** 产物里**没有字符坐标**（事件字段只有 `source_text`，
没有 offset），所以没法可靠地判断"这条记录落在不在某个子集区间里"——只能拿证据句回原文重新定位，
定位不到的就只能丢。那样切出来**既漏又偏**（留不留取决于"证据句能不能定位"，与抽取质量无关）。

**它不做的事**（别指望）：

- **不重算 `quality_report`**：那是给发布用的。合并产物只用于"把范围对齐到 dev/test"，
  所以这里写一条说明而不是假装有质量报告。
- **不去重**：两个子集的文本区间不重叠，事件与关系天然不重复；实体表里同一个地名可能在两边各出现
  一次（唐段与秦汉段都提到「长安」），**这是对的**——实体在评估里比的是**名称集合**。

用法：

    python tools/merge_extraction_outputs.py --inputs output/唐/9_final_all.json \\
        output/秦汉/9_final_all.json --output <仓库外>/dev_唐秦汉/9_final_all.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1]
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from war_extraction.utils.provenance import file_sha256  # noqa: E402

#: 产物里要首尾相接的三个列表族（键名取自产物自身，改产物 schema 时这里要跟着改）。
ENTITY_KINDS = ("places", "organizations", "persons")
RELATION_KINDS = ("event_place_relations", "event_organization_relations",
                  "event_person_relations", "event_event_relations")

#: 每个来源在 metadata 里留哪些字段：够回答"这份合并产物是哪几次跑出来的"。
PROVENANCE_FIELDS = ("extracted_at", "extraction_version", "prompt_version",
                     "model", "model_served", "api_base", "artifact_sha256", "git_commit")


def _counts(payload: dict) -> dict:
    return {
        "events": len((payload.get("events") or {}).get("events") or []),
        "entities": sum(len((payload.get("entities") or {}).get(k) or []) for k in ENTITY_KINDS),
        "relations": sum(len((payload.get("relations") or {}).get(k) or [])
                         for k in RELATION_KINDS),
    }


def merge(payloads: list) -> dict:
    """把若干份产物并成一份；返回合并后的 payload。"""
    merged = {
        "entities": {kind: [] for kind in ENTITY_KINDS},
        "events": {"events": [], "metadata": {}},
        "relations": {kind: [] for kind in RELATION_KINDS},
    }
    for payload in payloads:
        entities = payload.get("entities") or {}
        for kind in ENTITY_KINDS:
            merged["entities"][kind].extend(entities.get(kind) or [])
        merged["events"]["events"].extend((payload.get("events") or {}).get("events") or [])
        relations = payload.get("relations") or {}
        for kind in RELATION_KINDS:
            merged["relations"][kind].extend(relations.get(kind) or [])
    return merged


def build_metadata(payloads: list, paths: list) -> dict:
    """
    合并产物的 metadata：**逐份记来源**（路径、sha256、模型与版本、条数）。

    "合并产物是哪几次跑出来的"必须能自证——否则拿一份拼接出来的预测去评 dev，
    事后分不清它由哪几次运行、哪个模型产出，评估结论就没有归属。
    """
    sources = []
    for path, payload in zip(paths, payloads):
        meta = payload.get("metadata") or {}
        entry = {"path": str(path), "sha256": file_sha256(Path(path))}
        entry.update({field: meta.get(field) for field in PROVENANCE_FIELDS})
        entry["counts"] = _counts(payload)
        sources.append(entry)

    # 把**各份一致**的来源字段提到顶层：`evaluate.py` 的 metadata 读的是顶层
    # （`predictions.prompt_version` / `model` / `model_served`），不提上去就等于
    # "这次评估用的是哪套提示词、哪个模型"在报告里变成 None——而这几项正是
    # "指标必须与口径一起记"要记的东西。各份不一致时**不提**（宁缺勿错：混着两套
    # 提示词的合并产物，写哪一个都是错的）。
    hoisted = {}
    for field in ("model", "model_served", "api_base", "prompt_version",
                  "extraction_version", "git_commit"):
        values = {entry.get(field) for entry in sources}
        if len(values) == 1 and None not in values:
            hoisted[field] = values.pop()
    return {
        "merged_from": sources,
        "note": "这是把多次子集抽取**合并**出来的预测，只用于把评估范围对齐到参考集的 dev/test；"
                "不是一次独立运行的产物（`quality_report` 未重算）。"
                "下面几项来自各份一致的来源（不一致时不写，避免把两套口径混成一个）。",
        **hoisted,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="合并多个子集产物（把预测范围对齐到 dev/test）")
    parser.add_argument("--inputs", nargs="+", required=True, help="产物 JSON（如 output/唐/9_final_all.json）")
    parser.add_argument("--output", required=True, help="合并后的产物 JSON 路径")
    args = parser.parse_args()

    payloads, paths = [], []
    for raw in args.inputs:
        path = Path(raw)
        if not path.is_file():
            print(f"产物不存在: {path}")
            return 2
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not (payload.get("events") or {}).get("events"):
            print(f"{path} 里没有读到事件（是不是还没跑完？）")
            return 2
        payloads.append(payload)
        paths.append(path)

    merged = merge(payloads)
    merged["metadata"] = build_metadata(payloads, paths)
    merged["quality_report"] = {
        "note": "合并产物不重算质量报告（它只用于把评估范围对齐到 dev/test，不用于发布）。",
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

    counts = _counts(merged)
    print(f"合并完成: {output}")
    print(f"  来源 {len(paths)} 份: " + "、".join(p.name for p in paths))
    print(f"  合计 事件 {counts['events']} / 实体 {counts['entities']} / 关系 {counts['relations']}")
    for entry in merged["metadata"]["merged_from"]:
        c = entry["counts"]
        print(f"    {entry['path']}: 事件 {c['events']} / 实体 {c['entities']} / 关系 {c['relations']}"
              f"（模型 {entry.get('model_served') or entry.get('model')}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
