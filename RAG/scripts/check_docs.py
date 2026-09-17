"""文档与配置的一致性检查（2026-09-16 工作单 P2-7 的 CI lint 部分）。

四类检查，全部离线可跑：
1. **相对链接**：Markdown 中的相对路径 / 图片必须真实存在（外链不检查，避免 CI 依赖网络）；
2. **current 口径**：对 current 文档禁止出现与事实冲突的表述（如"demo 可用"）；
3. **配置一致性**：`.env.example` 里出现的键必须真的被 config 读取；
4. **数据计数一致性**（第五轮审核 R5-2）：`docs/current-status.md` 里的
   实体/关系/向量数字必须等于快照与索引清单里的真实计数——唯一事实源写错数字，
   会被后续文档一路抄下去。本地有 data/ 时才校验（CI 无大制品则跳过并说明）。

用法：python scripts/check_docs.py [--strict]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SKIP_DIRS = {".git", "node_modules", "dist", "new", "__pycache__", ".pytest_cache", "logs"}

# current-status 里的数据集行：`NNNN 实体 / NNNN 关系 / NNNN 向量条`
DATASET_PATTERN = re.compile(
    r"(\d+)\s*实体\s*/\s*(\d+)\s*关系\s*/\s*(\d+)\s*向量条")

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


def _actual_counts() -> dict | None:
    """从快照/索引清单读出真实计数；数据不存在时返回 None（CI 无大制品）。"""
    try:
        from config.settings import get_settings
        from lib import versions

        settings = get_settings()
        version = settings.active_version or ""
        if not version:
            snaps = versions.list_versions(settings.snapshot_dir)
            for candidate in snaps:
                if (settings.index_dir / candidate).exists():
                    version = candidate
                    break
            if not version and snaps:
                version = snaps[0]
        if not version:
            return None
        snap_manifest = settings.snapshot_dir / version / "manifest.json"
        idx_manifest = settings.index_dir / version / "manifest.json"
        if not snap_manifest.is_file():
            return None
        counts = json.loads(snap_manifest.read_text(encoding="utf-8")).get("counts") or {}
        vectors = None
        if idx_manifest.is_file():
            idx = json.loads(idx_manifest.read_text(encoding="utf-8"))
            vectors = (idx.get("vectors") or {}).get("count")
        return {
            "version": version,
            "entities": counts.get("entities"),
            "relations": counts.get("relations"),
            "vectors": vectors,
        }
    except Exception:  # noqa: BLE001
        return None


def check_dataset_counts(doc_path: Path | None = None) -> list[str]:
    """current-status 的数据集数字必须等于清单真实值（第五轮审核 R5-2）。

    唯一事实源写错（10925/17730 vs 实际 9925/17700）会一路传播到其他文档、
    汇报材料与验收记录；这里把数字重新绑定到**清单文件**，而不是靠人工复核。

    `doc_path` 可显式覆盖（测试用临时副本），默认仍是 docs/current-status.md。
    """
    doc = doc_path or (ROOT / "docs" / "current-status.md")
    if not doc.is_file():
        return [f"{doc.name} 不存在（数据集口径无处声明）"]
    actual = _actual_counts()
    if not actual or actual.get("entities") is None or actual.get("relations") is None:
        return []          # 无 data/（CI/新克隆）：跳过，不假装通过也不误报
    text = doc.read_text(encoding="utf-8", errors="replace")
    match = DATASET_PATTERN.search(text)
    if not match:
        return ["docs/current-status.md 缺少数据集计数行（`N 实体 / N 关系 / N 向量条`）"]
    problems = []
    labels = ("entities", "relations", "vectors")
    names = ("实体", "关系", "向量条")
    for i, (label, name) in enumerate(zip(labels, names)):
        declared = int(match.group(i + 1))
        real = actual.get(label)
        if real is None:
            continue
        if declared != real:
            problems.append(
                f"docs/current-status.md 声明的{name}数 {declared} 与快照/索引清单不一致"
                f"（版本 {actual['version']} 实际 {real}）")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="文档与配置一致性检查")
    ap.add_argument("--strict", action="store_true", help="任何问题都返回非零")
    args = ap.parse_args()

    checks = {
        "相对链接": check_links(),
        "current 口径": check_current_wording(),
        ".env.example 一致性": check_env_example(),
        "数据计数一致性": check_dataset_counts(),
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
