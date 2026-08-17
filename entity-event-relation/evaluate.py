"""
评估入口脚本
运行评估：python evaluate.py
"""

import json
import argparse
from pathlib import Path
from src.evaluation import OptimalEvaluator
from src.config import PROMPT_VERSION, EXTRACTION_VERSION, current_timestamp


def main():
    parser = argparse.ArgumentParser(description="评估历史战争文本抽取结果")
    parser.add_argument("--pred", default="output/中国历代战争简史/9_final_all.json", help="预测结果 JSON 路径")
    parser.add_argument("--config", default="config/eval_config.json", help="评估配置 JSON 路径")
    parser.add_argument("--output", default="evaluation/latest", help="评估输出目录")
    args = parser.parse_args()

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

    # Changed 2026-04-20 16:33:36 +08:00: Load thresholds from config so
    # prompt/evaluation experiments are reproducible.
    evaluator = OptimalEvaluator(
        annotation_dir=Path("data/annotations"),
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

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    with open(output_dir / "results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    # Added 2026-04-20 21:46:02 +08:00: Split reports make prompt iteration
    # easier without manually mining the full results JSON.
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
