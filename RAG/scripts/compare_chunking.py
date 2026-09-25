"""T8 分块参数对比实验：同一快照、不同切分参数 → 索引变体 → 离线评测 → 对照报告。

回答"CHUNK_MAX_CHARS / CHUNK_OVERLAP_CHARS 取多少合适"（RAGv1 起从未做过对照实验）。

用法：
  python scripts/compare_chunking.py --sizes 500/100,800/80,1200/120
  python scripts/compare_chunking.py --sizes 500/100 --skip-build     # 复用已建好的索引变体
  python scripts/compare_chunking.py --sizes 500/100 --skip-eval      # 只统计片段、不跑评测

约定：
- 与配置默认值相同的参数组视为**基线**，直接复用既有索引目录 data/index/<版本>/（不重建、不覆盖）；
  其余各组构建"索引变体" data/index/<版本>_c<max>o<overlap>/（data/index/ 已被 .gitignore 忽略）。
- 评测强制离线回答器（不接 LLM）保证可复现；各组跑同一套件与同一批检索配置。
- 指标从 traces 复算，口径与 evaluation/report.py 的"主套件客观指标"表一致
  （逐题 = 命中标注词数 / 标注词总数，再对题目取均值；拒答记录回答覆盖记 0）。
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_settings  # noqa: E402
from evaluation import report as report_mod  # noqa: E402
from lib.json_io import read_json  # noqa: E402
from server.runtime import resolve_version  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# 覆盖率维度（键名与 evaluation/metrics.py 的 auto.counts 对齐）
COVERAGE_KEYS = [
    ("entity_matched", "实体命中"),
    ("graph_hit", "图谱命中"),
    ("text_topk", "文本top-k召回"),
    ("fused_text", "融合文本携带"),
    ("cite_text", "引用携带(文本)"),
    ("answer", "回答覆盖"),
]
_SENT_END = "。！？!?；;"


def _parse_sizes(spec: str) -> list[tuple[int, int]]:
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        mc, ov = part.split("/")
        out.append((int(mc), int(ov)))
    if not out:
        raise SystemExit("--sizes 为空，示例：--sizes 500/100,800/80,1200/120")
    return out


def _label(mc: int, ov: int) -> str:
    return f"{mc}/{ov}"


def _suffix(mc: int, ov: int) -> str:
    return f"_c{mc}o{ov}"


def _run(cmd: list[str], *, quiet: bool = False) -> int:
    if not quiet:
        print("  $ " + " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(ROOT), text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if proc.returncode != 0:
        print(proc.stdout[-3000:])
    return proc.returncode


def _chunk_stats(index_dir: Path) -> dict:
    """从索引产物统计片段规模与长度分布（分块质量的第一手证据）。"""
    lengths: list[int] = []
    ends: Counter = Counter()
    by_type: Counter = Counter()
    chunks_path = index_dir / "chunks.jsonl"
    with open(chunks_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            c = json.loads(line)
            text = c.get("text") or ""
            lengths.append(len(text))
            by_type[c.get("chunk_type") or "?"] += 1
            ends["sentence" if text.strip()[-1:] in tuple(_SENT_END) else "other"] += 1
    total = len(lengths)
    manifest = read_json(index_dir / "manifest.json") if (index_dir / "manifest.json").exists() else {}
    return {
        "total": total,
        "by_type": dict(by_type),
        "mean": round(sum(lengths) / total, 1) if total else 0,
        "median": statistics.median(lengths) if lengths else 0,
        "short_lt10": sum(1 for n in lengths if n < 10),
        "end_sentence_pct": round(100.0 * ends["sentence"] / total, 1) if total else 0,
        "chunk_params": manifest.get("chunk_params") or {},
        "built_at": manifest.get("built_at") or "",
    }


def _build_variant(version: str, mc: int, ov: int, suffix: str) -> int:
    return _run([
        sys.executable, "scripts/build_index.py",
        "--version", version,
        "--index-suffix", suffix,
        "--no-embeddings",
        "--chunk-max-chars", str(mc),
        "--chunk-overlap-chars", str(ov),
    ])


def _run_eval(index_name: str, suites: str, configs: str, run_id: str) -> int:
    return _run([
        sys.executable, "scripts/run_evaluation.py", "run",
        "--version", index_name,
        "--suites", suites,
        "--configs", configs,
        "--run-id", run_id,
    ])


def _primary_cases(recs: list, suite: str, cfg: str) -> list[dict]:
    """某配置下指定套件的"默认变体"记录（与 report.py 的取法一致）。"""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in recs:
        if r.get("suite") == suite and r.get("config") == cfg:
            grouped[r["question_id"]].append(r)
    out = []
    for _qid, rs in grouped.items():
        picked = None
        for v in ("filters-on", "-"):
            for r in rs:
                if r.get("variant") == v:
                    picked = r
                    break
            if picked:
                break
        out.append(picked or rs[0])
    return out


def _coverage_pct(cases: list[dict], key: str) -> float | None:
    vals = []
    for rec in cases:
        auto = rec["trace"]["auto"]
        total = auto.get("expected_total", 0)
        if not total:
            continue
        vals.append(auto["counts"].get(key, 0) / total)
    return (sum(vals) / len(vals) * 100) if vals else None


def _elapsed_ms(cases: list[dict], stage: str) -> float | None:
    vals = []
    for rec in cases:
        ms = (rec["trace"].get("stage_ms") or {}).get(stage)
        if ms is not None:
            vals.append(ms)
    return (sum(vals) / len(vals)) if vals else None


def _fmt(x, nd: int = 1, suffix: str = "") -> str:
    return "-" if x is None else f"{x:.{nd}f}{suffix}"


def _aggregate(run_dir: Path, suite: str, configs: list[str]) -> dict:
    run = report_mod.load_run(run_dir)
    recs = run["recs"]
    out: dict[str, dict] = {}
    for cfg in configs:
        cases = _primary_cases(recs, suite, cfg)
        if not cases:
            continue
        row = {"n": len(cases), "coverage": {}, "verdicts": Counter()}
        for key, _label_cn in COVERAGE_KEYS:
            row["coverage"][key] = _coverage_pct(cases, key)
        for c in cases:
            row["verdicts"][c["_verdict"]] += 1
        row["elapsed_text_ms"] = _elapsed_ms(cases, "text")
        row["elapsed_total_ms"] = _elapsed_ms(cases, "total") or sum(
            _elapsed_ms(cases, s) or 0 for s in ("understand", "graph", "text", "fusion", "generate")
        )
        out[cfg] = row
    return {"configs": out, "recs": recs, "meta": run["meta"]}


def _report_md(version: str, rows: list[dict], suites: str, configs: list[str],
               base_label: str) -> str:
    L: list[str] = []
    A = L.append
    A("# RAGv5 分块参数对比实验报告（T8）")
    A("")
    A(f"- 快照版本：{version}　生成时间：{time.strftime('%Y-%m-%dT%H:%M:%S')}")
    A(f"- 评测口径：套件 `{suites}`、配置 `{configs}`、强制离线回答器（不接 LLM，保证可复现）")
    A(f"- 基线组：`{base_label}`（复用既有索引目录，未重建）")
    A("- 复现命令：`python scripts/compare_chunking.py --sizes "
      + ",".join(r["label"] for r in rows) + "`")
    A("")
    A("## 一、片段统计（分块质量第一手证据）")
    A("")
    A("| 参数(字/重叠) | 索引目录 | 片段总数 | raw | event_card | evidence | 平均长度 | 中位长度 | <10字 | 以句末标点结尾 |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in rows:
        s = r["stats"]
        t = s["by_type"]
        A(f"| {r['label']}{'（基线）' if r['label'] == base_label else ''} | `{r['index_name']}` | "
          f"{s['total']} | {t.get('raw', 0)} | {t.get('event_card', 0)} | {t.get('evidence', 0)} | "
          f"{s['mean']} | {s['median']} | {s['short_lt10']} | {s['end_sentence_pct']}% |")
    A("")
    A("## 二、客观指标（主套件）")
    A("")
    headers = ["参数", "配置", "n"] + [cn for _k, cn in COVERAGE_KEYS] + ["ok/n", "平均检索耗时(ms)"]
    A("| " + " | ".join(headers) + " |")
    A("| " + " | ".join(["---"] * len(headers)) + " |")
    for r in rows:
        agg = r.get("agg")
        if not agg:
            continue
        for cfg in configs:
            row = agg["configs"].get(cfg)
            if not row:
                continue
            ok = row["verdicts"].get("ok", 0)
            cells = [r["label"], cfg, str(row["n"])]
            cells += [_fmt(row["coverage"].get(k), 1, "%") for k, _cn in COVERAGE_KEYS]
            cells += [f"{ok}/{row['n']}", _fmt(row["elapsed_text_ms"], 0)]
            A("| " + " | ".join(cells) + " |")
    A("")
    A("口径说明：百分比 = 逐题（命中标注词数 / 标注词总数）的均值，与 `report.md` 主套件表一致；")
    A("拒答记录的回答覆盖记 0；`ok` = 自动判定覆盖了标注词（正确性仍以人工评分为准）。")
    A("")
    A("## 三、成本放大（片段数 → 向量化与存储）")
    A("")
    A("| 参数 | 片段总数 | 相对基线 | 向量化请求数(batch=10) | 1024 维向量体积(约) |")
    A("| --- | --- | --- | --- | --- |")
    base_total = next((r["stats"]["total"] for r in rows if r["label"] == base_label),
                      rows[0]["stats"]["total"] if rows else 0)
    for r in rows:
        total = r["stats"]["total"]
        ratio = f"{total / base_total:.2f}×" if base_total else "-"
        reqs = (total + 9) // 10
        mb = total * 1024 * 4 / 1024 / 1024
        A(f"| {r['label']} | {total} | {ratio} | 约 {reqs} 次 | 约 {mb:.0f} MB |")
    A("")
    A("## 四、结论")
    A("")
    A("（待判定：切换或保持。判定依据 = 客观指标是否退化 + 片段数/成本是否显著膨胀；")
    A("人工结论由评审在下方补充，本报告只给事实。）")
    A("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="T8 分块参数对比实验")
    ap.add_argument("--sizes", default="500/100,800/80,1200/120",
                    help="参数组，逗号分隔（格式 最大字数/重叠字数）")
    ap.add_argument("--version", default="", help="快照版本（默认最新一致版本）")
    ap.add_argument("--suites", default="main", help="评测套件（默认 main）")
    ap.add_argument("--configs", default="dual,text-only", help="评测配置（默认 dual,text-only）")
    ap.add_argument("--skip-build", action="store_true", help="不构建，复用已有索引变体")
    ap.add_argument("--skip-eval", action="store_true", help="不跑评测，只统计片段")
    ap.add_argument("--report", default="", help="报告输出路径（默认 data/eval/<版本>/chunk_exp_report.md）")
    args = ap.parse_args()

    settings = get_settings()
    version, _snap_dir, _index_dir = resolve_version(settings, args.version or None)
    configs = [c.strip() for c in args.configs.split(",") if c.strip()]
    base_params = (settings.chunk_max_chars, settings.chunk_overlap_chars)
    print(f"快照版本 {version}；基线切分参数 {base_params[0]}/{base_params[1]}；"
          f"待比较 {args.sizes}")

    rows: list[dict] = []
    for mc, ov in _parse_sizes(args.sizes):
        is_base = (mc, ov) == base_params
        suffix = "" if is_base else _suffix(mc, ov)
        index_name = f"{version}{suffix}"
        index_dir = settings.index_dir / index_name
        label = _label(mc, ov)
        print(f"\n[{label}] 索引 {index_name}"
              f"{'（基线，复用既有索引）' if is_base else ''}")

        if not index_dir.exists():
            if args.skip_build:
                print(f"  索引不存在且指定了 --skip-build，跳过该组：{index_dir}")
                continue
            print("  构建索引变体…")
            t0 = time.time()
            if _build_variant(version, mc, ov, suffix) != 0:
                print("  构建失败，跳过该组")
                continue
            print(f"  构建完成，耗时 {time.time() - t0:.1f}s")
        else:
            print("  索引已存在，复用")

        row = {"label": label, "index_name": index_name, "index_dir": index_dir,
               "stats": _chunk_stats(index_dir)}
        print(f"  片段 {row['stats']['total']}（平均 {row['stats']['mean']} 字 / "
              f"中位 {row['stats']['median']} / <10字 {row['stats']['short_lt10']}）")

        if not args.skip_eval:
            run_id = f"chunkexp_{label.replace('/', 'o')}"
            print(f"  评测 → run-id {run_id}")
            if _run_eval(index_name, args.suites, ",".join(configs), run_id) != 0:
                print("  评测失败，本组无指标")
                row["agg"] = None
            else:
                run_dir = settings.data_dir / "eval" / version / "runs" / run_id
                row["agg"] = _aggregate(run_dir, args.suites, configs)
                row["run_dir"] = run_dir
        rows.append(row)

    if not rows:
        print("无可用参数组")
        return 2

    print("\n==== 汇总 ====")
    for r in rows:
        line = f"{r['label']}: 片段 {r['stats']['total']}"
        agg = r.get("agg")
        if agg:
            for cfg in configs:
                row = agg["configs"].get(cfg)
                if row:
                    line += (f" | {cfg} 文本召回 {_fmt(row['coverage'].get('text_topk'), 1, '%')}"
                             f" 回答覆盖 {_fmt(row['coverage'].get('answer'), 1, '%')}"
                             f" ok {row['verdicts'].get('ok', 0)}/{row['n']}")
        print(line)

    out_path = Path(args.report) if args.report else \
        settings.data_dir / "eval" / version / "chunk_exp_report.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_report_md(version, rows, args.suites, configs,
                                   _label(*base_params)), encoding="utf-8")
    print(f"\n报告已写入: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
