#!/usr/bin/env python
"""阈值敏感性分析（EER-9 / 第 11 轮 C-6 第 2 条）。

四个阈值在 `config/eval_config.json` 里，但"换一个值指标会动多少"从来没有量化过——
评审问起"这些指标对阈值有多敏感"时只能凭感觉答。本脚本在**固定的预测产物**上做
一次一因子（OAT）扫描，把各指标的变动区间打成表。

用法（默认扫全部四个阈值，约 8 分钟；产物指纹会一起打出来）：

    cd entity-event-relation
    python tools/threshold_sensitivity.py                 # 打印 markdown 表
    python tools/threshold_sensitivity.py --out /tmp/sens.md
    python tools/threshold_sensitivity.py --only relation_threshold --only entity_fuzzy_threshold

**为什么先记产物指纹**：第 9 轮的教训是把"产物换代"误读成"匹配抖动"。指标变了要么是阈值
变了、要么是预测产物换了，脚本把 `--pred` 的 sha256 打在表头，两者才分得清。

**注意**：这里扫的是**评估口径**的敏感性，不改变抽取。改 `eval_config.json` 后要重跑的
是 `python evaluate.py`，不涉及大模型额度。
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import sys
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from war_extraction.evaluation import OptimalEvaluator  # noqa: E402

DEFAULT_PRED = MODULE_ROOT / "output" / "中国历代战争简史" / "9_final_all.json"
DEFAULT_CONFIG = MODULE_ROOT / "config" / "eval_config.json"
ANNOTATION_DIR = MODULE_ROOT / "data" / "annotations"

#: 一次一因子的扫描格点（默认值各自在 config 里：40 / 0.35 / 70 / 0.35）
GRIDS = {
    "relation_threshold": [30, 40, 50, 60, 70],
    "event_sim_threshold": [0.25, 0.35, 0.45, 0.55],
    "entity_fuzzy_threshold": [60, 70, 80, 90],
    "event_event_sim_threshold": [0.25, 0.35, 0.45, 0.55],
}


def file_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()[:16]


def evaluate_once(pred_data: dict, config: dict) -> dict:
    """跑一次评估，吞掉评估器自己的控制台输出（不然 17 次扫描会刷几百行）。"""
    evaluator = OptimalEvaluator(
        annotation_dir=ANNOTATION_DIR,
        relation_threshold=config["relation_threshold"],
        event_sim_threshold=config["event_sim_threshold"],
        entity_fuzzy_threshold=config["entity_fuzzy_threshold"],
        event_event_sim_threshold=config["event_event_sim_threshold"],
    )
    with contextlib.redirect_stdout(io.StringIO()):
        results = evaluator.run_evaluation(pred_data)
    return {
        "entity_f1": results["entity_extraction"]["f1"],
        "event_f1": results["event_extraction"]["full_events"]["f1"],
        "relation_f1": results["relation_extraction"]["f1"],
        "relation_tp": results["relation_extraction"]["counts"]["tp"],
        "relation_fp": results["relation_extraction"]["counts"]["fp"],
        "relation_fn": results["relation_extraction"]["counts"]["fn"],
        "macro_f1": results["summary"]["macro_avg_f1"],
    }


def scan(pred_data: dict, base_config: dict, only=None) -> dict:
    names = [n for n in GRIDS if (only is None or n in only)]
    report = {}
    for name in names:
        rows = []
        for value in GRIDS[name]:
            config = dict(base_config)
            config[name] = value
            metrics = evaluate_once(pred_data, config)
            rows.append((value, metrics))
            print(f"  {name}={value}: 实体F1={metrics['entity_f1']:.4f} "
                  f"事件F1={metrics['event_f1']:.4f} 关系F1={metrics['relation_f1']:.4f}",
                  file=sys.stderr)
        report[name] = rows
    return report


#: 表格里的四个指标列（键名, 列标题）。"区间"行按这个顺序把文字写进对应列。
_METRIC_COLUMNS = (
    ("entity_f1", "实体 F1"),
    ("event_f1", "事件 F1"),
    ("relation_f1", "关系 F1"),
    ("macro_f1", "宏观平均 F1"),
)


def _range_row(key: str, label: str, rows: list) -> str:
    """某个指标在本阈值各取值下的变动区间（只把文字写进它自己那一列）。"""
    values = [m[key] for _, m in rows]
    text = "**%.4f ~ %.4f（极差 %.4f）**" % (min(values), max(values), max(values) - min(values))
    cells = ["", "", "", "", "", ""]
    cells[[k for k, _ in _METRIC_COLUMNS].index(key)] = text
    return "| ↑ %s 区间 | %s |" % (label, " | ".join(cells))


def render_markdown(report: dict, base_config: dict, pred_path: Path, fingerprint: str) -> str:
    lines = []
    lines.append("| 阈值 | 取值 | 实体 F1 | 事件 F1 | 关系 F1 | 关系 TP/FP/FN | 宏观平均 F1 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for name, rows in report.items():
        for value, m in rows:
            marker = "（当前）" if value == base_config.get(name) else ""
            lines.append("| `%s` | %s%s | %.4f | %.4f | %.4f | %d/%d/%d | %.4f |" % (
                name, value, marker, m["entity_f1"], m["event_f1"], m["relation_f1"],
                m["relation_tp"], m["relation_fp"], m["relation_fn"], m["macro_f1"]))
        # 每个指标都给区间：只看关系 F1 会漏掉"事件阈值其实把事件 F1 拉了 0.12"这种事实
        for key, label in _METRIC_COLUMNS:
            lines.append(_range_row(key, label, rows))

    header = [
        "",
        "预测产物：`%s`" % pred_path.name,
        "产物指纹（sha256 前 16 位）：`%s`" % fingerprint,
        "当前阈值：`%s`" % json.dumps(base_config, ensure_ascii=False),
        "",
    ]
    return "\n".join(header + lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="评估阈值敏感性扫描（一次一因子）")
    parser.add_argument("--pred", default=str(DEFAULT_PRED))
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--only", action="append", choices=sorted(GRIDS),
                        help="只扫指定阈值（可重复）；默认全扫")
    parser.add_argument("--out", help="把 markdown 表写到该文件（同时仍打印到 stdout）")
    args = parser.parse_args()

    pred_path = Path(args.pred)
    pred_data = json.loads(pred_path.read_text(encoding="utf-8"))
    base_config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    fingerprint = file_fingerprint(pred_path)

    print(f"预测产物: {pred_path}\n产物指纹: {fingerprint}\n开始扫描……", file=sys.stderr)
    report = scan(pred_data, base_config, only=args.only)
    markdown = render_markdown(report, base_config, pred_path, fingerprint)

    print(markdown)
    if args.out:
        Path(args.out).write_text(markdown, encoding="utf-8")
        print(f"已写入 {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
