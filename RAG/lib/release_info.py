"""发布可追溯信息（2026-09-15 审核 P0-7 / P0-8）。

服务要能回答"我现在到底在用哪份数据、哪份代码"，否则灰度与回滚无从下手。
这里只做**只读**采集：文件哈希 + Git commit + 版本目录名，不进任何业务逻辑。

为什么不用 subprocess 调 git：启动路径上多一次进程创建既慢又依赖环境变量，
直接读 `.git` 文件即可（HEAD → refs → packed-refs 三段查找，覆盖常见仓库形态）。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Optional

_CHUNK = 1 << 20

# 参与配置指纹的密钥字段：只用于排除，不进入哈希（避免把密钥写进可外发的指纹）
_SECRET_FIELDS = ("llm_api_key", "fallback_llm_api_key", "embedding_api_key")


def file_sha256(path: Optional[Path]) -> str:
    """文件 SHA-256（十六进制）；文件不存在或读失败返回空串。"""
    if not path:
        return ""
    try:
        p = Path(path)
        if not p.is_file():
            return ""
        digest = hashlib.sha256()
        with open(p, "rb") as f:
            while True:
                block = f.read(_CHUNK)
                if not block:
                    break
                digest.update(block)
        return digest.hexdigest()
    except Exception:  # noqa: BLE001
        return ""


def _read_first_line(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except Exception:  # noqa: BLE001
        return ""


def git_commit(root: Path) -> str:
    """读取仓库 HEAD 指向的 commit（短哈希 + 分支名）；非仓库返回空串。

    支持三种形态：普通 ref 文件、packed-refs、detached HEAD（HEAD 直接是哈希）。
    """
    try:
        git_dir = Path(root) / ".git"
        if not git_dir.is_dir():
            return ""
        head = _read_first_line(git_dir / "HEAD")
        if not head:
            return ""
        if not head.startswith("ref:"):
            return head[:12]
        ref = head.split(":", 1)[1].strip()
        sha = _read_first_line(git_dir / ref)
        if not sha:
            packed = git_dir / "packed-refs"
            if packed.is_file():
                for line in packed.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if line.endswith(" " + ref):
                        sha = line.split(" ", 1)[0]
                        break
        if not sha:
            return ""
        branch = ref.rsplit("/", 1)[-1]
        return f"{sha[:12]}@{branch}"
    except Exception:  # noqa: BLE001
        return ""


# 明确排除在发布范围外的工作目录（2026-09-16 工作单要求 RAG/new/ 不修改、不提交）。
# 它未跟踪是"约定内"的状态，不应让发布门禁永远判脏。
RELEASE_IGNORE_PREFIXES = ("new/",)


def git_status_lines(root: Path, ignore_prefixes: tuple[str, ...] = RELEASE_IGNORE_PREFIXES
                     ) -> Optional[list[str]]:
    """`git status --porcelain` 的行（已过滤约定的排除目录）；失败返回 None。"""
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(root), capture_output=True, text=True, timeout=5,
        )
        if out.returncode != 0:
            return None
        lines = []
        for raw in out.stdout.splitlines():
            path = raw[3:].strip().strip('"')
            if any(path.startswith(p) or f"/{p}" in path for p in ignore_prefixes):
                continue
            lines.append(raw)
        return lines
    except Exception:  # noqa: BLE001
        return None


def git_dirty(root: Path, ignore_prefixes: tuple[str, ...] = RELEASE_IGNORE_PREFIXES
              ) -> Optional[bool]:
    """工作区是否有未提交改动（不含约定的排除目录）；非仓库或 git 不可用返回 None。

    为什么要它（第四轮复核 P0-4）：`git_commit` 只报告 HEAD，脏工作区里运行的代码
    可能和 HEAD 完全不同，验收证据无法反向定位到唯一源码。health 暴露该字段，
    release 构建脚本据此拒绝带未提交改动发布。
    """
    lines = git_status_lines(root, ignore_prefixes)
    return None if lines is None else bool(lines)


def config_fingerprint(settings: Any) -> str:
    """有效配置的 SHA-256 指纹（排除密钥）：同一份配置在任何机器上得到同一指纹。

    用于把验收证据绑定到具体配置：改了模型、阈值、上限或数据目录，指纹都会变。
    """
    try:
        import dataclasses

        data = {}
        for f in dataclasses.fields(settings):
            if f.name in _SECRET_FIELDS:
                continue
            value = getattr(settings, f.name, None)
            data[f.name] = str(value) if isinstance(value, Path) else value
        raw = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
    except Exception:  # noqa: BLE001
        return ""


def artifact_manifest_sha256(data_dir: Optional[Path]) -> str:
    """制品清单 `data/release/artifact-manifest.json` 的哈希；不存在返回空串。

    清单由 scripts/build_artifact_manifest.py 生成，覆盖 snapshot/index/eval/dist 等
    发布必需文件；health 只报告清单哈希，完整校验用脚本离线执行。
    """
    if not data_dir:
        return ""
    return file_sha256(Path(data_dir) / "release" / "artifact-manifest.json")


def version_artifacts(snapshot_dir: Optional[Path],
                      index_dir: Optional[Path]) -> dict:
    """活跃版本的关键制品指纹（供 /api/health 与启动日志使用）。

    只哈希 manifest 与向量 ids 这类"小而关键"的文件：全量哈希 snapshot/index
    在启动路径上太贵，且 manifest 已覆盖版本与计数口径。
    """
    snapshot_dir = Path(snapshot_dir) if snapshot_dir else None
    index_dir = Path(index_dir) if index_dir else None
    out = {
        "snapshot_dir": str(snapshot_dir or ""),
        "index_dir": str(index_dir or ""),
        "snapshot_manifest_sha256": "",
        "index_manifest_sha256": "",
        "vector_ids_sha256": "",
    }
    if snapshot_dir:
        out["snapshot_manifest_sha256"] = file_sha256(snapshot_dir / "manifest.json")
    if index_dir:
        out["index_manifest_sha256"] = file_sha256(index_dir / "manifest.json")
        out["vector_ids_sha256"] = file_sha256(index_dir / "vectors" / "ids.json")
    return out


def repo_root() -> Path:
    """RAG 子项目根目录（lib/ 的上一级）。"""
    return Path(__file__).resolve().parent.parent
