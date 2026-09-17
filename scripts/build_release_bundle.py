"""release 制品打包（2026-09-16 第五轮审核 P2-7 第 5 条）。

旧实现的打包步骤只复制了 manifest / SHA256SUMS / lineage / Chroma 审计与依赖声明，
**没有**源码、前端 dist、snapshot/index、eval/demo、smoke 报告、SBOM——
于是"在另一台机器下载后 verify 并启动服务"这个目标根本达不到：
解包出来只有一堆校验文件，没有可运行的任何东西。

本脚本按"下载即可验证、即可启动"的目标组装目录：

    release/
      README-RELEASE.md          验证与启动步骤（含哈希与版本）
      MANIFEST.txt               包内清单（相对路径 + 大小 + sha256）
      pypi 源码树                scripts/ server/ config/ lib/ contracts/ ...（git archive HEAD）
      data/snapshot/<v>/...      服务要读的快照
      data/index/<v>/...         索引 + 向量 + Chroma 段
      data/eval/<v>/...          题库 / demo / 评测 run（血缘的父节点）
      frontend/dist/...          前端构建产物（同源托管）
      data/release/...           manifest / SHA256SUMS / LOGICAL_HASHES / lineage /
                                 chroma 审计 / SBOM / smoke 报告
      deps 说明                  requirements*.lock 与 package-lock（源码树里已含）

    china-war-rag-release.tar.gz + .sha256

设计取舍：包内保持**仓库相对路径**，这样解包后 `scripts/build_artifact_manifest.py verify`
可以直接按清单逐文件核对（清单里的 relative_path 就是仓库相对路径），
不需要额外映射表。

用法：
    python scripts/build_release_bundle.py --version 20260915_v1
    python scripts/build_release_bundle.py --version 20260915_v1 --out dist-release --no-archive
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.release_info import git_commit, repo_root  # noqa: E402

_CHUNK = 1 << 20
# 打包的固定内容（相对仓库根）
REQUIRED_FILES = (
    "requirements.txt", "requirements-dev.txt",
    "requirements.lock", "requirements-dev.lock",
    "frontend/package.json", "frontend/package-lock.json",
)
EVIDENCE_FILES = (
    "artifact-manifest.json", "SHA256SUMS", "LOGICAL_HASHES.json",
    "lineage.json", "chroma-segment-audit.json", "sbom.json",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(_CHUNK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _copy_tree(src: Path, dst: Path) -> int:
    count = 0
    for path in sorted(src.rglob("*")):
        if path.is_file():
            target = dst / path.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            count += 1
    return count


def export_source(root: Path, dst: Path) -> int:
    """用 `git archive HEAD` 导出源码树（只含已提交内容，天然排除产物与密钥）。"""
    proc = subprocess.run(
        ["git", "archive", "--format=tar", "HEAD"],
        cwd=str(root), capture_output=True, timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git archive 失败: {proc.stderr.decode('utf-8', 'replace')[:300]}")
    count = 0
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            rel = Path(member.name)
            if rel.is_absolute() or ".." in rel.parts:
                continue                      # 防御：git archive 不应产生，但不信任输入
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(member)
            if src is None:
                continue
            with open(target, "wb") as out:
                shutil.copyfileobj(src, out, length=_CHUNK)
            count += 1
    return count


def _demo_parent_run(settings, version: str) -> str:
    """从血缘文件里读出 demo 的父 run id（有则把它一并打包，保证血缘可追溯）。"""
    lineage = settings.data_dir / "release" / "lineage.json"
    if not lineage.is_file():
        return ""
    try:
        data = json.loads(lineage.read_text(encoding="utf-8"))
        parent = ((data.get("demo") or {}).get("parent") or {})
        return parent.get("run_id", "") if parent.get("resolved") else ""
    except Exception:  # noqa: BLE001
        return ""


def write_readme(bundle: Path, version: str, commit: str, counts: dict) -> None:
    text = f"""# china-war RAG release bundle

- 数据版本：`{version}`
- 源码 commit：`{commit or '(未记录)'}`
- 打包时间：{time.strftime("%Y-%m-%dT%H:%M:%S")}
- 条目：snapshot {counts.get('snapshot', 0)} / index {counts.get('index', 0)} /
  eval {counts.get('eval', 0)} / dist {counts.get('dist', 0)} / 源码 {counts.get('source', 0)}

## 一、验证（解包后在本目录执行）

```bash
pip install --require-hashes -r requirements.lock      # 依赖（锁文件带哈希）
python scripts/build_artifact_manifest.py verify       # 逐文件核对制品
python scripts/build_artifact_manifest.py verify-sums  # 标准 SHA256SUMS 物理校验
python scripts/build_lineage.py --check                # 血缘 schema 与跨层一致性
python scripts/gen_sbom.py validate --path data/release/sbom.json
```

## 二、启动

```bash
python scripts/run_server.py --host 0.0.0.0 --port 8000 --version {version}
# 浏览器访问 http://127.0.0.1:8000/（前端 dist 已包含，同源托管）
```

## 三、发布前必须确认

