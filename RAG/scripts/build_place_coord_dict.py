"""把历次高德地理编码的产物合并成一份**按名称索引**的坐标词典。

## 为什么需要它（这是坐标整批丢失的根因之一）

高德产物的主键是 `place_id`——**旧库**的 places 主键。换代重导会把 places 表整表重建
（`import_json_to_sqlite.py` 先 `DELETE FROM places` 再按产物插入），主键随之全部错位：
实测旧 id 268 是"洛水"，新 id 268 是"大梁"。于是旧产物**按 id 回写必然写到错误的地点上**，
而按 id 回写又恰恰是旧链路唯一的写库方式（`geocoding/import_coordinates.py` 的
`UPDATE places ... WHERE id = ?`）。这就解释了 2026-09-27 换代后"5527 个地点坐标全空、
4819 条高德坐标明明还在磁盘上"这件事。

所以换代前必须先做一次**键翻译**：把"id 键"翻成"名称 + 行政区 + 朝代"键。这份词典就是
翻译结果，`apply_place_coords.py` 拿它回写，之后每一轮换代都能重复使用。

## 来源（按信息丰富度排序；都给也行，会按 (名称, 行政区, 朝代) 去重）

1. `--db` / 自动探测：**带坐标的 SQLite 库**。默认在 `backend/database.bak_*` 里挑带坐标
   最多的那份（换代前的那份备份就是它）。字段最全：名称、省、市、县、朝代、现代地名。
2. `RAG/data/cache/amap/coords_progress.jsonl`：高德逐条进度（断点续跑文件）。
3. `RAG/data/cache/amap/approved_coords_for_import.json`：上一轮审核通过的结论。
4. `entity-event-relation/war_extraction/geocoding/approved_coordinates_*.json`：旧项目产物。

2/3/4 只带地名与 `place_id`，缺行政区/朝代；脚本会按 `place_id` 回到 1 去补上下文
（**并校验 id 与名称是否对得上**：对不上说明这份产物来自另一个数据版本，宁可不补，
也不能挂上错的行政区）。

## 输出

`RAG/data/cache/amap/place_coord_dict.json`（不入库：它是按次计费 API 的产物，且随本机
数据版本走；换代时随流程重建即可）。格式：

    {"meta": {"built_at": ..., "sources": [...], "entries": N},
     "entries": [{"name": ..., "province": ..., "city": ..., "dynasty": ...,
                  "modern_name": ..., "longitude": ..., "latitude": ...,
                  "level": "区县", "confidence": "high", "address": "...",
                  "source": "amap"}, ...]}

用法：

    python scripts/build_place_coord_dict.py                 # 自动挑库 + 全部 JSON 产物
    python scripts/build_place_coord_dict.py --db <某份备份>  # 指定上下文库
    python scripts/build_place_coord_dict.py --dry-run       # 只统计，不落盘
"""

from __future__ import annotations

import argparse
import glob
import json
import sqlite3
import sys
import time
from pathlib import Path

RAG_ROOT = Path(__file__).resolve().parent.parent
REPO = RAG_ROOT.parent
CACHE_DIR = RAG_ROOT / "data" / "cache" / "amap"
DEFAULT_OUT = CACHE_DIR / "place_coord_dict.json"

#: 高德返回的 `level`（定位到的行政层级）→ 本项目的 `coord_confidence`。
#
# **为什么不直接用 level。** level 说的是"匹配到了哪一级行政区"，不是"这个坐标离真实
# 地点有多远"。直接当置信度用的话，"省级中心点"也会被算成高置信——而地图页的
# "低置信坐标"统计、以及质检页的"坐标待确认"清单，都读这个字段。按"离具体地点有多远"
# 折成三档，省/国家级的兜底定位就如实标低。
LEVEL_TO_CONFIDENCE = {
    "门牌号": "high", "兴趣点": "high", "住宅区": "high", "村庄": "high",
    "乡镇": "high", "道路": "high", "公交地铁站点": "high",
    "区县": "medium",
    "市": "medium",
    "省": "low", "国家": "low", "未知": "low", "": "low",
}


def confidence_for(level: str) -> str:
    return LEVEL_TO_CONFIDENCE.get((level or "").strip(), "medium")


