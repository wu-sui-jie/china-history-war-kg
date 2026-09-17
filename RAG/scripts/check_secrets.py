"""密钥/令牌扫描（2026-09-16 工作单 P2-7 的 CI 步骤）。

只做**入库内容**的静态扫描：`.env` 等本地文件按约定不入库，因此不扫（扫描时会先跳过）。
命中即返回非零，用于 CI 阻断。

用法：python scripts/check_secrets.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "node_modules", "dist", "__pycache__", ".pytest_cache", "logs", "new", "data"}
SKIP_FILES = {".env", ".env.local"}
SCAN_SUFFIXES = {".py", ".ts", ".vue", ".js", ".mjs", ".json", ".yml", ".yaml", ".md",
                 ".txt", ".example", ".sh", ".cfg", ".ini", ""}

# 只匹配"看起来像真密钥"的形态，避免把占位符与文档示例判成泄漏
PATTERNS = [
    (re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"), "OpenAI 风格密钥"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{20,}\b"), "Anthropic 风格密钥"),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"), "GitHub token"),
    (re.compile(r"\bAKIA[0-9A-Z]{12,}\b"), "AWS Access Key"),
    (re.compile(r"(?i)\b(api[_-]?key|secret|password|token)\s*[:=]\s*[\"']([A-Za-z0-9\-_]{20,})[\"']"),
     "硬编码的密钥/口令"),
]
# 明确允许的占位与示例
ALLOWLIST = re.compile(
    r"(?i)(your[-_]?|example|placeholder|xxx+|<[^>]+>|test[-_]?key|fake|dummy|redacted|\*{6,})"
)


def iter_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name in SKIP_FILES:
            continue
        if path.suffix not in SCAN_SUFFIXES:
            continue
        if path.stat().st_size > 2 * 1024 * 1024:
            continue
        yield path


def main() -> int:
    hits = []
    for path in iter_files():
        if path.name in {".env", ".env.example"}:
            continue          # 模板文件只允许占位；真实 .env 不入库也不该扫
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            continue
        for pattern, label in PATTERNS:
            for match in pattern.finditer(text):
                snippet = match.group(0)
                if ALLOWLIST.search(snippet):
                    continue
                line_no = text[: match.start()].count("\n") + 1
                hits.append(f"{path.relative_to(ROOT).as_posix()}:{line_no} 疑似{label}")

    if hits:
        print(f"发现 {len(hits)} 处疑似密钥：")
        for item in hits[:50]:
            print(f"  - {item}")
        return 1
    print("未发现明文密钥")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
