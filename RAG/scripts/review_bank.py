"""题库人工审核 / 人工评分的工作表工具（RAGv4 / F10）。

痛点：39 条黄金问答直接改 JSONL 不友好。本工具提供 Excel 友好的 CSV（utf-8-sig）
工作表往返：

- bank-export / bank-apply  ：题库审核。
  导出每行 = 一条题目；审核人在 Excel 里改 question/filters/expected_*/gold_notes
  /answerable/category，并把「reviewed」填 通过（其余值不写）表示该条审核通过；
  回填时只有非空单元格会覆盖题库原值（空单元格 = 不改），避免误清字段。
- scores-export / scores-apply ：对某次 run 的答案做人工评分。
  导出每行 = 一条系统回答（含引用明细），审核人填 answer_correctness /
  citation_correctness / notes / reviewer；回填到 run 目录 scores.jsonl，
  再用 `python scripts/run_evaluation.py report --run <run> --scores <scores.jsonl>`
  合并进报告。

用法（从 RAG/ 根目录）：
    python scripts/review_bank.py bank-export  [--bank <题库>] [--out <csv>]
    python scripts/review_bank.py bank-apply   --sheet <审核后的csv> [--reviewer 姓名]
    python scripts/review_bank.py scores-export --run <run目录> [--out <csv>]
    python scripts/review_bank.py scores-apply  --run <run目录> --sheet <评分后的csv> [--reviewer 姓名]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_settings  # noqa: E402

# 评分取值合法性（与 evaluation/grading.py 一致，此处仅用于回填校验提示）
_ANSWER_LEVELS = ("correct", "partial", "incorrect", "unknown_answer")
_CITATION_LEVELS = ("supported", "unrelated", "unsupported")

_BANK_HEADERS = [
    "id", "suite", "category", "question", "dynasty", "event_type", "answerable",
    "expected_entities", "expected_docs", "gold_notes", "source_ref",
    "data_digest", "auto_verify", "auto_verify_detail", "reviewed",
]

_SCORE_HEADERS = [
    "qid", "suite", "category", "question", "variant", "answerable",
    "expected_entities", "gold_notes", "source_ref", "finish_reason",
    "system_answer", "citations_text",
    "answer_correctness", "citation_correctness", "notes", "reviewer",
]


def _default_bank() -> Path:
    from server.runtime import resolve_version
    v = resolve_version(get_settings())[0]
    return Path("data/eval") / v / "questions.jsonl"


def _load_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _load_sheet(path: Path) -> list[dict]:
    """读取工作表：支持 .csv 与 .xlsx（xlsx 用标准库解析，不依赖 openpyxl）。

    返回 [{列名: 值}]；表头取第一行。空行忽略。
    """
    path = Path(path)
    if path.suffix.lower() != ".xlsx":
        return _load_csv(path)
    import xml.etree.ElementTree as ET
    import zipfile

    NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    z = zipfile.ZipFile(path)
    shared: list[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(f"{NS}si"):
            shared.append("".join(t.text or "" for t in si.iter(f"{NS}t")))
    sheet = sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet"))[0]
    root = ET.fromstring(z.read(sheet))
    grid: list[dict] = []
    for row in root.iter(f"{NS}row"):
        vals: dict[str, str] = {}
        for c in row.findall(f"{NS}c"):
            col = "".join(ch for ch in c.get("r") if ch.isalpha())
            t = c.get("t")
            v = c.find(f"{NS}v")
            isel = c.find(f"{NS}is")
            if t == "s" and v is not None:
                val = shared[int(v.text)]
            elif t == "inlineStr" and isel is not None:
                val = "".join(x.text or "" for x in isel.iter(f"{NS}t"))
            else:
                val = v.text if v is not None else ""
            vals[col] = (val or "").strip()
        grid.append(vals)
    if not grid:
        return []
    headers = grid[0]
    out = []
    for r in grid[1:]:
        rec = {headers.get(c, c): r.get(c, "") for c in headers}
        if any((v or "").strip() for v in rec.values()):
            out.append(rec)
    return out


def _write_csv(rows: list[dict], headers: list[str], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        _write_csv_to(out, rows, headers)
    except PermissionError:
        # 工作表可能正被 Excel/WPS 打开占用：换个文件名落盘，避免丢结果
        alt = out.with_name(f"{out.stem}_new{out.suffix}")
        _write_csv_to(alt, rows, headers)
        print(f"⚠ 原文件被占用（可能正在 Excel 中打开），已改写到: {alt}")
        print("  请关闭原文件后用新文件，或手工覆盖。")
        return
    print(f"已生成: {out}（{len(rows)} 行；用 Excel/WPS 打开，改完另存后用 apply 回填）")


def _write_csv_to(out: Path, rows: list[dict], headers: list[str]) -> None:
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _snapshot_dir() -> Path:
    from server.runtime import resolve_version
    settings = get_settings()
    version, snap, _idx = resolve_version(settings)
    return snap


def _clip(s, n: int) -> str:
    s = "" if s is None else str(s)
    s = s.replace("\n", " ")
    return s[:n] + ("…" if len(s) > n else "")


class _RefData:
    """按 source_ref 取快照切片（实体行 / 关系行）供人工核对。"""

    def __init__(self) -> None:
        snap = _snapshot_dir()
        self.snap = snap
        self.entities = {}
        for e in json.loads((snap / "entities.json").read_text(encoding="utf-8")):
            self.entities[e.get("entity_id")] = e
        # 关系行按 (legacy_table, source_row_id) 建键：
        # source_row_id 只在各 legacy 表内唯一（跨表会重复），单写行号会取错行
        self.relations = {}
        for r in json.loads((snap / "relations.json").read_text(encoding="utf-8")):
            self.relations[(r.get("legacy_table"), r.get("source_row_id"))] = r

    @staticmethod
    def _split(ref: str) -> list[str]:
        import re
        return [p.strip() for p in re.split(r"[,/;]", ref or "") if p.strip()]

    def _rel_rows(self, token: str) -> list[dict]:
        """解析 relations#<表>:<行号>；兼容旧写法 relations#<行号>（返回多候选供提示）。"""
        body = token.split("#", 1)[1]
        if ":" in body:
            table, rid = body.split(":", 1)
            try:
                r = self.relations.get((table, int(rid)))
            except ValueError:
                return []
            return [r] if r else []
        try:
            rid = int(body)
        except ValueError:
            return []
        return [r for (t, i), r in self.relations.items() if i == rid]

    def entity_digest(self, eid: str) -> Optional[str]:
        e = self.entities.get(eid)
        if not e:
            return None
        if e.get("type") == "事件":
            parts = [f"事件「{e.get('name')}」", f"朝代={e.get('dynasty')}",
                     f"时间={e.get('start_date')}",
                     f"战争类型={e.get('event_type')}"]
            for k, label in (("place", "地点"), ("aggressor", "进攻方"),
                             ("defender", "防守方"), ("action", "行动"),
                             ("result", "结果"), ("impact", "影响")):
                if e.get(k):
                    parts.append(f"{label}={_clip(e[k], 80)}")
        elif e.get("type") == "人物":
            parts = [f"人物「{e.get('name')}」", f"朝代={e.get('dynasty')}",
                     f"身份={e.get('role')}", f"所属={e.get('org')}"]
        elif e.get("type") == "地点":
            parts = [f"地点「{e.get('name')}」", f"现代地名={e.get('modern_name')}",
                     f"省市区={e.get('province')}/{e.get('city')}"]
        else:
            parts = [f"{e.get('type')}「{e.get('name')}」",
                     f"类型={e.get('org_type')}"]
        if e.get("source"):
            parts.append(f"原文={_clip(e['source'], 140)}")
        return "；".join(parts)

    def relation_digest(self, row: dict) -> str:
        tail = f"证据={_clip(row.get('evidence'), 60)}" if row.get("evidence") else ""
        return (f"关系行[{row.get('legacy_table')}:{row.get('source_row_id')}] "
                f"{row.get('source_name')} —{row.get('relation')}→ "
                f"{row.get('target_name')}（置信度={row.get('confidence')}）{tail}")

    def digest(self, ref: str, limit: int = 360) -> str:
        out = []
        for p in self._split(ref):
            if p.startswith("relations#"):
                rows = self._rel_rows(p)
                if len(rows) == 1:
                    out.append(self.relation_digest(rows[0]))
                elif not rows:
                    out.append(f"（未找到 {p}）")
                else:
                    out.append(f"（{p} 有歧义，命中 {len(rows)} 行："
                               f"{'、'.join(r.get('legacy_table','?') for r in rows)}；"
                               f"请改为 relations#<表名>:{p.split('#')[1]}）")
            else:
                out.append(self.entity_digest(p) or f"（未找到 {p}）")
        text = " ｜ ".join(out)
        return _clip(text, limit) if limit else text

    def detail(self, ref: str) -> str:
        """peek 用：完整打印（关系证据不截断）。"""
        lines = []
        for p in self._split(ref):
            if p.startswith("relations#"):
                rows = self._rel_rows(p)
                if not rows:
                    lines.append(f"{p}: 未在 relations.json 找到对应行")
                    continue
                if len(rows) > 1:
                    lines.append(f"{p}: ⚠ 行号跨表歧义，命中 {len(rows)} 行，"
                                 f"请使用 relations#<legacy表名>:{p.split('#')[1]}：")
                for r in rows:
                    lines.append(f"【关系行 {r.get('legacy_table')}:{r.get('source_row_id')}】"
                                 f"{r.get('source_name')} —{r.get('relation')}→ "
                                 f"{r.get('target_name')}")
                    lines.append(f"  置信度={r.get('confidence')}　"
                                 f"来源表={r.get('legacy_table')}　"
                                 f"pending_review={r.get('pending_review')}")
                    if r.get("evidence"):
                        lines.append(f"  证据原文：{r['evidence']}")
            else:
                e = self.entities.get(p)
                if not e:
                    lines.append(f"{p}: 未在 entities.json 找到该实体")
                    continue
                lines.append(f"【实体 {p}】{json.dumps(e, ensure_ascii=False)}")
        return "\n".join(lines)

    # ---- 全范围核对（verify 用）----
    def entity_scope(self, eid: str) -> list[tuple[str, str]]:
        """实体行全文（含 source 与结构化字段）。"""
        e = self.entities.get(eid)
        if not e:
            return []
        return [(f"实体行:{eid}", json.dumps(e, ensure_ascii=False))]

    def relations_of_entity(self, eid: str, limit: int = 60) -> list[tuple[str, str]]:
        """该实体作为任一端出现的全部关系行（含证据原文）。"""
        out = []
        for (tab, rid), r in self.relations.items():
            if r.get("source_entity_id") == eid or r.get("target_entity_id") == eid:
                text = (f"{r.get('source_name')} —{r.get('relation')}→ {r.get('target_name')} "
                        f"{r.get('evidence') or ''}")
                out.append((f"关系行:{tab}:{rid}", text))
                if len(out) >= limit:
                    break
        return out

    def relation_scope(self, token: str) -> list[tuple[str, str]]:
        out = []
        for p in self._split(token):
            if not p.startswith("relations#"):
                continue
            for r in self._rel_rows(p):
                text = (f"{r.get('source_name')} —{r.get('relation')}→ {r.get('target_name')} "
                        f"{r.get('evidence') or ''}")
                out.append((f"关系行:{r.get('legacy_table')}:{r.get('source_row_id')}", text))
        return out

    def scope_of(self, q, entity_ids: list[str], chunk_hits=None) -> list[tuple[str, str]]:
        """一条题目的核对数据范围：source_ref 指向 + 标注词命中的实体行及其关系 + 检索片段。"""
        scope: list[tuple[str, str]] = []
        scope += self.relation_scope(q.source_ref)
        for p in self._split(q.source_ref):
            if not p.startswith("relations#"):
                scope += self.entity_scope(p)
        for eid in entity_ids:
            scope += self.entity_scope(eid)
            scope += self.relations_of_entity(eid)
        if chunk_hits:
            scope += chunk_hits
        return scope


