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
from lib.json_io import write_text_lf  # noqa: E402
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

    latest_run = _latest_run(eval_dir)
    demo_meta = _demo_meta(eval_dir / "demo_examples.json")
    demo_run_id = (demo_meta.get("source_run") or "") if isinstance(demo_meta, dict) else ""
    demo_parent = _find_run(eval_dir, demo_run_id)
    checks, problems = _consistency(version, demo_meta, demo_parent, latest_run)

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
            "latest_run": latest_run,
        },
        "demo": {
            "path": str(eval_dir / "demo_examples.json"),
            "sha256": file_sha256(eval_dir / "demo_examples.json"),
            "meta": demo_meta,
            # 父节点：demo 由哪次评测 run 产出（P2-4 的关键一环）
            "parent": demo_parent,
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
        # 跨层一致性结论：把"run → demo → runtime 版本是否一致"写成机器可判定的字段，
        # 而不是留在两个互不相干的节点里等人肉对比（第五轮审核 P2-4）
        "checks": checks,
    }
    lineage["checks"]["problems"] = problems
    lineage["checks"]["consistent"] = not problems
    return lineage


def _consistency(version: str, demo_meta: dict, demo_parent: dict,
                 latest_run: dict) -> tuple:
    """跨层一致性判定：run → demo → runtime。

    返回 (checks, problems)。`problems` 非空即表示血缘链在某一层断了，
    `--check` 与 CI 据此判失败——旧实现只是把两个版本号并排记录，
    不一致时没有任何人会发现。
    """
    problems: list[str] = []
    demo_version = demo_meta.get("version") if isinstance(demo_meta, dict) else None
    mode = demo_meta.get("measurement_mode") if isinstance(demo_meta, dict) else None
    parent_version = demo_parent.get("version") if demo_parent.get("resolved") else None
    run_id = demo_parent.get("run_id") or ""

    if not demo_meta.get("available"):
        problems.append("demo 制品不存在或不可解析（demo_examples.json）")
    if not parent_version and demo_parent.get("resolved"):
        problems.append(f"demo 的父 run {run_id} 缺少 meta.json（无法确认其版本与 llm_used）")

    checks = {
        "runtime_version": version,
        "eval_latest_run": latest_run.get("run_id") or "",
        "demo_version": demo_version,
        "demo_source_run": run_id,
        "demo_source_run_resolved": bool(demo_parent.get("resolved")),
        "demo_source_run_version": parent_version,
        "demo_measurement_mode": mode,
        "demo_is_real_llm": mode == "real_llm",
        # run/demo/runtime 三者版本一致
        "demo_version_matches_runtime": demo_version == version,
        "demo_version_matches_parent_run": bool(parent_version) and demo_version == parent_version,
        "demo_uses_eval_latest_run": bool(run_id) and run_id == (latest_run.get("run_id") or ""),
        "parent_run_index_version_matches": demo_parent.get("index_version") == version
        if demo_parent.get("resolved") else False,
    }

    if not checks["demo_source_run_resolved"]:
        problems.append(
            f"demo.source_run={run_id or '(空)'} 无法在 eval/{version}/runs/ 下解析"
            f"（{demo_parent.get('reason') or '未声明'}）")
    if demo_meta.get("available") and not checks["demo_version_matches_runtime"]:
        problems.append(
            f"demo 版本 {demo_version!r} 与运行时版本 {version!r} 不一致："
            f"/api/demo/examples 会按一致性校验返回 503")
    if parent_version and not checks["demo_version_matches_parent_run"]:
        problems.append(
            f"demo 版本 {demo_version!r} 与其父 run {run_id} 的版本 {parent_version!r} 不一致")
    # 索引版本维度必须**判定**而不是只记录（第五轮整改复核 B3）：demo 的实测时延来自
    # 父 run 的索引，索引版本与运行时不一致时，时延数字不对应当前服务的检索行为
    if demo_parent.get("resolved") and demo_parent.get("index_version") \
            and not checks["parent_run_index_version_matches"]:
        problems.append(
            f"父 run {run_id} 的 index_version={demo_parent.get('index_version')!r} "
            f"与运行时索引版本 {version!r} 不一致：demo 的实测时延不对应当前检索索引")
    if demo_meta.get("available") and mode != "real_llm":
        problems.append(
            f"demo 的 measurement_mode={mode!r}：发布标准要求真实模型实测（real_llm）；"
            f"offline 只反映离线摘要回答器，null 表示未实测")
    if checks["demo_source_run_resolved"] and not checks["demo_uses_eval_latest_run"]:
        # 不是硬错误（run 目录会保留历史），但必须显式记录，避免"以为 demo 来自最新 run"
        problems.append(
            f"demo 的父 run {run_id} 不是 eval 的 latest_run "
            f"（{latest_run.get('run_id') or '(无)'}）：demo 可能来自一次更早的评测")
    return checks, problems


