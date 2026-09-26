"""F08 演示示例题清单生成（RAGv5 T1）。

从**已审核题库 + 人工评分**里筛出适合对外演示的题，并（可选）逐题实测响应表现。

筛选规则（全部可追溯、可复现）：
1. `reviewed=True`（已审核）；
2. 人工评分 `answer_correctness != "incorrect"`（v4 的评分口径，见 evaluation/grading.py）；
3. `answerable=True`（应拒答题不作为演示题）；
4. 套件默认只取 `main`（`long_rewrite`/`filter_loss`/`refusal` 是专项题，不作为门面）；
5. **实测筛题（--measure）**：每题走真实链路（含向量/hybrid 检索与真实 LLM）跑一遍，记录
   首 thinking / 首正文时延与是否被 `max_tokens` 截断；截断或首正文超阈值的题默认剔除
   —— 推理模型的时延与截断率在题目之间差异极大（实测 4.76 s vs 13.11 s），不实测就会踩雷。

输出：`data/eval/<v>/demo_examples.json`（入 Git、可人工复核），字段口径见
[../docs/data-contract.md](../docs/data-contract.md) 与 [../docs/features.md](../docs/features.md) 第八节。

用法：
  python scripts/gen_demo_examples.py --version 20260915_v1 --run v5_coords_v1
  python scripts/gen_demo_examples.py --measure --max-examples 12      # 实测后再定清单

版本口径：
- `--version` 缺省取 RAG_ACTIVE_VERSION / 最新一致版本，**不再硬编码**某个历史版本；
- `--run` 缺省读该版本 `runs/latest.txt`，找不到就报错而不是回落到别的版本；
- run 的 meta.json 若声明了 version，必须与目标版本一致（除非显式 --allow-run-version-mismatch），
  否则"示例时延来自另一份数据"会被写进清单，展示出来就是误导。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_settings            # noqa: E402
from contracts.request import QueryRequest          # noqa: E402
from lib.json_io import write_json       # noqa: E402
from server.runtime import build_runtime            # noqa: E402

# 类别 → 展示用中文标签（F08 要求"能力分类标签"）
CATEGORY_LABEL = {
    "single_entity": "实体介绍",
    "relation": "关系型",
    "background": "背景型",
    "timeline": "时间线型",
    "event_event": "事件关联",
    "comparison": "对比型",
    "other": "其他",
}


def _load_bank(path: Path) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _load_scores(run_dir: Path) -> dict[str, dict]:
    scores: dict[str, dict] = {}
    path = run_dir / "scores.jsonl"
    if not path.exists():
        return scores
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            qid = rec.get("qid") or rec.get("question_id")
            if qid:
                scores[qid] = rec
    return scores


def _capability_from_traces(run_dir: Path, qid: str) -> tuple[str, dict]:
    """从 baseline trace 推能力标注：有图谱证据 → graph；有文本证据 → text。"""
    path = run_dir / "traces.jsonl"
    if not path.exists():
        return "", {}
    best = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("question_id") != qid or rec.get("config") != "dual":
                continue
            if rec.get("variant") not in ("-", None, "filters-on"):
                continue
            if rec.get("variant") == "filters-on":
                continue
            best = rec
            break
    if best is None:
        return "", {}
    tr = best.get("trace") or {}
    g = (tr.get("graph") or {}).get("n") or 0
    t = (tr.get("text") or {}).get("n") or 0
    cap = "both" if (g and t) else ("graph" if g else ("text" if t else ""))
    return cap, {"graph_min": 1 if g else 0, "text_min": 1 if t else 0}


async def _measure_one(rt, question: str, sid: str, timeout_s: float = 90.0) -> dict:
    """走真实链路跑一遍，返回时延与截断信息（失败不抛，记 error）。"""
    from server.sse import run_query

    req = QueryRequest.from_dict({"session_id": sid, "question": question})

    async def _consume() -> dict:
        t0 = time.time()
        t_think = t_answer = None
        n_think = n_answer = 0
        finish = ""
        model_used = ""
        async for frame in run_query(rt, req):
            payload = json.loads(frame[len("data: "):])
            etype = payload.get("type")
            if etype == "thinking":
                if t_think is None:
                    t_think = time.time() - t0
                n_think += 1
            elif etype == "answer":
                if t_answer is None:
                    t_answer = time.time() - t0
                n_answer += 1
            elif etype == "done":
                done = payload.get("data") or {}
                finish = done.get("finish_reason", "")
                # 记录实际使用的模型：血缘据此判断"demo 是否真的来自模型"——
                # 只写 measurement_mode 声明还不够
                model_used = str(done.get("model_used") or "")
        return {
            "first_thinking_ms": int(t_think * 1000) if t_think else None,
            "first_answer_ms": int(t_answer * 1000) if t_answer else None,
            "total_ms": int((time.time() - t0) * 1000),
            "answer_frames": n_answer,
            "thinking_frames": n_think,
            "finish_reason": finish,
            "model_used": model_used,
            "truncated": bool(getattr(rt.generate, "last_truncated", False)),
            "usage": rt.generate.last_usage,
        }

    try:
        return await asyncio.wait_for(_consume(), timeout=timeout_s)
    except asyncio.TimeoutError:
        return {"error": f"timeout>{timeout_s:.0f}s"}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


async def _measure_all(rt, items: list[dict], logger=None) -> None:
    for i, it in enumerate(items, start=1):
        res = await _measure_one(rt, it["question"], f"demo-measure-{it['id']}")
        it["measured"] = res
        if logger:
            if "error" in res:
                logger.info(f"[{i}/{len(items)}] {it['id']} 实测失败: {res['error']}")
            else:
                logger.info(f"[{i}/{len(items)}] {it['id']} 首思考 {res['first_thinking_ms']} ms / "
                            f"首正文 {res['first_answer_ms']} ms / 截断={res['truncated']} / "
                            f"finish={res['finish_reason']}")


def _pick_balanced(cands: list[dict], limit: int) -> list[dict]:
    """按类别轮转挑选，保证能力覆盖多样（F08 验收：覆盖图谱与文本两类能力）。"""
    groups: dict[str, list[dict]] = defaultdict(list)
    for c in cands:
        groups[c["category"]].append(c)
    for v in groups.values():
        v.sort(key=lambda x: (x.get("measured") or {}).get("first_answer_ms") or 10 ** 9)
    picked: list[dict] = []
    order = sorted(groups, key=lambda k: -len(groups[k]))
    while len(picked) < limit and any(groups.values()):
        for cat in order:
            if groups[cat] and len(picked) < limit:
                picked.append(groups[cat].pop(0))
    return picked


def main() -> int:
    ap = argparse.ArgumentParser(description="F08 演示示例题清单生成")
    ap.add_argument("--version", default="",
                    help="快照/题库版本（缺省取 RAG_ACTIVE_VERSION，未配置则取最新一致版本）")
    ap.add_argument("--run", default="", help="评分与 trace 来源 run（缺省读该版本 runs/latest.txt）")
    ap.add_argument("--allow-run-version-mismatch", action="store_true",
                    help="允许 run 的 meta.version 与目标版本不一致（默认拒绝，避免元数据串版本）")
    ap.add_argument("--suites", default="main", help="允许的套件（逗号分隔，默认 main）")
    ap.add_argument("--max-examples", type=int, default=12, help="清单条数上限（默认 12）")
    ap.add_argument("--measure", action="store_true",
                    help="逐题实测（真实 LLM + 当前 TEXT_MODE），记录时延与是否截断")
    ap.add_argument("--max-first-answer-ms", type=int, default=9000,
                    help="实测后剔除首正文超过该值的题（默认 9000 ms）")
    ap.add_argument("--keep-truncated", action="store_true", help="保留被截断的题（默认剔除）")
    ap.add_argument("--out", default="", help="输出路径（默认 data/eval/<v>/demo_examples.json）")
    args = ap.parse_args()

    settings = get_settings()
    version = args.version or settings.active_version
    if not version:
        from lib.versions import list_versions

        snaps = list_versions(settings.snapshot_dir)
        if not snaps:
            print(f"无可用快照: {settings.snapshot_dir}；请显式传 --version")
            return 2
        version = snaps[0]
    run_id = args.run
    runs_root = settings.data_dir / "eval" / version / "runs"
    if not run_id:
        latest = runs_root / "latest.txt"
        if latest.exists():
            run_id = latest.read_text(encoding="utf-8").strip()
        if not run_id:
            available = sorted(p.name for p in runs_root.iterdir()) if runs_root.is_dir() else []
            print(f"未指定 --run，且 {runs_root}\\latest.txt 不存在；"
                  f"可用 run: {available or '（无）'}")
            return 2
    print(f"目标版本 {version}（{'显式固定' if args.version else '按活跃版本/最新一致版本解析'}）"
          f" | 来源 run {run_id}")

    bank_path = settings.data_dir / "eval" / version / "questions.jsonl"
    run_dir = runs_root / run_id
    if not bank_path.exists():
        print(f"题库不存在: {bank_path}")
        return 2
    if not run_dir.exists():
        print(f"run 目录不存在: {run_dir}（需要其中的 scores.jsonl 与 traces.jsonl）")
        return 2
    run_meta_path = run_dir / "meta.json"
    if run_meta_path.exists() and not args.allow_run_version_mismatch:
        try:
            run_meta = json.loads(run_meta_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            run_meta = {}
        run_version = str(run_meta.get("version") or "")
        if run_version and run_version != version:
            print(f"run 的 meta.version={run_version} 与目标版本 {version} 不一致："
                  f"示例的评分/时延来自另一份数据。"
                  f"请换用该版本的 run，或显式加 --allow-run-version-mismatch 认可这一事实。")
            return 3

    suites = {s.strip() for s in args.suites.split(",") if s.strip()}
    bank = _load_bank(bank_path)
    scores = _load_scores(run_dir)
    total = len(bank)

    dropped: Counter = Counter()
    cands: list[dict] = []
    for q in bank:
        qid = q["id"]
        if q.get("suite") not in suites:
            dropped["非目标套件"] += 1
            continue
        if not q.get("reviewed"):
            dropped["未审核"] += 1
            continue
        if not q.get("answerable", True):
            dropped["应拒答题"] += 1
            continue
        sc = scores.get(qid)
        if not sc:
            dropped["无评分记录"] += 1
            continue
        if sc.get("answer_correctness") == "incorrect":
            dropped["评分为 incorrect"] += 1
            continue
        cap, expect = _capability_from_traces(run_dir, qid)
        cands.append({
            "id": qid,
            "question": q["question"],
            "category": q["category"],
            "category_label": CATEGORY_LABEL.get(q["category"], q["category"]),
            "capability": cap or "text",
            "expect": expect or {"graph_min": 0, "text_min": 1},
            "baseline_score": sc.get("answer_correctness"),
            "cited": (sc.get("citation_correctness") or ""),
        })

    print(f"题库 {total} 条 → 候选 {len(cands)} 条；剔除：{dict(dropped)}")

    measured_note = "未实测"
    measurement_mode = None
    if args.measure:
        rt = build_runtime(settings, version)
        if not rt.generate.llm.available:
            print("⚠ 未配置可用 LLM，实测只反映离线回答器（无法评估真实时延/截断）")
        # 真实模型 / 离线摘要回答器必须显式区分：
        # demo 的时延与措辞来自哪条链路，直接决定它能不能当作"真实模型"证据
        measurement_mode = "real_llm" if rt.generate.llm.available else "offline"
        print(f"开始实测 {len(cands)} 条（TEXT_MODE={settings.text_mode}，模型={settings.llm_model}，"
              f"measurement_mode={measurement_mode}）…")
        asyncio.run(_measure_all(rt, cands))
        before = len(cands)
        ok = [c for c in cands if "error" not in (c.get("measured") or {})]
        dropped["实测失败"] += before - len(ok)
        cands = ok
        if not args.keep_truncated:
            trunc = [c for c in cands if (c.get("measured") or {}).get("truncated")]
            dropped["实测被截断"] += len(trunc)
            cands = [c for c in cands if not (c.get("measured") or {}).get("truncated")]
        slow = [c for c in cands
                if ((c.get("measured") or {}).get("first_answer_ms") or 0) > args.max_first_answer_ms]
        dropped[f"首正文>{args.max_first_answer_ms}ms"] += len(slow)
        cands = [c for c in cands if c not in slow]
        measured_note = f"已实测（阈值 {args.max_first_answer_ms} ms）"

    if not cands:
        print("没有可用示例题（候选为空）：不写文件，避免把空清单当成正常结果上线")
        return 4
    picked = _pick_balanced(cands, args.max_examples)
    if not picked:
        print("按类别轮转后仍无示例题：不写文件")
        return 4
    out_path = Path(args.out) if args.out else \
        settings.data_dir / "eval" / version / "demo_examples.json"
    payload = {
        "version": version,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source_run": run_id,
        # 实测链路：real_llm（真实模型）/ offline（离线摘要回答器）/ null（未实测）
        # 服务端校验与 lineage 都读它；缺字段等于"测了什么"无从判断（B3）
        "measurement_mode": measurement_mode,
        "filter": {
            "reviewed": True,
            "exclude_answer_correctness": ["incorrect"],
            "answerable": True,
            "suites": sorted(suites),
            "measured": bool(args.measure),
            "max_first_answer_ms": args.max_first_answer_ms if args.measure else None,
            "exclude_truncated": bool(args.measure and not args.keep_truncated),
        },
        "counts": {"bank_total": total, "candidates": len(cands),
                   "selected": len(picked), "dropped": dict(dropped)},
        "notes": measured_note,
        "examples": picked,
    }
    write_json(out_path, payload)
    print(f"\n清单已写入: {out_path}")
    print(f"选中 {len(picked)} 条：")
    for e in picked:
        m = e.get("measured") or {}
        extra = (f"（首思考 {m.get('first_thinking_ms')} ms / 首正文 {m.get('first_answer_ms')} ms）"
                 if m.get("first_answer_ms") else "")
        print(f"  [{e['id']}] {e['category_label']:<6} {e['capability']:<6} {e['question']}{extra}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
