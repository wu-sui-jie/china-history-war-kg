"""文档与配置的一致性检查（2026-09-16 工作单 P2-7 的 CI lint 部分）。

三类检查，全部离线可跑：
1. **相对链接**：Markdown 中的相对路径 / 图片必须真实存在（外链不检查，避免 CI 依赖网络）；
2. **current 口径**：对 current 文档禁止出现与事实冲突的表述（如"demo 可用"）；
3. **配置一致性**：`.env.example` 里出现的键必须真的被 config 读取。

用法：python scripts/check_docs.py [--strict]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "node_modules", "dist", "new", "__pycache__", ".pytest_cache", "logs"}

# current 文档（描述"现在能怎么用"）里不允许出现的句子片段
FORBIDDEN_IN_CURRENT = [
    (r"示例题?.{0,6}可用(?!性)", "demo 当前不可用（旧版本产物 → 接口 503）"),
    (r"依赖已锁定", "Python 侧仅声明下界；锁文件状态以 requirements.lock 是否存在为准"),
    (r"可发布|ready to release", "发布门禁尚未全部满足"),
]

CURRENT_DOCS = [
    "README.md",
    "docs/data-contract.md",
    "docs/architecture.md",
    "docs/deploy.md",
    "docs/features/06-grounded-answer.md",
    "docs/features/08-demo-mode.md",
    "frontend/README.md",
    "server/README.md",
]


def iter_markdown() -> list[Path]:
    out = []
    for path in ROOT.rglob("*.md"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        out.append(path)
    return sorted(out)


def check_links() -> list[str]:
    problems = []
    pattern = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
    for path in iter_markdown():
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in pattern.finditer(text):
            target = match.group(1).strip()
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target = target.split("#", 1)[0].strip()
            if not target:
                continue
            resolved = (path.parent / target).resolve()
            if not resolved.exists():
                problems.append(f"{path.relative_to(ROOT).as_posix()}: 链接指向不存在的路径 {target}")
    return problems


def check_current_wording() -> list[str]:
    problems = []
    for rel in CURRENT_DOCS:
        path = ROOT / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern, why in FORBIDDEN_IN_CURRENT:
            for match in re.finditer(pattern, text):
                line_no = text[: match.start()].count("\n") + 1
                problems.append(f"{rel}:{line_no} 出现与当前事实冲突的表述"
                                f"“{match.group(0)}”（{why}）")
    return problems


def check_env_example() -> list[str]:
    problems = []
    example = ROOT / ".env.example"
    if not example.is_file():
        return [".env.example 不存在"]
    keys = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]{2,})=",
                          example.read_text(encoding="utf-8"), flags=re.MULTILINE))
    sources = "\n".join(
        p.read_text(encoding="utf-8")
        for p in (ROOT / "config" / "settings.py", ROOT / "config" / "defaults.py")
    )
    aliases = {"LLM_API_KEY", "DEEPSEEK_API_KEY", "FALLBACK_LLM_API_KEY",
               "EMBEDDING_API_KEY", "DASHSCOPE_API_KEY"}
    for key in sorted(keys):
        if key not in sources and key not in aliases:
            problems.append(f".env.example 的 {key} 未被 config 读取（拼错或已废弃）")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="文档与配置一致性检查")
    ap.add_argument("--strict", action="store_true", help="任何问题都返回非零")
    args = ap.parse_args()

    checks = {
        "相对链接": check_links(),
        "current 口径": check_current_wording(),
        ".env.example 一致性": check_env_example(),
    }
    total = 0
    for name, problems in checks.items():
        if problems:
            print(f"[{name}] {len(problems)} 个问题")
            for item in problems[:30]:
                print(f"  - {item}")
            total += len(problems)
        else:
            print(f"[{name}] 通过")
    if total:
        print(f"\n合计 {total} 个问题")
        return 1 if args.strict else 0
    print("\n文档与配置检查通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
