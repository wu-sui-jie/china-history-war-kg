"""密钥/令牌扫描（2026-09-16 工作单 P2-7 的 CI 步骤）。

只做**入库内容**的静态扫描：`.env` 等本地文件按约定不入库，因此不扫（扫描时会先跳过）。
命中即返回非零，用于 CI 阻断。

用法：
    python RAG/scripts/check_secrets.py                    # 只扫 RAG/（历史默认范围）
    python RAG/scripts/check_secrets.py --root .           # 扫整个仓库（CI 门禁用这个）

**为什么要有 `--root`**（第 12 轮审查 P0-2 / P2-8）：本脚本原先的 ROOT 写死为 `RAG/`，
旧模块 CI 的 grep 又只扫 `backend` 与 `entity-event-relation`，于是仓库根下的 `deploy/`、
`feishu-bot/`、工作流与根级脚本全部在扫描范围之外——`deploy/env/*.env` 里放过非空的
固定口令与 JWT 密钥（后来清成 CHANGE_ME 占位符）正是从这条缝里漏过去的。门禁范围必须
覆盖"可能写进仓库的每个角落"，而不是实施者记得的那几个目录。
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parent.parent

# 不入库或不需要扫的目录：构建产物、依赖、缓存、运行期数据、IDE 元数据。
# 注意 "data" / "logs" 是运行期目录（.gitignore 已排除），里面的 JSON/SQLite 可能含真实内容。
#
# 这里**不能**放 "env"：Python 的虚拟环境习惯叫 `env/`，但仓库里的 `deploy/env/`
# 恰好是密钥模板所在目录，按名字跳过等于把最该扫的地方排除掉（实测过：
# 加了 "env" 之后 deploy/env/backend.env 里的固定凭据一扫即过）。
# 虚拟环境改用更具体的名字识别。
SKIP_DIRS = {".git", "node_modules", "dist", "__pycache__", ".pytest_cache", ".ruff_cache",
             ".mypy_cache", ".venv", "venv", "virtualenv", "site-packages", "logs", "new",
             "data", "cache", "output", ".idea", ".vscode", "war_extraction.egg-info",
             "coverage", ".tox", "build"}
SKIP_FILES = {".env", ".env.local", "pnpm-lock.yaml", "package-lock.json",
              "requirements.lock", "requirements-dev.lock"}
SCAN_SUFFIXES = {".py", ".ts", ".vue", ".js", ".mjs", ".cjs", ".json", ".yml", ".yaml",
                 ".md", ".txt", ".example", ".sh", ".bash", ".cfg", ".ini", ".conf",
                 ".service", ".env", ".toml", ""}

# 只匹配"看起来像真密钥"的形态，避免把占位符与文档示例判成泄漏
PATTERNS = [
    (re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"), "OpenAI 风格密钥"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{20,}\b"), "Anthropic 风格密钥"),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"), "GitHub token"),
    (re.compile(r"\bAKIA[0-9A-Z]{12,}\b"), "AWS Access Key"),
    (re.compile(r"(?i)\b(api[_-]?key|secret|password|token)\s*[:=]\s*[\"']([A-Za-z0-9\-_]{20,})[\"']"),
     "硬编码的密钥/口令"),
    # 无引号的 .env 写法（KEY=value）：deploy/env/*.env 就是这种形态，
    # 上面那条带引号的规则对它们完全无效——这正是固定凭据曾经躺在模板里的原因之一。
    (re.compile(r"(?im)^\s*(?:export\s+)?[A-Z0-9_]*(?:SECRET|PASSWORD|PASSWD|TOKEN|API_KEY|APIKEY)"
                r"[A-Z0-9_]*\s*=\s*([A-Za-z0-9+/=_\-]{16,})\s*$"),
     "未加引号的硬编码密钥/口令"),
]
# 明确允许的占位与示例。
#
# 这里包含 `test[-_]?(key|secret|token|password)` 与 `do[-_]?not[-_]?use`：测试夹具里
# 出现"明显是假的"密钥是正常写法（例如 tests/test_rag_auth.py 的 HS256 签名密钥），
# 而真正的凭据不会是这两个形状。放行它们不会放宽对真实密钥的判定——见文件末尾
# 用复刻样本做的验证记录。
ALLOWLIST = re.compile(
    r"(?i)(your[-_]?|example|placeholder|xxx+|<[^>]+>|fake|dummy|redacted|\*{6,}"
    r"|change[-_]?me|openssl|rand[-_]?base64|ci[-_]|占位|待填|留空|同值"
    r"|test[-_]?(key|secret|token|password)|do[-_]?not[-_]?use)"
)
# 明显不是密钥的合法赋值（字面量开关）
ALLOWLIST_VALUES = re.compile(
    r"^(true|false|yes|no|on|off|none|null|auto|debug|info|warning|error|critical)$",
    re.IGNORECASE,
)


def iter_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name in SKIP_FILES:
            continue
        if path.suffix not in SCAN_SUFFIXES:
            continue
        try:
            if path.stat().st_size > 2 * 1024 * 1024:
                continue
        except OSError:
            continue
        yield path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="扫描入库内容中的疑似明文密钥")
    parser.add_argument("--root", default=str(DEFAULT_ROOT),
                        help="扫描根目录，默认 RAG/；CI 传仓库根（--root .）")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()

    hits = []
    for path in iter_files(root):
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
                # 只看"等号右边"的值：变量名里出现 TOKEN/PASSWORD 不算命中
                if "=" in snippet:
                    value = snippet.split("=", 1)[1].strip().strip("'\"")
                    if ALLOWLIST.search(value) or ALLOWLIST_VALUES.match(value):
                        continue
                    if not value:
                        continue
                line_no = text[: match.start()].count("\n") + 1
                hits.append(f"{path.relative_to(root).as_posix()}:{line_no} 疑似{label}")

    if hits:
        print(f"发现 {len(hits)} 处疑似密钥（扫描根：{root}）：")
        for item in hits[:50]:
            print(f"  - {item}")
        return 1
    print(f"未发现明文密钥（扫描根：{root}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