# ---------------------------------------------------------------- 来源 1：带坐标的库

PLACE_COLUMNS = ("id", "name", "province", "city", "dynasty", "modern_name",
                 "longitude", "latitude")


def _has_coord_columns(db_path: Path) -> bool:
    try:
        con = sqlite3.connect(str(db_path))
        try:
            cols = {row[1] for row in con.execute("PRAGMA table_info(places)")}
            return {"longitude", "latitude", "name"} <= cols
        finally:
            con.close()
    except sqlite3.Error:
        return False


def coord_db_count(db_path: Path) -> int:
    if not _has_coord_columns(db_path):
        return 0
    con = sqlite3.connect(str(db_path))
    try:
        return con.execute(
            "SELECT COUNT(*) FROM places WHERE longitude IS NOT NULL AND latitude IS NOT NULL"
        ).fetchone()[0]
    except sqlite3.Error:
        return 0
    finally:
        con.close()


def pick_context_db(explicit: str | None) -> Path | None:
    """上下文库：显式指定 → 仓库里带坐标最多的 `backend/database.bak_*` → 主库本身。"""
    if explicit:
        path = Path(explicit)
        if not path.exists():
            raise SystemExit(f"指定的库不存在：{path}")
        return path

    candidates = [Path(p) for p in glob.glob(str(REPO / "backend" / "database.bak*"))]
    candidates.append(REPO / "backend" / "database")
    scored = sorted(
        ((coord_db_count(p), p) for p in candidates if p.exists()),
        key=lambda item: item[0],
        reverse=True,
    )
    return scored[0][1] if scored and scored[0][0] > 0 else None


def load_entries_from_db(db_path: Path) -> tuple[list[dict], dict]:
    """从带坐标的库直接取条目，并顺带返回 `place_id -> 上下文` 索引（供 JSON 来源补上下文）。"""
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            f"SELECT {', '.join(PLACE_COLUMNS)}, coord_source, coord_confidence, coord_note "
            "FROM places WHERE longitude IS NOT NULL AND latitude IS NOT NULL"
        ).fetchall()
    finally:
        con.close()

    entries = []
    context: dict[int, dict] = {}
    for row in rows:
        context[int(row["id"])] = {k: row[k] for k in PLACE_COLUMNS[1:]}
        # 库里 `coord_confidence` 存的是**折好的三档**（本脚本的产物）；但旧链路写进去的是
        # 高德原始 level（"区县""市"…）。两种都要能读：已经是三档就照用，否则折一次。
        raw_confidence = (row["coord_confidence"] or "").strip()
        confidence = (raw_confidence if raw_confidence in ("high", "medium", "low")
                      else confidence_for(raw_confidence))
        entries.append({
            "name": row["name"] or "",
            "province": row["province"] or "",
            "city": row["city"] or "",
            "dynasty": row["dynasty"] or "",
            "modern_name": row["modern_name"] or "",
            "longitude": float(row["longitude"]),
            "latitude": float(row["latitude"]),
            "level": raw_confidence,
            "confidence": confidence,
            "address": row["coord_note"] or "",
            "source": row["coord_source"] or "amap",
        })
    return entries, context


# ---------------------------------------------------------------- 来源 2/3/4：JSON 产物

def _entry_from_amap_record(record: dict, context: dict[int, dict], origin: str) -> dict | None:
    """把一条高德记录转成词典条目；`place_id` 能与上下文对上时才补行政区/朝代。"""
    name = record.get("original_name") or record.get("name") or ""
    longitude = record.get("longitude")
    latitude = record.get("latitude")
    if not name or longitude is None or latitude is None:
        return None

    entry = {
        "name": name,
        "province": "",
        "city": "",
        "dynasty": "",
        "modern_name": "",
        "longitude": float(longitude),
        "latitude": float(latitude),
        "level": record.get("level") or record.get("confidence") or "",
        "confidence": confidence_for(record.get("level") or record.get("confidence") or ""),
        "address": record.get("formatted_address") or record.get("note") or "",
        "source": record.get("source") or "amap",
        "origin": origin,
    }

    place_id = record.get("place_id")
    ctx = context.get(int(place_id)) if isinstance(place_id, int) else None
    # id 对得上、但名字对不上 = 这份产物来自另一个数据版本，宁可不补上下文
    if ctx and ctx.get("name") == name:
        entry.update({
            "province": ctx.get("province") or "",
            "city": ctx.get("city") or "",
            "dynasty": ctx.get("dynasty") or "",
            "modern_name": ctx.get("modern_name") or "",
        })
    return entry