# ---------- 题库审核 ----------
def _entity_ids_of(q, qu) -> list[str]:
    """把标注词经 F02 词典映射为实体 id（用于扩展核对范围）。"""
    ids: list[str] = []
    for w in q.expected_entities:
        for h in qu.matcher.match(w):
            if h.entity_id and h.entity_id not in ids:
                ids.append(h.entity_id)
    return ids


def _chunk_scope(qu, searcher, q, limit: int = 8) -> list[tuple[str, str]]:
    """索引里含标注词/问题关键词的片段（跨文档的证据范围）。"""
    probes: list[str] = []
    for w in q.expected_entities:
        if len(w) >= 2:
            probes.append(w)
    if not probes:
        probes.append(q.question[:10])
    out: list[tuple[str, str]] = []
    seen = set()
    for nm in probes[:3]:
        for h in searcher.search_keyword(nm, limit=5, keyword_mode="or"):
            text = h.get("text") or ""
            if nm not in text:
                continue
            key = h.get("chunk_id")
            if key in seen:
                continue
            seen.add(key)
            out.append((f"检索片段:{h.get('chunk_type')}:{h.get('doc_id')}", text))
            if len(out) >= limit:
                return out
    return out


def _split_clauses(text: str) -> list[str]:
    import re
    return [c.strip() for c in re.split(r"[；;。\n]", text or "") if len(c.strip()) >= 3]


