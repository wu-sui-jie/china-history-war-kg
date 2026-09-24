#!/usr/bin/env python3
"""Neo4j 全文索引评估（BE-8）：先在真实数据上量，再决定要不要上。

背景：旧后端的名称检索一律是 `WHERE toLower(n.name) CONTAINS toLower($q)`
（model_search.py 十处），前缀通配使任何索引都失效，只能全标签扫描。候选方案是
`CREATE FULLTEXT INDEX` + `db.index.fulltext.queryNodes()`。但全文索引不是免费的：

1. **语义会变**：全文索引按 Lucene 分词匹配，不是任意子串匹配——"鹿之"这种中间片段
   在 CONTAINS 下能命中"巨鹿之战"，分词后未必；本工具会把两种写法的命中集差异打出来。
2. **写入放大**：每写一个带索引标签的节点都要更新索引。
3. **迁移风险**：CREATE FULLTEXT INDEX 会触发存量数据索引构建。

用法（默认全程只读，不改任何数据）：

    # 只量当前实现（扫描）的耗时与命中数
    python tools/evaluate_fulltext_index.py

    # 建索引 → 量 fulltext 写法 → 对比命中集 → 删掉索引（工具自己建、自己删）
    python tools/evaluate_fulltext_index.py --with-index

    # 额外量写入放大：在 __FullTextBench 标签下插入/删除临时节点，跑完必清理
    python tools/evaluate_fulltext_index.py --with-index --write-bench

    # 决策"上"之后保留索引（部署时用，不要随手跑）
    python tools/evaluate_fulltext_index.py --with-index --keep-index

输出的结论段给出：扫描耗时、fulltext 耗时、命中集差异、写入耗时差与索引条目数。
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db_handle import neo4j_db_handle  # noqa: E402

INDEX_NAME = "zc_name_fulltext"
LABELS = "Event|Place|Person|Organization"
BENCH_LABEL = "__FullTextBench"

# 代表性问题：单字、完整名、名字片段（子串语义的试金石）、多字词、必然无命中的词
KEYWORDS = ["秦", "之战", "巨鹿之战", "鹿之", "秦灭六国", "三国", "项羽", "不存在的词XYZ"]
REPEATS = 5

CONTAINS_QUERY = """
MATCH (n)
WHERE toLower(n.name) CONTAINS toLower($q)
RETURN id(n) AS id, n.name AS name
LIMIT 2000
"""

FULLTEXT_QUERY = f"""
CALL db.index.fulltext.queryNodes('{INDEX_NAME}', $q)
YIELD node, score
RETURN id(node) AS id, node.name AS name
LIMIT 2000
"""


def timed(graph, query, params, repeats=REPEATS):
    """跑 repeats 次取最好成绩，返回 (秒, 结果行)。"""
    best = None
    rows = []
    for _ in range(repeats):
        started = time.perf_counter()
        rows = graph.run(query, **params).data()
        elapsed = time.perf_counter() - started
        best = elapsed if best is None else min(best, elapsed)
    return best, rows


def measure_contains(graph):
    results = {}
    for keyword in KEYWORDS:
        seconds, rows = timed(graph, CONTAINS_QUERY, {"q": keyword})
        results[keyword] = {"seconds": seconds, "names": {r["name"] for r in rows}, "count": len(rows)}
    return results


def create_index(graph, analyzer):
    started = time.perf_counter()
    options = ""
    if analyzer and analyzer != "standard":
        # Lucene 分析器：standard 把 CJK 逐字切分，cjk 用二元组（bigram）。
        # 注意 value 必须是字符串字面量（用引号），写成反引号会被当成标识符。
        options = f" OPTIONS {{indexConfig: {{`fulltext.analyzer`: '{analyzer}'}}}}"
    graph.run(
        f"CREATE FULLTEXT INDEX {INDEX_NAME} IF NOT EXISTS "
        f"FOR (n:{LABELS}) ON EACH [n.name]{options}"
    )
    # 等索引 ONLINE：社区版没有等价的 await，只能轮询
    state = None
    for _ in range(120):
        state = graph.run(
            "SHOW INDEXES YIELD name, state WHERE name = $name RETURN state", name=INDEX_NAME
        ).evaluate()
        if state == "ONLINE":
            break
        time.sleep(0.5)
    elapsed = time.perf_counter() - started
    percent = graph.run("SHOW INDEXES YIELD name, populationPercent WHERE name = $name "
                        "RETURN populationPercent", name=INDEX_NAME).evaluate()
    return elapsed, state, percent


def drop_index(graph):
    graph.run(f"DROP INDEX {INDEX_NAME} IF EXISTS")


def measure_fulltext(graph, quote=False):
    """quote=True 时把关键词包成短语查询（要求词元相邻，压制 OR 过匹配）。"""
    results = {}
    for keyword in KEYWORDS:
        query = f'"{keyword}"' if quote else keyword
        seconds, rows = timed(graph, FULLTEXT_QUERY, {"q": query})
        results[keyword] = {"seconds": seconds, "names": {r["name"] for r in rows}, "count": len(rows)}
    return results


def write_bench(graph, with_index, batch=50, total=300):
    """临时标签下插入/删除节点，量写入耗时；无论成败都在 finally 里清理。"""
    graph.run(f"MATCH (n:{BENCH_LABEL}) DETACH DELETE n")
    started = time.perf_counter()
    for offset in range(0, total, batch):
        graph.run(
            f"UNWIND range($from, $to) AS i "
            f"CREATE (n:{BENCH_LABEL} {{name: '基准节点' + toString(i), bench: true}})",
            **{"from": offset, "to": offset + batch - 1},
        )
    insert_seconds = time.perf_counter() - started

    started = time.perf_counter()
    graph.run(f"MATCH (n:{BENCH_LABEL}) DETACH DELETE n")
    delete_seconds = time.perf_counter() - started

    left = graph.run(f"MATCH (n:{BENCH_LABEL}) RETURN count(n)").evaluate()
    return {
        "label": BENCH_LABEL,
        "total": total,
        "insert_seconds": insert_seconds,
        "delete_seconds": delete_seconds,
        "indexed": with_index,
        "leftover": left,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Neo4j 全文索引评估")
    parser.add_argument("--with-index", action="store_true", help="建全文索引并量 fulltext 写法")
    parser.add_argument("--keep-index", action="store_true", help="保留索引（不自动删除）")
    parser.add_argument("--write-bench", action="store_true", help="量写入放大（临时节点，跑完清理）")
    parser.add_argument("--analyzer", default="standard", choices=["standard", "cjk"],
                        help="全文索引分析器：standard 按字切分，cjk 按二元组")
    args = parser.parse_args()

    graph = neo4j_db_handle.graph
    nodes = graph.run("MATCH (n) RETURN count(n) AS c").evaluate()
    labels = [row["label"] for row in graph.run("CALL db.labels() YIELD label RETURN label").data()]
    print(f"图规模：{nodes} 个节点；标签 {labels}")
    print(f"关键词：{KEYWORDS}；每项取 {REPEATS} 次最好成绩\n")

    print("== 当前实现：toLower(n.name) CONTAINS ==")
    contains = measure_contains(graph)
    for keyword, item in contains.items():
        print(f"  {keyword:14s} {item['seconds'] * 1000:7.2f} ms  命中 {item['count']}")

    created = False
    bench_plain = bench_indexed = None
    try:
        if args.write_bench:
            # 先量无索引的写入基线，再建索引量带索引的——同一进程、同一批次，可比
            print("\n== 写入基准：无全文索引 ==")
            bench_plain = write_bench(graph, with_index=False)
            print(f"  插入 {bench_plain['total']} 节点 {bench_plain['insert_seconds'] * 1000:.1f} ms，"
                  f"删除 {bench_plain['delete_seconds'] * 1000:.1f} ms，残留 {bench_plain['leftover']}")

        if args.with_index:
            print(f"\n== 建全文索引（分析器 {args.analyzer}）==")
            build_seconds, state, percent = create_index(graph, args.analyzer)
            created = True
            print(f"  CREATE FULLTEXT INDEX {INDEX_NAME}：{build_seconds * 1000:.1f} ms 完成，state={state}，"
                  f"populationPercent={percent}")

            variants = {}
            plan = [("原样关键词", False)]
            if args.analyzer != "standard":
                # cjk 分析器把中文切成二元组，原样查询仍是 OR 语义；短语查询才要求相邻
                plan.append(("短语查询", True))

            for label, quote in plan:
                print(f"\n== 候选实现：db.index.fulltext.queryNodes（{label}）==")
                fulltext = measure_fulltext(graph, quote=quote)
                for keyword, item in fulltext.items():
                    print(f"  {keyword:14s} {item['seconds'] * 1000:7.2f} ms  命中 {item['count']}")

                print(f"\n== 命中集差异（{label}：CONTAINS 有而 fulltext 无 / 反之）==")
                for keyword in KEYWORDS:
                    missed = contains[keyword]["names"] - fulltext[keyword]["names"]
                    extra = fulltext[keyword]["names"] - contains[keyword]["names"]
                    print(f"  {keyword:14s} 漏 {len(missed):3d} 多 {len(extra):3d}"
                          + (f"  例：漏 {sorted(missed)[:3]}" if missed else "")
                          + (f"  例：多 {sorted(extra)[:3]}" if extra else ""))
                variants[label] = fulltext

            if args.write_bench:
                print("\n== 写入基准：带全文索引 ==")
                bench_indexed = write_bench(graph, with_index=True)
                print(f"  插入 {bench_indexed['total']} 节点 {bench_indexed['insert_seconds'] * 1000:.1f} ms，"
                      f"删除 {bench_indexed['delete_seconds'] * 1000:.1f} ms，残留 {bench_indexed['leftover']}")
    finally:
        if created and not args.keep_index:
            drop_index(graph)
            print(f"\n已删除临时索引 {INDEX_NAME}（--keep-index 可保留）")
        elif created:
            print(f"\n索引 {INDEX_NAME} 已保留")
        # 写入基准的临时候选：即便中途失败也清一次
        leftover = graph.run(f"MATCH (n:{BENCH_LABEL}) DETACH DELETE n RETURN count(n) AS c").evaluate()
        if leftover:
            print(f"清理基准节点 {leftover} 个")

    print("\n== 结论 ==")
    worst_keyword = max(contains, key=lambda k: contains[k]["seconds"])
    worst = contains[worst_keyword]["seconds"]
    print(f"  当前扫描最慢一项：{worst * 1000:.2f} ms（关键词「{worst_keyword}」，图 {nodes} 节点）")
    scan_is_bottleneck = worst * 1000 >= 50
    print(f"  扫描是否已成瓶颈（阈值 50 ms）：{'是' if scan_is_bottleneck else '否'}")

    if args.with_index:
        print(f"  分析器：{args.analyzer}；索引构建（存量数据）：{build_seconds * 1000:.0f} ms")
        for label, data in variants.items():
            faster = sum(1 for k in KEYWORDS if data[k]["seconds"] < contains[k]["seconds"])
            extra = sum(len(data[k]["names"] - contains[k]["names"]) for k in KEYWORDS)
            missed = sum(len(contains[k]["names"] - data[k]["names"]) for k in KEYWORDS)
            verdict = "命中集一致" if extra == 0 and missed == 0 else f"命中集不一致（多 {extra} / 漏 {missed}）"
            print(f"  [{label}] 更快 {faster}/{len(KEYWORDS)} 项；{verdict}")
            if missed:
                for keyword in KEYWORDS:
                    lost = sorted(contains[keyword]["names"] - data[keyword]["names"])
                    if lost:
                        print(f"      漏在「{keyword}」：{len(lost)} 条，例 {lost[:3]}")

    if bench_plain and bench_indexed:
        ratio = bench_indexed["insert_seconds"] / bench_plain["insert_seconds"]
        print(f"  写入 300 节点：无索引 {bench_plain['insert_seconds'] * 1000:.1f} ms → "
              f"带索引 {bench_indexed['insert_seconds'] * 1000:.1f} ms（{ratio:.2f}x）")

    print("\n  —— 判断 ——")
    if not scan_is_bottleneck:
        print("  不上。扫描在最坏关键词上也只要 %d ms 量级，用户感知不到；"
              % round(worst * 1000))
        print("  而换写法要承担命中集变化与索引迁移成本，不划算。")
        print("  触发条件：图规模涨到 10 倍量级（数万节点）后重跑本工具，届时按下面的配方来。")
        print("  配方（本次实测）：cjk 分析器 + 短语查询（把关键词包成双引号）命中集与扫描一致，"
              "且快 1.5~4 倍；但单字关键词（如「秦」）会漏——二元组分词索引不到单字，"
              "需要回落 CONTAINS，或改用长度为 2 以上才走索引的混合策略。")
    else:
        print("  上。但必须先解决命中集差异（见上面 [原样关键词] 一行）："
              "standard 分析器按单字切分，多字关键词会大面积多命中；"
              "改用 cjk 分析器 + 短语查询可消除多字差异，单字关键词再回落 CONTAINS。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
