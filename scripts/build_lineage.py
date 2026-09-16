"""数据血缘生成（2026-09-16 工作单 P2-4）。

把 `source → snapshot → index → vectors → eval → demo → release` 各层用**可校验的哈希**
串起来，任一输出都能反查输入。产物：`data/release/lineage.json`。

设计取舍：
- 本机绝对路径只作为附加字段（`path`），跨机器识别一律用 sha256/大小/版本号；
- 不记录任何密钥；上游数据缺失时写 `available: false` 而不是编造哈希；
- 不反向引用 artifact-manifest（那会造成循环依赖）：清单里包含 lineage.json，
  health 同时暴露两者的哈希，链条由"清单 → lineage → 各层输入"单向闭合。
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
from lib.release_info import (  # noqa: E402
    config_fingerprint,
    file_sha256,
    git_commit,
    repo_root,
)

_CHUNK = 1 << 20
# 上游只读数据源（旧项目）：不参与发布，但必须可追溯
SOURCE_FILES = ("database", "中国历代战争简史.txt", "中国战争史地图集.txt")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(_CHUNK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _file_info(path: Path) -> dict:
    if not path or not path.is_file():
        return {"available": False, "path": str(path) if path else ""}
    return {
        "available": True,
        "path": str(path),
        "size": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _pkg_version(name: str) -> str:
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:  # noqa: BLE001
        return ""


def _run(cmd: list[str], cwd: Path) -> str:
    try:
        out = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def _latest_run(eval_dir: Path) -> dict:
    runs = eval_dir / "runs"
    if not runs.is_dir():
        return {"available": False}
    latest = runs / "latest.txt"
    run_id = latest.read_text(encoding="utf-8").strip() if latest.is_file() else ""
    if not run_id:
        return {"available": False, "runs_dir": str(runs)}
    run_dir = runs / run_id
    info: dict = {"available": run_dir.is_dir(), "run_id": run_id, "dir": str(run_dir)}
    meta_path = run_dir / "meta.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            info["meta"] = {k: meta.get(k) for k in (
                "version", "llm_used", "text_mode", "index_version", "generated_at",
                "configs", "suites", "chunk_params")}
        except Exception:  # noqa: BLE001
            info["meta"] = {}
    for name in ("traces.jsonl", "scores.jsonl", "report.md"):
        path = run_dir / name
        if path.is_file():
            info[f"{name}_sha256"] = _sha256(path)
            info[f"{name}_size"] = path.stat().st_size
    return info


def build(version: str) -> dict:
    settings = get_settings()
    root = repo_root()
    snap = settings.snapshot_dir / version
    idx = settings.index_dir / version
    eval_dir = settings.data_dir / "eval" / version

    lineage: dict = {
        "lineage_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "version": version,
        "git_commit": git_commit(root),
        "config_fingerprint": config_fingerprint(settings),
        "python_version": sys.version.split()[0],
        "node_version": _run(["node", "--version"], root),
        "source": {
            "legacy_sqlite": _file_info(Path(settings.legacy_sqlite_path)),
            "raw_texts": [_file_info(Path(p)) for p in settings.legacy_raw_texts],
            "license_note": "原书文本受版权约束，仅在本机/私有环境使用，不入公开仓库",
        },
        "snapshot": {
            "version": version,
            "dir": str(snap),
            "manifest_sha256": file_sha256(snap / "manifest.json"),
            "manifest": _load_json(snap / "manifest.json"),
        },
        "index": {
            "version": version,
            "dir": str(idx),
            "manifest_sha256": file_sha256(idx / "manifest.json"),
            "manifest": _load_json(idx / "manifest.json"),
            "embedding_model": settings.embedding_model,
            "embedding_dim": settings.embedding_dim,
            "chroma_version": _pkg_version("chromadb"),
            "sqlite_version": _sqlite_version(),
            "chunk_params": {"max_chars": settings.chunk_max_chars,
                             "overlap_chars": settings.chunk_overlap_chars},
            "ids_sha256": file_sha256(idx / "vectors" / "ids.json"),
            "embeddings_npy_sha256": file_sha256(idx / "vectors" / "embeddings.npy"),
        },
        "eval": {
            "version": version,
            "dir": str(eval_dir),
            "questions_sha256": file_sha256(eval_dir / "questions.jsonl"),
            "questions_meta": _load_json(eval_dir / "questions.meta.json"),
            "latest_run": _latest_run(eval_dir),
        },
        "demo": {
            "path": str(eval_dir / "demo_examples.json"),
            "sha256": file_sha256(eval_dir / "demo_examples.json"),
            "meta": _demo_meta(eval_dir / "demo_examples.json"),
        },
        "release": {
            "frontend_dist_index_sha256": file_sha256(
                settings.frontend_dist / "index.html"),
            "requirements_sha256": file_sha256(root / "requirements.txt"),
            "requirements_dev_sha256": file_sha256(root / "requirements-dev.txt"),
            "requirements_lock_sha256": file_sha256(root / "requirements.lock"),
            "requirements_dev_lock_sha256": file_sha256(root / "requirements-dev.lock"),
            "package_lock_sha256": file_sha256(root / "frontend" / "package-lock.json"),
        },
    }
    return lineage


def _sqlite_version() -> str:
    import sqlite3

    return sqlite3.sqlite_version


def _load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _demo_meta(path: Path) -> dict:
    data = _load_json(path)
    if not data:
        return {"available": False}
    return {
        "available": True,
        "version": data.get("version"),
        "source_run": data.get("source_run"),
        "measurement_mode": data.get("measurement_mode"),
        "counts": data.get("counts"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="生成数据血缘 data/release/lineage.json")
    ap.add_argument("--version", default="", help="数据版本（缺省取 RAG_ACTIVE_VERSION）")
    args = ap.parse_args()

    settings = get_settings()
    version = args.version or settings.active_version
    if not version:
        print("未指定版本且 RAG_ACTIVE_VERSION 为空：请用 --version 指定")
        return 2

    lineage = build(version)
    out_path = settings.data_dir / "release" / "lineage.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(lineage, ensure_ascii=False, indent=2), encoding="utf-8")

    missing = [k for k, v in lineage["source"].items()
               if isinstance(v, dict) and not v.get("available")]
    print(f"血缘已写入: {out_path}")
    print(f"  版本 {version} | commit {lineage['git_commit'][:12] or '(无)'}")
    print(f"  snapshot manifest={bool(lineage['snapshot']['manifest_sha256'])} "
          f"index manifest={bool(lineage['index']['manifest_sha256'])} "
          f"questions={bool(lineage['eval']['questions_sha256'])} "
          f"demo={bool(lineage['demo']['sha256'])}")
    if missing:
        print(f"  上游数据不可用（记为 available=false）：{', '.join(missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