_FACT_RE = None


def _facts_in(clause: str, qu) -> list[str]:
    """从句子里抽出"可核对的硬事实"：年份/数字 + 词典能识别的实体词。

    gold_notes 是改写摘要（不会逐字出现在数据里），所以按事实词核对，
    而不是整句字面匹配。
    """
    global _FACT_RE
    import re
    if _FACT_RE is None:
        _FACT_RE = re.compile(
            r"(公元前\d+世纪|约?前\d+年|公元\d+年|\d+(?:年|月|万|千|县|次|人))")
    facts: list[str] = []
    for m in _FACT_RE.finditer(clause):
        token = m.group(0)
        if token not in facts:
            facts.append(token)
    if qu is not None:
        for h in qu.matcher.match(clause):
            if h.name and h.name not in facts:
                facts.append(h.name)
    return facts


def bank_verify(args) -> int:
    """对全部题目做数据侧自动核对，输出 bank_verify.csv（不影响题库）。"""
    from evaluation.bank import load_bank

    bank = load_bank(args.bank)
    rd = _RefData()
    settings = get_settings()
    qu = None
    searcher = None
    try:
        from server.query import load_understanding
        qu = load_understanding(rd.snap)
    except Exception as e:  # noqa: BLE001
        print(f"（词典核对跳过：{e}）")
    try:
        from server.runtime import resolve_version
        from server.text.searcher import TextSearcher
        version, _snap, index_dir = resolve_version(settings)
        searcher = TextSearcher(index_dir, version, top_k=5)
    except Exception as e:  # noqa: BLE001
        print(f"（索引片段核对跳过：{e}）")

    rows = []
    ok_n = 0
    suspects = []
    if not args.out:
        args.out = str(Path(args.bank).parent / "review" / "bank_verify.csv")
    informational_suites = {"long_rewrite", "filter_loss"}  # 其 gold_notes 为口径说明
    for q in bank.items:
        eids = _entity_ids_of(q, qu) if qu else []
        chunks = _chunk_scope(qu, searcher, q) if searcher else []
        scope = rd.scope_of(q, eids, chunks)
        blob = "\n".join(t for _, t in scope)

        ent_parts, ent_missing = [], []
        for w in q.expected_entities:
            where = [lab for lab, t in scope if w in t]
            if where:
                ent_parts.append(f"{w}=存在[{where[0]}]")
            else:
                dict_hit = bool(qu and qu.matcher.match(w))
                ent_parts.append(f"{w}=未在核对范围找到" + ("（词典可识别，可能仅在其它片段）"
                                                        if dict_hit else "（词典也识别不到）"))
                ent_missing.append(w)

        note_parts, note_missing = [], []
        for c in _split_clauses(q.gold_notes):
            facts = _facts_in(c, qu)
            if not facts:
                note_parts.append(f"[描述性] {c}=无需事实核对")
                continue
            missing = [f for f in facts if f not in blob]
            if missing:
                note_parts.append(f"{c}=缺口{missing}")
                note_missing.extend(missing)
            else:
                note_parts.append(f"{c}=事实齐（{len(facts)}项）")

        informational = q.suite in informational_suites
        verdict = "通过" if not ent_missing and (informational or not note_missing) else "存疑"
        if verdict == "通过":
            ok_n += 1
        else:
            suspects.append((q.id, q.suite, ent_missing, note_missing))
        rows.append({
            "id": q.id, "suite": q.suite, "category": q.category,
            "verdict": verdict,
            "expected_entity_check": "；".join(ent_parts),
            "gold_notes_check": ("（口径说明类，不作事实核对）" if informational
                                 else "；".join(note_parts)),
            "scope_sources": " | ".join(lab for lab, _ in scope),
            "source_ref": q.source_ref,
        })
    _write_csv(rows, ["id", "suite", "category", "verdict", "expected_entity_check",
                      "gold_notes_check", "scope_sources", "source_ref"],
               Path(args.out))
    print(f"自动核对：通过 {ok_n} / 存疑 {len(suspects)}（共 {len(bank.items)} 条）")
    for qid, suite, em, nm in suspects[:15]:
        print(f"  存疑 {qid}[{suite}] 实体缺口={em or '无'} 要点缺口={nm or '无'}")
    return 0