# ---- lineage 字段 schema（第五轮审核 P2-4：字段与父子一致性必须有独立校验）----
# 每一项： dotted_path -> (python 类型, 值正则或 None)
LINEAGE_SCHEMA: dict = {
    "lineage_version": (int, None),
    "generated_at": (str, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$"),
    "version": (str, r"^\d{8}_v\d+$"),
    "git_commit": (str, None),
    "config_fingerprint": (str, None),
    "python_version": (str, None),
    "source": (dict, None),
    "snapshot.version": (str, r"^\d{8}_v\d+$"),
    "snapshot.manifest_sha256": (str, r"^([0-9a-f]{64})?$"),
    "index.version": (str, r"^\d{8}_v\d+$"),
    "index.manifest_sha256": (str, r"^([0-9a-f]{64})?$"),
    "index.ids_sha256": (str, r"^([0-9a-f]{64})?$"),
    "eval.version": (str, r"^\d{8}_v\d+$"),
    "eval.questions_sha256": (str, r"^([0-9a-f]{64})?$"),
    "eval.latest_run": (dict, None),
    "demo.path": (str, None),
    "demo.sha256": (str, r"^([0-9a-f]{64})?$"),
    "demo.meta": (dict, None),
    "demo.parent": (dict, None),
    "release.package_lock_sha256": (str, r"^([0-9a-f]{64})?$"),
    "checks": (dict, None),
}


def _get_path(data: dict, dotted: str):
    cur = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None, False
        cur = cur[part]
    return cur, True


def validate_lineage(lineage: dict) -> list:
    """校验 lineage 的字段 schema 与跨层一致性，返回问题列表（空 = 通过）。"""
    import re as _re

    problems: list[str] = []
    for path, (expected_type, pattern) in LINEAGE_SCHEMA.items():
        value, found = _get_path(lineage, path)
        if not found:
            problems.append(f"缺少字段: {path}")
            continue
        if not isinstance(value, expected_type):
            problems.append(
                f"字段类型错误: {path} 期望 {expected_type.__name__}，实际 {type(value).__name__}")
            continue
        if pattern and isinstance(value, str) and not _re.match(pattern, value):
            problems.append(f"字段取值非法: {path}={value!r}")

    checks = lineage.get("checks") or {}
    if not checks.get("consistent"):
        problems.append(f"跨层一致性未通过: {checks.get('problems') or 'checks.consistent=false'}")
    if checks.get("problems"):
        problems.append(f"checks.problems 非空: {checks['problems']}")

    version = lineage.get("version")
    demo_meta = (lineage.get("demo") or {}).get("meta") or {}
    parent = (lineage.get("demo") or {}).get("parent") or {}
    if demo_meta.get("available"):
        if demo_meta.get("version") != version:
            problems.append(
                f"demo.meta.version={demo_meta.get('version')!r} ≠ lineage.version={version!r}")
        if not parent.get("resolved"):
            problems.append("demo.parent 未解析成功（source_run 找不到对应 run 目录）")
        elif parent.get("version") != version:
            problems.append(
                f"demo.parent.version={parent.get('version')!r} ≠ lineage.version={version!r}")
        # measurement_mode 是"这条 demo 能不能当真实模型证据"的唯一判据（B3）：
        # 有 demo 制品就必须声明，且发布标准要求 real_llm
        mode = demo_meta.get("measurement_mode")
        if mode not in ("real_llm", "offline"):
            problems.append(
                f"demo.meta.measurement_mode={mode!r} 非法（必须是 real_llm/offline）："
                f"由 scripts/gen_demo_examples.py --measure 写入")
        elif mode != "real_llm":
            problems.append(
                f"demo.meta.measurement_mode={mode!r}：发布要求真实模型实测（real_llm）")
    return problems


def cmd_check(args) -> int:
    """校验已生成的 lineage.json（CI 门禁；不重新采集）。"""
    settings = get_settings()
    path = settings.data_dir / "release" / "lineage.json"
    if not path.is_file():
        print(f"血缘文件不存在: {path}（先运行 build_lineage.py 生成）")
        return 2
    lineage = _load_json(path)
    if not lineage:
        print(f"血缘文件无法解析: {path}")
        return 2
    problems = validate_lineage(lineage)
    print(f"血缘校验: {path}")
    print(f"  版本 {lineage.get('version')} | commit {str(lineage.get('git_commit'))[:12] or '(无)'} "
          f"| 一致性 {((lineage.get('checks') or {}).get('consistent'))}")
    if problems:
        print(f"发现 {len(problems)} 处问题：")
        for item in problems[:30]:
            print(f"  - {item}")
        return 1
    print("schema 与跨层一致性均通过")
    return 0


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


def _find_run(eval_dir: Path, run_id: str) -> dict:
    """按 run_id 在评测目录里定位 run（demo 的**父节点**，P2-4）。

    旧实现只记录 `eval.latest_run` 与 `demo.source_run` 两个字符串，两者指向不同的
    run 时无人发现——于是"demo 来自哪次评测"在血缘里其实是断链。这里把 demo 声明的
    source_run 真正解析成目录 + 元数据 + 哈希，父子关系可校验。
    """
    if not run_id:
        return {"resolved": False, "reason": "demo 未声明 source_run"}
    run_dir = eval_dir / "runs" / run_id
    if not run_dir.is_dir():
        return {"resolved": False, "run_id": run_id, "dir": str(run_dir),
                "reason": f"demo.source_run={run_id} 在 {eval_dir.name}/runs/ 下不存在"}
    info: dict = {"resolved": True, "run_id": run_id, "dir": str(run_dir)}
    meta_path = run_dir / "meta.json"
    if meta_path.is_file():
        info["meta_sha256"] = _sha256(meta_path)
        meta = _load_json(meta_path)
        info["version"] = meta.get("version")
        info["llm_used"] = meta.get("llm_used")
        info["text_mode"] = meta.get("text_mode")
        info["index_version"] = meta.get("index_version")
        info["generated_at"] = meta.get("generated_at")
    for name in ("traces.jsonl", "scores.jsonl", "report.md"):
        path = run_dir / name
        if path.is_file():
            info[f"{name}_sha256"] = _sha256(path)
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description="生成/校验数据血缘 data/release/lineage.json")
    ap.add_argument("--version", default="", help="数据版本（缺省取 RAG_ACTIVE_VERSION）")
    ap.add_argument("--check", action="store_true",
                    help="只校验已生成的 lineage.json（schema + 跨层一致性），不重新采集")
    args = ap.parse_args()

    if args.check:
        return cmd_check(args)

    settings = get_settings()
    version = args.version or settings.active_version
    if not version:
        print("未指定版本且 RAG_ACTIVE_VERSION 为空：请用 --version 指定")
        return 2

    lineage = build(version)
    out_path = settings.data_dir / "release" / "lineage.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(out_path, json.dumps(lineage, ensure_ascii=False, indent=2))

    missing = [k for k, v in lineage["source"].items()
               if isinstance(v, dict) and not v.get("available")]
    checks = lineage["checks"]
    print(f"血缘已写入: {out_path}")
    print(f"  版本 {version} | commit {lineage['git_commit'][:12] or '(无)'}")
    print(f"  snapshot manifest={bool(lineage['snapshot']['manifest_sha256'])} "
          f"index manifest={bool(lineage['index']['manifest_sha256'])} "
          f"questions={bool(lineage['eval']['questions_sha256'])} "
          f"demo={bool(lineage['demo']['sha256'])}")
    print(f"  demo 父 run: {checks['demo_source_run'] or '(未声明)'} "
          f"→ 解析={checks['demo_source_run_resolved']} "
          f"版本={checks['demo_source_run_version']} "
          f"measurement_mode={checks['demo_measurement_mode']}")
    if missing:
        print(f"  上游数据不可用（记为 available=false）：{', '.join(missing)}")
    if checks["problems"]:
        # 血缘断裂不是"警告"：它意味着 demo 展示的数据无法追溯到某次评测
        print(f"  跨层一致性问题 {len(checks['problems'])} 条（--check 会判失败）：")
        for item in checks["problems"]:
            print(f"    - {item}")
        return 1
    print("  跨层一致性：run → demo → runtime 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
