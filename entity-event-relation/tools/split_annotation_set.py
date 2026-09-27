#!/usr/bin/env python
"""
把成稿的参考集按朝代切成 **development / test**（`docs/参考集重建规范.md` 第 ⑦ 步）。

**为什么要有工具而不是手工拷文件。** 切分有一条硬规则：**同一场战役的事件与它的关系必须落在
同一边**。手工切很容易把"事件进了开发集、它的关系留在测试集"——那会让测试集里的关系在评估时
因为事件不在而根本不进分母（旧参考集就是这个毛病的一种，`head` 不在事件表的有 605 条）。
工具按"关系跟随它 head 事件的朝代"来切，这条规则就机械成立了。

**实体怎么切。** 实体是全书级的（没有事件归属），按它自己的 `DynastyName` 切；
跨朝代被引用的实体（如某个进入本子集的异族）会同时出现在两边的实体表里——
**这不构成泄漏**：评估用的是实体**名称集合**，开发集里知道"匈奴"这个名字不会泄露测试集的答案。

**哪边当 development 是可调的，依据是样本量。** 当前是 `--dev 唐 秦汉`（dev 221 个事件、test 明 75 个）：
dev 只有几十个事件时，置信区间宽到判不出"区间不重叠"，而验收判据恰恰是这个。改它会**换分母的口径**，
所以要连同 `data/annotations/README.md` §5.2 的说明与 `evaluation/baseline_reference_v2/` 的冻结一起动。
`--dev` 可给多个子集，但**不给全部**（那样 test 为空，"只看一次"的纪律就没了）。

用法：

    python tools/split_annotation_set.py --annotations data/annotations/v2 \
        --dev 唐 秦汉 --output data/annotations/v2_split

切完两个目录各有一份三件套（`sample_entities.json` / `sample_events.json` / `sample_relations.json`），
外加一份 `split_manifest.json`（切分说明 + 两侧条数，供事后区分"标注变了"与"dev/test 换边了"）。
`evaluate.py` 用 `--annotations-dir` 指向**开发集**（`data/annotations/v2_split/dev`）。
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

from tools.annotation_io import GOLD_FILES, RELATION_CATEGORY_LABELS, load_annotations  # noqa: E402

#: 四类关系的**连字符键名**（与 `load_annotations` 返回的键一致）。
RELATION_LABELS = tuple(label for label, _layer in RELATION_CATEGORY_LABELS.values())

DEFAULT_DIR = MODULE_ROOT / "data" / "annotations" / "v2"
#: 子集名 → 朝代取值（与 `data/dynasty_subsets/README.md` 的切分对齐）。
SUBSET_DYNASTIES = {"明": ("明",), "唐": ("唐",), "秦汉": ("秦", "西汉", "东汉")}


def split(gold: dict, dev_dynasties: tuple) -> tuple:
    """
    按朝代切开；返回 ((事件, 实体, 关系), 孤儿关系数)。

    **实体的归边规则是两条的并集**（只按实体自己的朝代归边是不够的）：

    1. 按自己的 `DynastyName` 归边；
    2. **被该边的某条关系引用为目标**（`tail`）时，也归到那一边。

    第 2 条不能省。理由：关系的归边跟随**头事件**，而目标实体可能不属于同一朝代——
    「明子集里的某场战事，参战方是一个唐时才有的政权」这种行是正当的。只按朝代切的话，
    这条关系落在 dev、它的目标实体落在 test，dev 那边就报出「tail 不在对应名单」——
    结构自检会红，而这种红是**切分造成的假问题**，会盖住真正的结构问题。
    跨边被引用的实体会同时出现在两边，**这不构成泄漏**：评估用的是实体**名称集合**，
    开发集里知道「匈奴」这个名字不会泄露测试集的答案。
    """
    def side(dynasty: str) -> str:
        return "dev" if (dynasty or "").strip() in dev_dynasties else "test"

    events = {"dev": [], "test": []}
    event_side = {}
    for row in gold["events"]:
        bucket = side(row.get("DynastyName"))
        events[bucket].append(row)
        # 关系按 **head 事件**归边；事件名按原样做键（参考集内部的自洽由结构自检保证）
        event_side[(row.get("EventName") or "").strip()] = bucket

    relations = {"dev": {label: [] for label in RELATION_LABELS},
                 "test": {label: [] for label in RELATION_LABELS}}
    orphan = 0
    for label, rows in gold["relations"].items():
        for row in rows:
            bucket = event_side.get((row.get("head") or "").strip())
            if bucket is None:
                # head 不在事件表：这是**结构问题**（旧参考集有 605 条），工具不替它猜边
                orphan += 1
                continue
            relations[bucket][label].append(row)

    #: 关系类别 → 它目标所在的实体类（`event-event` 的目标是事件，不在这里）
    tail_kind = {"event-place": "places", "event-org": "organizations",
                 "event-person": "persons"}
    name_field = {"places": "geo_name", "persons": "PersonName", "organizations": "OrgName"}
    referenced = {"dev": {kind: set() for kind in tail_kind.values()},
                  "test": {kind: set() for kind in tail_kind.values()}}
    for bucket in ("dev", "test"):
        for label, rows in relations[bucket].items():
            kind = tail_kind.get(label)
            if kind is None:
                continue
            for row in rows:
                referenced[bucket][kind].add((row.get("tail") or "").strip())

    entities = {"dev": {"places": [], "persons": [], "organizations": []},
                "test": {"places": [], "persons": [], "organizations": []}}
    for kind in ("places", "persons", "organizations"):
        for row in gold[kind]:
            name = (row.get(name_field[kind]) or "").strip()
            buckets = {side(row.get("DynastyName"))}
            buckets |= {bucket for bucket in ("dev", "test")
                        if name in referenced[bucket][kind]}
            for bucket in buckets:
                entities[bucket][kind].append(row)
    return (events, entities, relations), orphan


def write_side(output: Path, side: str, events, entities, relations) -> None:
    target = output / side
    target.mkdir(parents=True, exist_ok=True)
    (target / GOLD_FILES["events"]).write_text(
        json.dumps({"events": events[side]}, ensure_ascii=False, indent=2), encoding="utf-8")
    (target / GOLD_FILES["entities"]).write_text(
        json.dumps(entities[side], ensure_ascii=False, indent=2), encoding="utf-8")
    (target / GOLD_FILES["relations"]).write_text(
        json.dumps(relations[side], ensure_ascii=False, indent=2), encoding="utf-8")


def build_manifest(dev_subsets: tuple, dev_dynasties: tuple, events, entities, relations,
                   orphan: int) -> dict:
    """
    切分说明（规范第 ⑦ 步要求的"记录 ③：按什么切、边界在哪"）。

    **为什么要落成文件**：切分的两侧只写在命令行里，事后翻 git 历史才知道哪边是 dev；
    而"指标为什么变了"有一半的可能是"dev/test 换边了"。写成文件随切分一起放进目录，
    下一个人 open 就能看到，不用猜。
    """
    sides = {}
    for side in ("dev", "test"):
        sides[side] = {
            "事件": len(events[side]),
            "实体": sum(len(entities[side][kind]) for kind in entities[side]),
            "关系": sum(len(rows) for rows in relations[side].values()),
            "按朝代（事件）": dict(Counter(row.get("DynastyName") or "" for row in events[side])),
        }
    return {
        "怎么切的": "按朝代整块切，不随机按条切。事件按自己的 DynastyName；"
                    "关系跟随 head 事件的边；实体 = 自己朝代 ∪ 被该边关系引用为目标"
                    "（跨边被引用的实体会同时出现在两边——评估用实体名称集合，不构成泄漏）。",
        "development 子集": list(dev_subsets),
        "development 朝代": list(dev_dynasties),
        "test 朝代": "其余全部",
        "两侧条数": sides,
        "head 不在事件表的关系（既没进 dev 也没进 test）": orphan,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="参考集按朝代切 development / test")
    parser.add_argument("--annotations", default=str(DEFAULT_DIR))
    parser.add_argument("--dev", required=True, nargs="+", choices=sorted(SUBSET_DYNASTIES),
                        help="哪些子集当 development（可给多个，如 `--dev 唐 秦汉`；其余当 test）")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    dev_subsets = tuple(dict.fromkeys(args.dev))  # 去重、保序
    if set(dev_subsets) == set(SUBSET_DYNASTIES):
        print("development 不能给全部子集——那样 test 是空的，就没有「只看一次」的集合了")
        return 2
    dev_dynasties = tuple(dynasty for name in dev_subsets for dynasty in SUBSET_DYNASTIES[name])

    source = Path(args.annotations)
    if not source.is_dir():
        print(f"参考集目录不存在: {source}")
        return 2
    gold = load_annotations(source)
    if not gold["events"]:
        print(f"{source} 里没有读到事件（是不是还没成稿？）")
        return 2

    (events, entities, relations), orphan = split(gold, dev_dynasties)
    output = Path(args.output)
    for side in ("dev", "test"):
        write_side(output, side, events, entities, relations)
    manifest = build_manifest(dev_subsets, dev_dynasties, events, entities, relations, orphan)
    output.mkdir(parents=True, exist_ok=True)
    (output / "split_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"开发集（{' + '.join(dev_subsets)}）与测试集已写入: {output}")
    for side in ("dev", "test"):
        counts = {k: v for k, v in manifest["两侧条数"][side].items() if k != "按朝代（事件）"}
        detail = "、".join(f"{k} {v}" for k, v in counts.items())
        print(f"  {side}: {detail}")
        print(f"      事件朝代分布: {manifest['两侧条数'][side]['按朝代（事件）']}")
    if orphan:
        print(f"\n**注意**：有 {orphan} 条关系的 head 不在事件表里，工具没有替它们猜边（既没进 dev 也没进 test）。"
              "\n  这是结构问题——先回第 ③ 步把它们修掉（或删掉），再切。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
