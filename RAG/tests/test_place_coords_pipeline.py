"""地点坐标链路：词典（按名称键）与回填（分层匹配 + 歧义保护）。

**为什么要有这份用例。** 坐标在 2026-09-27 换代后整批丢过一次：5527 个地点 0 条坐标，
而 4819 条高德坐标还躺在磁盘上。根因是"回填"这件事从来不在发布流程里（旧链路是导入后
手工跑一次旧项目的 geocoding import），而它按 `place_id` 写库——换代重导会整表重建
places、主键全部错位（实测旧 id 268 是"洛水"，新 id 268 是"大梁"），所以那条路既容易漏、
漏了也不报错，还只能对上旧库。

改成"名称键词典 + 分层匹配"之后，这几条口径必须钉住，任何一条被放松都会写出**错的坐标**
（比没有坐标更糟：地图上会显示到别的地方去）：

1. 换代/主键错位不影响结果——词典按名称匹配，不认 `place_id`；
2. 同一个键上词典给了两个不同坐标时**宁可不写**；
3. 高德返回的行政层级要折成 high/medium/low（省/国家级就是"低置信"，
   直接当高置信用会让地图页的"低置信坐标"统计失去意义）；
4. 一条都没命中时退出码非 0 —— `scripts/publish.py` 靠它拦住"发布一份没有坐标的数据"。
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    """按文件路径导入脚本（`scripts/` 不是包，且脚本自己锚定 RAG 根）。"""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


build_dict = _load("build_place_coord_dict")
apply_coords = _load("apply_place_coords")


# ---------------------------------------------------------------- 夹具

PLACE_COLUMNS = ("id", "name", "modern_name", "dynasty", "province", "city", "district",
                 "specific_location", "longitude", "latitude", "coord_source",
                 "coord_confidence", "coord_note")

PLACE_SCHEMA = """
CREATE TABLE places (
    id INTEGER PRIMARY KEY,
    name TEXT, modern_name TEXT, dynasty TEXT,
    province TEXT, city TEXT, district TEXT, specific_location TEXT,
    longitude REAL, latitude REAL,
    coord_source TEXT, coord_confidence TEXT, coord_note TEXT
)
"""


def _insert(path: Path, rows: list[dict], extra_defaults: dict) -> None:
    """按列名插入：用例只写它关心的字段，其余走默认值（None / 空串）。"""
    con = sqlite3.connect(str(path))
    con.execute(PLACE_SCHEMA)
    defaults = {column: None for column in PLACE_COLUMNS}
    defaults.update(extra_defaults)
    for row in rows:
        values = {**defaults, **row}
        con.execute(
            f"INSERT INTO places ({', '.join(PLACE_COLUMNS)}) "
            f"VALUES ({', '.join(':' + column for column in PLACE_COLUMNS)})",
            values,
        )
    con.commit()
    con.close()


def make_coord_db(path: Path, rows: list[dict]):
    """造一个"带坐标的库"（相当于换代前的那份备份）。

    没写经纬度的行给一个占位坐标——`load_entries_from_db` 只认"有坐标"的行，
    而用例考的是键与匹配，不是这家地点究竟在哪。
    """
    _insert(path, rows, {"longitude": 110.0, "latitude": 30.0,
                         "coord_source": "amap", "coord_confidence": "区县",
                         "coord_note": "高德测试"})


def make_place_db(path: Path, rows: list[dict]):
    """造一个"待回填的库"（相当于换代后的新库：坐标字段全空、主键已错位）。"""
    _insert(path, rows, {})


def make_place_row(path: Path, row: dict) -> None:
    """往已有的待回填库里再加一行。"""
    con = sqlite3.connect(str(path))
    values = {**{column: None for column in PLACE_COLUMNS}, **row}
    con.execute(
        f"INSERT INTO places ({', '.join(PLACE_COLUMNS)}) "
        f"VALUES ({', '.join(':' + column for column in PLACE_COLUMNS)})",
        values,
    )
    con.commit()
    con.close()


def read_places(path: Path) -> dict[str, dict]:
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    try:
        return {row["name"]: dict(row) for row in con.execute("SELECT * FROM places")}
    finally:
        con.close()


def run_apply(monkeypatch, dict_path: Path, db_path: Path, *extra: str) -> int:
    argv = ["apply_place_coords.py", "--dict", str(dict_path), "--db", str(db_path),
            "--unmatched-out", str(db_path.parent / "unmatched.json"), "--yes", *extra]
    monkeypatch.setattr(sys, "argv", argv)
    return apply_coords.main()


# ---------------------------------------------------------------- 词典构建

def test_词典按名称与行政区建键_与主键无关(tmp_path):
    coord_db = tmp_path / "bak.sqlite"
    make_coord_db(coord_db, [{"id": 268, "name": "洛水", "modern_name": "陕西北部",
                              "dynasty": "战国", "province": "陕西省", "city": "延安市"}])
    out = tmp_path / "dict.json"

    monkeypatched = build_dict.pick_context_db  # 直接调内部函数，绕开"自动挑库"
    assert monkeypatched  # 存在即可
    entries, context = build_dict.load_entries_from_db(coord_db)

    assert entries[0]["name"] == "洛水"
    assert entries[0]["province"] == "陕西省"
    # 置信度字段：库里是旧口径的原始 level（这里是"区县"），要折成三档
    assert entries[0]["confidence"] == "medium"
    assert set(context[268]) >= {"name", "province", "city", "dynasty"}
    assert not out.exists()


def test_高德行政层级折成三档():
    assert build_dict.confidence_for("村庄") == "high"
    assert build_dict.confidence_for("区县") == "medium"
    assert build_dict.confidence_for("市") == "medium"
    # 省/国家级只是兜底定位，必须如实标低——地图页的"低置信坐标"就靠它
    assert build_dict.confidence_for("省") == "low"
    assert build_dict.confidence_for("国家") == "low"
    assert build_dict.confidence_for("") == "low"


# ---------------------------------------------------------------- 回填匹配

def _write_dict(tmp_path: Path, coord_db: Path) -> Path:
    entries, _ctx = build_dict.load_entries_from_db(coord_db)
    dict_path = tmp_path / "dict.json"
    dict_path.write_text(json.dumps({"meta": {}, "entries": entries}, ensure_ascii=False),
                         encoding="utf-8")
    return dict_path


def test_回填不依赖place_id(monkeypatch, tmp_path):
    """新旧主键错位（旧 268 = 洛水，新 268 = 大梁）也要写对地方。"""
    coord_db = tmp_path / "bak.sqlite"
    make_coord_db(coord_db, [{"id": 268, "name": "洛水", "modern_name": "陕西北部",
                              "dynasty": "战国", "province": "陕西省", "city": "延安市"}])
    place_db = tmp_path / "database"
    make_place_db(place_db, [{"id": 268, "name": "大梁", "modern_name": "开封市",
                              "dynasty": "战国", "province": "河南省", "city": "开封市"}])
    # 另放一个能命中的地点：脚本的契约是"一条都没写进去才退出 1"，
    # 只有一条不匹配的地点时退出码本来就该是 1，那样就测不出"有没有写错"了。
    make_place_row(place_db, {"id": 269, "name": "洛水", "modern_name": "陕西北部",
                             "dynasty": "战国", "province": "陕西省", "city": "延安市"})

    assert run_apply(monkeypatch, _write_dict(tmp_path, coord_db), place_db) == 0

    places = read_places(place_db)
    # 大梁没有词典条目 → 仍为空；不能因为"id 268 有坐标"就把它写到大梁上
    assert places["大梁"]["longitude"] is None
    assert places["洛水"]["longitude"] is not None


def test_按名称加朝代命中并折低置信(monkeypatch, tmp_path):
    coord_db = tmp_path / "bak.sqlite"
    make_coord_db(coord_db, [{"id": 1, "name": "洛水", "modern_name": "陕西北部",
                              "dynasty": "战国", "province": "陕西省", "city": "延安市"}])
    place_db = tmp_path / "database"
    # province 与旧库不同 → 名称+省不命中，退到名称+朝代
    make_place_db(place_db, [{"id": 1, "name": "洛水", "modern_name": "陕西北部",
                              "dynasty": "战国"}])

    assert run_apply(monkeypatch, _write_dict(tmp_path, coord_db), place_db) == 0

    row = read_places(place_db)["洛水"]
    assert row["coord_source"] == "amap"
    assert row["longitude"] is not None
    assert "名称+朝代" in row["coord_note"]


def test_同名不同地点时不按名称硬匹配(monkeypatch, tmp_path):
    """同一个地名在不同朝代是两个地方（"新城""东京"这类）→ 不猜，落进补抓清单。"""
    coord_db = tmp_path / "bak.sqlite"
    make_coord_db(coord_db, [
        {"id": 1, "name": "新城", "dynasty": "战国", "longitude": 100.0, "latitude": 30.0},
        {"id": 2, "name": "新城", "dynasty": "明", "longitude": 120.0, "latitude": 40.0},
    ])
    place_db = tmp_path / "database"
    # 新库这一行没标朝代、也没标省 → 无法判断它是哪一个"新城"
    make_place_db(place_db, [{"id": 1, "name": "新城"}])

    assert run_apply(monkeypatch, _write_dict(tmp_path, coord_db), place_db) == 1

    assert read_places(place_db)["新城"]["longitude"] is None
    # 没写进去的条目要进"下一轮补抓清单"，否则它永远没人管
    unmatched = json.loads((tmp_path / "unmatched.json").read_text(encoding="utf-8"))
    assert [item["name"] for item in unmatched] == ["新城"]


def test_同一个键上坐标冲突时宁可不写(monkeypatch, tmp_path):
    """合并多份产物时，同一（名称+省+朝代）可能出现两个不同坐标 → 该键整体作废。

    这是"名称键"相比 `place_id` 唯一真正危险的地方：按 id 写库不会串，按名字匹配会。
    所以冲突键必须整键丢弃，而不是"先到先得"。
    """
    coord_db = tmp_path / "bak.sqlite"
    make_coord_db(coord_db, [
        {"id": 1, "name": "新城", "province": "河南省", "dynasty": "战国",
         "longitude": 100.0, "latitude": 30.0},
        {"id": 2, "name": "新城", "province": "河南省", "dynasty": "战国",
         "longitude": 120.0, "latitude": 40.0},
    ])
    place_db = tmp_path / "database"
    make_place_db(place_db, [{"id": 1, "name": "新城", "province": "河南省", "dynasty": "战国"}])

    assert run_apply(monkeypatch, _write_dict(tmp_path, coord_db), place_db,
                     "--show-ambiguous") == 1

    assert read_places(place_db)["新城"]["longitude"] is None
    unmatched = json.loads((tmp_path / "unmatched.json").read_text(encoding="utf-8"))
    assert any("新城" == item["name"] for item in unmatched)


def test_一条都没命中时退出码非零(monkeypatch, tmp_path):
    """这是 publish.py 拦住"发布无坐标数据"的哨兵。"""
    coord_db = tmp_path / "bak.sqlite"
    make_coord_db(coord_db, [{"id": 1, "name": "洛水", "dynasty": "战国",
                              "province": "陕西省", "city": "延安市"}])
    place_db = tmp_path / "database"
    make_place_db(place_db, [{"id": 1, "name": "完全不相干的地名"}])

    assert run_apply(monkeypatch, _write_dict(tmp_path, coord_db), place_db) == 1


def test_已有坐标默认不覆盖(monkeypatch, tmp_path):
    coord_db = tmp_path / "bak.sqlite"
    make_coord_db(coord_db, [{"id": 1, "name": "洛水", "dynasty": "战国",
                              "province": "陕西省", "city": "延安市"}])
    place_db = tmp_path / "database"
    make_place_db(place_db, [{"id": 1, "name": "洛水", "dynasty": "战国",
                              "province": "陕西省", "city": "延安市"}])
    con = sqlite3.connect(str(place_db))
    con.execute("UPDATE places SET longitude = 1.0, latitude = 2.0, coord_source = 'manual'")
    con.commit()
    con.close()

    assert run_apply(monkeypatch, _write_dict(tmp_path, coord_db), place_db) == 0

    row = read_places(place_db)["洛水"]
    assert row["coord_source"] == "manual"
    assert row["longitude"] == 1.0


def test_词典缺失时明确报错(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "apply_place_coords.py", "--dict", str(tmp_path / "nope.json"),
        "--db", str(tmp_path / "database"), "--yes",
    ])
    with pytest.raises(SystemExit) as excinfo:
        apply_coords.main()
    assert "先跑" in str(excinfo.value)
