"""Chroma collection → segment 映射与孤儿判定（2026-09-16 工作单 P2-5）。

背景：向量目录下有两个 UUID 子目录，但"目录数 > 1"并不能证明存在孤儿——
一个 collection 可能合法对应多个不同 scope 的 segment。本脚本用 Chroma 自己的
元数据库（`chroma.sqlite3`，只读打开）建立权威映射，再与磁盘目录、`ids.json`、
构建清单对账，最后给出**有证据**的孤儿结论。

默认只审计不删除；`--apply` 才会把孤儿目录移到备份区（移动前记录每个文件的
sha256，并保留审计 JSON），随后由调用方重跑 count / 随机 query / manifest verify。

用法：
    python scripts/audit_chroma_segments.py --version 20260915_v1            # 审计
    python scripts/audit_chroma_segments.py --version 20260915_v1 --apply    # 备份并清理孤儿
    python scripts/audit_chroma_segments.py --version 20260915_v1 --random 5 # 随机抽样 get/query
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_settings  # noqa: E402
from lib.release_info import repo_root  # noqa: E402

_CHUNK = 1 << 20


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(_CHUNK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _connect_ro(db: Path) -> sqlite3.Connection:
    """只读打开元数据库：审计不得改动正式制品（工作单 P2-3 的同一要求）。"""
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def collect_mapping(db: Path) -> dict:
    con = _connect_ro(db)
    try:
        collections = [dict(r) for r in con.execute(
            "SELECT id, name, dimension FROM collections ORDER BY name")]
        segments = [dict(r) for r in con.execute(
            "SELECT id, type, scope, collection FROM segments ORDER BY id")]
        counts = {}
        for c in collections:
            row = con.execute("SELECT COUNT(*) AS n FROM embeddings WHERE segment_id = ?",
                              (None,)).fetchone() if False else None
            counts[c["id"]] = row["n"] if row else None
        emb_total = con.execute("SELECT COUNT(*) AS n FROM embeddings").fetchone()["n"]
        emb_by_segment = {r["segment_id"]: r["n"] for r in con.execute(
            "SELECT segment_id, COUNT(*) AS n FROM embeddings GROUP BY segment_id")}
    finally:
        con.close()
    return {
        "collections": collections,
        "segments": segments,
        "embeddings_total": emb_total,
        "embeddings_by_segment": emb_by_segment,
        "counts": counts,
        "segment_ids": {s["id"] for s in segments},
    }


def audit(index_dir: Path, *, do_random: int = 0) -> dict:
    vectors_dir = index_dir / "vectors"
    chroma_dir = vectors_dir / "chroma"
    db = chroma_dir / "chroma.sqlite3"
    report: dict = {
        "index_version": index_dir.name,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "chroma_dir": str(chroma_dir),
        "db_exists": db.is_file(),
    }
    if not db.is_file():
        report["error"] = "chroma.sqlite3 不存在"
        return report

    mapping = collect_mapping(db)
    segments = mapping["segments"]
    segment_ids = mapping["segment_ids"]

    dirs = sorted(p for p in chroma_dir.iterdir() if p.is_dir())
    dir_infos = []
    orphans = []
    for d in dirs:
        files = sorted(f for f in d.rglob("*") if f.is_file())
        total = sum(f.stat().st_size for f in files)
        referenced = d.name in segment_ids
        dir_infos.append({
            "dir": d.name,
            "referenced_by_segment": referenced,
            "file_count": len(files),
            "total_bytes": total,
            "files": [f.relative_to(d).as_posix() for f in files],
        })
        if not referenced:
            orphans.append(d)

    ids_path = vectors_dir / "ids.json"
    ids_count = None
    if ids_path.is_file():
        try:
            ids_count = len(json.loads(ids_path.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            ids_count = None

    manifest_count = None
    manifest_path = index_dir / "manifest.json"
    if manifest_path.is_file():
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_count = (m.get("counts") or {}).get("vectors") or m.get("vector_count")
        except Exception:  # noqa: BLE001
            manifest_count = None

    report.update({
        "collections": mapping["collections"],
        "segments": segments,
        "segment_dir_count": len(dirs),
        "segment_dirs": dir_infos,
        "orphan_dirs": [d.name for d in orphans],
        "embeddings_total": mapping["embeddings_total"],
        "embeddings_by_segment": mapping["embeddings_by_segment"],
        "ids_json_count": ids_count,
        "manifest_vector_count": manifest_count,
        "counts_consistent": (
            ids_count is not None
            and ids_count == mapping["embeddings_total"]
        ),
    })

    # 随机抽样 get/query：证明"当前引用到的段"确实可查
    if do_random:
        try:
            from data.index import chroma_store
            from data.index.embeddings import build_embedding_client

            settings = get_settings()
            collection = chroma_store.load_collection(index_dir, settings.chroma_collection)
            sample = []
            if collection is not None:
                ids = []
                if ids_path.is_file():
                    ids = json.loads(ids_path.read_text(encoding="utf-8"))
                picked = random.sample(ids, min(do_random, len(ids))) if ids else []
                got = collection.get(ids=picked, include=["metadatas"]) if picked else {}
                got_ids = got.get("ids") or []
                sample.append({"op": "get", "requested": len(picked), "returned": len(got_ids),
                               "sample_ids": got_ids[:3]})
                client = build_embedding_client(settings)
                if client is not None and picked:
                    vec = client(["随机抽样查询"])[0]
                    res = collection.query(query_embeddings=[vec], n_results=do_random)
                    sample.append({"op": "query", "returned": len((res.get("ids") or [[]])[0])})
                    client.close()
            report["random_sample"] = sample
        except Exception as e:  # noqa: BLE001
            report["random_sample_error"] = f"{type(e).__name__}: {e}"

    return report


def apply_cleanup(index_dir: Path, report: dict, out_path: Path) -> dict:
    """把孤儿目录移入备份区（不直接删除），并记录移动前哈希。"""
    chroma_dir = index_dir / "vectors" / "chroma"
    orphans = [chroma_dir / name for name in report.get("orphan_dirs", [])]
    if not orphans:
        report["cleanup"] = {"action": "none", "reason": "没有孤儿目录，无需清理"}
        return report

    # 备份放在索引目录之外（data/index/_orphan_backup_*/<version>/）：
    # 放回 chroma/ 下会被下一轮审计再判成"未引用目录"，也会污染制品清单
    backup_root = (index_dir.parent / f"_orphan_backup_{time.strftime('%Y%m%d_%H%M%S')}"
                   / index_dir.name)
    backup_root.mkdir(parents=True, exist_ok=True)
    moved = []
    for d in orphans:
        hashes = {f.relative_to(d).as_posix(): _sha256(f) for f in sorted(d.rglob("*")) if f.is_file()}
        dest = backup_root / d.name
        shutil.move(str(d), str(dest))
        # 校验移动后内容一致（防止移动过程损坏）
        ok = all(_sha256(dest / rel) == sha for rel, sha in hashes.items())
        moved.append({"dir": d.name, "backup": str(dest), "files": len(hashes),
                      "hash_verified": ok})
    report["cleanup"] = {"action": "moved_to_backup", "backup_root": str(backup_root),
                         "moved": moved}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="Chroma collection/segment 映射与孤儿审计")
    ap.add_argument("--version", default="", help="索引版本（缺省取 RAG_ACTIVE_VERSION）")
    ap.add_argument("--apply", action="store_true", help="把孤儿目录移入备份区（默认只审计）")
    ap.add_argument("--random", type=int, default=0, help="随机抽样 get/query 的条数")
    args = ap.parse_args()

    settings = get_settings()
    version = args.version or settings.active_version
    if not version:
        print("未指定版本且 RAG_ACTIVE_VERSION 为空：请用 --version 指定")
        return 2
    index_dir = settings.index_dir / version
    if not index_dir.is_dir():
        print(f"索引目录不存在: {index_dir}")
        return 2

    report = audit(index_dir, do_random=args.random)
    out_path = settings.data_dir / "release" / "chroma-segment-audit.json"

    if args.apply:
        report = apply_cleanup(index_dir, report, out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"审计报告: {out_path}")
    print(f"  collections: {[c['name'] for c in report.get('collections', [])]}")
    print(f"  segments: {[(s['id'][:8], s['scope']) for s in report.get('segments', [])]}")
    print(f"  磁盘段目录: {report.get('segment_dir_count')} 个"
          f"，孤儿: {report.get('orphan_dirs')}")
    print(f"  embeddings={report.get('embeddings_total')} "
          f"ids.json={report.get('ids_json_count')} "
          f"一致={report.get('counts_consistent')}")
    if "cleanup" in report:
        print(f"  清理: {report['cleanup'].get('action')}")
    if report.get("random_sample"):
        print(f"  随机抽样: {report['random_sample']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