def _digests_for(bank) -> dict:
    rd = _RefData()
    return {q.id: rd.digest(q.source_ref) for q in bank.items}


def bank_export(args) -> int:
    from evaluation.bank import load_bank

    bank = load_bank(args.bank)
    digests = _digests_for(bank)
    # 若已有自动核对报告（verify 产物），合并结论列，方便审核人一眼看结论
    verify_path = Path(args.out).parent / "bank_verify.csv"
    vmap: dict[str, dict] = {}
    if verify_path.exists():
        for r in _load_csv(verify_path):
            vmap[(r.get("id") or "").strip()] = r
    rows = []
    for q in bank.items:
        v = vmap.get(q.id, {})
        rows.append({
            "id": q.id, "suite": q.suite, "category": q.category,
            "question": q.question,
            "dynasty": ";".join(q.filters.get("dynasty") or []),
            "event_type": ";".join(q.filters.get("event_type") or []),
            "answerable": "True" if q.answerable else "False",
            "expected_entities": ";".join(q.expected_entities),
            "expected_docs": ";".join(q.expected_docs),
            "gold_notes": q.gold_notes,
            "source_ref": q.source_ref,
            "data_digest": digests.get(q.id, ""),
            "auto_verify": v.get("verdict", "（未跑 verify）"),
            "auto_verify_detail": _clip(
                "；".join(x for x in (v.get("expected_entity_check", ""),
                                      v.get("gold_notes_check", "")) if x), 200),
            "reviewed": "通过" if q.reviewed else "",
        })
    _write_csv(rows, _BANK_HEADERS, Path(args.out))
    print("data_digest 列 = source_ref 指向的数据切片（截断，完整版用 peek）。")
    print("auto_verify 列 = python scripts/review_bank.py verify 的事实词级自动核对结论。")
    print("reviewed 列：填「通过」表示该题审核通过；留空 = 保持未审核。")
    return 0


