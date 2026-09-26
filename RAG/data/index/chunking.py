"""F11 语料拼装与切分。

- `load_corpora(snapshot_dir, raw_dir)`：读取三类语料（原文/事件卡片/关系证据）。
- `build_chunks(...)`：把语料切分成片段，返回 dict 列表 + 切分统计。

切分规则见 data/index/README.md：
- 原文按 段落→句子 边界切分，超长按句子聚合 + 前后重叠；
- 事件卡片以事件为最小单元；
- 关系证据保留为可引用的短文本。
"""

from __future__ import annotations

import re
from pathlib import Path

from config.settings import Settings
from lib.json_io import read_json

_PARA_SPLIT = re.compile(r"\n\s*\n|\r\n\r\n")
_SENT_SPLIT = re.compile(r"(?<=[。；;！？!])|\n+")


def _sentences(text: str) -> list[str]:
    """按句子边界切分（保留标点），去空段。"""
    return [p.strip() for p in _SENT_SPLIT.split(text) if p and p.strip()]


def _slice_long(text: str, max_chars: int, overlap: int) -> list[str]:
    """把一段长文本按句子聚合为 ≤ max_chars 的片段；相邻片段重叠 overlap 字符。"""
    sents = _sentences(text)
    if not sents:
        return [text[:max_chars]] if text else []
    pieces: list[str] = []
    cur = ""
    for s in sents:
        if len(s) > max_chars:  # 单句超长则硬切
            if cur:
                pieces.append(cur)
                cur = ""
            for i in range(0, len(s), max_chars):
                pieces.append(s[i : i + max_chars])
            continue
        if cur and len(cur) + 1 + len(s) > max_chars:
            pieces.append(cur)
            tail = cur[-overlap:] if overlap else ""
            cur = tail + s if tail else s
        else:
            cur = cur + s if cur else s
    if cur:
        pieces.append(cur)
    return pieces


def _chunk_raw(doc_id: str, text: str, source_version: str,
               max_chars: int, overlap: int) -> list[dict]:
    """原文：段落为单位，段落超长再按句子切并保留重叠。"""
    paras = [p.strip() for p in _PARA_SPLIT.split(text) if p.strip()] or [text]
    chunks: list[dict] = []
    seq = 0
    for para in paras:
        pieces = [para] if len(para) <= max_chars else _slice_long(para, max_chars, overlap)
        for piece in pieces:
            seq += 1
            chunks.append({
                "doc_id": doc_id,
                "source_version": source_version,
                "chunk_type": "raw",
                "text": piece,
                "seq_in_doc": seq,
            })
    return chunks


def _chunk_event_cards(cards: list[dict], source_version: str,
                       max_chars: int, overlap: int) -> list[dict]:
    """事件卡片：以事件为最小单元；描述超长再按句子切。"""
    chunks: list[dict] = []
    for c in cards:
        text = c.get("description") or ""
        if not text.strip():
            continue
        pieces = [text] if len(text) <= max_chars else _slice_long(text, max_chars, overlap)
        for i, piece in enumerate(pieces, start=1):
            chunks.append({
                "doc_id": f"event_card_{c['event_id']}",
                "source_version": source_version,
                "chunk_type": "event_card",
                "text": piece,
                "event_id": c.get("event_id"),
                "event_name": c.get("name"),
                "event_type": c.get("event_type"),
                "dynasty": c.get("dynasty"),
                "start_date": c.get("start_date"),
                "source": c.get("source"),
                "related_entities": [c.get("name")] if c.get("name") else [],
                "seq_in_doc": i,
            })
    return chunks


def _chunk_evidence(evidences: list[dict], source_version: str, max_chars: int,
                    name2type: dict | None = None) -> list[dict]:
    """关系证据：保留为可引用的短文本，超长再按句子切（无重叠）。

    关系证据按 `source_name`（事件名）关联事件卡片补齐 `event_type`，
    使"按战争类型筛选"时证据片段不再被整批剔除。
    """
    name2type = name2type or {}
    chunks: list[dict] = []
    for ev in evidences:
        text = ev.get("text") or ""
        if not text.strip():
            continue
        pieces = [text] if len(text) <= max_chars else _slice_long(text, max_chars, 0)
        for i, piece in enumerate(pieces, start=1):
            chunks.append({
                "doc_id": f"rel_ev_{ev.get('source_row_id')}",
                "source_version": source_version,
                "chunk_type": "evidence",
                "text": piece,
                "event_name": ev.get("source_name"),
                # 由事件卡片映射得到（缺失则 None → 筛选时按"无该元数据"处理，不剔除）
                "event_type": name2type.get(ev.get("source_name")),
                "related_entities": [ev.get("source_name"), ev.get("target_name")],
                "source": f"evidence_corpus#{ev.get('source_row_id')}",
                "seq_in_doc": i,
            })
    return chunks


def load_corpora(settings: Settings, snapshot_dir: Path, logger=None) -> tuple[list[dict], list[dict], list[dict]]:
    """读取原文/事件卡片/关系证据三类语料。"""
    raw_docs: list[dict] = []
    if settings.raw_dir.exists():
        for f in sorted(settings.raw_dir.glob("*.txt")):
            raw_docs.append({"doc_id": f.stem, "path": f, "text": f.read_text(encoding="utf-8")})
    cards = read_json(snapshot_dir / "event_cards.json")
    evidences = read_json(snapshot_dir / "evidence_corpus.json")
    if logger:
        logger.info(f"语料加载: 原文{len(raw_docs)} 事件卡片{len(cards)} 证据{len(evidences)}")
    return raw_docs, cards, evidences


def build_chunks(settings: Settings, snapshot_dir: Path, logger=None) -> tuple[list[dict], dict]:
    """切分全部语料 → (chunks, stats)。chunk_id 在索引层统一分配（与 FTS 行对齐）。"""
    raw_docs, cards, evidences = load_corpora(settings, snapshot_dir, logger)
    mc, ov = settings.chunk_max_chars, settings.chunk_overlap_chars
    chunks: list[dict] = []
    stats = {"raw_paras": 0, "event_cards": 0, "evidences": 0}

    for doc in raw_docs:
        parts = _chunk_raw(doc["doc_id"], doc["text"], "", mc, ov)
        stats["raw_paras"] += len(parts)
        chunks.extend(parts)

    ec = _chunk_event_cards(cards, "", mc, ov)
    stats["event_cards"] = len(ec)
    chunks.extend(ec)

    # 事件名 → 战争类型（供关系证据补 event_type，见 §2.5-3）
    name2type = {c.get("name"): c.get("event_type")
                 for c in cards if c.get("name") and c.get("event_type")}
    ev = _chunk_evidence(evidences, "", mc, name2type)
    stats["evidences"] = len(ev)
    stats["evidence_with_event_type"] = sum(1 for c in ev if c.get("event_type"))
    chunks.extend(ev)

    # 统一分配 chunk_id + 补 source_version（原文片段之前为空）
    for i, c in enumerate(chunks, start=1):
        c["chunk_id"] = f"chunk_{i:06d}"
        c["source_version"] = snapshot_dir.name
    stats["total"] = len(chunks)
    return chunks, stats
