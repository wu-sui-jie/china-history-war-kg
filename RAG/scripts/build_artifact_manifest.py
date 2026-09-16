"""发布制品清单生成与校验（2026-09-15 第四轮复核 P2-3 / P0-4）。

为什么需要它：health 里原有的哈希只覆盖 snapshot manifest、index manifest 与向量 ids，
**不能证明真正被服务读取的 JSON、FTS、chunks、embeddings、Chroma 段和前端 dist 没变**。
本脚本递归记录发布必需文件的 relative_path / size / sha256 / artifact_type / source_version，
产出 `data/release/artifact-manifest.json`；health 返回该清单的 sha256，
解包方可用 `verify` 逐文件核对。

用法：
    python scripts/build_artifact_manifest.py                      # 生成（含活跃版本）
    python scripts/build_artifact_manifest.py --version 20260915_v1
    python scripts/build_artifact_manifest.py verify               # 校验现有清单
    python scripts/build_artifact_manifest.py build --require-clean  # 工作区脏则不生成（发布门禁）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_settings  # noqa: E402
from lib.release_info import repo_root  # noqa: E402

# 运行时真正要读的东西；不含 logs/、cache/、runs/（评测痕迹，不参与服务）
SNAPSHOT_FILES = ("*.json",)
INDEX_FILES = ("manifest.json", "chunks_fts.db", "chunks.jsonl", "vectors/ids.json",
               "vectors/embeddings.npy")
EVAL_FILES = ("questions.jsonl", "questions.meta.json", "demo_examples.json")
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


def _sqlite_logical_hash(path: Path) -> str:
    """SQLite 元数据库的**逻辑**哈希（工作单 P2-3）。

    为什么不能直接哈希文件：Chroma 打开数据库时会写 WAL、更新 acquire_write / max_seq_id
    之类的运行态表，"跑一次服务再校验"必然失败——那不是制品变了，是元数据自更新。
    这里只对**语义内容**做规范化 dump 后哈希：collection 定义、segment 映射、每个
    segment 的向量条数。任何真正的数据变化（增删向量、换集合）都会改变它。
    """
    import sqlite3

    digest = hashlib.sha256()
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        queries = (
            ("collections", "SELECT id, name, dimension FROM collections ORDER BY id"),
            ("segments", "SELECT id, type, scope, collection FROM segments ORDER BY id"),
            ("embeddings", "SELECT segment_id, COUNT(*) FROM embeddings "
                           "GROUP BY segment_id ORDER BY segment_id"),
        )
        for label, sql in queries:
            digest.update(label.encode("utf-8"))
            for row in con.execute(sql):
                digest.update(("|".join("" if v is None else str(v) for v in row))
                              .encode("utf-8"))
                digest.update(b"\n")
    finally:
        con.close()
    return digest.hexdigest()


def _entry(path: Path, root: Path, artifact_type: str, source_version: str) -> dict:
    # 只有 Chroma 的元数据库会被客户端在运行时改写（WAL / 运行态表）；
    # 其它 SQLite（如 FTS5 的 chunks_fts.db）构建后只读，仍用物理哈希。
    is_sqlite = path.name.startswith("chroma") and path.suffix.lower() == ".sqlite3"
    entry = {
        "relative_path": path.relative_to(root).as_posix(),
        "size": path.stat().st_size,
        "artifact_type": artifact_type,
        "source_version": source_version,
        "hash_mode": "sqlite_logical" if is_sqlite else "file",
        "sha256": _sqlite_logical_hash(path) if is_sqlite else _sha256(path),
    }
    if is_sqlite:
        # 物理哈希只作参考：Chroma 运行时改写元数据库属正常行为，不参与校验
        entry["physical_sha256"] = _sha256(path)
    return entry


def _add_file(entries: list, path: Path, root: Path, kind: str, version: str,
              missing: list) -> None:
    if path.is_file():
        entries.append(_entry(path, root, kind, version))
    else:
        missing.append(f"{kind}: {path.relative_to(root).as_posix()}")


def _add_tree(entries: list, directory: Path, root: Path, kind: str, version: str) -> None:
    if not directory.is_dir():
        return
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            entries.append(_entry(path, root, kind, version))


def expected_paths(settings, version: str) -> dict[str, str]:
    """发布必需文件的**权威清单**（路径 → artifact_type）。

    校验时用它做双向比对（工作单 P2-3）：清单里的文件必须存在，目录里出现的新文件
    也必须已被登记——只做单向检查会漏掉"新增了未登记的运行时文件"。
    """
    root = repo_root()
    expected: dict[str, str] = {}

    def _add(path: Path, kind: str) -> None:
        if path.is_file():
            expected[path.relative_to(root).as_posix()] = kind

    snap = settings.snapshot_dir / version
    for path in sorted(snap.glob("*.json")):
        _add(path, "snapshot")

    idx = settings.index_dir / version
    for rel in INDEX_FILES:
        _add(idx / rel, "index")
    chroma = idx / "vectors" / "chroma"
    if chroma.is_dir():
        for path in sorted(chroma.rglob("*")):
            _add(path, "vector_store")

    eval_dir = settings.data_dir / "eval" / version
    for rel in EVAL_FILES:
        _add(eval_dir / rel, "eval")

    if settings.frontend_dist.is_dir():
        for path in sorted(settings.frontend_dist.rglob("*")):
            _add(path, "frontend_dist")

    # 数据血缘与审计结论属于发布证据：存在即登记（血缘由 build_lineage.py 生成）
    release_dir = settings.data_dir / "release"
    if release_dir.is_dir():
        for path in sorted(release_dir.glob("*.json")):
            # 清单自身不可能登记自己（会形成"先有鸡还是先有蛋"），SHA256SUMS 同理
            if path.name == "artifact-manifest.json":
                continue
            _add(path, "release_evidence")

    for rel in ("requirements.txt", "requirements-dev.txt",
                "requirements.lock", "requirements-dev.lock"):
        _add(root / rel, "python_deps")
    for rel in ("frontend/package.json", "frontend/package-lock.json"):
        _add(root / rel, "frontend_deps")

    return expected


def collect(settings, version: str) -> dict:
    root = repo_root()
    entries: list = []
    missing: list = []
    expected = expected_paths(settings, version)

    snap = settings.snapshot_dir / version
    if not snap.is_dir():
        missing.append(f"snapshot: {snap.relative_to(root).as_posix()}（版本不存在）")

    for rel_path, kind in expected.items():
        entries.append(_entry(root / rel_path, root, kind, version))

    for required in ("requirements.txt", "requirements-dev.txt",
                     "frontend/package.json"):
        if not (root / required).is_file():
            missing.append(f"依赖声明缺失: {required}")

    return {
        "manifest_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source_version": version,
        "git_commit": _git(["rev-parse", "HEAD"], root) or "",
        "git_dirty": bool(git_status_lines(root) or []),
        "git_dirty_ignored_paths": list(RELEASE_IGNORE_PREFIXES),
        "python_version": sys.version.split()[0],
        "entry_count": len(entries),
        "total_bytes": sum(e["size"] for e in entries),
        "missing": missing,
        "entries": entries,
    }


def _add_tree_small(entries: list, directory: Path, root: Path, kind: str, version: str) -> None:
    """递归记录目录下所有文件（用于 Chroma 段与前端 dist）。"""
    if not directory or not Path(directory).is_dir():
        return
    for path in sorted(Path(directory).rglob("*")):
        if path.is_file():
            entries.append(_entry(path, root, kind, version))


def _git(args: list[str], cwd: Path) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                             text=True, timeout=10)
        return out.stdout if out.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def cmd_build(args) -> int:
    settings = get_settings()
    version = args.version or settings.active_version
    if not version:
        print("未指定版本且 RAG_ACTIVE_VERSION 为空：请用 --version 指定")
        return 2
    root = repo_root()
    from lib.release_info import RELEASE_IGNORE_PREFIXES, git_status_lines

    dirty_lines = git_status_lines(root) or []
    if args.require_clean and dirty_lines:
        print("工作区存在未提交改动（已按约定排除 " + ", ".join(RELEASE_IGNORE_PREFIXES) + "）："
              "发布门禁拒绝生成清单")
        for line in dirty_lines[:20]:
            print(f"  - {line}")
        return 3

    manifest = collect(settings, version)
    if manifest["missing"]:
        print("缺少发布必需文件：")
        for item in manifest["missing"]:
            print(f"  - {item}")
        return 4

    out_path = settings.data_dir / "release" / "artifact-manifest.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    sums_path = _write_sha256sums(settings, manifest, out_path)
    print(f"清单已写入: {out_path}")
    print(f"校验和文件: {sums_path}")
    print(f"  版本 {version} | commit {manifest['git_commit'][:12] or '(无)'} "
          f"| dirty={manifest['git_dirty']} | 条目 {manifest['entry_count']} "
          f"| 合计 {manifest['total_bytes'] / 1024 / 1024:.1f} MB")
    print(f"  清单 sha256: {_sha256(out_path)}")
    return 0


def cmd_verify(args) -> int:
    settings = get_settings()
    out_path = settings.data_dir / "release" / "artifact-manifest.json"
    if not out_path.exists():
        print(f"清单不存在: {out_path}（先运行 build）")
        return 2
    manifest = json.loads(out_path.read_text(encoding="utf-8"))
    root = repo_root()
    version = manifest.get("source_version", "")
    drift: list[str] = []
    checked = 0
    registered = set()
    for entry in manifest.get("entries", []):
        rel = entry["relative_path"]
        registered.add(rel)
        path = root / rel
        if not path.is_file():
            drift.append(f"缺失: {rel}")
            continue
        checked += 1
        mode = entry.get("hash_mode", "file")
        if mode == "sqlite_logical":
            if _sqlite_logical_hash(path) != entry["sha256"]:
                drift.append(f"逻辑内容变化: {rel}")
            continue
        if path.stat().st_size != entry["size"]:
            drift.append(f"大小变化: {rel}")
            continue
        if _sha256(path) != entry["sha256"]:
            drift.append(f"内容变化: {rel}")

    # 双向比对（P2-3）：目录里出现、但清单未登记的文件必须被发现
    if version:
        expected = expected_paths(get_settings(), version)
        for rel in sorted(set(expected) - registered):
            drift.append(f"未登记的新文件: {rel}")
        for rel in sorted(registered - set(expected)):
            drift.append(f"清单中存在但当前规则不再覆盖: {rel}")

    print(f"校验 {checked}/{manifest.get('entry_count', 0)} 个文件"
          f"（版本 {manifest.get('source_version')}）")
    if drift:
        print(f"发现 {len(drift)} 处不一致：")
        for item in drift[:50]:
            print(f"  - {item}")
        return 1
    print("制品与清单一致")
    return 0


def _write_sha256sums(settings, manifest: dict, out_path: Path) -> Path:
    """生成 SHA256SUMS（工作单 P2-3 第 6 条）：把清单本身也纳入校验链。"""
    root = repo_root()
    lines = []
    for entry in manifest.get("entries", []):
        lines.append(f"{entry['sha256']}  {entry['relative_path']}")
    lines.append(f"{_sha256(out_path)}  {out_path.relative_to(root).as_posix()}")
    sums_path = out_path.parent / "SHA256SUMS"
    sums_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sums_path


def main() -> int:
    ap = argparse.ArgumentParser(description="发布制品清单生成/校验")
    sub = ap.add_subparsers(dest="command")
    b = sub.add_parser("build", help="生成清单")
    b.add_argument("--version", default="", help="数据版本（缺省取 RAG_ACTIVE_VERSION）")
    b.add_argument("--require-clean", action="store_true",
                   help="工作区有未提交改动时拒绝生成（发布门禁）")
    v = sub.add_parser("verify", help="按清单校验制品")
    args = ap.parse_args()

    if args.command in (None, "build"):
        if not hasattr(args, "version"):
            args.version = ""
            args.require_clean = False
        return cmd_build(args)
    if args.command == "verify":
        return cmd_verify(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
