"""F10 评测命令行入口（evaluation/cli.py）。

用法（从 RAG/ 根目录）：
    python -m evaluation.cli check-bank            # 校验题库结构
    python -m evaluation.cli run [--suites main]   # 复跑问答链生成 run 目录+报告+评分模板
    python -m evaluation.cli report --run <dir> [--scores <scores.jsonl>]
    python -m evaluation.cli report                # 默认汇总最近一次 run

一条命令可重复运行 = `run`（含 auto 报告），人工评分后再跑 `report --scores` 更新报告。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from config.settings import get_settings

# source_ref 可回溯格式：实体 id 或 关系行 id（relations#<legacy表名>:<行号>）
_REF_RE = re.compile(
    r"^(?:(?:event|person|place|org)_\d+|relations#[\w]+:\d+)$")

# 各套件的默认检索配置（可在 run 子命令用 --configs 整体覆盖）
SUITE_DEFAULT_CONFIGS = {
    "main": ["dual", "text-only"],
    "refusal": ["dual"],
    "long_rewrite": ["dual", "text-only-and", "text-only-or"],
    "filter_loss": ["dual", "text-only"],
}


def _resolve_version(settings, version):
    if version:
        return version
    from server.runtime import resolve_version
    return resolve_version(settings)[0]


def _build_runtime(settings, version, allow_llm: bool):
    if not allow_llm:
        # 默认离线摘要回答器：确定性，保证评测可复现
        settings.llm_base_url = ""
        settings.llm_api_key = ""
        settings.fallback_llm_base_url = ""
        settings.fallback_llm_api_key = ""
    from server.runtime import build_runtime
    return build_runtime(settings, version)


def _bank_path_of(version: str, path: str | None) -> Path:
    if path:
        return Path(path)
    return Path(f"data/eval/{version}/questions.jsonl")


def _run_dir_of(version: str) -> Path:
    return Path(f"data/eval/{version}/runs")


# ---- check-bank ----
def cmd_check_bank(args) -> int:
    settings = get_settings()
    version = _resolve_version(settings, args.version)
    bank_path = _bank_path_of(version, args.bank)
    from evaluation.bank import load_bank

    if not bank_path.exists():
        print(f"题库不存在：{bank_path}")
        return 2
    bank = load_bank(bank_path)
    res = bank.validate()
    print(f"题库: {bank_path}")
    print(f"version={bank.version} annotation_version={bank.annotation_version or '(未填)'} "
          f"items={len(bank.items)}")
    print(json.dumps(res["summary"], ensure_ascii=False, indent=2))
    if res["errors"]:
        print("\n结构问题：")
        for e in res["errors"]:
            print(f"- {e['id']}: {'; '.join(e['problems'])}")
        return 1

    # 期望实体能否被词典识别（数据版本耦合检查，warning 不阻塞）
    from server.query import load_understanding

    qu = load_understanding(settings.snapshot_dir / version)
    warnings = 0
    for q in bank.items:
        for name in q.expected_entities:
            hits = qu.matcher.match(name)
            if not hits:
                print(f"  ⚠ {q.id} 标注词「{name}」在词典中未命中（可能仅为文本层标注）")
                warnings += 1

    # source_ref 可回溯性：应为实体 id（event_/person_/place_/org_ + 数字）或
    # 关系行 id（relations#<legacy表名>:<行号>，可多值；描述性文字无法回溯）；
    bad_refs = []
    for q in bank.items:
        parts = [p.strip() for p in re.split(r"[,/;]", q.source_ref or "") if p.strip()]
        if not parts or any(not _REF_RE.match(p) for p in parts):
            bad_refs.append((q.id, q.source_ref))
    if bad_refs:
        print(f"\n⚠ source_ref 不可回溯（{len(bad_refs)} 条，应为 event_XXXX / person_XXXX "
              f"/ relations#<legacy表名>:<行号>）：")
        for qid, ref in bad_refs[:10]:
            print(f"  - {qid}: {ref!r}")
        warnings += len(bad_refs)

    print(f"\n结构校验通过（含 {warnings} 条提示，用于人工审核时核对标注词/出处）。")
    return 0


# ---- run ----
async def _run_all(args) -> int:
    settings = get_settings()
    version = _resolve_version(settings, args.version)
    bank_path = _bank_path_of(version, args.bank)
    from evaluation.bank import load_bank, save_bank
    from evaluation import chain

    if not bank_path.exists():
        print(f"题库不存在：{bank_path}")
        return 2
    bank = load_bank(bank_path)
    res = bank.validate()
    if res["errors"]:
        print("题库结构校验失败，请先修正：")
        for e in res["errors"]:
            print(f"- {e['id']}: {'; '.join(e['problems'])}")
        return 2

    requested = args.suites or list(SUITE_DEFAULT_CONFIGS)
    bad = [s for s in requested if s not in SUITE_DEFAULT_CONFIGS]
    if bad:
        print(f"未知套件: {bad}（可用: {list(SUITE_DEFAULT_CONFIGS)}）")
        return 2

    # 每套件的配置
    if args.configs:
        cfg_names = [c.strip() for c in args.configs.split(",") if c.strip()]
        suite_cfgs = {s: cfg_names for s in requested}
    else:
        suite_cfgs = {s: list(SUITE_DEFAULT_CONFIGS[s]) for s in requested}

    labels = {c for cfgs in suite_cfgs.values() for c in cfgs}
    for lab in labels:
        if lab not in chain.CONFIG_PRESETS:
            print(f"未知配置: {lab}（可用: {list(chain.CONFIG_PRESETS)}）")
            return 2

    # 组装评测用例：filter_loss 题目展开成 开/关筛选 两个 variant
    cases: list[dict] = []
    for q in bank.items:
        if q.suite not in requested:
            continue
        cfgs = suite_cfgs[q.suite]
        if q.suite == "filter_loss":
            if not (q.filters or {}).get("event_type"):
                print(f"⚠ {q.id} 属 filter_loss 套件但 filters.event_type 为空，跳过该变体对比")
            for cfg in cfgs:
                for variant, flt in (("filters-on", q.filters),
                                     ("filters-off", {})):
                    cases.append(_case_of(q, cfg, variant, flt))
        else:
            for cfg in cfgs:
                cases.append(_case_of(q, cfg, "-", q.filters))

    if not cases:
        print("没有可运行的评测用例（检查 --suites/题库套件）。")
        return 2

    # run 目录
    run_id = args.run_id or "run_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = _run_dir_of(version) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    runtime = _build_runtime(settings, version, allow_llm=args.llm)
    print(f"数据版本: {runtime.version}　text_mode={runtime.meta.get('text_mode')} "
          f"llm_available={runtime.meta.get('llm_available')}")
    print(f"评测用例: {len(cases)}（题目 {len({c['question_id'] for c in cases})} × 变体/配置）")
    print(f"run 目录: {run_dir}")

    from evaluation.chain import CONFIG_PRESETS

    total = len(cases)
    traces_path = run_dir / "traces.jsonl"
    with open(traces_path, "w", encoding="utf-8") as f:
        for i, case in enumerate(cases, 1):
            cfg = CONFIG_PRESETS[case["config"]]
            trace = await chain.run_question(
                runtime, cfg, case["question"],
                filters=case["filters"], expected_names=case["expected_entities"],
            )
            rec = {
                "question_id": case["question_id"],
                "question": case["question"],
                "suite": case["suite"],
                "category": case["category"],   # 供报告"逐题对照表"的类别列使用
                "variant": case["variant"],
                "config": case["config"],
                "answerable": case["answerable"],
                "expected_entities": case["expected_entities"],
                "filters": case["filters"],
                "trace": trace,
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if args.verbose or i == 1 or i == total:
                print(f"  [{i}/{total}] {case['question_id']} "
                      f"suite={case['suite']} variant={case['variant']} "
                      f"config={case['config']}")

    meta = {
        "run_id": run_id,
        "version": runtime.version,
        "annotation_version": bank.annotation_version,
        "bank_path": str(bank_path),
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "configs": sorted(labels),
        "suites": requested,
        "llm_used": bool(args.llm),
        "text_mode": runtime.meta.get("text_mode"),
        "command": "python -m evaluation.cli run" + _cmd_suffix(args, bank_path),
    }
    (run_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    # 写最近一次指针
    latest = run_dir.parent / "latest.txt"
    latest.write_text(run_dir.name, encoding="utf-8")

    # 生成自动报告 + 评分模板
    _refresh_report(run_dir)
    _export_template(run_dir)
    print("\n完成。报告: report.md；人工评分模板: scoring_template.jsonl")
    return 0


def _case_of(q, config: str, variant: str, filters) -> dict:
    return {
        "question_id": q.id,
        "question": q.question,
        "suite": q.suite,
        "category": q.category,
        "variant": variant,
        "config": config,
        "answerable": q.answerable,
        "expected_entities": list(q.expected_entities),
        "filters": dict(filters or {}),
    }


def _cmd_suffix(args, bank_path) -> str:
    parts = [f" --bank {bank_path}"]
    if args.suites:
        parts.append(f" --suites {','.join(args.suites)}")
    return "".join(parts)


def _refresh_report(run_dir) -> None:
    from evaluation import report as report_mod

    run = report_mod.load_run(run_dir)
    text = report_mod.build_report(run, md_out=run_dir / "report.md")
    (run_dir / "report.md").write_text(text, encoding="utf-8")
    print(f"报告: {(run_dir / 'report.md')}")


def _export_template(run_dir) -> None:
    from evaluation import report as report_mod
    from evaluation import grading

    run = report_mod.load_run(run_dir)
    recs = run["recs"]
    bank = run["bank"]
    if bank is None:
        print("（题库文件缺失，跳过评分模板导出）")
        return
    by_id = bank.by_id()
    configs = sorted({r.get("config") for r in recs})
    config_label = "dual" if "dual" in configs else (configs[0] if configs else "dual")
    rows = grading.build_template_rows(by_id, recs, config_label)
    out = run_dir / "scoring_template.jsonl"
    grading.write_template(rows, out)
    print(f"评分模板: {out}（{config_label} 配置，已含 {len(rows)} 条待评分记录）")


# ---- report ----
def cmd_report(args) -> int:
    from evaluation import report as report_mod

    if args.run:
        run_dir = Path(args.run)
    else:
        run_dir = _resolve_latest_run(args)
    if run_dir is None or not (run_dir / "meta.json").exists():
        print("未找到 run 目录（用 --run 指定，或确认 data/eval/<version>/runs/latest.txt）")
        return 2
    scores_path = Path(args.scores) if args.scores else None
    run = report_mod.load_run(run_dir)
    if scores_path and not scores_path.exists():
        print(f"评分文件不存在：{scores_path}")
        return 2
    if scores_path:
        run["meta"]["scores_path"] = str(scores_path)
        meta_p = run_dir / "meta.json"
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
        meta["scores_path"] = str(scores_path)
        meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    text = report_mod.build_report(run, scores_path=scores_path,
                                   md_out=run_dir / "report.md")
    (run_dir / "report.md").write_text(text, encoding="utf-8")
    print(f"报告已更新: {run_dir / 'report.md'}")
    return 0


def _resolve_latest_run(args) -> Path | None:
    settings = get_settings()
    try:
        version = _resolve_version(settings, args.version)
    except FileNotFoundError:
        return None
    latest = _run_dir_of(version) / "latest.txt"
    if not latest.exists():
        return None
    name = latest.read_text(encoding="utf-8").strip()
    return _run_dir_of(version) / name


# ---- 入口 ----
def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="F10 问答效果评测（RAGv4）")
    sub = p.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("check-bank", help="校验题库结构（含词典命中提示）")
    _add_common(pc, bank=True)

    pr = sub.add_parser("run", help="复跑问答链并生成报告与评分模板")
    _add_common(pr, bank=True)
    pr.add_argument("--configs", default="",
                    help="覆盖各套件默认检索配置，如 'dual,text-only'（空=按套件默认）")
    pr.add_argument("--suites", default="",
                    help="运行套件子集，如 'main,long_rewrite'（空=全部）")
    pr.add_argument("--run-id", default="", help="run 目录名（默认 run_时间戳）")
    pr.add_argument("--llm", action="store_true",
                    help="允许使用已配置的真实 LLM（默认强制离线回答器保证可复现）")
    pr.add_argument("--verbose", action="store_true")

    pe = sub.add_parser("report", help="汇总评分并更新报告（默认最近一次 run）")
    _add_common(pe, bank=False)
    pe.add_argument("--run", default="", help="run 目录")
    pe.add_argument("--scores", default="", help="已评分 jsonl 文件")
    return p


def _add_common(p, bank: bool = False) -> None:
    p.add_argument("--version", default="", help="快照/索引版本（默认最新一致版本）")
    if bank:
        p.add_argument("--bank", default="", help="题库 jsonl 路径（默认 data/eval/<version>/questions.jsonl）")


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    if args.cmd == "check-bank":
        return cmd_check_bank(args)
    if args.cmd == "run":
        return asyncio.run(_run_all(args))
    if args.cmd == "report":
        return cmd_report(args)
    print("未知子命令")
    return 2


if __name__ == "__main__":
    sys.exit(main())