def _parse_list(raw) -> list[str]:
    if raw is None:
        return []
    return [x.strip() for x in str(raw).split(";") if x.strip()]


def bank_apply(args) -> int:
    from evaluation.bank import load_bank, save_bank

    sheet = _load_sheet(Path(args.sheet))
    bank = load_bank(args.bank)
    by_id = bank.by_id()
    changed = 0
    passed = 0
    errors: list[str] = []
    for row in sheet:
        qid = (row.get("id") or "").strip()
        q = by_id.get(qid)
        if q is None:
            errors.append(f"题库无此 id: {qid}")
            continue
        if _nonempty(row.get("category")):
            q.category = row["category"].strip()
        if _nonempty(row.get("question")):
            q.question = row["question"].strip()
        if _nonempty(row.get("gold_notes")):
            q.gold_notes = row["gold_notes"].strip()
        if _nonempty(row.get("source_ref")):
            # 允许审核人纠正数据出处（如把描述性占位改成 event_XXXX / relations#NNN）
            q.source_ref = row["source_ref"].strip()
        # answerable：只接受 True/False 显式值
        ab = (row.get("answerable") or "").strip().lower()
        if ab in ("true", "false"):
            q.answerable = (ab == "true")
        # 列表字段：空 = 不改；非空 = 覆盖
        for key in ("expected_entities", "expected_docs"):
            raw = row.get(key)
            if _nonempty(raw):
                setattr(q, key, _parse_list(raw))
        dy = _parse_list(row.get("dynasty"))
        et = _parse_list(row.get("event_type"))
        if dy or et:
            flt = dict(q.filters or {})
            if dy:
                flt["dynasty"] = dy
            if et:
                flt["event_type"] = et
            q.filters = flt
        # reviewed：填「通过」才置已审核
        rv = (row.get("reviewed") or "").strip()
        if rv == "通过":
            q.reviewed = True
            q.reviewer = args.reviewer or q.reviewer or "reviewer"
            q.reviewed_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            passed += 1
        changed += 1
    # 校验
    res = bank.validate()
    if res["errors"]:
        print("⚠ 回填后题库校验失败，未保存：")
        for e in res["errors"]:
            print(f"- {e['id']}: {'; '.join(e['problems'])}")
        return 1
    if errors:
        print("警告（不影响已保存内容）：")
        for e in errors:
            print(f"- {e}")
    if getattr(args, "annotation_version", ""):
        # 元数据与条目级版本同步：条目字段若停在 draft-0 会与 meta 冲突
        bank.annotation_version = args.annotation_version
        for q in bank.items:
            q.annotation_version = args.annotation_version
    save_bank(bank, args.bank)
    print(f"已回填 {changed} 条（其中审核通过 {passed} 条）→ {args.bank}")
    if getattr(args, "annotation_version", ""):
        print(f"标注版本已更新为: {bank.annotation_version}")
    print(f"当前已审核: {sum(1 for x in bank.items if x.reviewed)}/{len(bank.items)}")
    return 0


