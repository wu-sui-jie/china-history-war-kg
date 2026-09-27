"""把坐标词典回填进当前主库的 `places` 表。

## 为什么它必须是流程里的固定一步

坐标原先是在"导入 SQLite"之后**手工**跑一次旧项目的 `geocoding/import` 写进库的，
既不在 `scripts/publish.py` 的步骤里、也没有任何断言。结果是 2026-09-27 换代重导
（会整表 DELETE 再插入 places）之后坐标全空——5527 个地点 0 条坐标，而 4819 条高德
坐标好好躺在磁盘上，页面上却只剩后端内置的省/市中心点兜底。**漏一步不报错、只是结果
缺一块**，所以补上这一步和它的断言。

## 匹配口径（保守优先：宁可不写，也不写错）

按 `(名称, 省, 市, 朝代)` 分层匹配，逐级放松：

    1. 名称 + 省 + 朝代      ← 最可信
    2. 名称 + 省
    3. 名称 + 市
    4. 名称 + 朝代
    5. 名称（仅当词典里这个地名的所有条目坐标一致）

**每一级都要过歧义检查**：同一个键上词典给了不止一个坐标时，这一级直接跳过、落到下一级
（`--show-ambiguous` 可以看清单）。地名相同但其实是不同地点的（如"新城""东京"）就是靠
这一条挡住的——旧链路按 id 写库时没有这个问题，换成名称键之后必须有。

匹配不到的地点、以及各级因歧义跳过的地点，都会写进 `--unmatched-out`，正好是下一轮
高德补抓的输入清单（`export_unmapped_places.py` 的同类产物 + `fetch_place_coords.py`）。

## 用法

    python scripts/apply_place_coords.py --dry-run          # 先看命中率，不写库
    python scripts/apply_place_coords.py --yes              # 写库
    python scripts/apply_place_coords.py --yes --overwrite  # 连已有坐标的一起覆盖

退出码：0 正常；1 = 一条都没写（"坐标整批丢失"的哨兵，`publish.py` 靠它拦发布）。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

RAG_ROOT = Path(__file__).resolve().parent.parent
REPO = RAG_ROOT.parent
CACHE_DIR = RAG_ROOT / "data" / "cache" / "amap"
DEFAULT_DICT = CACHE_DIR / "place_coord_dict.json"
DEFAULT_DB = REPO / "backend" / "database"

#: 行政区后缀归一：与后端同口径（`backend/report_builders._normalize_region_name`）。
# 两侧都用这一条归一，键才对得上；后端那边改了这里也要跟着改。
REGION_SUFFIXES = ("省", "市", "地区", "盟", "自治区", "特别行政区", "县", "区")

#: 每个匹配层级的名字，命中统计按它分组打印
MATCH_LEVELS = ("名称+省+朝代", "名称+省", "名称+市", "名称+朝代", "名称(唯一)")


def norm_region(value: str | None) -> str:
    text = (value or "").strip()
    for suffix in REGION_SUFFIXES:
        text = text.replace(suffix, "")
    return text


class CoordIndex:
    """分层索引。同一键上坐标不一致时该键标记为歧义，查询一律跳过。"""

    def __init__(self, entries: list[dict]) -> None:
        self.by_key: dict[str, dict[tuple, tuple]] = {name: {} for name in MATCH_LEVELS}
        self.ambiguous: dict[str, set[tuple]] = {name: set() for name in MATCH_LEVELS}
        self._name_coords: dict[str, set[tuple]] = {}

        for entry in entries:
            name = (entry.get("name") or "").strip()
            if not name:
                continue
            coords = (float(entry["longitude"]), float(entry["latitude"]))
            province = norm_region(entry.get("province"))
            city = norm_region(entry.get("city"))
            dynasty = (entry.get("dynasty") or "").strip()

            self._name_coords.setdefault(name, set()).add(coords)
            keys = {
                "名称+省+朝代": (name, province, dynasty),
                "名称+省": (name, province),
                "名称+市": (name, city),
                "名称+朝代": (name, dynasty),
            }
            for level, key in keys.items():
                # 键里带空字段的没有区分力（比如省为空时的 (名称, "")），交给下一级
                if not all(key[1:]):
                    continue
                self._store(level, key, coords, entry)

    def _store(self, level: str, key: tuple, coords: tuple, entry: dict) -> None:
        existing = self.by_key[level].get(key)
        if existing is None:
            self.by_key[level][key] = (coords, entry)
            return
        if existing[0] != coords:
            # 同一个键上有两个不同坐标：这一级不可信，标记歧义并撤掉已存的那个
            self.ambiguous[level].add(key)
            self.by_key[level].pop(key, None)

    def lookup(self, place: dict) -> tuple[str, dict] | None:
        """返回 `(命中层级, 条目)`；全都不命中返回 None。"""
        name = (place.get("name") or "").strip()
        if not name:
            return None
        province = norm_region(place.get("province"))
        city = norm_region(place.get("city"))
        dynasty = (place.get("dynasty") or "").strip()

        probes = {
            "名称+省+朝代": (name, province, dynasty),
            "名称+省": (name, province),
            "名称+市": (name, city),
            "名称+朝代": (name, dynasty),
        }
        for level in MATCH_LEVELS[:-1]:
            key = probes[level]
            if not all(key[1:]):
                continue
            hit = self.by_key[level].get(key)
            if hit:
                return level, hit[1]

        coords = self._name_coords.get(name)
        if coords and len(coords) == 1:
            # 名称唯一：从任意一条同名词条上取来源信息
            for entry in self._iter_name_entries(name):
                return "名称(唯一)", entry
        return None

    def _iter_name_entries(self, name: str):
        for level in MATCH_LEVELS[:-1]:
            for (key, (_coords, entry)) in self.by_key[level].items():
                if key[0] == name:
                    yield entry

    def skipped_ambiguous(self, place: dict) -> bool:
        """是否因为"词典里同名同区有多份坐标"而没敢写。"""
        name = (place.get("name") or "").strip()
        province = norm_region(place.get("province"))
        city = norm_region(place.get("city"))
        dynasty = (place.get("dynasty") or "").strip()
        for level, key in (
            ("名称+省+朝代", (name, province, dynasty)),
            ("名称+省", (name, province)),
            ("名称+市", (name, city)),
            ("名称+朝代", (name, dynasty)),
        ):
            if not all(key[1:]):
                continue
            if key in self.ambiguous[level]:
                return True
        return False


def load_places(con: sqlite3.Connection, only_missing: bool) -> list[dict]:
    where = "WHERE longitude IS NULL OR latitude IS NULL" if only_missing else ""
    rows = con.execute(
        f"SELECT id, name, province, city, dynasty, modern_name FROM places {where} ORDER BY id"
    ).fetchall()
    return [dict(row) for row in rows]


def count_existing(con: sqlite3.Connection) -> int:
    return con.execute(
        "SELECT COUNT(*) FROM places WHERE longitude IS NOT NULL AND latitude IS NOT NULL"
    ).fetchone()[0]


def main() -> int:
    ap = argparse.ArgumentParser(description="把坐标词典回填进 places 表")
    ap.add_argument("--dict", default=str(DEFAULT_DICT), help="坐标词典（build_place_coord_dict.py 的产物）")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="主库路径")
    ap.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    ap.add_argument("--yes", action="store_true", help="确认写库（不加就是 dry-run，防误跑）")
    ap.add_argument("--overwrite", action="store_true", help="连已有坐标的地点一起覆盖")
    ap.add_argument("--unmatched-out", default=str(CACHE_DIR / "unmatched_places.json"),
                    help="没匹配到的地点清单（下一轮高德补抓的输入）")
    ap.add_argument("--show-ambiguous", action="store_true", help="打印因歧义跳过的地名")
    args = ap.parse_args()

    dict_path = Path(args.dict)
    if not dict_path.exists():
        raise SystemExit(
            f"坐标词典不存在：{dict_path}\n"
            "  先跑：python RAG/scripts/build_place_coord_dict.py"
        )
    payload = json.loads(dict_path.read_text(encoding="utf-8"))
    entries = payload.get("entries") or []
    index = CoordIndex(entries)

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"主库不存在：{db_path}")

    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        places = load_places(con, only_missing=not args.overwrite)
        before = count_existing(con)
        print(f"词典 {len(entries)} 条（{payload.get('meta', {}).get('built_at', '未知时间')}）"
              f"；库内已有坐标 {before} 条；本次待处理 {len(places)} 条")

        matched: list[tuple[dict, str, dict]] = []
        unmatched: list[dict] = []
        skipped: list[dict] = []
        for place in places:
            hit = index.lookup(place)
            if hit:
                matched.append((place, hit[0], hit[1]))
            elif index.skipped_ambiguous(place):
                skipped.append(place)
            else:
                unmatched.append(place)

        by_level: dict[str, int] = {}
        for _place, match_level, _entry in matched:
            by_level[match_level] = by_level.get(match_level, 0) + 1
        for match_level in MATCH_LEVELS:
            if by_level.get(match_level):
                print(f"  命中 {match_level}: {by_level[match_level]}")

        print(f"合计命中 {len(matched)} / 待处理 {len(places)}"
              f"；未命中 {len(unmatched)}；因同名歧义跳过 {len(skipped)}")

        if args.dry_run or not args.yes:
            print("（dry-run：未写库；确认无误后加 --yes）")
            return 0

        for place, match_level, entry in matched:
            # 备注写清"这条坐标是怎么来的"：人工复核时一眼能看出匹配层级与高德给的行政层级
            note = " / ".join(
                item for item in [
                    (entry.get("address") or entry.get("modern_name") or "").strip(),
                    f"匹配层级 {match_level}",
                    f"高德层级 {entry.get('level')}" if entry.get("level") else "",
                ] if item
            )
            con.execute(
                "UPDATE places SET longitude = ?, latitude = ?, coord_source = ?, "
                "coord_confidence = ?, coord_note = ? WHERE id = ?",
                (
                    entry["longitude"],
                    entry["latitude"],
                    entry.get("source") or "amap",
                    entry.get("confidence") or "medium",
                    note,
                    place["id"],
                ),
            )
        con.commit()
        after = count_existing(con)
        print(f"✅ 写库完成：带坐标地点 {before} → {after}")

        unmatched_path = Path(args.unmatched_out)
        unmatched_path.parent.mkdir(parents=True, exist_ok=True)
        unmatched_path.write_text(
            json.dumps(unmatched + skipped, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"待补抓清单（未命中 + 歧义跳过）：{len(unmatched) + len(skipped)} 条 → {unmatched_path}")
        if args.show_ambiguous and skipped:
            print("歧义跳过示例：" + "、".join(p["name"] for p in skipped[:20]))

        if after == 0:
            print("❌ 一条坐标都没写进去：坐标词典与当前库对不上，先查词典是不是另一版的产物")
            return 1
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