- `data/release/artifact-manifest.json` 的 `git_dirty=false`；
- `data/release/SHA256SUMS` 全部通过（物理哈希）；
- `LOGICAL_HASHES.json` 里的 Chroma 逻辑哈希与 manifest 一致（由 verify 覆盖）；
- `lineage.json` 的 `checks.consistent=true`（run → demo → runtime 版本一致）；
- smoke 报告 `data/release/smoke_release.json` 退出码为 0。
"""
    (bundle / "README-RELEASE.md").write_text(text, encoding="utf-8")


def write_manifest_txt(bundle: Path) -> int:
    lines = []
    total = 0
    for path in sorted(bundle.rglob("*")):
        if not path.is_file() or path.name in ("MANIFEST.txt",):
            continue
        size = path.stat().st_size
        total += size
        lines.append(f"{_sha256(path)}  {size:>12}  {path.relative_to(bundle).as_posix()}")
    (bundle / "MANIFEST.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return total


def make_archive(bundle: Path, out_path: Path) -> Path:
    """把 bundle 打成 tar.gz（含 gzip 压缩），并写出 .sha256。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out_path, "w:gz", compresslevel=6) as tar:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                tar.add(path, arcname=f"{bundle.name}/{path.relative_to(bundle).as_posix()}")
    digest = _sha256(out_path)
    out_path.with_suffix(out_path.suffix + ".sha256").write_text(
        f"{digest}  {out_path.name}\n", encoding="utf-8")
    print(f"归档: {out_path}（{out_path.stat().st_size / 1024 / 1024:.1f} MB）")
    print(f"  sha256: {digest}")
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser(description="组装完整 release 包")
    ap.add_argument("--version", default="", help="数据版本（缺省取 RAG_ACTIVE_VERSION）")
    ap.add_argument("--out", default="release", help="输出目录（会被清空重建）")
    ap.add_argument("--no-archive", action="store_true", help="只组装目录，不打 tar.gz")
    ap.add_argument("--smoke-report", default="", help="smoke 报告路径（默认自动查找最新一份）")
    args = ap.parse_args()

    from config.settings import get_settings

    settings = get_settings()
    version = args.version or settings.active_version
    if not version:
        print("未指定版本且 RAG_ACTIVE_VERSION 为空：请用 --version 指定")
        return 2
    root = repo_root()
    bundle = Path(args.out)
    if not bundle.is_absolute():
        bundle = root / bundle
    if bundle.exists():
        shutil.rmtree(bundle)
    bundle.mkdir(parents=True, exist_ok=True)

    counts: dict = {}
    print(f"组装 release 包 → {bundle}")
    counts["source"] = export_source(root, bundle)
    print(f"  源码（git archive HEAD）: {counts['source']} 个文件")

    for rel, key in ((f"data/snapshot/{version}", "snapshot"),
                     (f"data/index/{version}", "index"),
                     (f"data/eval/{version}", "eval"),
                     ("frontend/dist", "dist")):
        src = root / rel
        if not src.is_dir():
            print(f"::error:: 缺少必需目录: {rel}（先构建，再打包）")
            return 3
        counts[key] = _copy_tree(src, bundle / rel)
        print(f"  {rel}: {counts[key]} 个文件")

    for rel in REQUIRED_FILES:
        src = root / rel
        if not src.is_file():
            print(f"::error:: 缺少依赖声明: {rel}")
            return 3
        target = bundle / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)

    # 发布证据 + smoke 报告
    evidence_dir = bundle / "data" / "release"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    missing_evidence = []
    for name in EVIDENCE_FILES:
        src = settings.data_dir / "release" / name
        if src.is_file():
            shutil.copy2(src, evidence_dir / name)
        else:
            missing_evidence.append(name)
    if missing_evidence:
        # 证据不全就不该发布：SBOM/血缘缺失意味着"发布包无法自证"
        print(f"::error:: 缺少发布证据: {', '.join(missing_evidence)}")
        print("  依次运行：build_lineage.py → audit_chroma_segments.py → "
              "build_artifact_manifest.py build → gen_sbom.py generate")
        return 4

    smoke_src = Path(args.smoke_report) if args.smoke_report else None
    if smoke_src is None:
        candidates = sorted(settings.log_dir.glob("smoke_*.json")) if settings.log_dir.is_dir() else []
        smoke_src = candidates[-1] if candidates else None
    # smoke 报告**必须有且退出码为 0**（第五轮整改复核 B7）：旧实现对缺失报告只打 warning，
    # 于是手动跑打包就能绕过"服务冒烟"这道门禁。发布包不允许在缺少链路证据时生成。
    if smoke_src is None or not smoke_src.is_file():
        print("::error:: 缺少 smoke 报告：先跑 `python scripts/smoke_deploy.py "
              "--base <服务地址> --report logs/smoke_release.json` 再打包")
        print("  （release 工作流会自动产出该报告；手工打包请显式传 --smoke-report）")
        return 5
    try:
        report = json.loads(smoke_src.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"::error:: smoke 报告无法解析: {exc}")
        return 5
    if report.get("exit_code") != 0:
        print(f"::error:: smoke 报告退出码为 {report.get('exit_code')!r}"
              f"（passed={report.get('passed')!r}）：失败报告不得随包发布")
        for item in (report.get("failures") or [])[:5]:
            print(f"    - {item}")
        return 5
    shutil.copy2(smoke_src, evidence_dir / "smoke_release.json")
    print(f"  smoke 报告: {smoke_src.name}（exit_code=0，"
          f"{len(report.get('examples') or [])} 条示例题）")

    demo_run = _demo_parent_run(settings, version)
    if demo_run:
        print(f"  demo 父 run: {demo_run}（已随 eval 目录打包）")

    commit = git_commit(root)
    write_readme(bundle, version, commit, counts)
    total = write_manifest_txt(bundle)
    print(f"  MANIFEST.txt: 合计 {total / 1024 / 1024:.1f} MB")

    if not args.no_archive:
        make_archive(bundle, bundle.parent / f"{bundle.name}.tar.gz")
    print("完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
