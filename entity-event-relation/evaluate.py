"""
评估入口脚本
运行评估：python evaluate.py
"""

import json
import argparse
from pathlib import Path
from war_extraction.evaluation import OptimalEvaluator
from war_extraction.config import PROMPT_VERSION, EXTRACTION_VERSION, current_timestamp, current_time_tag

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


def main():
    parser = argparse.ArgumentParser(description="评估历史战争文本抽取结果")
    parser.add_argument("--pred", default=str(DEFAULT_PRED), help="预测结果 JSON 路径")
    parser.add_argument("--config", default=str(DEFAULT_EVAL_CONFIG), help="评估配置 JSON 路径")
    parser.add_argument("--output", default=None,
                        help="评估输出目录，默认 evaluation/run_<时间戳>"
                             "（历史基线 evaluation/latest 需显式指定）")
    args = parser.parse_args()

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
    with open(pred_path, "r", encoding="utf-8") as f:
        pred_data = json.load(f)

    # 阈值从 config 读，这样提示词/评估实验才可复现
    evaluator = OptimalEvaluator(
        annotation_dir=DEFAULT_ANNOTATION_DIR,
        relation_threshold=eval_config.get("relation_threshold", 40),
        event_sim_threshold=eval_config.get("event_sim_threshold", 0.35),
        entity_fuzzy_threshold=eval_config.get("entity_fuzzy_threshold", 70),
        event_event_sim_threshold=eval_config.get("event_event_sim_threshold", 0.35)
    )

    results = evaluator.run_evaluation(pred_data)
    results["metadata"] = {
        "evaluated_at": current_timestamp(),
        "prompt_version": PROMPT_VERSION,
        "extraction_version": EXTRACTION_VERSION,
        "eval_config": eval_config,
        "pred_path": str(pred_path)
    }

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
    main()