def load_entries_from_progress(path: Path, context: dict[int, dict]) -> list[dict]:
    """读 `coords_progress.jsonl`（一行一条，同 group_key 以最后一次为准）。"""
    latest: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("code") != "ok" or record.get("longitude") is None:
            continue
        latest[str(record.get("group_key") or record.get("name") or len(latest))] = record

    entries = []
    for record in latest.values():
        entry = _entry_from_amap_record(record, context, path.name)
        if entry:
            entries.append(entry)
    return entries


def load_entries_from_json(path: Path, context: dict[int, dict]) -> list[dict]:
    """读 `approved_coordinates_*.json` / `approved_coords_for_import.json`（列表形式）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(data, dict):
        data = data.get("approved") or []
    if not isinstance(data, list):
        return []

    entries = []
    for record in data:
        if not isinstance(record, dict):
            continue
        entry = _entry_from_amap_record(record, context, path.name)
        if entry:
            entries.append(entry)
    return entries


def discover_json_sources() -> list[Path]:
    patterns = [
        CACHE_DIR / "coords_progress.jsonl",
        CACHE_DIR / "approved_coords_for_import.json",
        REPO / "entity-event-relation" / "war_extraction" / "geocoding" / "approved_coordinates_*.json",
    ]
    found: list[Path] = []
    for pattern in patterns:
        found.extend(Path(p) for p in sorted(glob.glob(str(pattern))))
    return found


# ---------------------------------------------------------------- 去重与落盘

def dedupe(entries: list[dict]) -> list[dict]:
    """按 (名称, 省, 市, 朝代) 去重；同一个键上坐标不一致的**两条都留**——
    要不要用由 apply 脚本的歧义保护决定，词典这一层不做取舍（否则没法复核）。"""
    seen = set()
    unique = []
    for entry in entries:
        key = (entry["name"], entry.get("province", ""), entry.get("city", ""),
               entry.get("dynasty", ""), round(entry["longitude"], 6), round(entry["latitude"], 6))
        if key in seen:
            continue
        seen.add(key)
        unique.append(entry)
    return unique


def main() -> int:
    ap = argparse.ArgumentParser(description="合并高德坐标产物为按名称索引的坐标词典")
    ap.add_argument("--db", default=None, help="上下文库（带坐标的 SQLite）；默认自动挑")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出词典路径")
    ap.add_argument("--dry-run", action="store_true", help="只统计，不落盘")
    args = ap.parse_args()

    context_db = pick_context_db(args.db)
    entries: list[dict] = []
    sources: list[str] = []
    context: dict[int, dict] = {}

    if context_db:
        db_entries, context = load_entries_from_db(context_db)
        entries.extend(db_entries)
        sources.append(f"{context_db.name}({len(db_entries)} 条，带上下文)")
    else:
        print("⚠ 没找到带坐标的库：JSON 产物只能按地名建词典，行政区/朝代将为空。")

    for path in discover_json_sources():
        if not path.exists():
            continue
        loaded = (load_entries_from_progress(path, context) if path.suffix == ".jsonl"
                  else load_entries_from_json(path, context))
        if loaded:
            entries.extend(loaded)
            sources.append(f"{path.name}({len(loaded)} 条)")

    unique = dedupe(entries)
    names = {entry["name"] for entry in unique}
    with_province = sum(1 for entry in unique if entry.get("province"))
    print(f"合并后条目 {len(unique)}（去重前 {len(entries)}），不同地名 {len(names)}，"
          f"带行政区 {with_province}")
    for source in sources:
        print(f"  来源 {source}")

    if args.dry_run:
        print("（dry-run：未落盘）")
        return 0

    if not unique:
        print("❌ 没有任何条目可写：先确认备份库或高德产物还在")
        return 1

    payload = {
        "meta": {
            "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "sources": sources,
            "entries": len(unique),
            "distinct_names": len(names),
        },
        "entries": unique,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✅ 词典已写入 {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
