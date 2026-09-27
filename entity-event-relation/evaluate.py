"""
评估入口脚本
运行评估：python evaluate.py
"""

import json
import argparse
from pathlib import Path
from war_extraction.evaluation import OptimalEvaluator
from war_extraction.config import PROMPT_VERSION, EXTRACTION_VERSION, current_timestamp, current_time_tag
from war_extraction.utils.provenance import file_sha256, git_commit

#: 默认路径一律以本文件位置锚定（模块根），不随当前工作目录变。
#: 若用相对路径，从仓库根跑 `python entity-event-relation/evaluate.py` 会去找
#: `<仓库根>/output/...`（不存在），只有从模块目录跑才对——同一命令不该给出两种结果。
_PROJECT_ROOT = Path(__file__).resolve().parent

DEFAULT_PRED = _PROJECT_ROOT / "output" / "中国历代战争简史" / "9_final_all.json"
DEFAULT_EVAL_CONFIG = _PROJECT_ROOT / "config" / "eval_config.json"
DEFAULT_ANNOTATION_DIR = _PROJECT_ROOT / "data" / "annotations"


def default_output_dir() -> Path:
    """
    本次评估的默认输出目录：``evaluation/run_<时间戳>``（项目根下）。

    刻意**不**默认写 ``evaluation/latest``：那个目录是**跟踪入库的历史基线**
    （``metadata.snapshot_note`` 说明了它的来历与局限），而且它的值属于定序化之前的
    评估口径、不可复现——被一次随手运行覆盖掉的话，``git diff`` 只显示"数值变了"，
    看不出注释与口径说明一起没了。要覆盖历史基线必须显式写 ``--output evaluation/latest``。
    """
    return _PROJECT_ROOT / "evaluation" / f"run_{current_time_tag()}"


