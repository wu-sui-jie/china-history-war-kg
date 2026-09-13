# -*- coding: utf-8 -*-
"""审核辅助脚本（一次性工具，不修改任何输入文件）。

功能：
1. 把 scores_review_*.csv 的 citations_text 解析成结构化清单（类型 + 内容摘要）；
2. 逐条比对知识库快照（relations.json / event_cards.json / 原文），判断引用是否真实可查。
输出：_citation_audit.txt（供人工审阅），不写回任何输入文件。
"""
import json
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[4]          # RAG/
REVIEW = Path(__file__).resolve().parent            # .../review/
SNAP = ROOT / "data" / "snapshot" / "20260904_v2"

# ---------- 载入知识库 ----------
relations = json.loads((SNAP / "relations.json").read_text(encoding="utf-8"))
event_cards = json.loads((SNAP / "event_cards.json").read_text(encoding="utf-8"))
entities = json.loads((SNAP / "entities.json").read_text(encoding="utf-8"))
evidence = json.loads((SNAP / "evidence_corpus.json").read_text(encoding="utf-8"))
ev_ids = {}
_ev_pool = evidence if isinstance(evidence, list) else list(evidence.values())
for e in _ev_pool:
    if isinstance(e, dict):
        for k in ("evidence_id", "id", "rel_ev_id", "source"):
            if e.get(k):
                ev_ids[str(e[k])] = e
                break

# (legacy_table, source_row_id) -> 行
rel_by_row = {}
for r in relations:
    rel_by_row.setdefault((r.get("legacy_table"), r.get("source_row_id")), []).append(r)
# (source_name, relation, target_name) -> 行
rel_by_triple = {}
for r in relations:
    rel_by_triple.setdefault((r.get("source_name"), r.get("relation"), r.get("target_name")), []).append(r)

print("relations:", len(relations), "event_cards:", len(event_cards), file=sys.stderr)
if isinstance(event_cards, dict):
    ec_keys = list(event_cards.keys())[:5]
    print("event_cards is dict, sample keys:", ec_keys, file=sys.stderr)
else:
    print("event_cards[0] keys:", sorted(event_cards[0].keys()), file=sys.stderr)

# 原文
texts = {}
for p in (ROOT / "data" / "raw" / "source_texts").glob("*.txt"):
    texts[p.stem] = p.read_text(encoding="utf-8", errors="ignore")

# ---------- 解析引用 ----------
CITE_RE = re.compile(r"\[(\d+)\]\s*\((\w+)\)\s*(.*?)(?=\[\d+\]\s*\(\w+\)|$)", re.S)
TRIPLE_RE = re.compile(r"^(.*?)\s*[—\-–]\s*(.*?)\s*→\s*(.*)$")


def parse_citations(raw):
    out = []
    for m in CITE_RE.finditer(str(raw)):
        idx, ctype, body = m.group(1), m.group(2), m.group(3).strip()
        out.append((int(idx), ctype, body))
    return out


def check_triple(body):
    """返回 (状态, 说明)"""
    m = TRIPLE_RE.match(body)
    if not m:
        return "解析失败", body[:60]
    s, rel, t = (x.strip() for x in m.groups())
    rel = rel.strip("「」")
    hit = rel_by_triple.get((s, rel, t))
    if hit:
        return "命中", f"{hit[0]['legacy_table']}:{hit[0]['source_row_id']}"
    # 反向
    hit = rel_by_triple.get((t, rel, s))
    if hit:
        return "命中(反向)", f"{hit[0]['legacy_table']}:{hit[0]['source_row_id']}"
    # 名称/关系宽松匹配
    cands = [(k, v) for k, v in rel_by_triple.items()
             if (k[0] == s and k[2] == t) or (k[0] == t and k[2] == s)]
    if cands:
        k, v = cands[0]
        return "关系名不符", f"实际关系={k[1]!r} @{v[0]['legacy_table']}:{v[0]['source_row_id']}"
    return "未命中", f"{s}|{rel}|{t}"


def check_event_card(body):
    title = body.split("—")[0].split("：")[0].strip()
    pool = event_cards if isinstance(event_cards, list) else list(event_cards.values())
    names = {c.get("name") or c.get("event_name") or c.get("title") for c in pool}
    if title in names:
        return "命中", title
    # 名称包含
    for n in names:
        if n and (title and title in n):
            return "近似命中", n
    return "未命中", title


