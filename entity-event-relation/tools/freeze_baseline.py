#!/usr/bin/env python
"""
固化当前基线：把"被评估的东西"与"评估用的东西"各自的指纹记下来。

**为什么要单独做一次记录。** 这个项目的产物由一次付费抽取生成，而标注、字典与评估阈值
都可以独立变动；任何一样变了，指标就会变，而事后**无法从产物反推是哪一样变了**——
现有 `9_final_all.json` 的 metadata 里连 `model` 都没有（见 2.5 节）。所以每次"要认真比指标"
之前先跑一次本脚本，把这一时刻的产物 / 标注 / 配置 / 提示词的 sha256 与提交号冻下来，
下一轮出现"指标变了"时才有据可查。

**适用范围与边界**：本脚本**只读**，不生成产物、不改标注、不调 API。它记录的是"当时是什么样"，
不做任何判断。产物与标注本身照旧不入库（见 `.gitignore`），入库的是这份指纹清单。

用法：

    python tools/freeze_baseline.py                     # 写到 evaluation/baseline/
    python tools/freeze_baseline.py --eval-dir evaluation/run_20260926_184355
    python tools/freeze_baseline.py --note "阶段 0 冻结：整改前的原始基线"
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from war_extraction.config import EXTRACTION_VERSION, PROMPT_VERSION, _TZ_SINGAPORE  # noqa: E402
from war_extraction.utils.provenance import file_sha256, git_commit  # noqa: E402

MODULE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRED = MODULE_ROOT / "output" / "中国历代战争简史" / "9_final_all.json"
DEFAULT_ANNOTATIONS = MODULE_ROOT / "data" / "annotations"
DEFAULT_CONFIG = MODULE_ROOT / "config"
DEFAULT_BASELINE = MODULE_ROOT / "evaluation" / "baseline"

#: 参考标注的来源说明（据项目负责人 2026-09-26 说明，与 `data/annotations/README.md` 的旧表述不一致）。
#: 三条指标合起来才说明白"这批 gold 到底是什么"，缺一条都会被后续接手的人误读成人工标注。
ANNOTATION_PROVENANCE = {
    "kind": "multi_model_merged",
    "is_human_annotated": False,
    "how": (
        "几个人用不同的模型把整本书分成几份、每个模型负责两份，最后把各份结果汇总，"
        "字段没有做人工处理，直接作为标注数据使用。"
    ),
    "consequences": [
        "当前指标测的是'模型 A 的产出'与'模型 B/C/D 汇总产出'之间的分歧，不是抽取正确率",
        "结构性缺陷（head 大量不在事件表、完全重复关系、同名多行、命名风格不统一）"
        "源于汇总环节缺失实体与事件对齐，不是个别标注者的疏漏",
        "在人工逐条核验之前，任何基于它的指标都不能作为抽取质量或模型能力的证据",
    ],
    "still_usable_as": [
        "候选清单 / 词表（事件名、人名、地名是书中真实存在的内容）",
        "别名表（config/aliases.json）的来源",
        "回归 / 可复现性检测的参照",
    ],
    "not_usable_as": [
        "准确性指标的分母",
        "跨版本'改好没改好'的判断依据（会把口径差异误读成质量升降）",
    ],
    "source_note": (
        "data/annotations/README.md 现写的'只有一名标注者、没有 IAA'与上述实际制作方式不符，"
        "该文件的更正措辞待标注所有人确认后另行处理（见整改方案 10.2 第 1 条）。"
    ),
}


#: **重建后的**参考集（`data/annotations/v2/`）的来源说明。与上面那份的区别是**性质不同**：
#: 它不是多模型汇总，是逐条人工核验的成稿（口径与流程见 `docs/参考集重建规范.md`）。
#: 冻结时按目录自动选一份，免得把"人工核验过的"标成"多模型汇总"、或反过来。
REBUILT_ANNOTATION_PROVENANCE = {
    "kind": "human_verified_rebuild",
    "is_human_annotated": True,
    "how": (
        "三个朝代子集（明 / 唐 / 秦汉）的草稿核验表由人逐条判定（保留/删除/修改/新增），"
        "明子集各层抽 20% 由两人独立判定并逐条仲裁；成稿由 apply_rebuild_table.py 机械落地"
        "（含仲裁覆盖与去重），证据锚点由 backfill_annotation_evidence.py 从原文重新定位。"
    ),
    "consequences": [
        "结构自检四项硬指标归零（head/tail 不在名单、重复关系行、残缺年份）",
        "事件与关系各带原文 evidence + 字符区间，可逐条回溯原文",
        "仍有两处未做的：唐/秦汉两个子集没有做 IAA；少量人工补漏行没有原文锚点",
    ],
    "still_usable_as": [
        "准确性指标的**分母**（这是重建的全部意义）",
        "调提示词与阈值时的 development 集（`data/annotations/v2_split/dev`）",
    ],
    "not_usable_as": [
        "test 集不可用于调参（用一次少一次）",
        "不可与旧标注算出的指标直接比大小（分母口径不同）",
    ],
    "source_note": (
        "四个留给人判的口子（萨尔浒之战主动方、大顺军攻占北京之战的投降行、一对多角色配对口径、"
        "归一化名称造成的定位误杀）见 data/annotations/README.md §5.4。"
    ),
}


def _counts(pred_path: Path) -> dict:
    """产物各类条数：与下游 `current_dataset.json` 的计数口径对应，便于逐项核对。"""
    with open(pred_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    entities = data.get("entities") or {}
    events_block = data.get("events") or {}
    events = events_block if isinstance(events_block, list) else (events_block.get("events") or [])
    relations = data.get("relations") or {}
    return {
        "events": len(events),
        "places": len(entities.get("places") or []),
        "persons": len(entities.get("persons") or []),
        "organizations": len(entities.get("organizations") or []),
        "relations": sum(
            len(relations.get(key) or [])
            for key in (
                "event_place_relations",
                "event_person_relations",
                "event_organization_relations",
                "event_event_relations",
            )
        ),
    }


#: 每个目录各自的后缀。**必须逐目录给**：原先这里写死 `*.json`，而提示词目录下
#: 全是 `.py`——于是两份 baseline.json 的 `prompts.files` 都是空字典，
#: "冻结了提示词指纹"这句话是假的（而空字典看起来像"这个目录没文件"，
#: 不会有人去查）。配置目录是 json、提示词目录是 py，列出来就不会再错。
_DIR_PATTERNS = {
    "annotations": ("*.json",),
    "config": ("*.json",),
    "prompts": ("*.py",),
}


#: 基线记录里"某一组文件在此刻磁盘上的指纹"用的键名。
#:
#: **为什么键名要这么长。** 同一份 `baseline.json` 里有两个都是"配置指纹"的东西，语义不同：
#: **冻结时磁盘上**的文件（本键）与**那次评估真正生效**的版本
#: （`baseline_metrics.effective_at_evaluation.eval_config_sha256`，来自评估自己的 metadata）。
#: 原键名 `files` 太泛，取值的人几乎必然取错——`evaluation/baseline/` 是在改完配置之后才冻结的，
#: 它的 `files` 记的是**改后**的文件，而被评估的是**改前**的口径。改名之后，
#: "冻结时状态"与"当时生效"在命名上就分得开了。
FILES_ON_DISK_KEY = "files_on_disk_at_freeze"


def _dir_fingerprints(directory: Path, patterns=None, kind: str = None) -> dict:
    """
    目录下每个文件的 sha256（只取一层，按文件名排序）。

    `kind` 取 `annotations` / `config` / `prompts` 之一时用 `_DIR_PATTERNS` 里对应的后缀；
    显式给 `patterns` 时以它为准。**不允许既不给 kind 也不给 patterns**——那正是
    过去那个"看起来冻了、其实一个文件都没冻"的坑。
    """
    if patterns is None:
        if kind is None or kind not in _DIR_PATTERNS:
            raise ValueError("_dir_fingerprints 需要 kind（annotations/config/prompts）或显式 patterns")
        patterns = _DIR_PATTERNS[kind]
    if not directory.is_dir():
        return {}
    files = sorted({p for pattern in patterns for p in directory.glob(pattern)},
                   key=lambda p: p.name)
    return {p.name: {"sha256": file_sha256(p),
                     "sha256_lf": _sha256_lf(p)} for p in files}


def _sha256_lf(path: Path) -> str:
    """
    **行尾归一后的** sha256（CRLF → LF），跨机器核对比对这个。

    为什么两个都要记：`.gitattributes` 是 `* text=auto eol=lf`，所以**新克隆拿到的文件是 LF**，
    而 Windows 上这些 JSON/CSV 是写入端用文本模式写出来的、**工作区里是 CRLF**。
    两个口径算出的哈希不同（实测 v2 三份文件全部不同），于是"按冻结记录核对"这件事
    在别的机器上会给出假的"文件变了"。所以：
    `sha256` 是**当时磁盘口径**（也是这个项目所有历史记录的算法，保留它免得新旧记录不可比），
    `sha256_lf` 是**跨机器口径**——要核对"这份参考集是不是当初冻的那份"，用它。
    """
    import hashlib
    data = path.read_bytes()
    return hashlib.sha256(data.replace(bytes([13, 10]), bytes([10]))).hexdigest()


def _baseline_metrics(eval_dir: Path) -> dict:
    """
    从某次评估的 results.json 抽指标（连带它自己的 metadata，口径与数值要一起留）。

    同时抽出**那次评估自己记录的** `eval_config_sha256` 与标注文件指纹。这两样与
    "冻结时磁盘上的文件哈希"可能不同——**必须先记下来**：`evaluation/baseline/`（改前）
    是在改完之后才生成的，它记录的 `config.files.eval_config.json` 是**改后**的文件，
    而同一次评估记录在 `metadata.evaluator.eval_config_sha256` 里的才是**当时生效**的版本。
    两个都留着，"这份基线到底按哪版口径算的"才是可查的；只留一个就会出现
    "同一份 baseline.json 里两处哈希互相矛盾"。
    """
    results_file = eval_dir / "results.json"
    if not results_file.is_file():
        return {}
    with open(results_file, "r", encoding="utf-8") as f:
        results = json.load(f)
    metadata = results.get("metadata") or {}
    evaluator = metadata.get("evaluator") or {}
    return {
        "source": str(results_file),
        "source_sha256": file_sha256(results_file),
        "summary": results.get("summary"),
        "metadata": metadata,
        # 该次评估**运行时**认定的口径文件（来自它自己的 metadata，权威）
        "effective_at_evaluation": {
            "eval_config_path": evaluator.get("eval_config_path"),
            "eval_config_sha256": evaluator.get("eval_config_sha256"),
            "annotation_files": evaluator.get("annotation_files"),
            "evaluator_prompt_version": evaluator.get("prompt_version"),
            "evaluator_git_commit": evaluator.get("git_commit"),
            "predictions_sha256": (metadata.get("predictions") or {}).get("sha256"),
        },
    }


def build_record(pred_path: Path, eval_dir: Path | None, note: str | None,
                 annotations_dir: Path = None) -> dict:
    annotations_dir = Path(annotations_dir or DEFAULT_ANNOTATIONS)
    record = {
        # `frozen_at` 与 `run_id` 都由调用方填（保持本函数纯函数化，便于测试）。
        # 为什么要 `run_id`：`current_timestamp()` 只精确到秒，两次冻结很容易落在同一秒上，
        # 于是两份 baseline.json 的 `frozen_at` **完全相同**，"这是两次独立运行的结果"
        # 就无从自证——而"改前/改后是两次独立冻结"正是这对记录的全部意义。
        "frozen_at": None,
        "frozen_at_epoch": None,
        "run_id": None,
        "note": note,
        "git_commit": git_commit(),
        "module_versions": {
            "prompt_version": PROMPT_VERSION,
            "extraction_version": EXTRACTION_VERSION,
        },
        "artifact": {
            "path": str(pred_path),
            "exists": pred_path.is_file(),
            "sha256": file_sha256(pred_path),
        },
        "annotations": {
            "dir": str(annotations_dir),
            FILES_ON_DISK_KEY: _dir_fingerprints(annotations_dir, kind="annotations"),
            "provenance": (ANNOTATION_PROVENANCE if Path(annotations_dir) == DEFAULT_ANNOTATIONS
                           else REBUILT_ANNOTATION_PROVENANCE),
        },
        "config": {
            "dir": str(DEFAULT_CONFIG),
            FILES_ON_DISK_KEY: _dir_fingerprints(DEFAULT_CONFIG, kind="config"),
        },
        "prompts": {
            "dir": str(MODULE_ROOT / "war_extraction" / "prompts"),
            FILES_ON_DISK_KEY: _dir_fingerprints(MODULE_ROOT / "war_extraction" / "prompts", kind="prompts"),
        },
    }
    if pred_path.is_file():
        record["artifact"]["counts"] = _counts(pred_path)
        with open(pred_path, "r", encoding="utf-8") as f:
            artifact_metadata = (json.load(f).get("metadata") or {})
        record["artifact"]["metadata"] = artifact_metadata
    if eval_dir:
        record["baseline_metrics"] = _baseline_metrics(eval_dir)
    return record


def render_markdown(record: dict) -> str:
    """人读版：JSON 是给脚本比对的，这份是给接手的人看的。"""
    artifact = record["artifact"]
    lines = [
        "# 基线记录（自动生成，勿手改）",
        "",
        f"- 冻结时间：{record['frozen_at']}（run_id `{record.get('run_id')}`）",
        f"- 代码提交：`{record['git_commit']}`",
        f"- 提示词版本（生成侧派生值）：`{record['module_versions']['prompt_version']}`",
        f"- 抽取版本：`{record['module_versions']['extraction_version']}`",
    ]
    if record.get("note"):
        lines.append(f"- 说明：{record['note']}")
    lines += [
        "",
        "## 被评估的产物",
        "",
        f"- 路径：`{artifact['path']}`",
        f"- 文件存在：{artifact['exists']}",
        f"- 文件 sha256：`{artifact['sha256']}`",
    ]
    if artifact.get("counts"):
        counts = artifact["counts"]
        lines.append(
            "- 条数：事件 {events} / 地点 {places} / 人物 {persons} / 组织 {organizations} / 关系 {relations}".format(**counts)
        )
    metadata = artifact.get("metadata") or {}
    if metadata:
        lines += [
            "",
            "产物 metadata（产物自证，缺项即当时的产物没有记录这一项）：",
            "",
            "| 键 | 值 |",
            "| --- | --- |",
        ]
        for key in ("extracted_at", "prompt_version", "extraction_version", "model",
                    "api_base", "git_commit", "artifact_sha256", "text_length"):
            lines.append(f"| `{key}` | `{metadata.get(key)}` |")
    if record.get("baseline_metrics"):
        metrics = record["baseline_metrics"]
        summary = metrics.get("summary") or {}
        lines += [
            "",
            "## 基线指标",
            "",
            f"- 来源：`{metrics['source']}`（sha256 `{metrics['source_sha256']}`）",
            "",
            "| 指标 | 值 |",
            "| --- | ---: |",
        ]
        for key, value in summary.items():
            lines.append(f"| {key} | {value} |")
    lines += [
        "",
        "## 参考标注的来源（引用指标前必读）",
        "",
        f"- 类型：`{ANNOTATION_PROVENANCE['kind']}`，**是否人工标注：{ANNOTATION_PROVENANCE['is_human_annotated']}**",
        f"- 制作方式：{ANNOTATION_PROVENANCE['how']}",
        "",
        "后果：",
    ]
    lines += [f"- {item}" for item in ANNOTATION_PROVENANCE["consequences"]]
    lines += ["", "仍可用作："]
    lines += [f"- {item}" for item in ANNOTATION_PROVENANCE["still_usable_as"]]
    lines += ["", "不可用作："]
    lines += [f"- {item}" for item in ANNOTATION_PROVENANCE["not_usable_as"]]
    lines += ["", f"> {ANNOTATION_PROVENANCE['source_note']}"]
    lines += [
        "",
        "## 文件指纹",
        "",
        f"下表是**冻结这一时刻磁盘上**的文件。它与「那次评估真正生效」的版本可能不同"
        f"（见上文 `effective_at_evaluation`），这也是这个键叫 `{FILES_ON_DISK_KEY}` 的原因。",
        "",
        "哈希有**两列**：`sha256` 是冻结当时磁盘口径（Windows 下这些 JSON/CSV 是 CRLF，"
        "项目所有历史记录用的也是它，保留以免新旧不可比）；`sha256_lf` 是行尾归一（CRLF→LF）后的口径。"
        "**在别的机器上核对「这份文件是不是当初冻的那份」要用 `sha256_lf`**——"
        "`.gitattributes` 是 `* text=auto eol=lf`，新克隆拿到的就是 LF，拿 `sha256` 对会得出假的「文件变了」。",
        "",
        "| 目录 | 文件 | sha256（当时磁盘口径） | sha256_lf（跨机器核对用） |",
        "| --- | --- | --- | --- |",
    ]
    for group in ("annotations", "config", "prompts"):
        for name, digest in (record[group][FILES_ON_DISK_KEY] or {}).items():
            sha, sha_lf = (digest["sha256"], digest["sha256_lf"]) if isinstance(digest, dict) else (digest, "—")
            lines.append(f"| {group} | `{name}` | `{sha}` | `{sha_lf}` |")
    lines.append("")
    return "\n".join(lines)


def main():
    from war_extraction.config import current_timestamp

    parser = argparse.ArgumentParser(description="固化抽取产物/标注/配置的基线指纹")
    parser.add_argument("--pred", default=str(DEFAULT_PRED), help="产物 JSON 路径")
    parser.add_argument("--eval-dir", default=None,
                        help="某次评估的输出目录（含 results.json），用于一并记录基线指标")
    parser.add_argument("--output", default=str(DEFAULT_BASELINE), help="基线记录输出目录")
    parser.add_argument("--note", default=None, help="本次冻结的说明")
    parser.add_argument("--annotations-dir", default=str(DEFAULT_ANNOTATIONS),
                        help="参考集目录：默认是旧那三份（多模型汇总）；冻结重建后的参考集时"
                             "指向 data/annotations/v2 —— **来源说明会跟着换**"
                             "（human_verified_rebuild，与 multi_model_merged 不是一回事）")
    args = parser.parse_args()

    pred_path = Path(args.pred)
    eval_dir = Path(args.eval_dir) if args.eval_dir else None
    record = build_record(pred_path, eval_dir, args.note,
                          Path(args.annotations_dir))
    frozen_at = datetime.now(_TZ_SINGAPORE)
    record["frozen_at"] = frozen_at.strftime("%Y-%m-%d %H:%M:%S.%f +08:00")
    record["frozen_at_epoch"] = round(frozen_at.timestamp(), 6)
    record["run_id"] = uuid.uuid4().hex[:12]

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 评估目录默认写在 evaluation/run_*/ 下（不入库），而基线记录是要入库的。
    # 把 results.json 复制一份到记录旁边，基线才自洽——否则记录里那个路径过几天就没了。
    metrics = record.get("baseline_metrics")
    if metrics:
        source_file = Path(metrics["source"])
        if source_file.is_file():
            frozen_copy = output_dir / "baseline_results.json"
            frozen_copy.write_bytes(source_file.read_bytes())
            metrics["original_source"] = metrics["source"]
            metrics["source"] = str(frozen_copy)

    json_path = output_dir / "baseline.json"
    md_path = output_dir / "baseline.md"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    md_path.write_text(render_markdown(record), encoding="utf-8")

    print(f"基线记录已写入: {json_path}")
    print(f"人读版已写入: {md_path}")
    if not pred_path.is_file():
        print(f"注意：产物不存在（{pred_path}），只有标注/配置/提示词的指纹被记录")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