def warn_if_overwriting_baseline(output_dir: Path):
    """
    目标目录里已有带 ``snapshot_note`` 的结果时，覆盖前说清楚。

    默认目录已经避开历史基线，但 ``--output`` 仍可显式指过去（也包含
    ``evaluation/recheck-*`` 这类带注释的快照），所以覆盖前先提示，避免把注释
    连同旧值一起冲掉。
    """
    results_file = output_dir / "results.json"
    if not results_file.exists():
        return
    try:
        previous = json.loads(results_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    note = (previous.get("metadata") or {}).get("snapshot_note")
    if not note:
        return
    print("!" * 70)
    print(f"! 警告：{results_file} 是被加注的历史快照，本次运行将覆盖它")
    print(f"! 原注释：{note}")
    print("!" * 70)


def build_eval_metadata(pred_path: Path, pred_data: dict, config_path: Path, eval_config: dict,
                        annotation_dir: Path = None) -> dict:
    """
    评估结果的 metadata：把"被评估的产物"与"跑评估的评估器"**分成两组**记录。

    **为什么要分开。** 原先这里只写"评估时"的 `prompt_version` / `extraction_version`，
    对一份旧产物重跑评估，就会把旧产物标成当前提示词版本——产物自身的版本因此永久丢失。
    现在 `predictions` 组读的是**产物 metadata 里写的**版本（产物自证），
    `evaluator` 组才是本次运行的代码版本，另附预测文件与标注文件的 sha256，
    满足"报告指标必须同时给出口径"这条要求。

    `annotation_dir` 由调用方给（`--annotations-dir`）：**参考集换了目录，指标就换了分母**，
    所以它必须与指标一起记进 metadata，否则两份报告对比时看不出用的是哪套标注。
    """
    artifact_metadata = pred_data.get("metadata") or {}
    annotation_dir = annotation_dir or DEFAULT_ANNOTATION_DIR
    annotation_files = {}
    if annotation_dir.is_dir():
        for name in sorted(p.name for p in annotation_dir.glob("*.json")):
            annotation_files[name] = file_sha256(annotation_dir / name)

    return {
        "evaluated_at": current_timestamp(),
        # —— 被评估的产物（取自产物自证，不是评估时的代码版本）——
        "predictions": {
            "path": str(pred_path),
            "sha256": file_sha256(pred_path),
            "artifact_sha256": artifact_metadata.get("artifact_sha256"),
            "extracted_at": artifact_metadata.get("extracted_at"),
            "prompt_version": artifact_metadata.get("prompt_version"),
            "extraction_version": artifact_metadata.get("extraction_version"),
            "model": artifact_metadata.get("model"),
            # `model_served` 是**服务端实际服务**的模型名，与请求名可能不同
            # （实测把 `deepseek-chat` 路由到了 `deepseek-flash`）。只记请求名等于自证错信息，
            # 所以它是"产物自证"里最该抄过来的一项之一。
            "model_served": artifact_metadata.get("model_served"),
            "api_base": artifact_metadata.get("api_base"),
            "git_commit": artifact_metadata.get("git_commit"),
        },
        # —— 本次评估运行的代码与口径 ——
        "evaluator": {
            "prompt_version": PROMPT_VERSION,
            "extraction_version": EXTRACTION_VERSION,
            "git_commit": git_commit(short=True),
            "eval_config": eval_config,
            "eval_config_path": str(config_path),
            "eval_config_sha256": file_sha256(config_path),
            "annotation_dir": str(annotation_dir),
            "annotation_files": annotation_files,
        },
    }


def main():
    parser = argparse.ArgumentParser(description="评估历史战争文本抽取结果")
    parser.add_argument("--pred", default=str(DEFAULT_PRED), help="预测结果 JSON 路径")
    parser.add_argument("--config", default=str(DEFAULT_EVAL_CONFIG), help="评估配置 JSON 路径")
    parser.add_argument("--output", default=None,
                        help="评估输出目录，默认 evaluation/run_<时间戳>"
                             "（历史基线 evaluation/latest 需显式指定）")
    parser.add_argument("--also-published", default=None, nargs="?", const="__auto__",
                        help="额外评估发布子集（published/final.json）；不给值时取批次目录下的默认路径")
    parser.add_argument("--annotations-dir", default=str(DEFAULT_ANNOTATION_DIR),
                        help="参考集目录（默认 data/annotations 这份**旧**标注；"
                             "用重建后的参考集时指向 data/annotations/v2 或它的 dev 切分"
                             " data/annotations/v2_split/dev）。换了它指标就换了分母，"
                             "所以运行的 metadata 里会记下目录与三份文件的 sha256")
    args = parser.parse_args()
    annotation_dir = Path(args.annotations_dir)
    if not annotation_dir.is_dir():
        print(f"参考集目录不存在: {annotation_dir}")
        return 2

    output_dir = Path(args.output) if args.output else default_output_dir()
    warn_if_overwriting_baseline(output_dir)

    config_path = Path(args.config)
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            eval_config = json.load(f)
    else:
        eval_config = {}

    # 加载预测结果
    pred_path = Path(args.pred)
    if args.also_published == "__auto__":
        # 全量产物的同批次发布子集。变量名必须叫 published——`candidate` 这个叫法会被
        # 误读成候选区产物（`candidate/final.json`），而这里读的是发布子集。
        published_path = pred_path.parent / "published" / "final.json"
        args.also_published = str(published_path)
    with open(pred_path, "r", encoding="utf-8") as f:
        pred_data = json.load(f)

    # 阈值从 config 读，这样提示词/评估实验才可复现
    evaluator = OptimalEvaluator(
        annotation_dir=annotation_dir,
        relation_threshold=eval_config.get("relation_threshold", 40),
        event_sim_threshold=eval_config.get("event_sim_threshold", 0.35),
        entity_fuzzy_threshold=eval_config.get("entity_fuzzy_threshold", 70),
        event_event_sim_threshold=eval_config.get("event_event_sim_threshold", 0.35),
        event_year_tolerance=eval_config.get("event_year_tolerance", 30),
        enforce_semantic_constraints=eval_config.get("enforce_semantic_constraints", True),
    )

    results = evaluator.run_evaluation(pred_data)
    results["metadata"] = build_eval_metadata(pred_path, pred_data, config_path, eval_config,
                                              annotation_dir)

    # 发布子集也评一遍：下游知识库实际导的是 `published/final.json`（口径已确认，2026-09-27），
    # 两份的粒度不同，指标不可混用——所以两套都报，且各自带文件哈希。
    if args.also_published:
        published_path = Path(args.also_published)
        if not published_path.is_file():
            print(f"[跳过发布子集评估] 文件不存在: {published_path}")
        else:
            with open(published_path, "r", encoding="utf-8") as f:
                published_data = json.load(f)
            published_evaluator = OptimalEvaluator(
                annotation_dir=annotation_dir,
                relation_threshold=eval_config.get("relation_threshold", 40),
                event_sim_threshold=eval_config.get("event_sim_threshold", 0.35),
                entity_fuzzy_threshold=eval_config.get("entity_fuzzy_threshold", 70),
                event_event_sim_threshold=eval_config.get("event_event_sim_threshold", 0.35),
                event_year_tolerance=eval_config.get("event_year_tolerance", 30),
                enforce_semantic_constraints=eval_config.get("enforce_semantic_constraints", True),
            )
            print("#" * 70)
            print(f"# 发布子集评估: {published_path}")
            print("#" * 70)
            results["published"] = published_evaluator.run_evaluation(published_data)
            results["published"]["metadata"] = build_eval_metadata(
                published_path, published_data, config_path, eval_config, annotation_dir)
            results["summary_published"] = results["published"]["summary"]

    output_dir.mkdir(parents=True, exist_ok=True)
    with open(output_dir / "results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    # 拆出错误分析/字段报告两份小文件，省得每次翻整份 results.json
    error_analysis = {
        "entity_extraction": results.get("entity_extraction", {}).get("error_samples", {}),
        "event_extraction": results.get("event_extraction", {}).get("full_events", {}).get("error_samples", {}),
        "relation_extraction": results.get("relation_extraction", {}).get("error_samples", {}),
    }
    with open(output_dir / "error_analysis.json", "w", encoding="utf-8") as f:
        json.dump(error_analysis, f, ensure_ascii=False, indent=2)

    field_report = results.get("event_extraction", {}).get("field_metrics", {})
    with open(output_dir / "field_report.json", "w", encoding="utf-8") as f:
        json.dump(field_report, f, ensure_ascii=False, indent=2)
    print(f"\n详细结果已保存: {output_dir / 'results.json'}")
    print(f"错误分析已保存: {output_dir / 'error_analysis.json'}")
    print(f"字段报告已保存: {output_dir / 'field_report.json'}")


if __name__ == "__main__":
    raise SystemExit(main())
