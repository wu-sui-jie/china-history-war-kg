"""题库（黄金问答集）schema 与读写（evaluation/bank.py）。

题库是人工维护的评测资产，逐条字段：

- id：题目编号（Q001…），题库内唯一；
- suite：评测套件分组。main = 主基线套件（进入总指标统计）；
  long_rewrite / filter_loss / refusal 为专项套件（单独统计口径，
  见 docs/RAG_v1/RAGv4-开发说明.md 的"专项评测口径"）；
- category：问题类型，取 QuestionType 枚举值 + other（未答/拒答类）；
- question：自然语言问题（发送给系统的原文）；
- filters：随请求携带的朝代/战争类型筛选 {"dynasty": [], "event_type": []}；
- answerable：True 表示知识库内应有依据可答；False 表示该问题应拒答或
  明确说明无依据（供"应拒答时正确拒答"口径使用）；
- expected_entities：期望出现在"检索证据 / 回答 / 引用"中的实体词
  （标准名或别名均可，可短于实体标准名，如"牧野之战"⊂"周武王灭商牧野之战"）。
  用于计算实体命中 / 图谱命中 / 文本 top-k 覆盖 / 回答覆盖等客观指标；
- expected_docs：期望覆盖的原始文档（doc01_中国历代战争简史 /
  doc02_中国战争史地图集），由人工标注补全（可选）；
- gold_notes：参考要点（数据出处或人工要点），供评分人对照系统回答；
- source_ref：数据出处，便于审核人回溯核对，取值两种：
  ① 快照实体 id（entities.json 的 entity_id，形如 event_0166 / person_0303；全局唯一）；
  ② 关系行 id（relations.json 的 source_row_id，**必须带 legacy 表名**，
     写作 relations#<legacy_table>:<source_row_id>，多行逗号分隔，例如
     relations#event_person_relations:730）。
     注意 source_row_id 只在各 legacy 表（event_person_relations /
     event_place_relations / event_event_relations / event_organization_rel）内唯一，
     跨表会重复（实测 6437 个 id 重复出现），因此不能只写 relations#730。
  审核中若发现指向的数据与题目不符，可直接改这一列并回填（apply 支持 source_ref 覆盖）；
- reviewed / reviewer / reviewed_at：人工审核状态；
- annotation_version：召回标注版本（报告需记录，验收标准 3 条）。

文件约定：
- 数据存 data/eval/<version>/questions.jsonl（每行一条）；
- 同目录 questions.meta.json 记 annotation_version 等元信息；
- 人工审核/召回标注通过 CLI `check-bank` + 直接编辑 jsonl 进行；
  字段全部可编辑，代码只校验结构不校验内容正确性。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from contracts.question import QuestionType

# 题库允许的类别（= 在线链路 QuestionType + 兜底 other）
CATEGORIES = {q.value for q in QuestionType} | {"other"}

# 评测套件（专项口径分组）
SUITES = ("main", "long_rewrite", "filter_loss", "refusal")

# 专项说明（report 展示用；report 会在标题前统一加“专项：”前缀）
SUITE_LABEL = {
    "main": "主基线套件",
    "long_rewrite": "长改写问题的相关文本归因",
    "filter_loss": "事件类型筛选的原文召回损耗",
    "refusal": "应拒答问题（unknown_answer）",
}

# 朝代/战争类型筛选项可用值来自 data/snapshot/<v>/dicts.json，
# 本 schema 不强校验具体取值（留给 check-bank 与数据版本耦合）。
DOC_IDS = ("doc01_中国历代战争简史", "doc02_中国战争史地图集")


@dataclass
class GoldQuestion:
    id: str
    suite: str = "main"
    category: str = QuestionType.SINGLE_ENTITY.value
    question: str = ""
    filters: dict = field(default_factory=dict)          # {"dynasty": [...], "event_type": [...]}
    answerable: bool = True
    expected_entities: List[str] = field(default_factory=list)
    expected_docs: List[str] = field(default_factory=list)
    gold_notes: str = ""
    source_ref: str = ""
    reviewed: bool = False
    reviewer: str = ""
    reviewed_at: str = ""
    annotation_version: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @staticmethod
    def from_dict(d: dict) -> "GoldQuestion":
        return GoldQuestion(**{k: d.get(k) for k in GoldQuestion.__dataclass_fields__})

    def validate(self) -> list[str]:
        """结构校验，返回问题列表（空 = 通过）。"""
        errs: list[str] = []
        if not self.id:
            errs.append(f"{self.question[:20] or '?'} id 为空")
        if self.suite not in SUITES:
            errs.append(f"{self.id} suite 不在 {SUITES}")
        if self.category not in CATEGORIES:
            errs.append(f"{self.id} category 非法: {self.category}")
        if not self.question.strip():
            errs.append(f"{self.id} question 为空")
        for e in self.expected_entities:
            if not isinstance(e, str) or len(e.strip()) < 2:
                errs.append(f"{self.id} expected_entities 项非法: {e!r}")
        for d in self.expected_docs:
            if d not in DOC_IDS:
                errs.append(f"{self.id} expected_docs 非法: {d}（应为 {DOC_IDS} 之一）")
        fl = self.filters or {}
        if set(fl.keys()) - {"dynasty", "event_type"}:
            errs.append(f"{self.id} filters 只允许 dynasty/event_type")
        if self.reviewed and not (self.reviewer or self.reviewed_at):
            errs.append(f"{self.id} reviewed=True 需 reviewer/reviewed_at")
        return errs


@dataclass
class QuestionBank:
    version: str = ""                # 对应快照/索引版本，如 20260904_v2
    annotation_version: str = ""     # 召回标注版本（人工更新）
    generated_at: str = ""
    items: List[GoldQuestion] = field(default_factory=list)

    def by_id(self) -> dict:
        return {q.id: q for q in self.items}

    def ids(self) -> list[str]:
        return [q.id for q in self.items]

    def validate(self) -> dict:
        """校验题库整体结构；返回 {ok, errors:[{id, problems:[]}], summary}。"""
        errors = []
        seen: set[str] = set()
        for q in self.items:
            problems = q.validate()
            if q.id in seen:
                problems.append("id 重复")
            seen.add(q.id)
            if problems:
                errors.append({"id": q.id, "problems": problems})
        summary = {
            "total": len(self.items),
            "suites": {s: sum(1 for q in self.items if q.suite == s) for s in SUITES},
            "categories": _count_by(self.items, "category"),
            "reviewed": sum(1 for q in self.items if q.reviewed),
            "answerable_false": sum(1 for q in self.items if not q.answerable),
        }
        return {"ok": not errors, "errors": errors, "summary": summary}


def _count_by(items: list[GoldQuestion], key: str) -> dict:
    out: dict = {}
    for it in items:
        out[it.__dict__[key]] = out.get(it.__dict__[key], 0) + 1
    return out


# ---- 读写 ----
def load_bank(bank_path: Path, meta_path: Optional[Path] = None) -> QuestionBank:
    bank_path = Path(bank_path)
    if not bank_path.exists():
        raise FileNotFoundError(f"题库不存在: {bank_path}")
    items = []
    with open(bank_path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{bank_path} 第 {i} 行 JSON 非法: {e}") from e
            items.append(GoldQuestion.from_dict(d))

    meta: dict = {}
    mp = Path(meta_path) if meta_path else bank_path.with_name("questions.meta.json")
    if mp.exists():
        meta = json.loads(mp.read_text(encoding="utf-8"))

    default_version = meta.get("version", "")
    if not default_version:
        # 从目录名推断（data/eval/<version>/questions.jsonl）
        try:
            default_version = bank_path.parent.name
        except Exception:
            default_version = ""
    return QuestionBank(
        version=meta.get("version") or default_version,
        annotation_version=meta.get("annotation_version", ""),
        generated_at=meta.get("generated_at", ""),
        items=items,
    )


def save_bank(bank: QuestionBank, bank_path: Path, meta_path: Optional[Path] = None) -> None:
    bank_path = Path(bank_path)
    bank_path.parent.mkdir(parents=True, exist_ok=True)
    with open(bank_path, "w", encoding="utf-8") as f:
        for q in bank.items:
            f.write(json.dumps(q.to_dict(), ensure_ascii=False) + "\n")
    mp = Path(meta_path) if meta_path else bank_path.with_name("questions.meta.json")
    meta = {
        "version": bank.version,
        "annotation_version": bank.annotation_version,
        "generated_at": bank.generated_at or datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "count": len(bank.items),
    }
    mp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
