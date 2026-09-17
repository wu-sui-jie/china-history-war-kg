"""人工评分模板导出/读回（evaluation/grading.py）。

F10 验收第 4 条：人工评分结果可导出并逐条复核。评分针对默认（dual）配置的系统回答，
评分模板每行一条题目记录；评分人只需填写 answer_correctness / citation_correctness /
notes / reviewer，再由 `report` 汇总进报告。

答案正确性：correct / partial / incorrect / unknown_answer（应拒答且系统正确拒答）；
引用正确性：supported / unrelated / unsupported；
评分为空 = 尚未评分。导出文件存 run 目录下 scoring_template.jsonl。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

ANSWER_LEVELS = ("correct", "partial", "incorrect", "unknown_answer")
CITATION_LEVELS = ("supported", "unrelated", "unsupported")

ANSWER_LABEL = {
    "correct": "正确",
    "partial": "部分正确",
    "incorrect": "错误",
    "unknown_answer": "应拒答且正确拒答",
}
CITATION_LABEL = {
    "supported": "引用可验证且支持答案",
    "unrelated": "引用存在但与结论无关",
    "unsupported": "关键结论无引用支持",
}


def _citations_text(citations) -> str:
    lines = []
    for c in citations or []:
        lines.append(
            f"[{c.get('index')}] ({c.get('kind')}) {c.get('title')}"
            f"{' — ' + c.get('snippet') if c.get('snippet') else ''}"
        )
    return "\n".join(lines)


def build_template_rows(bank_items_by_id: dict, recs: list,
                        config_label: str = "dual") -> list[dict]:
    """由某配置下的评测记录生成待评分行。

    同一 (qid, 配置) 存在多个 variant（filter_loss 的 filters-on/filters-off）时，
    取 filters-on（带筛选的真实使用形态）为主，其次 '-'；避免重复行。
    """
    rows = []
    recs = [r for r in recs if r.get("config") == config_label]
    grouped: dict[str, list[dict]] = {}
    for r in recs:
        grouped.setdefault(r["question_id"], []).append(r)
    for qid in sorted(grouped):
        q = bank_items_by_id.get(qid)
        if q is None:
            continue
        rec = _primary(recs=grouped[qid])
        if rec is None:
            continue
        trace = rec["trace"]
        rows.append({
            "qid": qid,
            "suite": q.suite,
            "category": q.category,
            "question": q.question,
            "variant": rec.get("variant", "-"),
            "answerable": q.answerable,
            "expected_entities": q.expected_entities,
            "gold_notes": q.gold_notes,
            "source_ref": q.source_ref,
            "system_answer": trace["answer"]["text"],
            "finish_reason": trace["answer"]["finish_reason"],
            "citations_text": _citations_text(trace["citations"]),
            "answer_correctness": "",
            "citation_correctness": "",
            "notes": "",
            "reviewer": "",
            "graded": False,
        })
    return rows


def _primary(recs: list) -> Optional[dict]:
    """同一题目的多 variant 取主记录（filters-on 优先，其次 '-'）。"""
    for v in ("filters-on", "-"):
        for r in recs:
            if r.get("variant") == v:
                return r
    return recs[0] if recs else None


def write_template(rows: list[dict], out: Path) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return out


def read_scores(path: Path) -> tuple[dict, list[str]]:
    """读回已评分文件。返回 ({qid: record}, errors)。"""
    scores: dict = {}
    errors: list[str] = []
    path = Path(path)
    if not path.exists():
        return scores, ["评分文件不存在: %s" % path]
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                errors.append(f"第 {i} 行 JSON 非法: {e}")
                continue
            qid = rec.get("qid", "")
            ac = (rec.get("answer_correctness") or "").strip()
            cc = (rec.get("citation_correctness") or "").strip()
            if ac and ac not in ANSWER_LEVELS:
                errors.append(f"{qid} answer_correctness 非法: {ac}")
                continue
            if cc and cc not in CITATION_LEVELS:
                errors.append(f"{qid} citation_correctness 非法: {cc}")
                continue
            rec["graded"] = bool(ac) or bool(cc) or bool(rec.get("reviewer"))
            scores[qid] = rec
    return scores, errors


def scores_stats(scores: dict) -> dict:
    graded = {q: r for q, r in scores.items() if r.get("graded")}
    out: dict = {"graded_n": len(graded), "total_n": len(scores)}
    for key, levels, label in (("answer_correctness", ANSWER_LEVELS, "答案正确性"),
                               ("citation_correctness", CITATION_LEVELS, "引用正确性")):
        out[key] = {lv: sum(1 for r in graded.values() if r.get(key) == lv)
                    for lv in levels}
        out[key + "_label"] = label
    return out