def bank_peek(args) -> int:
    """按题号查看：题目字段 + source_ref 指向的完整数据 + 书中原文片段。"""
    from evaluation.bank import load_bank

    bank = load_bank(args.bank)
    q = bank.by_id().get(args.id)
    if q is None:
        print(f"题库中无此 id: {args.id}")
        return 2
    print(f"【{q.id}】suite={q.suite} category={q.category} answerable={q.answerable}"
          f" reviewed={q.reviewed}")
    print(f"问题：{q.question}")
    print(f"标注词：{'；'.join(q.expected_entities) or '（空）'}")
    print(f"期望文档：{'；'.join(q.expected_docs) or '（未标注）'}")
    print(f"参考要点：{q.gold_notes}")
    print(f"source_ref：{q.source_ref}")
    print("-" * 60)
    rd = _RefData()
    print(rd.detail(q.source_ref))
    print("-" * 60)
    # 顺带给出书中原文片段（若索引可用），便于核对要点措辞
    try:
        from server.runtime import resolve_version
        from server.text.searcher import TextSearcher

        settings = get_settings()
        version, _snap, index_dir = resolve_version(settings)
        searcher = TextSearcher(index_dir, version, top_k=3)
        probes = list(q.expected_entities[:2]) or [q.question[:8]]
        shown = 0
        for nm in probes:
            for h in searcher.search_keyword(nm, limit=5):
                text = h.get("text") or ""
                if nm not in text:
                    continue
                print(f"[{h.get('chunk_type')}/{h.get('doc_id')}] {_clip(text, 180)}")
                shown += 1
                if shown >= 3:
                    break
            if shown >= 3:
                break
        if shown == 0:
            print("（未在索引中直接命中含该标注词的片段，可放宽 expected_entities 或核对标注词）")
    except Exception as e:  # noqa: BLE001
        print(f"（原文片段查询跳过：{e}）")
    return 0


# ---------- 人工评分 ----------
def scores_export(args) -> int:
    from evaluation import report as report_mod

    run = report_mod.load_run(args.run)
    bank = run["bank"]
    if bank is None:
        print("（run 目录缺少题库引用，无法导出评分表）")
        return 2
    rows = []
    for q in bank.items:
        recs = [r for r in run["recs"]
                if r["question_id"] == q.id and r["config"] == "dual"]
        if not recs:
            continue
        # filter_loss 等含 variant 的取主形态（filters-on 优先，其次 '-'）
        rec = _primary(recs)
        t = rec["trace"]
        citations = "\n".join(
            f"[{c.get('index')}] ({c.get('kind')}) {c.get('title')}"
            + (f" — {c.get('snippet')}" if c.get("snippet") else "")
            for c in t["citations"]
        )
        rows.append({
            "qid": q.id, "suite": q.suite, "category": q.category,
            "question": q.question, "variant": rec.get("variant", "-"),
            "answerable": "True" if q.answerable else "False",
            "expected_entities": ";".join(q.expected_entities),
            "gold_notes": q.gold_notes, "source_ref": q.source_ref,
            "finish_reason": t["answer"]["finish_reason"],
            "system_answer": t["answer"]["text"].replace("\n", " "),
            "citations_text": citations.replace("\n", " "),
            "answer_correctness": "", "citation_correctness": "",
            "notes": "", "reviewer": "",
        })
    _write_csv(rows, _SCORE_HEADERS, Path(args.out))
    print("取值：answer_correctness = correct/partial/incorrect/unknown_answer；"
          "citation_correctness = supported/unrelated/unsupported。")
    return 0


def _primary(recs: list) -> dict:
    for v in ("filters-on", "-"):
        for r in recs:
            if r.get("variant") == v:
                return r
    return recs[0]


