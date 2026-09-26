#!/usr/bin/env python3
"""总览构建器的耗时与 SQL 语句数剖析。

`build_map_overview` / `build_timeline_overview` 里是逐地点、逐事件的循环：每个元素
都发一条查询，也就是典型的 N+1。本工具不靠猜，直接量：

    # 全部目标，每项跑 3 次取最好成绩，并附一次 cProfile 热点
    python tools/profile_overviews.py

    # 只看地图总览，跑 5 次
    python tools/profile_overviews.py --only map --runs 5

    # 机器可读输出（前后对比用）
    python tools/profile_overviews.py --json /tmp/overview_before.json

输出里的 `SQL 条数` 是 SQLAlchemy `before_cursor_execute` 钩子的计数：它和 `select`
条数一起，是判断"是否值得下推 SQL"的硬指标——耗时受机器影响，语句数不受。
"""

from __future__ import annotations

import argparse
import cProfile
import io
import json
import pstats
import statistics
import sys
import time
from pathlib import Path

# 允许从 backend/ 目录下直接运行（脚本在 tools/ 子目录）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402  触发 db 初始化与 schema 检查
import report_builders  # noqa: E402
from models import Event, EventPersonRelation, EventPlaceRelation, Organization, Person, Place  # noqa: E402
from models import db  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402

# 目标清单：(名称, 函数, 位置参数, 关键字参数)
TARGETS = [
    ("map_overview", report_builders.build_map_overview, (), {}),
    ("map_overview.html_keyword", report_builders.build_map_overview, ("秦",), {}),
    ("map_overview.dynasty", report_builders.build_map_overview, ("", "秦"), {}),
    ("timeline_overview", report_builders.build_timeline_overview, (), {}),
    ("timeline_overview.keyword", report_builders.build_timeline_overview, ("秦",), {}),
    ("timeline_overview.participant", report_builders.build_timeline_overview, ("", "", "秦"), {}),
]


class StatementCounter:
    """统计执行的 SQL 条数与其中 select 的条数。"""

    def __init__(self):
        self.total = 0
        self.selects = 0
        self.sql_time = 0.0

    def reset(self):
        self.total = 0
        self.selects = 0
        self.sql_time = 0.0

    def as_dict(self):
        return {"statements": self.total, "selects": self.selects,
                "sql_seconds": round(self.sql_time, 4)}


COUNTER = StatementCounter()


@event.listens_for(Engine, "before_cursor_execute")
def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    COUNTER.total += 1
    if statement.lstrip()[:6].lower() in ("select", "with s"):
        COUNTER.selects += 1
    context._zc_t0 = time.perf_counter()


@event.listens_for(Engine, "after_cursor_execute")
def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    started = getattr(context, "_zc_t0", None)
    if started is not None:
        COUNTER.sql_time += time.perf_counter() - started


def table_sizes():
    return {
        "Event": Event.query.count(),
        "Place": Place.query.count(),
        "Person": Person.query.count(),
        "Organization": Organization.query.count(),
        "EventPlaceRelation": EventPlaceRelation.query.count(),
        "EventPersonRelation": EventPersonRelation.query.count(),
    }


def profile_one(fn, args, kwargs, runs: int, top: int):
    # 预热一次：避开首次查询的建连接 / 缓存填充
    fn(*args, **kwargs)

    times = []
    counts = []
    for _ in range(runs):
        COUNTER.reset()
        t0 = time.perf_counter()
        fn(*args, **kwargs)
        times.append(time.perf_counter() - t0)
        counts.append(COUNTER.as_dict())

    profiler = cProfile.Profile()
    profiler.enable()
    fn(*args, **kwargs)
    profiler.disable()
    buf = io.StringIO()
    pstats.Stats(profiler, stream=buf).sort_stats("cumulative").print_stats(top)
    hot = [line.strip() for line in buf.getvalue().splitlines()
           if line.strip().startswith(("ncalls", "{")) is False and "/" in line][:top]

    return {
        "runs": runs,
        "best_seconds": round(min(times), 4),
        "median_seconds": round(statistics.median(times), 4),
        "all_seconds": [round(t, 4) for t in times],
        "statements_best": min(c["statements"] for c in counts),
        "selects_best": min(c["selects"] for c in counts),
        "sql_seconds_best": min(c["sql_seconds"] for c in counts),
        "hot": hot,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="总览构建器剖析")
    parser.add_argument("--runs", type=int, default=3, help="每项重复次数（取最好）")
    parser.add_argument("--only", choices=["map", "timeline", "all"], default="all")
    parser.add_argument("--top", type=int, default=8, help="cProfile 热点条数")
    parser.add_argument("--json", help="结果写入 JSON（前后对比用）")
    args = parser.parse_args()

    targets = [t for t in TARGETS
               if args.only == "all" or t[0].startswith(args.only)]

    with app_module.app.app_context():
        sizes = table_sizes()
        print("数据规模:", ", ".join(f"{k}={v}" for k, v in sizes.items()))
        print()
        results = {}
        for name, fn, pargs, pkwargs in targets:
            result = profile_one(fn, pargs, pkwargs, args.runs, args.top)
            results[name] = result
            print(f"[{name}] 参数={list(pargs)}")
            print(f"  最好 {result['best_seconds']}s（中位 {result['median_seconds']}s，"
                  f"各次 {result['all_seconds']}）")
            print(f"  SQL: 共 {result['statements_best']} 条，其中 select {result['selects_best']} 条，"
                  f"SQL 占用 {result['sql_seconds_best']}s")
            print("  热点（cumulative）:")
            for line in result["hot"]:
                print(f"    {line}")
            print()

    if args.json:
        Path(args.json).write_text(
            json.dumps({"tables": sizes, "targets": results}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"已写入 {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
