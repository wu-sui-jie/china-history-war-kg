"""评测报告生成（evaluation/report.py）。

报告回答 F10 验收标准：
- 一条命令可重复运行（run 子命令生成 run 目录 + report.md + 评分模板）；
- 报告区分四类失败：答案错 / 引用错 / 检索失败 / 无证据
  （自动归因 verdict + 人工评分合并后给出类别清单）；
- 输出"图谱+文本双通道 vs 纯文本检索"对比结论（客观指标层面）；
- 人工评分结果可导出并逐条复核（grading.py 模板 + 本模块合并进报告）。

数据结构：run 目录 traces.jsonl 每行是一条 (题目 × 配置 × variant) 的评测记录。
variant 表示同一题目的变体：'-'（默认，无筛选变化）与 filter_loss 套件的
filters-on / filters-off。同 qid 的不同 variant 是独立记录，报告需按 variant 展开，
避免同 (qid, config) 覆盖丢失。
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Optional

from evaluation import grading
from evaluation.bank import QuestionBank, load_bank, SUITE_LABEL
from evaluation.grading import ANSWER_LABEL, CITATION_LABEL
from evaluation.metrics import VERDICT_LABEL, classify

_HUMAN_CATEGORY = {
    "answer_wrong": "答案错误（人工评分 incorrect/partial）",
    "citation_wrong": "引用错误（人工评分 unsupported/unrelated）",
    "retrieval_fail": "检索失败（自动归因：相关实体与文本均未召回）",
    "no_evidence": "无证据（拒答/依据不足，含应拒答却作答）",
}

# 无人工评分时的自动候选口径（标题必须标明来源，避免读成"系统没有答案/引用错"）
_AUTO_CATEGORY = {
    "answer_wrong": "答案错候选（自动：answer_miss —— 证据携带但回答未覆盖标注词）",
    "citation_wrong": "引用错候选（自动：fusion_cut —— 相关文本被排序/裁剪挤出）",
    "retrieval_fail": "检索失败（自动归因：相关实体与文本均未召回）",
    "no_evidence": "无证据（拒答/依据不足，含应拒答却作答）",
}

# 评分模板/人工复核使用的默认变体优先级（filter_loss 取"带筛选"的真实使用形态）
_PRIMARY_VARIANTS = ("filters-on", "-")


def _primary_variant(recs: list) -> Optional[dict]:
    for v in _PRIMARY_VARIANTS:
        for r in recs:
            if r.get("variant") == v:
                return r
    return recs[0] if recs else None


def load_run(run_dir) -> dict:
    """加载 run 目录；返回 {meta, bank, recs(扁平记录), by_id, scores...}。"""
    run_dir = Path(run_dir)
    meta_path = run_dir / "meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"run 目录缺少 meta.json: {run_dir}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    bank = None
    if meta.get("bank_path") and Path(meta["bank_path"]).exists():
        bank = load_bank(Path(meta["bank_path"]))

    recs: list[dict] = []
    with open(run_dir / "traces.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            rec["_verdict"] = classify(
                rec["trace"]["auto"], rec.get("answerable", True),
                rec["trace"].get("refusal"),
            )
            recs.append(rec)

    scores = None
    scores_errs: list[str] = []
    sp = meta.get("scores_path")
    if sp and Path(sp).exists():
        scores, scores_errs = grading.read_scores(Path(sp))

    return {
        "meta": meta,
        "bank": bank,
        "recs": recs,
        "scores": scores,
        "scores_errs": scores_errs,
        "run_dir": run_dir,
    }


def _fmt(x: float, nd: int = 2) -> str:
    return f"{x:.{nd}f}"


def _mean(vals: list) -> Optional[float]:
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def _covpct(cases: list[dict], key: str) -> str:
    """某覆盖率维度的逐题均值（百分比）。"""
    vals = [_coverage_ratio(c, key) * 100 for c in cases]
    return "-" if not vals else f"{_mean(vals):.1f}%"


def _coverage_ratio(rec: dict, key: str) -> float:
    auto = rec["trace"]["auto"]
    total = auto.get("expected_total", 0)
    if not total:
        return 0.0
    return auto["counts"].get(key, 0) / total


def _verdict_stats(cases: list[dict]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for c in cases:
        out[c["_verdict"]] += 1
    return dict(out)


def _row(seq) -> str:
    return "| " + " | ".join(str(s) for s in seq) + " |"


def _table(headers: list[str], rows: list[list]) -> str:
    lines = [_row(headers), _row(["---"] * len(headers))]
    for r in rows:
        lines.append(_row(r))
    return "\n".join(lines)


def _category_of(rec: dict, bank=None) -> str:
    """trace 记录里的 category；旧 traces 无该键时用题库回填（T3 修复）。"""
    cat = (rec.get("category") or "").strip()
    if cat:
        return cat
    if bank is not None:
        q = bank.by_id().get(rec.get("question_id"))
        if q is not None:
            return q.category
    return ""


def _short(s: str, n: int) -> str:
    s = s.replace("\n", " ")
    return s[:n] + ("…" if len(s) > n else "")


def _cfg_label(cfg: str) -> str:
    return cfg


def build_report(run: dict, scores_path: Optional[Path] = None,
                 md_out: Optional[Path] = None) -> str:
    """生成 markdown 报告全文（可选写文件）。"""
    meta = run["meta"]
    recs = run["recs"]
    bank: Optional[QuestionBank] = run["bank"]

    if scores_path is not None:
        scores, errs = grading.read_scores(scores_path)
        run["scores"] = scores
        run["scores_errs"] = errs

    L: list[str] = []
    A = L.append

    A(f"# RAGv4（F10）问答效果评测报告")
    A("")
    A(f"- run_id：{meta.get('run_id', run['run_dir'].name)}")
    A(f"- 数据版本：{meta.get('version')}　题库标注版本：{meta.get('annotation_version') or '（未填）'}")
    A(f"- 题库文件：{meta.get('bank_path')}")
    A(f"- 检索配置：{'、'.join(meta.get('configs') or [])}")
    A(f"- 生成时间：{meta.get('generated_at')}　运行命令见 meta.json 的 command")
    if run["scores_errs"]:
        A(f"- ⚠️ 评分文件读取告警：{run['scores_errs']}")
    A("")
    A("## 一、总览")
    A("")
    if bank:
        v = bank.validate()
        A(f"- 题库 {v['summary']['total']} 条；已审核 {v['summary']['reviewed']} 条"
          f"（reviewed=False 为待人工审核草案）；套件分布 {v['summary']['suites']}。")
    A(f"- 本 run 评测记录：{len(recs)} 条（题目 × 配置 × variant）。")
    A("")
    A("配置说明：dual = 生产口径双通道（图谱+文本，AND 优先/OR 兜底）；text-only = "
      "关闭图谱通道；text-only-and / text-only-or = 关键词纯 AND / 纯 OR（专项用）。"
      "variant：'-' 为默认变体；filter_loss 套件额外跑 filters-on（带事件类型筛选）与 "
      "filters-off（不筛选）两种变体做损耗对比。")

    # ---------- 主套件 ----------
    A("")
    A("## 二、主套件客观指标（main，双通道 vs 纯文本）")
    A("")
    cfg_order = _cfg_order(meta)
    table_rows = []
    for cfg in cfg_order:
        cl = _suite_cases(recs, "main", cfg)
        if not cl:
            continue
        vc = _verdict_stats(cl)
        rows = [
            _cfg_label(cfg), len(cl),
            _covpct(cl, "entity_matched"),
            _covpct(cl, "graph_hit") if cfg.startswith("dual") else "-",
            _covpct(cl, "text_topk"),
            _covpct(cl, "fused_text"),
            _covpct(cl, "cite_text"),
            _covpct(cl, "answer"),
            f"{vc.get('ok', 0)}/{len(cl)}",
            _fails_short(vc),
        ]
        table_rows.append(rows)
    if table_rows:
        A(_table(["配置", "n", "实体命中", "图谱命中", "文本top-k召回", "融合文本携带",
                  "引用携带(文本)", "回答覆盖", "ok/n", "失败类别"], table_rows))
    A("")
    A("口径：每列百分比 = 该维度命中标注词数 / 题库标注词总数 的逐题均值。"
      "ok = 自动判定回答覆盖了标注词（正确性仍待人工评分）；"
      "拒答记录的回答覆盖一律记 0（拒答文案只是机械复述问句）。")

    A("")
    A("### 双通道 vs 纯文本：逐题对照（main，默认变体）")
    A("")
    main_dual = _suite_cases(recs, "main", "dual")
    main_text = _suite_cases(recs, "main", "text-only")
    dmap = _by_qid(_default_variant_map(main_dual))
    tmap = _by_qid(_default_variant_map(main_text))
    rows = []
    for qid in sorted(dmap):
        d = dmap[qid]
        t = tmap.get(qid)
        rows.append([
            qid, _category_of(d, bank), _short(d.get("question", ""), 34),
            d["_verdict"], _pctv(_coverage_ratio(d, "answer")),
            t["_verdict"] if t else "-",
            _pctv(_coverage_ratio(t, "answer")) if t else "-",
        ])
    if rows:
        A(_table(["qid", "类别", "问题", "dual", "dual回答覆盖", "text-only",
                  "text-only回答覆盖"], rows))

    A("")
    A("对比结论（自动口径）：")
    A("")
    if dmap:
        both_ok = sum(1 for qid in dmap if qid in tmap
                      and dmap[qid]["_verdict"] == "ok" and tmap[qid]["_verdict"] == "ok")
        only_dual_ok = sum(1 for qid in dmap if qid in tmap
                           and dmap[qid]["_verdict"] == "ok"
                           and tmap[qid]["_verdict"] != "ok")
        only_text_ok = sum(1 for qid in dmap if qid in tmap
                           and dmap[qid]["_verdict"] != "ok"
                           and tmap[qid]["_verdict"] == "ok")
        A(f"- 双通道可覆盖而纯文本不可：{only_dual_ok} 题；纯文本反而覆盖而双通道不可："
          f"{only_text_ok} 题；两者均可：{both_ok} 题。")
        A("- 结论需结合题库类别构成与人工评分解释；自动口径只说明『相关实体/文本是否进入回答』"
          "这一层差异，不能替代人工答案/引用正确性判断。")

    # ---------- 专项套件 ----------
    for suite in ("long_rewrite", "filter_loss", "refusal"):
        _suite_section(A, suite, recs, cfg_order)

    _failure_list(A, recs, bank, run.get("scores"))
    _human_scores_section(A, run, bank)
    _reproduce(A, meta, run["run_dir"])

    text = "\n".join(L)
    if md_out is not None:
        Path(md_out).parent.mkdir(parents=True, exist_ok=True)
        Path(md_out).write_text(text, encoding="utf-8")
    return text


# ---- 记录选取辅助 ----
def _cfg_order(meta) -> list[str]:
    order = meta.get("configs") or []
    return [c for c in order if c in ("dual", "text-only", "text-only-and", "text-only-or")]


def _suite_cases(recs: list, suite: str, cfg: str) -> list[dict]:
    """某个配置下指定套件的"默认变体"记录（列表）。"""
    return list(
        _default_variant_map(
            [r for r in recs if r.get("suite") == suite and r.get("config") == cfg]
        ).values()
    )


def _default_variant_map(recs: list) -> dict[str, dict]:
    """同 (suite, config, qid) 多 variant 时取默认变体（filters-on 优先，其次 '-'）。"""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in recs:
        grouped[r["question_id"]].append(r)
    out = {}
    for qid, rs in grouped.items():
        p = _primary_variant(rs)
        if p:
            out[qid] = p
    return out


def _by_qid(m: dict) -> dict:
    return m


def _pctv(x: float) -> str:
    return f"{x * 100:.0f}%"


def _fails_short(vc: dict[str, int]) -> str:
    parts = []
    for k, v in vc.items():
        if k != "ok" and v:
            parts.append(f"{VERDICT_LABEL.get(k, k)[:12]}×{v}")
    return "；".join(parts) if parts else "-"


def _suite_section(A, suite: str, recs: list, cfg_order: list):
    A("")
    A(f"## 专项：{SUITE_LABEL.get(suite, suite)}")
    A("")
    recs_suite = [r for r in recs if r.get("suite") == suite]
    used_cfgs = [c for c in cfg_order if any(r.get("config") == c for r in recs_suite)]
    if not recs_suite or not used_cfgs:
        A("（本 run 未包含该套件题目）")
        A("")
        return
    # 按 qid 分组；每个 qid 列出全部 (config, variant) 行
    by_q: dict[str, list[dict]] = defaultdict(list)
    for r in recs_suite:
        by_q[r["question_id"]].append(r)
    for qid in sorted(by_q):
        runs = by_q[qid]
        first = runs[0]
        A(f"### {qid}　{first.get('question')}")
        A("")
        rows = []
        for r in sorted(runs, key=lambda x: (x["config"], x.get("variant"))):
            rows.append([
                _cfg_label(r["config"]), r.get("variant", "-"),
                _pctv(_coverage_ratio(r, "text_topk")),
                _pctv(_coverage_ratio(r, "fused_text")),
                _pctv(_coverage_ratio(r, "cite_text")),
                _pctv(_coverage_ratio(r, "answer")),
                r["_verdict"],
            ])
        A(_table(["配置", "variant", "文本top-k召回", "融合文本携带", "引用携带",
                  "回答覆盖", "verdict"], rows))
        A("")
    A("")


def _failure_list(A, recs, bank=None, scores=None):
    """失败样例分桶。

    有已评分（graded）记录时：`answer_wrong`/`citation_wrong` 两桶按**人工评分**
    分桶（与下方"人工判定失败清单"口径一致）；无评分时退回**自动候选**并在标题
    标明来源（T4 修复：此前标题写"人工评分"但内容其实是自动归因）。
    """
    graded = {q: r for q, r in (scores or {}).items() if r.get("graded")}
    A("## 失败样例与归因")
    A("")
    if graded:
        A(f"分桶来源：**人工评分**（已评分 {len(graded)} 条）；"
          "`retrieval_fail` / `no_evidence` 为自动归因补充。")
    else:
        A("分桶来源：**自动归因候选**（本 run 尚无人工评分；`answer_wrong`/"
          "`citation_wrong` 仅代表候选，最终以人工评分为准）。")
    A("")
    A("四类口径：**检索失败**（相关实体与文本都未召回）、**无证据**（拒答，"
      "或应拒答却作答）、**引用错误**（引用 unsupported/unrelated 或不支撑结论）、"
      "**答案错误**（incorrect/partial）。")
    A("")

    dual_map = _default_variant_map([r for r in recs if r.get("config") == "dual"])
    buckets: dict[str, list[tuple]] = defaultdict(list)
    for qid in sorted(dual_map):
        c = dual_map[qid]
        v = c["_verdict"]
        summary = _fail_summary(c)
        if graded:
            r = graded.get(qid)
            ac = (r or {}).get("answer_correctness")
            cc = (r or {}).get("citation_correctness")
            note = (r or {}).get("notes") or ""
            if ac in ("incorrect", "partial"):
                buckets["answer_wrong"].append(
                    (qid, c.get("suite"), c.get("question"), c.get("variant", "-"),
                     f"人工答案={ac}" + (f"；{_short(note, 46)}" if note else "")))
            if cc in ("unsupported", "unrelated"):
                buckets["citation_wrong"].append(
                    (qid, c.get("suite"), c.get("question"), c.get("variant", "-"),
                     f"人工引用={cc}" + (f"；{_short(note, 46)}" if note else "")))
        else:
            key = {
                "fusion_cut": "citation_wrong",
                "answer_miss": "answer_wrong",
            }.get(v)
            if key:
                buckets[key].append((qid, c.get("suite"), c.get("question"),
                                     c.get("variant", "-"), summary))
        if v == "retrieval_fail":
            buckets["retrieval_fail"].append((qid, c.get("suite"), c.get("question"),
                                              c.get("variant", "-"), summary))
        if v in ("refused", "should_refuse_answered"):
            buckets["no_evidence"].append((qid, c.get("suite"), c.get("question"),
                                           c.get("variant", "-"),
                                           summary if v == "refused" else "应拒答却作答"))

    label = _HUMAN_CATEGORY if graded else _AUTO_CATEGORY
    for key, text in label.items():
        bucket = buckets.get(key)
        A(f"### {text}")
        if not bucket:
            A("- （无）")
            continue
        for qid, suite, q, variant, summary in bucket[:10]:
            A(f"- `{qid}`[{suite}/{variant}] {_short(q, 58)}　→ {summary}")
        if len(bucket) > 10:
            A(f"- （共 {len(bucket)} 条，此处仅列前 10；完整明细见同题的人工评分/自动归因表）")
        A("")
    if not graded:
        A("说明：自动归因只圈定候选失败；接入/填写人工评分后本节将改按人工评分分桶"
          "（`report --scores ...`）。")
    A("")


def _fail_summary(rec: dict) -> str:
    t = rec["trace"]
    if t.get("refusal"):
        return "拒答：" + _short(t["refusal"].get("text", ""), 42)
    auto = t["auto"]
    counts = auto["counts"]
    parts = []
    if counts.get("text_topk") == 0:
        parts.append("文本top-k未召回")
    if counts.get("fused_text") == 0:
        parts.append("融合后文本证据未携带")
    if counts.get("answer") == 0:
        parts.append("回答未覆盖标注词")
    return "、".join(parts) or "见 trace"


def _human_scores_section(A, run, bank):
    scores = run.get("scores")
    A("")
    A("## 人工评分结果")
    A("")
    if not scores:
        A(f"本 run 尚无已评分文件。导出模板：`{run['run_dir'].name}/scoring_template.jsonl`，"
          "填写后执行 `report --run <run_dir> --scores <文件>` 汇总。")
        A("")
        return
    stats = grading.scores_stats(scores)
    A(f"- 已评分：{stats['graded_n']} / {stats['total_n']} 条")
    A("")
    A(_table(["维度", "评分", "条数", "含义"],
             [["答案正确性", k, stats["answer_correctness"].get(k, 0), ANSWER_LABEL[k]]
              for k in grading.ANSWER_LEVELS]))
    A("")
    A(_table(["维度", "评分", "条数", "含义"],
             [["引用正确性", k, stats["citation_correctness"].get(k, 0), CITATION_LABEL[k]]
              for k in grading.CITATION_LEVELS]))
    A("")
    A("### 人工判定失败清单")
    A("")
    A("| qid | suite | 问题 | 答案评分 | 引用评分 | 自动verdict | 备注 |")
    A("| --- | --- | --- | --- | --- | --- | --- |")
    dual_map = _default_variant_map([r for r in run["recs"] if r.get("config") == "dual"])
    for qid in sorted(scores.keys()):
        r = scores[qid]
        if not r.get("graded"):
            continue
        ac = r.get("answer_correctness")
        cc = r.get("citation_correctness")
        is_fail = (ac in ("incorrect", "partial")) or (cc in ("unsupported", "unrelated"))
        if not is_fail:
            continue
        v = dual_map.get(qid, {}).get("_verdict", "?")
        note = (r.get("notes") or "")[:40]
        A(f"| {qid} | {r.get('suite','')} | {_short(r.get('question',''),48)} | "
          f"{ac or '-'} | {cc or '-'} | {v} | {note} |")
    A("")
    if bank:
        A("### 应拒答题库核验（unknown_answer）")
        A("")
        rows = []
        for q in bank.items:
            if q.answerable:
                continue
            v = dual_map.get(q.id, {}).get("_verdict", "?")
            rows.append([q.id, _short(q.question, 50), v,
                         "正确拒答" if v == "correct_refusal" else "需复核"])
        A(_table(["qid", "问题", "verdict", "结论"], rows))
        A("")
    A("评分规则见 `docs/RAG_v1/RAGv4-开发说明.md` 附录（correct/partial/incorrect/"
      "unknown_answer；supported/unrelated/unsupported）。")
    A("")


def _reproduce(A, meta, run_dir):
    A("")
    A("## 如何复现 / 更新")
    A("")
    A("```bash")
    A(f"# 重跑本 run（命令见 meta.json；以实际为准）")
    A(meta.get("command", "python -m evaluation.cli run --bank <bank>"))
    A(f"# 人工评分后更新报告")
    A(f"python -m evaluation.cli report --run {run_dir} --scores <scores.jsonl>")
    A("```")
    A("")
