"""F11 关键词索引：SQLite FTS5 + jieba 中文分词。

设计：FTS5 内置分词对中文按连续串处理，无法命中词内子串。
因此把片段用 jieba（加载实体名词典）分词后以空格 join 存入 keywords 列，
建 trigram 之外更精确的中文检索。查询词同样切词后 AND 匹配。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import jieba


def load_jieba_dicts(snapshot_dir: Path) -> None:
    """把快照实体名/别名加入 jieba 词典，提升领域分词准确度。"""
    entities_path = snapshot_dir / "entities.json"
    if not entities_path.exists():
        return
    import json
    ens = json.loads(entities_path.read_text(encoding="utf-8"))
    words = set()
    for e in ens:
        nm = e.get("name")
        if nm and len(nm) <= 12:
            words.add(nm)
        for a in e.get("aliases") or []:
            if a and len(a) <= 12:
                words.add(a)
    for w in words:
        jieba.add_word(w)
    dicts = json.loads((snapshot_dir / "dicts.json").read_text(encoding="utf-8"))
    for std_list in (dicts.get("event_type_standard") or []):
        if std_list:
            jieba.add_word(std_list)


def tokenize(text: str) -> list[str]:
    return [t.strip() for t in jieba.cut(text) if t.strip()]


def build_fts5(db_path: Path, chunks: list[dict], logger=None) -> int:
    """建 chunks_fts.db：content 表(与 chunks.jsonl 对齐) + FTS5 keywords 列。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(str(db_path))
    cur = con.cursor()
    # chunks 主表（rowid 对齐 chunks.jsonl 顺序）
    cur.execute("""
        CREATE TABLE chunks(
            chunk_id TEXT PRIMARY KEY,
            chunk_type TEXT, doc_id TEXT, source_version TEXT,
            event_id TEXT, event_name TEXT, event_type TEXT, dynasty TEXT,
            text TEXT, keywords TEXT
        )
    """)
    # FTS5 虚表：存 jieba 分词后的关键词
    cur.execute("""
        CREATE VIRTUAL TABLE chunks_fts USING fts5(
            keywords, chunk_id UNINDEXED,
            tokenize = 'unicode61 remove_diacritics 2'
        )
    """)
    seen = 0
    for i, c in enumerate(chunks, start=1):
        text = c["text"]
        words = tokenize(text)
        keywords = " ".join(words)
        # 只保留有意义词，避免 FTS 空串
        cur.execute(
            "INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?)",
            (c["chunk_id"], c.get("chunk_type"), c.get("doc_id"),
             c.get("source_version"), c.get("event_id"), c.get("event_name"),
             c.get("event_type"), c.get("dynasty"), text, keywords),
        )
        if keywords.strip():
            cur.execute(
                "INSERT INTO chunks_fts(keywords, chunk_id) VALUES (?,?)",
                (keywords, c["chunk_id"]),
            )
            seen += 1
    con.commit()
    con.close()
    if logger:
        logger.info(f"FTS5 建库完成: {db_path.name} 片段{len(chunks)} 可检索{seen}")
    return seen