def scores_apply(args) -> int:
    from evaluation import report as report_mod

    sheet = _load_sheet(Path(args.sheet))
    report_mod.load_run(args.run)  # 校验 run 目录可读，结果此处不需要
    out_path = Path(args.run) / "scores.jsonl"
    rows = []
    used = 0
    errors = []
    for row in sheet:
        qid = (row.get("qid") or "").strip()
        ac = (row.get("answer_correctness") or "").strip()
        cc = (row.get("citation_correctness") or "").strip()
        rec = {
            "qid": qid,
            "suite": row.get("suite", ""),
            "category": row.get("category", ""),
            "question": row.get("question", ""),
            "variant": row.get("variant", "-"),
            "answerable": (row.get("answerable", "True") == "True"),
            "expected_entities": _parse_list(row.get("expected_entities")),
            "gold_notes": row.get("gold_notes", ""),
            "source_ref": row.get("source_ref", ""),
            "finish_reason": row.get("finish_reason", ""),
            "system_answer": row.get("system_answer", ""),
            "citations_text": row.get("citations_text", ""),
            "answer_correctness": ac,
            "citation_correctness": cc,
            "notes": (row.get("notes") or "").strip(),
            "reviewer": (row.get("reviewer") or args.reviewer or "").strip(),
            "graded": bool(ac or cc or (row.get("notes") or "").strip()
                           or args.reviewer),
        }
        if ac and ac not in _ANSWER_LEVELS:
            errors.append(f"{qid} answer_correctness 非法: {ac}")
        if cc and cc not in _CITATION_LEVELS:
            errors.append(f"{qid} citation_correctness 非法: {cc}")
        rows.append(rec)
        if rec["graded"]:
            used += 1
    if errors:
        print("评分取值非法，未保存：")
        for e in errors:
            print(f"- {e}")
        return 1
    out_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8")
    print(f"已写入 {used}/{len(rows)} 条已评分记录 → {out_path}")
    print("下一步合并报告：")
    print(f"  python scripts/run_evaluation.py report --run {args.run} "
          f"--scores {out_path}")
    return 0


def _nonempty(v) -> bool:
    return v is not None and str(v).strip() != ""


# ---------- 入口 ----------
def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="F10 人工审核/评分工作表工具")
    sub = p.add_subparsers(dest="cmd", required=True)

    pb = sub.add_parser("bank-export", help="题库审核表导出（csv）")
    pb.add_argument("--bank", default=None)
    pb.add_argument("--out", required=True)
    pb.set_defaults(fn=bank_export)

    pa = sub.add_parser("bank-apply", help="题库审核表回填")
    pa.add_argument("--bank", default=None)
    pa.add_argument("--sheet", required=True)
    pa.add_argument("--reviewer", default="")
    pa.add_argument("--annotation-version", default="",
                    help="可选：回填同时更新题库标注版本（如 reviewed-1）")
    pa.set_defaults(fn=bank_apply)

    pp = sub.add_parser("peek", help="按题号查看 source_ref 切片与书中原文")
    pp.add_argument("--id", required=True, help="题号，如 R02")
    pp.add_argument("--bank", default=None)
    pp.set_defaults(fn=bank_peek)

    pg = sub.add_parser("verify", help="全范围自动核对（实体行+关系行+检索片段）并出报告")
    pg.add_argument("--bank", default=None)
    pg.add_argument("--out", default="", help="核对报告 csv（默认 data/eval/<v>/review/bank_verify.csv）")
    pg.set_defaults(fn=bank_verify)

    pe = sub.add_parser("scores-export", help="人工评分表导出（csv）")
    pe.add_argument("--run", required=True)
    pe.add_argument("--out", required=True)
    pe.set_defaults(fn=scores_export)

    ps = sub.add_parser("scores-apply", help="人工评分表回填到 run 目录 scores.jsonl")
    ps.add_argument("--run", required=True)
    ps.add_argument("--sheet", required=True)
    ps.add_argument("--reviewer", default="")
    ps.set_defaults(fn=scores_apply)

    args = p.parse_args(argv)
    # 默认题库（bank 相关子命令）
    if args.cmd in ("bank-export", "bank-apply", "peek", "verify") and not getattr(args, "bank", None):
        args.bank = _default_bank()
    if args.cmd == "bank-export":
        args.bank = Path(args.bank)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
