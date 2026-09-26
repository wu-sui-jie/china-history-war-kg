"""发布链路脚本的守护用例。

覆盖对象是 `scripts/` 下的发布工具，全部在 tmp_path 里构造最小制品，
不读写 `data/` 下的正式数据：

- `build_artifact_manifest`：物理 SHA256SUMS 与逻辑哈希分离、verify-sums、commit 规范化；
- `audit_chroma_segments`：四方计数（ids / embeddings / collection / manifest）与缺失判定；
- `build_lineage`：demo 父节点解析、跨层一致性与 schema 校验；
- `gen_sbom`：SPDX 2.3 必需字段与生成-校验往返；
- `fetch_data_artifact`：哈希门禁、zip-slip 防护、范围限制。
"""

from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path


from config.settings import Settings, get_settings


def _settings(**overrides) -> Settings:
    s = get_settings()
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _make_chroma_db(path: Path, vectors: int = 3, collection: str = "chunks_v1") -> None:
    """最小 Chroma 元数据库：collections / segments / embeddings 三张表。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.executescript(
        """
        CREATE TABLE collections (id TEXT, name TEXT, dimension INT);
        CREATE TABLE segments (id TEXT, type TEXT, scope TEXT, collection TEXT);
        CREATE TABLE embeddings (id TEXT, segment_id TEXT);
        """
    )
    con.execute("INSERT INTO collections VALUES ('c1', ?, 1024)", (collection,))
    con.execute("INSERT INTO segments VALUES ('seg-vector', 'hnsw', 'VECTOR', 'c1')")
    con.execute("INSERT INTO segments VALUES ('seg-meta', 'sqlite', 'METADATA', 'c1')")
    for i in range(vectors):
        # 实测形态：embedding 行挂在 METADATA 段上
        con.execute("INSERT INTO embeddings VALUES (?, 'seg-meta')", (f"id{i}",))
    con.commit()
    con.close()


# ---------------------------------------------------------------- 制品清单
def _manifest_fixture(tmp_path: Path, monkeypatch, vectors: int = 3):
    """构造一个含 Chroma 逻辑哈希条目的最小制品树，返回 (模块, settings, 根目录)。"""
    from scripts import build_artifact_manifest as bam

    root = tmp_path
    data_dir = root / "data"
    idx = data_dir / "index" / "v1"
    snap = data_dir / "snapshot" / "v1"
    eval_dir = data_dir / "eval" / "v1"
    for path in (idx, snap, eval_dir):
        path.mkdir(parents=True, exist_ok=True)

    (snap / "entities.json").write_text("[]", encoding="utf-8")
    (snap / "manifest.json").write_text("{}", encoding="utf-8")
    (idx / "manifest.json").write_text('{"source_snapshot":"v1","vectors":{"count":3}}',
                                       encoding="utf-8")
    (idx / "chunks_fts.db").write_bytes(b"fts")
    (idx / "vectors").mkdir(exist_ok=True)
    (idx / "vectors" / "ids.json").write_text(json.dumps([f"id{i}" for i in range(vectors)]),
                                              encoding="utf-8")
    (eval_dir / "questions.jsonl").write_text("{}\n", encoding="utf-8")
    _make_chroma_db(idx / "vectors" / "chroma" / "chroma.sqlite3", vectors=vectors)

    for rel in ("requirements.txt", "requirements-dev.txt", "frontend/package.json"):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")

    settings = _settings(data_dir=data_dir, snapshot_dir=data_dir / "snapshot",
                         index_dir=data_dir / "index", frontend_dist=root / "dist")
    monkeypatch.setattr(bam, "repo_root", lambda: root)
    monkeypatch.setattr(bam, "get_settings", lambda: settings)
    monkeypatch.setattr(bam, "git_status_lines", lambda root, prefixes=None: [])
    return bam, settings, root


def test_sha256sums_uses_physical_hash_and_logical_stays_separate(tmp_path, monkeypatch):
    """SHA256SUMS 必须是物理哈希（sha256sum -c 语义），逻辑哈希单独成表。"""
    bam, settings, root = _manifest_fixture(tmp_path, monkeypatch)
    manifest = bam.collect(settings, "v1")
    out = settings.data_dir / "release" / "artifact-manifest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    sums_path, logical_path = bam._write_sha256sums(settings, manifest, out)

    chroma_rel = "data/index/v1/vectors/chroma/chroma.sqlite3"
    entry = next(e for e in manifest["entries"] if e["relative_path"] == chroma_rel)
    assert entry["hash_mode"] == "sqlite_logical"

    sums = {line.split("  ", 1)[1]: line.split("  ", 1)[0]
            for line in sums_path.read_text(encoding="utf-8").splitlines() if line.strip()}
    assert sums[chroma_rel] == entry["physical_sha256"], \
        "SHA256SUMS 里必须写物理哈希，否则 sha256sum -c 永远报不一致"
    assert sums[chroma_rel] != entry["sha256"]

    logical = json.loads(logical_path.read_text(encoding="utf-8"))
    logical_entry = next(e for e in logical["entries"] if e["relative_path"] == chroma_rel)
    assert logical_entry["logical_sha256"] == entry["sha256"]
    assert logical_entry["hash_mode"] == "sqlite_logical"
    # 逻辑哈希条目不得混进标准校验和文件
    assert entry["sha256"] not in sums.values()

    assert bam.cmd_verify_sums(type("A", (), {})()) == 0

    # 物理内容被改动 → 标准校验必须失败
    (root / "data" / "snapshot" / "v1" / "entities.json").write_text("[1]", encoding="utf-8")
    assert bam.cmd_verify_sums(type("A", (), {})()) == 1


def test_verify_sums_reports_missing_and_bad_lines(tmp_path, monkeypatch):
    bam, settings, _root = _manifest_fixture(tmp_path, monkeypatch)
    manifest = bam.collect(settings, "v1")
    out = settings.data_dir / "release" / "artifact-manifest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    sums_path, _ = bam._write_sha256sums(settings, manifest, out)
    sums_path.write_text("deadbeef  data/x\n" + sums_path.read_text(encoding="utf-8"),
                         encoding="utf-8")
    assert bam.cmd_verify_sums(type("A", (), {})()) == 1


def test_manifest_git_commit_is_stripped(tmp_path, monkeypatch):
    """commit/branch 等标识必须 trim，否则 JSON 里带换行会破坏严格比较。"""
    from scripts import build_artifact_manifest as bam

    class _Out:
        returncode = 0
        stdout = "abc123\n"

    monkeypatch.setattr(bam.subprocess, "run", lambda *a, **k: _Out())
    assert bam._git(["rev-parse", "HEAD"], tmp_path) == "abc123"


# ---------------------------------------------------------------- Chroma 四方计数
def _audit_fixture(tmp_path, monkeypatch, vectors=4, manifest_count=4, ids=True):
    from scripts import audit_chroma_segments as acs

    index_dir = tmp_path / "data" / "index" / "v1"
    _make_chroma_db(index_dir / "vectors" / "chroma" / "chroma.sqlite3", vectors=vectors)
    if ids:
        (index_dir / "vectors").mkdir(parents=True, exist_ok=True)
        (index_dir / "vectors" / "ids.json").write_text(
            json.dumps([f"id{i}" for i in range(vectors)]), encoding="utf-8")
    (index_dir / "manifest.json").write_text(
        json.dumps({"vectors": {"count": manifest_count}}), encoding="utf-8")
    monkeypatch.setattr(acs, "get_settings",
                        lambda: _settings(data_dir=tmp_path / "data"))
    return acs, index_dir


def test_chroma_audit_counts_four_sources(tmp_path, monkeypatch):
    """ids / embeddings / collection / manifest 四者一致才判通过。"""
    acs, index_dir = _audit_fixture(tmp_path, monkeypatch)
    report = acs.audit(index_dir, collection_name="chunks_v1")
    assert report["counts_consistent"] is True, report["counts_detail"]
    detail = report["counts_detail"]
    assert detail["ids_json_count"] == 4
    assert detail["embeddings_total"] == 4
    assert detail["collection_vector_count"] == 4
    assert detail["manifest_vector_count"] == 4
    assert detail["manifest_count_source"] == "vectors.count"


def test_chroma_audit_detects_manifest_mismatch_and_missing_ids(tmp_path, monkeypatch):
    """读不到 manifest 计数时不能判"一致"：任一来源缺失或不一致都判失败。"""
    acs, index_dir = _audit_fixture(tmp_path, monkeypatch, vectors=4, manifest_count=99)
    report = acs.audit(index_dir, collection_name="chunks_v1")
    assert report["counts_consistent"] is False
    assert report["manifest_vector_count"] == 99

    acs2, index_dir2 = _audit_fixture(tmp_path / "second", monkeypatch, ids=False)
    report2 = acs2.audit(index_dir2, collection_name="chunks_v1")
    assert report2["counts_consistent"] is False
    assert "ids_json_count" in report2["counts_missing"]


def test_chroma_audit_main_returns_nonzero_on_mismatch(tmp_path, monkeypatch):
    acs, index_dir = _audit_fixture(tmp_path, monkeypatch, vectors=4, manifest_count=5)
    monkeypatch.setattr(acs, "get_settings", lambda: _settings(
        data_dir=tmp_path / "data", index_dir=tmp_path / "data" / "index"))
    monkeypatch.setattr(acs.sys, "argv",
                        ["audit_chroma_segments.py", "--version", "v1"])
    assert acs.main() == 1


# ---------------------------------------------------------------- 数据血缘
def _lineage_doc(**overrides) -> dict:
    doc = {
        "lineage_version": 1,
        "generated_at": "2026-09-16T12:00:00",
        "version": "20260915_v1",
        "git_commit": "abc@main",
        "config_fingerprint": "f" * 64,
        "python_version": "3.11.15",
        "source": {},
        "snapshot": {"version": "20260915_v1", "manifest_sha256": "a" * 64},
        "index": {"version": "20260915_v1", "manifest_sha256": "b" * 64,
                  "ids_sha256": "c" * 64},
        "eval": {"version": "20260915_v1", "questions_sha256": "d" * 64,
                 "latest_run": {"run_id": "run_a"}},
        "demo": {
            "path": "data/eval/20260915_v1/demo_examples.json",
            "sha256": "e" * 64,
            "meta": {"available": True, "version": "20260915_v1",
                     "source_run": "run_a", "measurement_mode": "real_llm"},
            "parent": {"resolved": True, "run_id": "run_a", "version": "20260915_v1"},
        },
        "release": {"package_lock_sha256": "f" * 64},
        "checks": {"consistent": True, "problems": []},
    }
    doc.update(overrides)
    return doc


def test_lineage_schema_accepts_consistent_document():
    from scripts.build_lineage import validate_lineage

    assert validate_lineage(_lineage_doc()) == []


def test_lineage_schema_rejects_missing_fields_and_inconsistent_demo():
    """schema 与"run → demo → runtime 版本一致"都必须可机器判定。"""
    from scripts.build_lineage import validate_lineage

    doc = _lineage_doc()
    del doc["demo"]
    assert any("demo" in p for p in validate_lineage(doc))

    broken = _lineage_doc()
    broken["demo"]["meta"]["version"] = "20260904_v2"
    broken["demo"]["parent"]["resolved"] = False
    problems = validate_lineage(broken)
    assert any("demo.meta.version" in p for p in problems)
    assert any("demo.parent" in p for p in problems)


def test_lineage_consistency_flags_demo_from_other_run(tmp_path):
    """demo 指向的 run 不是 latest_run / 目录不存在时必须被记录为问题。"""
    from scripts.build_lineage import _consistency, _find_run

    eval_dir = tmp_path / "eval" / "20260915_v1"
    (eval_dir / "runs" / "run_a").mkdir(parents=True)
    (eval_dir / "runs" / "run_a" / "meta.json").write_text(
        json.dumps({"version": "20260915_v1", "llm_used": True}), encoding="utf-8")

    parent = _find_run(eval_dir, "run_a")
    assert parent["resolved"] is True and parent["version"] == "20260915_v1"

    demo_meta = {"available": True, "version": "20260915_v1",
                 "source_run": "run_a", "measurement_mode": "real_llm"}
    checks, problems = _consistency("20260915_v1", demo_meta, parent,
                                    {"run_id": "run_b"})
    assert checks["demo_source_run_resolved"] is True
    assert checks["demo_version_matches_runtime"] is True
    assert any("latest_run" in p for p in problems)

    missing = _find_run(eval_dir, "run_zzz")
    assert missing["resolved"] is False
    _checks2, problems2 = _consistency("20260915_v1", demo_meta, missing, {"run_id": "run_a"})
    assert any("无法在" in p for p in problems2)


def test_lineage_flags_parent_run_index_version_mismatch():
    """B3：父 run 的 index_version 与运行时不一致时必须产出 problem（不能只记录）。"""
    from scripts.build_lineage import _consistency

    demo_meta = {"available": True, "version": "20260915_v1",
                 "source_run": "run_a", "measurement_mode": "real_llm"}
    parent = {"resolved": True, "run_id": "run_a", "version": "20260915_v1",
              "index_version": "20260901_v1"}
    checks, problems = _consistency("20260915_v1", demo_meta, parent, {"run_id": "run_a"})
    assert checks["parent_run_index_version_matches"] is False
    assert any("index_version" in p for p in problems), problems


def test_lineage_requires_real_llm_measurement_mode():
    """B3：measurement_mode 缺失或为 offline 时，血缘校验必须判失败。"""
    from scripts.build_lineage import _consistency, validate_lineage

    base = _lineage_doc()
    # 缺字段
    del base["demo"]["meta"]["measurement_mode"]
    assert any("measurement_mode" in p for p in validate_lineage(base))

    offline = _lineage_doc()
    offline["demo"]["meta"]["measurement_mode"] = "offline"
    assert any("real_llm" in p for p in validate_lineage(offline))

    _checks, problems = _consistency(
        "20260915_v1",
        {"available": True, "version": "20260915_v1", "source_run": "run_a",
         "measurement_mode": None},
        {"resolved": True, "run_id": "run_a", "version": "20260915_v1",
         "index_version": "20260915_v1"},
        {"run_id": "run_a"})
    assert any("measurement_mode" in p for p in problems), problems


def test_gen_demo_examples_writes_measurement_mode(tmp_path, monkeypatch):
    """B3：生成器的 payload 必须带 measurement_mode 与实测 model_used。"""
    import scripts.gen_demo_examples as gde

    written: dict = {}

    def _fake_write(path, data, indent=2):
        written["path"] = str(path)
        written["data"] = data

    monkeypatch.setattr(gde, "write_json", _fake_write)
    monkeypatch.setattr(gde, "_pick_balanced", lambda cands, limit: cands[:1])
    # 直接把 payload 组装后的 write_json 拦下来，不跑真实检索链路
    monkeypatch.setattr(gde, "_measure_all", lambda *a, **k: None)

    class _FakeLLM:
        available = True

    class _FakeGen:
        llm = _FakeLLM()
        last_usage = {}
        last_truncated = False

    class _FakeRuntime:
        generate = _FakeGen()
        version = "20260915_v1"

    monkeypatch.setattr(gde, "build_runtime", lambda settings, version: _FakeRuntime())
    monkeypatch.setattr(gde, "get_settings", lambda: _settings(
        data_dir=tmp_path, snapshot_dir=tmp_path / "snapshot",
        index_dir=tmp_path / "index"))
    monkeypatch.setattr(gde, "_capability_from_traces",
                        lambda run_dir, qid: ("both", {"graph_min": 1, "text_min": 1}))

    async def _fake_measure_all(*a, **k):
        return None

    monkeypatch.setattr(gde, "_measure_all", _fake_measure_all)
    # 题库/评分：main 会读它们筛候选
    (tmp_path / "eval" / "20260915_v1" / "runs" / "run_a").mkdir(parents=True)
    (tmp_path / "eval" / "20260915_v1" / "runs" / "run_a" / "meta.json").write_text(
        json.dumps({"version": "20260915_v1"}), encoding="utf-8")
    (tmp_path / "eval" / "20260915_v1" / "runs" / "run_a" / "scores.jsonl").write_text(
        json.dumps({"qid": "Q1", "answer_correctness": "correct"}) + "\n", encoding="utf-8")
    (tmp_path / "eval" / "20260915_v1" / "questions.jsonl").write_text(
        json.dumps({"id": "Q1", "question": "长平之战是什么？", "suite": "main",
                    "reviewed": True, "answerable": True, "category": "entity_intro"}) + "\n",
        encoding="utf-8")
    monkeypatch.setattr(gde.sys, "argv", [
        "gen_demo_examples.py", "--version", "20260915_v1", "--run", "run_a", "--measure"])

    rc = gde.main()
    assert rc == 0, rc
    assert written["data"]["measurement_mode"] == "real_llm", written["data"].get("measurement_mode")
    assert written["data"]["examples"], "至少要有 1 条示例题"


def test_sha256sums_is_lf_only(tmp_path, monkeypatch):
    """B1：SHA256SUMS 必须是 LF 行尾——CRLF 会让 `sha256sum -c` 在 Linux 上逐行失败。

    断言落在**字节**层面：自建 verify-sums 用 splitlines() 能容忍 `\\r`，
    所以只有字节级检查才能覆盖"验证工具 ≠ 验收标准"这个根因。
    """
    bam, settings, _root = _manifest_fixture(tmp_path, monkeypatch)
    manifest = bam.collect(settings, "v1")
    out = settings.data_dir / "release" / "artifact-manifest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    sums_path, logical_path = bam._write_sha256sums(settings, manifest, out)

    sums_bytes = sums_path.read_bytes()
    assert b"\r" not in sums_bytes, "SHA256SUMS 含 CR：Linux 上 sha256sum -c 会全部失败"
    assert sums_bytes.count(b"\n") == len(sums_bytes.splitlines())

    # 其余发布证据同样是 LF（跨平台字节一致 → 哈希可复现）
    assert b"\r" not in logical_path.read_bytes()
    assert b"\r" not in out.read_bytes()
    # 并且物理校验仍然通过（换行改变的只是行尾，不是内容哈希口径）
    assert bam.cmd_verify_sums(type("A", (), {})()) == 0


def test_release_evidence_writers_use_lf(tmp_path):
    """B1：证据写入统一走 lib.json_io.write_text_lf / write_json（LF）。"""
    from lib.json_io import write_json, write_text_lf

    text_path = tmp_path / "evidence.txt"
    write_text_lf(text_path, "a\nb\n")
    assert text_path.read_bytes() == b"a\nb\n"

    json_path = tmp_path / "evidence.json"
    write_json(json_path, {"k": "v"})
    assert b"\r" not in json_path.read_bytes()


# ---------------------------------------------------------------- 打包与运行器
def test_release_bundle_refuses_missing_or_failed_smoke(tmp_path, monkeypatch):
    """B7：打包脚本必须在 smoke 报告缺失/失败/损坏时硬拒绝（不能只打 warning）。"""
    from scripts import build_release_bundle as brb

    root = tmp_path
    data_dir = root / "data"
    # 最小必需目录与依赖声明（否则会先撞上"缺少必需目录/依赖声明"分支）
    for rel in ("snapshot/v1", "index/v1", "eval/v1"):
        (data_dir / rel).mkdir(parents=True, exist_ok=True)
        (data_dir / rel / "manifest.json" if "snapshot" in rel else
         data_dir / rel / "manifest.json").write_text("{}", encoding="utf-8")
    (root / "frontend" / "dist").mkdir(parents=True, exist_ok=True)
    (root / "frontend" / "dist" / "index.html").write_text("<html></html>", encoding="utf-8")
    for rel in ("requirements.txt", "requirements-dev.txt", "requirements.lock",
                "requirements-dev.lock", "frontend/package.json",
                "frontend/package-lock.json"):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    # 发布证据齐备
    release_dir = data_dir / "release"
    release_dir.mkdir(parents=True, exist_ok=True)
    for name in brb.EVIDENCE_FILES:
        (release_dir / name).write_text("{}", encoding="utf-8")

    settings = _settings(data_dir=data_dir, log_dir=root / "logs",
                         snapshot_dir=data_dir / "snapshot", index_dir=data_dir / "index")
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    # build_release_bundle 在 main() 内部 import get_settings，无法直接替换模块属性；
    # 用环境变量把真实 get_settings 指到临时目录（顺带覆盖配置读取路径）
    monkeypatch.setenv("RAG_DATA_DIR", str(data_dir))
    monkeypatch.setenv("RAG_LOG_DIR", str(settings.log_dir))
    monkeypatch.setenv("RAG_SNAPSHOT_DIR", str(data_dir / "snapshot"))
    monkeypatch.setenv("RAG_INDEX_DIR", str(data_dir / "index"))
    monkeypatch.setenv("FRONTEND_DIST", str(root / "frontend" / "dist"))
    monkeypatch.setattr(brb, "repo_root", lambda: root)
    monkeypatch.setattr(brb, "export_source", lambda r, d: 0)   # 不跑 git archive

    # 1) 完全没有 smoke 报告
    assert _run_bundle_with_stubs(brb, tmp_path, smoke=None) == 5

    # 2) 报告存在但退出码非 0
    bad = settings.log_dir / "smoke_bad.json"
    bad.write_text(json.dumps({"exit_code": 1, "passed": False, "failures": ["x"]}),
                   encoding="utf-8")
    assert _run_bundle_with_stubs(brb, tmp_path, smoke=bad) == 5

    # 3) 报告损坏
    broken = settings.log_dir / "smoke_broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert _run_bundle_with_stubs(brb, tmp_path, smoke=broken) == 5

    # 4) 退出码为 0 才放行（对照，避免"永远返回 5"的假通过）
    good = settings.log_dir / "smoke_good.json"
    good.write_text(json.dumps({"exit_code": 0, "passed": True, "examples": []}),
                    encoding="utf-8")
    assert _run_bundle_with_stubs(brb, tmp_path, smoke=good) == 0


def _run_bundle_with_stubs(brb, tmp_path, smoke):
    """在最小桩环境下跑 build_release_bundle.main()，返回退出码。"""
    import sys as _sys

    argv = ["build_release_bundle.py", "--version", "v1",
            "--out", str(tmp_path / "bundle"), "--no-archive"]
    if smoke is not None:
        argv += ["--smoke-report", str(smoke)]
    old_argv = _sys.argv
    _sys.argv = argv
    try:
        return brb.main()
    finally:
        _sys.argv = old_argv


def test_lock_hashes_parse_and_check(tmp_path):
    """lock_hashes 的解析/检查逻辑（不联网）。"""
    from scripts import lock_hashes as lh

    text = (
        "#\n# autogenerated\n#\n--index-url https://pypi.org/simple\n\n"
        "fastapi==0.121.0 \\\n"
        "    --hash=sha256:" + "a" * 64 + " \\\n"
        "    --hash=sha256:" + "b" * 64 + "\n"
        "    # via app\n"
        "uvicorn==0.32.0\n"
        "    # via app\n"
    )
    lock = tmp_path / "requirements.lock"
    lock.write_text(text, encoding="utf-8")

    blocks = lh.parse_lock(text)
    reqs = [(b["name"], b["version"]) for b in blocks if b["kind"] == "req"]
    assert reqs == [("fastapi", "0.121.0"), ("uvicorn", "0.32.0")]
    raws = [b["raw"] for b in blocks if b["kind"] == "raw"]
    assert "--index-url https://pypi.org/simple" in raws, "选项行必须原样保留"

    problems = lh.check(lock)
    assert problems and "uvicorn" in problems[0], problems

    rendered = lh.render(blocks, {("fastapi", "0.121.0"): ["c" * 64],
                                  ("uvicorn", "0.32.0"): ["d" * 64]})
    assert rendered.count("--hash=sha256:") == 2
    assert "# via app" in rendered
    lock.write_text(rendered, encoding="utf-8")
    assert lh.check(lock) == []


def test_demo_examples_503_when_version_mismatch(tmp_path):
    """demo 清单版本与运行时不一致时必须 503（而不是返回旧时延）。"""
    import server.api as api_mod
    from fastapi.testclient import TestClient

    version_dir = tmp_path / "eval" / "v2"
    version_dir.mkdir(parents=True)
    (version_dir / "demo_examples.json").write_text(json.dumps({
        "version": "v1", "source_run": "run_x", "measurement_mode": "real_llm",
        "examples": [{"id": "Q1", "question": "q", "category": "c", "capability": "both"}],
    }, ensure_ascii=False), encoding="utf-8")

    class _Rt:
        version = "v2"
        settings = _settings(data_dir=tmp_path)

    previous = getattr(api_mod.app.state, "runtime", None)
    api_mod.app.state.runtime = _Rt()
    try:
        client = TestClient(api_mod.app)
        resp = client.get("/api/demo/examples")
    finally:
        api_mod.app.state.runtime = previous
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "error" and "版本不一致" in body["message"]


def test_chroma_audit_apply_moves_orphan_and_reaudits(tmp_path, monkeypatch):
    """`--apply` 的清理 + 清理后重审计路径。"""
    acs, index_dir = _audit_fixture(tmp_path, monkeypatch)
    orphan = index_dir / "vectors" / "chroma" / "orphan-segment-dir"
    orphan.mkdir(parents=True, exist_ok=True)
    (orphan / "data_level0.bin").write_bytes(b"orphan")

    report = acs.audit(index_dir, collection_name="chunks_v1")
    assert report["orphan_dirs"] == ["orphan-segment-dir"]

    out_path = tmp_path / "data" / "release" / "chroma-segment-audit.json"
    applied = acs.apply_cleanup(index_dir, report, out_path)
    assert applied["cleanup"]["action"] == "moved_to_backup"
    moved = applied["cleanup"]["moved"][0]
    assert moved["dir"] == "orphan-segment-dir" and moved["hash_verified"] is True
    assert not orphan.exists(), "孤儿目录必须被移出制品目录"

    after = acs.audit(index_dir, collection_name="chunks_v1")
    assert after["orphan_dirs"] == [], "清理后重审计不应再有孤儿"
    assert after["counts_consistent"] is True
    assert out_path.is_file()


# ---------------------------------------------------------------- SBOM
def test_sbom_generate_and_validate_roundtrip(tmp_path, monkeypatch):
    from scripts import gen_sbom

    (tmp_path / "requirements.lock").write_text(
        "fastapi==0.121.0\n# via 注释\nuvicorn==0.32.0\n", encoding="utf-8")
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package-lock.json").write_text(json.dumps({
        "packages": {"": {"name": "root", "version": "0.3.0"},
                     "node_modules/vue": {"version": "3.5.12"}},
    }), encoding="utf-8")

    doc = gen_sbom.generate(tmp_path, "20260915_v1")
    assert doc["spdxVersion"] == "SPDX-2.3"
    assert doc["dataLicense"] == "CC0-1.0"
    assert doc["SPDXID"] == "SPDXRef-DOCUMENT"
    assert doc["creationInfo"]["creators"]
    names = {p["name"] for p in doc["packages"]}
    assert {"fastapi", "uvicorn", "vue"} <= names
    assert any(r["relationshipType"] == "DESCRIBES" for r in doc["relationships"])
    assert all(p["externalRefs"][0]["referenceLocator"].startswith(("pkg:pypi/", "pkg:npm/"))
               for p in doc["packages"])
    assert gen_sbom.validate(doc) == []


def test_sbom_validate_rejects_incomplete_document():
    from scripts.gen_sbom import validate

    problems = validate({"spdxVersion": "SPDX-2.3", "name": "x", "packages": []})
    assert any("dataLicense" in p for p in problems)
    assert any("SPDXID" in p for p in problems)
    assert any("creationInfo" in p for p in problems)

    doc = {
        "spdxVersion": "SPDX-2.3", "dataLicense": "CC0-1.0", "SPDXID": "SPDXRef-DOCUMENT",
        "name": "x", "documentNamespace": "https://example.invalid/x",
        "creationInfo": {"created": "2026-09-16T00:00:00Z", "creators": ["Tool: t"]},
        "packages": [{"SPDXID": "SPDXRef-Package-a", "name": "a", "versionInfo": "1",
                      "downloadLocation": "NOASSERTION", "licenseConcluded": "NOASSERTION",
                      "licenseDeclared": "NOASSERTION", "copyrightText": "NOASSERTION",
                      "filesAnalyzed": False, "hashes": [{"algorithm": "SHA256", "value": "x"}],
                      "externalRefs": []}],
        "relationships": [],
    }
    problems2 = validate(doc)
    assert any("filesAnalyzed=false" in p for p in problems2)
    assert any("purl" in p for p in problems2)
    assert any("DESCRIBES" in p for p in problems2)


def test_sbom_purl_and_namespace_are_canonical_and_reproducible(tmp_path):
    """D2：scoped npm 包要按规范编码；命名空间必须可重现（同内容同命名空间）。"""
    from scripts.gen_sbom import generate, purl_for

    assert purl_for("npm", "@vue/test-utils", "2.5.0") == "pkg:npm/%40vue/test-utils@2.5.0"
    assert purl_for("npm", "vue", "3.5.12") == "pkg:npm/vue@3.5.12"
    assert purl_for("pypi", "PyYAML", "6.0") == "pkg:pypi/pyyaml@6.0"

    (tmp_path / "requirements.lock").write_text("fastapi==0.121.0\n", encoding="utf-8")
    (tmp_path / "requirements-dev.lock").write_text("pytest==9.0.2\n", encoding="utf-8")
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package-lock.json").write_text(json.dumps({
        "packages": {"node_modules/@vue/test-utils": {"version": "2.5.0"}},
    }), encoding="utf-8")

    first = generate(tmp_path, "20260915_v1")
    second = generate(tmp_path, "20260915_v1")
    assert first["documentNamespace"] == second["documentNamespace"], "命名空间必须可重现"
    names = {p["name"] for p in first["packages"]}
    assert {"fastapi", "pytest", "@vue/test-utils"} <= names, "两份锁都要收进 SBOM"
    scoped = next(p for p in first["packages"] if p["name"] == "@vue/test-utils")
    assert scoped["externalRefs"][0]["referenceLocator"] == "pkg:npm/%40vue/test-utils@2.5.0"


# ---------------------------------------------------------------- 数据制品
def _make_zip(path: Path, entries: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return path


def test_fetch_data_artifact_rejects_zip_slip(tmp_path):
    from scripts.fetch_data_artifact import plan_extraction

    evil = _make_zip(tmp_path / "evil.zip", {
        "data/ok.json": "{}",
        "../../etc/passwd": "pwned",
        "data/../escape.txt": "nope",
    })
    entries, problems = plan_extraction(evil, ("data/",))
    assert len(entries) == 1
    assert len(problems) == 2
    # 只放行 data/ 下的那一个文件
    assert [str(rel) for _info, rel in entries] == ["data/ok.json"]

    outside = _make_zip(tmp_path / "outside.zip", {"server/api.py": "x"})
    entries2, problems2 = plan_extraction(outside, ("data/",))
    assert entries2 == [] and any("超出允许范围" in p for p in problems2)


def test_fetch_data_artifact_rejects_bad_hash_and_extracts_good_one(tmp_path, monkeypatch):
    from scripts import fetch_data_artifact as fda

    zip_path = _make_zip(tmp_path / "data.zip", {
        "data/snapshot/v1/manifest.json": '{"version":"v1"}',
    })
    good = fda.sha256_file(zip_path)

    monkeypatch.setattr(fda.sys, "argv", [
        "fetch_data_artifact.py", "--from-file", str(zip_path),
        "--sha256", "0" * 64, "--dest", str(tmp_path / "out")])
    assert fda.main() == 3, "哈希不匹配必须拒绝解包"

    monkeypatch.setattr(fda.sys, "argv", [
        "fetch_data_artifact.py", "--from-file", str(zip_path),
        "--sha256", good, "--dest", str(tmp_path / "out")])
    assert fda.main() == 0
    assert (tmp_path / "out" / "data" / "snapshot" / "v1" / "manifest.json").is_file()


def test_fetch_data_artifact_requires_expected_hash(tmp_path, monkeypatch):
    from scripts import fetch_data_artifact as fda

    zip_path = _make_zip(tmp_path / "data.zip", {"data/a.json": "{}"})
    monkeypatch.setattr(fda.sys, "argv", [
        "fetch_data_artifact.py", "--from-file", str(zip_path), "--dest", str(tmp_path)])
    assert fda.main() == 2, "没有预期哈希必须拒绝（不允许先解包再说）"


# ---------------------------------------------------------------- 文档数字
def test_check_docs_dataset_counts_matches_manifest(tmp_path, monkeypatch):
    """D5：用临时文档副本测计数校验，不改写仓库里的 current-status.md。"""
    from scripts import check_docs

    doc = tmp_path / "current-status.md"
    monkeypatch.setattr(check_docs, "_actual_counts", lambda: {
        "version": "v1", "entities": 9925, "relations": 17700, "vectors": 9544})

    doc.write_text("| 数据集 | 9925 实体 / 17700 关系 / 9544 向量条 | 说明 |\n", encoding="utf-8")
    assert check_docs.check_dataset_counts(doc) == []

    doc.write_text("| 数据集 | 10925 实体 / 17730 关系 / 9544 向量条 | 说明 |\n", encoding="utf-8")
    problems = check_docs.check_dataset_counts(doc)
    assert len(problems) == 2 and all("不一致" in p for p in problems)

    doc.write_text("没有计数行的文档\n", encoding="utf-8")
    assert check_docs.check_dataset_counts(doc), "缺少计数行也要报出来"

    assert check_docs.check_dataset_counts(tmp_path / "missing.md"), "文档缺失要报错"


def test_check_docs_skips_when_data_absent(monkeypatch):
    from scripts import check_docs

    monkeypatch.setattr(check_docs, "_actual_counts", lambda: None)
    assert check_docs.check_dataset_counts() == []
