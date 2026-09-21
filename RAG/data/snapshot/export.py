"""F09 导出：只读旧 SQLite 与原文文本 → 统一中间记录。

- 只读 `backend/database`（不写、不改），把 events/places/persons/organizations
  与四类关系表导出为结构化 EntityNode/RelationEdge 雏形（含 source_row_id 可追溯）。
- 读取旧原文文本（utf-8，剥离 BOM），拷贝到 RAG/data/raw/source_texts（若源不可写则复制）。
- 不在此处做归一/消歧（那是 normalize/alias/governance 的职责）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from config.settings import Settings

# 旧表 → (类型, id 字段名)
ENTITY_TABLES = {
    "places": ("地点", "id"),
    "persons": ("人物", "id"),
    "organizations": ("组织", "id"),
}
RELATION_TABLES = {
    # 表名: (源id列, 目标id列, 源实体类型, 目标实体类型, 关系列, 源名称列, 目标名称列,
    #        可选证据列, 可选来源类型列, 可选置信度列)
    "event_place_relations": ("event_id", "place_id", "事件", "地点", "relation_type",
                              "event_name", "place_name", "evidence", "source_type", "confidence"),
    "event_person_relations": ("event_id", "person_id", "事件", "人物", "relation_type",
                               "event_name", "person_name", None, None, None),
    "event_organization_rel": ("event_id", "org_id", "事件", "组织", "relation_type",
                               "event_name", "org_name", None, None, None),
    "event_event_relations": ("event_a_id", "event_b_id", "事件", "事件", "relation_type",
                              "event_a_name", "event_b_name", None, None, None),
}


def _connect(sqlite_path: Path) -> sqlite3.Connection:
    if not sqlite_path.exists():
        raise FileNotFoundError(f"旧 SQLite 不存在: {sqlite_path}")
    con = sqlite3.connect(str(sqlite_path))
    con.row_factory = sqlite3.Row
    return con


def _pick(row: dict, *keys: str) -> Any:
    """按优先级取第一个非空且非“不详”的值；无则 None。"""
    for k in keys:
        v = row.get(k)
        if v is not None:
            s = str(v).strip()
            if s and s != "不详":
                return v
    return None


def export_entities(con: sqlite3.Connection) -> list[dict]:
    """导出四类实体表为统一实体雏形 dict（尚未赋快照内 entity_id）。"""
    out = []
    for table, (etype, idcol) in ENTITY_TABLES.items():
        rows = con.execute(f"SELECT * FROM {table}").fetchall()
        for r in rows:
            d = dict(r)
            ent = {
                "legacy_table": table,
                "source_row_id": d[idcol],
                "type": etype,
                "name": (d.get("name") or "").strip(),
                "source": f"{table}#{d[idcol]}",
            }
            if etype == "地点":
                ent.update(
                    modern_name=d.get("modern_name"),
                    dynasty=d.get("dynasty"),
                    province=d.get("province"),
                    city=d.get("city"),
                    longitude=_to_float(d.get("longitude")),
                    latitude=_to_float(d.get("latitude")),
                )
            elif etype == "人物":
                ent.update(dynasty=d.get("dynasty"), role=_pick(d, "role"), org=_pick(d, "org"))
            elif etype == "组织":
                ent.update(dynasty=d.get("dynasty"), org_type=_pick(d, "org_type"))
            out.append(ent)
    return out


def export_events(con: sqlite3.Connection) -> list[dict]:
    """导出 events 表为事件实体 + 事件卡片雏形。

    events 富字段（action/result/impact/source/place 等）拼装成事件卡片文本，
    供 F11 作为 event_card 语料；结构化字段保留用于图谱/卡片。
    """
    out = []
    rows = con.execute("SELECT * FROM events").fetchall()
    for r in rows:
        d = dict(r)
        ev = {
            "legacy_table": "events",
            "source_row_id": d["id"],
            "type": "事件",
            "name": (d.get("name") or "").strip(),
            "event_type": (d.get("event_type") or "").strip() or None,
            "dynasty": (d.get("dynasty") or "").strip() or None,
            "start_date": (d.get("start_date") or "").strip() or None,
            "end_date": (d.get("end_date") or "").strip() or None,
            "place": (d.get("place") or "").strip() or None,
            "aggressor": _pick(d, "aggressor"),
            "defender": _pick(d, "defender"),
            "person": _pick(d, "person"),
            "action": _pick(d, "action"),
            "result": _pick(d, "result"),
            "scale": _pick(d, "scale"),
            "impact": _pick(d, "impact"),
            "source": _pick(d, "source"),
            "source_row": d["id"],
        }
        out.append(ev)
    return out


def export_relations(con: sqlite3.Connection) -> list[dict]:
    """导出四类关系表为统一关系雏形（保留 source_row_id 与冗余名称字段）。"""
    out = []
    for (table, (scol, tcol, stype, ttype, relcol, sname_col, tname_col,
                 ev_col, src_col, conf_col)) in RELATION_TABLES.items():
        rows = con.execute(f"SELECT * FROM {table}").fetchall()
        for r in rows:
            d = dict(r)
            rel = {
                "legacy_table": table,
                "source_row_id": d["id"],
                "source_id": d[scol],
                "target_id": d[tcol],
                "source_type_hint": stype,
                "target_type_hint": ttype,
                "relation": (d.get(relcol) or "").strip(),
            }
            # 冗余名称便于报告/追溯，正式关系用 id 解析到快照实体
            if sname_col in d and d[sname_col]:
                rel["source_name"] = d[sname_col]
            if tname_col in d and d[tname_col]:
                rel["target_name"] = d[tname_col]
            # 可选溯源字段（event_place_relations 有 evidence/source_type/confidence）
            if ev_col and d.get(ev_col):
                rel["evidence"] = d[ev_col]
            if src_col and d.get(src_col):
                rel["source_type"] = d[src_col]
            if conf_col and d.get(conf_col):
                rel["legacy_confidence"] = d[conf_col]
            out.append(rel)
    return out


def _to_float(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        return f
    except (TypeError, ValueError):
        return None


def copy_raw_texts(settings: Settings, logger=None) -> list[dict]:
    """把旧原文文本（utf-8）拷入 RAG/data/raw/source_texts，返回 [{doc_id, path}]。

    源 txt 带 3 字节 utf-8 BOM，读取时 strip；以 GBK 误读的旧文件不在来源清单。
    """
    raw_dir = settings.raw_dir
    raw_dir.mkdir(parents=True, exist_ok=True)
    docs = []
    for i, src in enumerate(settings.legacy_raw_texts, start=1):
        src = Path(src)
        if not src.exists():
            if logger:
                logger.warning(f"原文不存在，跳过: {src}")
            continue
        text = src.read_text(encoding="utf-8-sig", errors="replace")
        if not text.strip():
            continue
        dest = raw_dir / f"doc{i:02d}_{src.stem}.txt"
        dest.write_text(text, encoding="utf-8")
        docs.append({"doc_id": f"doc{i:02d}", "path": str(dest), "chars": len(text)})
        if logger:
            logger.info(f"原文已收录 {dest.name} ({len(text)} 字)")
    return docs


def export_snapshot(settings: Settings, out_dir: Path, logger=None) -> dict:
    """执行导出：返回 {entities, events, relations, raw_docs} 中间产物摘要。

    out_dir 由 governance 层创建并传入。
    """
    con = _connect(settings.legacy_sqlite_path)
    try:
        entities = export_entities(con)
        events = export_events(con)
        relations = export_relations(con)
    finally:
        con.close()

    raw_docs = copy_raw_texts(settings, logger)

    summary = {
        "entity_records": len(entities),
        "event_records": len(events),
        "relation_records": len(relations),
        "raw_docs": raw_docs,
    }
    if logger:
        logger.info(
            f"导出完成: 实体{len(entities)} 事件{len(events)} "
            f"关系{len(relations)} 原文{len(raw_docs)}篇"
        )
    return summary
