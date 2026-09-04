"""治理版本号工具。

版本号格式：`YYYYMMDD_vN`，例如 `20260903_v1`。
F09 每次治理生成新快照版本；F11 基于快照版本构建索引并沿用同一版本号，
保证 data-contract 的 source_version 在快照与索引间一致。
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

_VERSION_RE = re.compile(r"^(\d{8})_v(\d+)$")


def next_version(base_dir: Path, date: datetime.date | None = None) -> str:
    """在 base_dir 下生成下一个版本号（当天已存在 v1 则递增 v2...）。"""
    date = date or datetime.date.today()
    prefix = date.strftime("%Y%m%d")
    max_n = 0
    if base_dir.exists():
        for child in base_dir.iterdir():
            m = _VERSION_RE.match(child.name)
            if m and m.group(1) == prefix:
                max_n = max(max_n, int(m.group(2)))
    return f"{prefix}_v{max_n + 1}"


def parse_version(name: str) -> tuple[str, int] | None:
    """解析版本目录名，返回 (YYYYMMDD, N) 或 None。"""
    m = _VERSION_RE.match(name)
    if not m:
        return None
    return m.group(1), int(m.group(2))


def list_versions(base_dir: Path) -> list[str]:
    """列出 base_dir 下的版本目录名（按版本倒序）。"""
    if not base_dir.exists():
        return []
    versions = [p.name for p in base_dir.iterdir() if p.is_dir() and _VERSION_RE.match(p.name)]
    return sorted(versions, key=lambda v: (parse_version(v)[0], parse_version(v)[1]), reverse=True)


def is_valid_version(name: str) -> bool:
    return _VERSION_RE.match(name) is not None
