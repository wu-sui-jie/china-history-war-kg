#!/usr/bin/env python
"""数据发布全流程：一条命令把"抽取产物 → 知识库 → RAG 可用"走完。

## 为什么要有它

换数据版本原先要**手工走 9~10 步**（重放产物 / 导 SQLite / 同步 Neo4j / 导出快照 /
生成规则推理产物 / 建索引 / 评测 / 评 demo / 血缘 / 制品清单），而步骤只散落在四份文档里。
后果是**漏一步不报错、只是结果缺一块**：2026-09-27 那两次发布，我手工走了三遍、漏了两遍
（漏跑 `inferred_relations.json`，规则推理检索通道静默少一块；`gen_demo_examples --run`
传成完整路径，脚本打一行错就正常退出）。

所以本脚本不只是"把命令串起来"，它把**每一步该有的断言**也写进来了——
断言就是那两次漏跑的症状（产物条数、图库是否相等、推理产物在不在、索引段数与向量数是否对齐）。

## 用法

    python scripts/publish.py --version 20260927_v4              # 只打印计划（默认，安全）
    python scripts/publish.py --version 20260927_v4 --verify-only # 不跑命令，只核对现有版本
    python scripts/publish.py --version 20260927_v4 --yes         # 真跑
    python scripts/publish.py --version X --rebuild-pred --yes    # 连产物一起重放（慢，7 分钟）
    python scripts/publish.py --version X --with-eval --yes       # 含评测与 demo（要调大模型）

## 三组步骤

    A 数据入库  ① 重放产物（仅 --rebuild-pred）② 导入 SQLite  ③ 同步 Neo4j
    B RAG 制品  ④ 导出快照  ⑤ 规则推理产物  ⑥ 建索引  ⑦ SBOM  ⑧ 血缘  ⑨ 制品清单  ⑩ 文档核对
    C 评测线    ⑪ 真实模型评测  ⑫ demo 清单（仅 --with-eval；评分需人工/AI 补，见下）

## 评测线的两个人工口子

`run_evaluation.py run` 产出 `scoring_template.jsonl`（28 条待评分）。评分由人或 AI 代理填，
再用 `review_bank.py scores-apply` 回写。本脚本**不代评分**（那是判断题，不是机械步骤）：
若 `scores.jsonl` 不存在，第 ⑫ 步会跳过并提示怎么补；补完再单独跑 ⑫。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENTITY = REPO / "entity-event-relation"
BACKEND = REPO / "backend"
RAG = REPO / "RAG"
PY = sys.executable

#: 语料（批次名与产物目录同名；换语料时改这里）
CORPUS = "data/中国历代战争简史.txt"
BATCH = "中国历代战争简史"

ENTITY_TABLES = ("events", "places", "persons", "organizations")
RELATION_TABLES = ("event_event_relations", "event_place_relations",
                   "event_person_relations", "event_organization_rel")


def say(msg: str = "") -> None:
    print(msg, flush=True)


def run_step(title: str, cmd: list[str], cwd: Path) -> str:
    """跑一步，失败即停（并把命令原文打出来，便于手工复现）。"""
    say(f"\n{'=' * 72}\n▶ {title}\n  cwd: {cwd.name}/   cmd: {' '.join(cmd)}\n{'=' * 72}")
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    out = (proc.stdout or "") + (proc.stderr or "")
    say(out.rstrip()[-4000:] if len(out) > 4000 else out.rstrip())
    if proc.returncode != 0:
        say(f"\n❌ 这一步失败（退出码 {proc.returncode}），已停下——后面的步骤不该在坏数据上继续。")
        say(f"   手工复现：cd {cwd} && {' '.join(cmd)}")
        sys.exit(1)
    return out


def sqlite_counts() -> dict:
    con = sqlite3.connect(str(BACKEND / "database"))
    try:
        out = {}
        for t in ENTITY_TABLES + RELATION_TABLES:
            out[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        return out
    finally:
        con.close()


def neo4j_counts() -> tuple[int, int]:
    """(节点数, 关系数)。用 py2neo（与后端同一套驱动与配置）。"""
    sys.path.insert(0, str(BACKEND))
    import local_settings  # noqa: PLC0415
    from py2neo import Graph  # noqa: PLC0415
    graph = Graph(local_settings.NEO4J_URI,
                  auth=(local_settings.NEO4J_USER, local_settings.require_neo4j_password()))
    nodes = graph.run("MATCH (n) RETURN count(n) AS c").data()[0]["c"]
    rels = graph.run("MATCH ()-[r]->() RETURN count(r) AS c").data()[0]["c"]
    return nodes, rels


def verify(version: str, with_eval: bool) -> bool:
    """核对版本的自洽性。**每一项都对应一次真实踩过的坑**，不是形式检查。"""
    say(f"\n{'=' * 72}\n▶ 核对 {version} 的自洽性\n{'=' * 72}")
    ok = True

    def check(label: str, passed: bool, detail: str) -> None:
        nonlocal ok
        say(f"  {'✓' if passed else '✗'} {label}：{detail}")
        ok = ok and passed

    # 1) SQLite 有数据（导入步骤跑过、且没有整表为空）
    c = sqlite_counts()
    check("SQLite 四类实体与关系均非空",
          all(v > 0 for v in c.values()),
          " ".join(f"{k}={v}" for k, v in c.items()))
    sqlite_nodes = sum(c[t] for t in ENTITY_TABLES)
    sqlite_rels = sum(c[t] for t in RELATION_TABLES)

    # 2) 图与库**逐项相等**——2026-09-27 那次图内边少 32 条的根因就在这里
    #    （同步器 `if not neo4j_id` 把 id=0 误判成失败，21 条关系静默丢失）
    try:
        n_nodes, n_rels = neo4j_counts()
        check("Neo4j 节点数 == SQLite 行数", n_nodes == sqlite_nodes,
              f"图 {n_nodes} / 库 {sqlite_nodes}")
        check("Neo4j 关系数 == SQLite 关系数", n_rels == sqlite_rels,
              f"图 {n_rels} / 库 {sqlite_rels}"
              + ("" if n_rels == sqlite_rels else "（差值先看：同步器跳过/塌陷计数）"))
    except Exception as exc:  # noqa: BLE001
        check("Neo4j 可达", False, f"{type(exc).__name__}: {exc}")

    # 3) 快照齐备，且 count 与库一致
    snap = RAG / "data" / "snapshot" / version
    if (snap / "manifest.json").is_file():
        manifest = json.loads((snap / "manifest.json").read_text(encoding="utf-8"))
        counts = manifest.get("counts") or {}
        check("快照 relations 与库一致",
              counts.get("relations") == sqlite_rels,
              f"快照 {counts.get('relations')} / 库 {sqlite_rels}")
        # 规则推理产物**单独一步生成**，漏跑过一次：它在，检索的"规则推理"通道才在
        check("快照含 inferred_relations.json", (snap / "inferred_relations.json").is_file(),
              "缺它 = 规则推理检索通道静默少一块")
    else:
        check("快照目录存在", False, str(snap))

    # 4) 索引齐备，段数与向量数对齐
    index = RAG / "data" / "index" / version
    if (index / "manifest.json").is_file():
        n_chunks = sum(1 for _ in (index / "chunks.jsonl").open(encoding="utf-8"))
        ids = json.loads((index / "vectors" / "ids.json").read_text(encoding="utf-8"))
        check("索引段数 == 向量条数", n_chunks == len(ids), f"{n_chunks} / {len(ids)}")
    else:
        check("索引目录存在", False, str(index))

    # 5) 血缘与制品清单都指向这个版本（漏重建会让它们停在旧版本上）
    lineage = RAG / "data" / "release" / "lineage.json"
    if lineage.is_file():
        check("血缘版本 == " + version,
              json.loads(lineage.read_text(encoding="utf-8")).get("version") == version,
              json.loads(lineage.read_text(encoding="utf-8")).get("version") or "(空)")
    manifest_file = RAG / "data" / "release" / "artifact-manifest.json"
    if manifest_file.is_file():
        got = json.loads(manifest_file.read_text(encoding="utf-8")).get("source_version")
        check("制品清单版本 == " + version, got == version, got or "(空)")

    # 6) 评测线（可选）：demo 在不在——它不在，/api/demo/examples 会 503
    if with_eval:
        demo = RAG / "data" / "eval" / version / "demo_examples.json"
        check("demo 清单存在", demo.is_file(),
              "缺它首页示例题返回 503（评分补完后跑第 ⑫ 步）")

    say(f"\n{'✅ 全部通过' if ok else '❌ 有不一致项（见上）'}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="数据发布全流程")
    ap.add_argument("--version", required=True, help="快照/索引/评测的版本号，如 20260927_v4")
    ap.add_argument("--yes", action="store_true", help="真跑（不加只打印计划）")
    ap.add_argument("--verify-only", action="store_true", help="不跑命令，只核对现有版本")
    ap.add_argument("--rebuild-pred", action="store_true",
                    help="先重放抽取产物（改过产物侧规则时才需要；缓存命中也要约 7 分钟）")
    ap.add_argument("--with-eval", action="store_true",
                    help="跑真实模型评测与 demo（要调大模型；评分另需人工/AI 补）")
    ap.add_argument("--skip", default="", help="逗号分隔的步骤名，跳过它们")
    args = ap.parse_args()
    version = args.version.strip()
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}

    steps: list[tuple[str, str, list[str], Path]] = []
    if args.rebuild_pred:
        steps.append(("① 重放抽取产物", "rebuild", [PY, "main.py", CORPUS], ENTITY))
    steps += [
        ("② 导入 SQLite（导的是发布子集 published/final.json）", "import",
         [PY, "import_json_to_sqlite.py", "--yes"], BACKEND),
        ("③ 同步 Neo4j（全量）", "sync", [PY, "sync_sqlite_to_neo4j.py", "--mode", "full"], BACKEND),
        ("④ 导出 RAG 快照", "snapshot", [PY, "scripts/export_snapshot.py", "--version", version], RAG),
        ("⑤ 生成规则推理产物", "infer",
         [PY, "scripts/build_inferred_relations.py", "--version", version], RAG),
        ("⑥ 建索引（切分 + FTS + 向量）", "index",
         [PY, "scripts/build_index.py", "--version", version], RAG),
        ("⑦ 生成 SBOM", "sbom", [PY, "scripts/gen_sbom.py", "generate", "--version", version], RAG),
        ("⑧ 重建数据血缘", "lineage", [PY, "scripts/build_lineage.py", "--version", version], RAG),
        ("⑨ 生成制品清单", "manifest",
         [PY, "scripts/build_artifact_manifest.py", "build", "--version", version], RAG),
        ("⑩ 校验制品清单", "verify-manifest",
         [PY, "scripts/build_artifact_manifest.py", "verify"], RAG),
        ("⑪ 文档数字核对", "docs", [PY, "scripts/check_docs.py", "--strict"], RAG),
    ]
    if args.with_eval:
        steps += [
            ("⑫ 真实模型评测", "eval",
             [PY, "scripts/run_evaluation.py", "run", "--bank",
              f"data/eval/{version}/questions.jsonl", "--suites", "main", "--llm"], RAG),
        ]

    if args.verify_only:
        return 0 if verify(version, args.with_eval) else 1

    say(f"版本：{version}")
    say(f"模式：{'真跑' if args.yes else '只打印计划（加 --yes 才执行）'}")
    say("\n将按顺序执行：")
    for i, (title, key, cmd, cwd) in enumerate(steps, 1):
        mark = "（跳过）" if key in skip else ""
        say(f"  {i:2d}. {title}{mark}")
        say(f"      cd {cwd.relative_to(REPO)} && {' '.join(cmd)}")
    if args.with_eval:
        say("\n评测后的人工口子：填 data/eval/<版本>/runs/<run>/scoring_template.jsonl，")
        say("再 review_bank.py scores-apply 回写，最后单独跑 gen_demo_examples --measure（第 ⑫ 步之外）。")
    say("\n完成后核对：python scripts/publish.py --version {} --verify-only{}"
        .format(version, " --with-eval" if args.with_eval else ""))

    if not args.yes:
        say("\n（计划模式，未执行任何命令）")
        return 0

    for title, key, cmd, cwd in steps:
        if key in skip:
            say(f"\n⏭ 跳过：{title}")
            continue
        run_step(title, cmd, cwd)

    say("\n" + "=" * 72)
    return 0 if verify(version, args.with_eval) else 1


if __name__ == "__main__":
    sys.exit(main())