def check_raw(body):
    # 形如 "doc01_中国历代战争简史 — 片段"
    if "—" in body:
        doc, snip = body.split("—", 1)
    else:
        doc, snip = body[:40], body
    doc = doc.strip()
    snip = snip.strip()
    for stem, text in texts.items():
        if doc and doc[:6] in stem:
            # 取多个位置的片段尝试（引用片段是原文切片，首尾可能带省略）
            for start in (0, 4, 8, len(snip) // 3, len(snip) // 2):
                key = snip[start:start + 20]
                if len(key) >= 8 and key in text:
                    return "命中", stem
            return "片段未在原文字面命中", f"{stem} | {snip[:30]}"
    return "文档未识别", doc


def check_evidence(body):
    # 形如 "rel_ev_1954 — 片段"
    if "—" in body:
        eid, snip = body.split("—", 1)
    else:
        eid, snip = body[:40], body
    eid = eid.strip()
    snip = snip.strip()
    hit = ev_ids.get(eid)
    if hit is None:
        return "未命中", eid
    blob = json.dumps(hit, ensure_ascii=False)
    key = snip[4:20] if len(snip) > 20 else snip
    return ("命中", f"{eid} → {hit.get('source') or hit.get('source_row_id') or ''}"
            + ("" if (key and key in blob) else "（片段文本未在该条 evidence 中匹配）"))


TOPIC_STOP = {"长平之战", "赤壁之战", "巨鹿之战", "淝水之战", "涿鹿之战", "鸣条之战", "官渡之战",
              "城濮之战", "昆阳之战", "阪泉之战", "项羽", "白起", "韩信", "诸葛亮"}


def topic_of(qid, s):
    """从题面与标注词取本题主题词（事件名/人物名）"""
    row = s[s["qid"] == qid].iloc[0]
    words = []
    for w in str(row["expected_entities"]).split(";"):
        w = w.strip()
        if w and w in TOPIC_STOP:
            words.append(w)
    if not words:
        q = str(row["question"])
        for w in TOPIC_STOP:
            if w in q:
                words.append(w)
        for w in ("曹操", "袁绍", "周瑜", "谢玄", "苻坚", "商汤", "黄帝", "蚩尤", "晋文公"):
            if w in q and w not in words:
                words.append(w)
    return words


def on_topic(body, words):
    return any(w in body for w in words) if words else None


s = pd.read_csv(REVIEW / "scores_review_20260913_143352.csv")
lines = []
summary = []
for _, row in s.iterrows():
    qid = row["qid"]
    if str(row["finish_reason"]) == "refused":
        lines.append(f"\n{'='*100}\n[{qid}] 拒答行，citations_text={row['citations_text']!r}")
        summary.append((qid, 0, 0, 0))
        continue
    words = topic_of(qid, s)
    cites = parse_citations(row["citations_text"])
    lines.append(f"\n{'='*100}\n[{qid}] 共 {len(cites)} 条引用；本题主题词={words}")
    n_ok = n_topic = 0
    for idx, ctype, body in cites:
        if ctype == "graph_triple":
            st, note = check_triple(body)
        elif ctype == "event_card":
            st, note = check_event_card(body)
        elif ctype == "raw_text":
            st, note = check_raw(body)
        elif ctype == "evidence":
            st, note = check_evidence(body)
        else:
            st, note = "未知类型", ctype
        ok = st in ("命中", "命中(反向)", "近似命中")
        n_ok += ok
        ot = on_topic(body, words)
        n_topic += bool(ot)
        flag = "★相关" if ot else "  无关"
        short = re.sub(r"\s+", " ", body)[:96]
        lines.append(f"  [{idx:>2}] {ctype:<12} {flag} {st:<10} {note:<26} | {short}")
    summary.append((qid, len(cites), n_ok, n_topic))

lines.append("\n\n" + "=" * 100 + "\n汇总：每题引用条数 / 真实命中 / 与本题主题相关\n")
lines.append(f"{'qid':<6}{'条数':>5}{'命中':>6}{'相关':>6}")
for qid, n, k, t in summary:
    lines.append(f"{qid:<6}{n:>5}{k:>6}{t:>6}")

(REVIEW / "_citation_audit.txt").write_text("\n".join(lines), encoding="utf-8")
print("written:", REVIEW / "_citation_audit.txt")
