#!/usr/bin/env python3
"""生成**全仓统一 lock**（第 14 轮审计 §5.8 / 方案 §5.8）。

## 它做什么

四步，与 RAG 既有锁文件的生成口径一致（不另造一套）：

1. `pip freeze` 出当前环境的精确版本，作为 pip-compile 的约束（过滤掉可编辑安装，
   它是仓库内的包、不是三方依赖）；
2. `pip-compile --strip-extras --all-extras pyproject.toml` 解析出**完整依赖树**
   （四个模块的 extras 全含），固定精确版本；
3. **后处理**——这三件不做，锁文件既不合规也不可用：
   - 去掉 `--index-url`：锁文件固定的是版本与哈希，镜像属于本机/网络环境的选择
     （RAG 锁文件第六轮复核 G1 的同一口径）；写死在锁里会让海外 CI 连镜像。
   - 去掉 `china-war @ file:///...` 这类自引用：`[all]` extra 里含 `china-war[...]`，
     pip-compile 会把本仓库自身也当成需求，而那是一个**本机绝对路径**——
     别人拿到的锁文件在自己的目录下必然装不上。
   - 把命令头与 `# via -c <绝对路径>` 里的本机路径改写成可移植的占位符
     （否则锁文件里会带着用户名与临时目录，等同于把本机信息提交进仓库）。
4. 可选 `--with-hashes`：调 `RAG/scripts/lock_hashes.py` 走 PyPI JSON API 补哈希
   （不下载制品，几十秒完成；见该脚本的文档字符串）。

## 用法

    python scripts/gen_unified_lock.py                 # 生成 requirements.lock（带哈希）
    python scripts/gen_unified_lock.py --no-hashes      # 只生成版本，不补哈希
    python scripts/gen_unified_lock.py --check          # 只校验现有锁文件（CI 用）

**必须在装了 `[all]` 的 3.11 环境里跑**（先用 china-war-py311 激活或用绝对路径），
否则 freeze 出来的约束只覆盖一半依赖，锁出来的版本组合与验证过的那套不是一回事。

## 为什么 lock 必须在测试全绿之后才生成

锁文件的价值是"可复现"：它把**验证通过的那套版本组合**固定下来。反过来先锁再验，
锁住的是一个尚未验证的组合，看起来可复现、实际不可信（方案第四节第 6 条）。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCK = REPO_ROOT / "requirements.lock"
HASH_SCRIPT = REPO_ROOT / "RAG" / "scripts" / "lock_hashes.py"

# 自引用行（本仓库自身的可编辑安装）与它们的各种写法
SELF_REFERENCE = re.compile(r"^(china[-_]war|war[-_]extraction)\s*@\s*file://|^-e\s", re.I)
_CONSTRAINT_PATH = re.compile(r"(-c\s+)(?:'[^']+'|\"[^\"]+\"|\S+)")


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", **kwargs)


def freeze_constraint() -> str:
    """当前环境的精确版本，作为 pip-compile 的约束（去掉可编辑安装）。"""
    result = run([sys.executable, "-m", "pip", "freeze"])
    if result.returncode != 0:
        raise SystemExit(f"pip freeze 失败：{result.stderr}")
    lines = [line for line in result.stdout.splitlines() if not SELF_REFERENCE.match(line)]
    return "\n".join(lines) + "\n"


def compile_lock(constraint: Path) -> str:
    """让 pip-compile 解析出完整依赖树；返回生成的文本。"""
    out = Path(tempfile.mkdtemp(prefix="china-war-lock-")) / "requirements.lock"
    result = run([
        sys.executable, "-m", "piptools", "compile",
        "--strip-extras", "--all-extras",
        "--constraint", str(constraint),
        "--output-file", str(out),
        "pyproject.toml",
    ], cwd=str(REPO_ROOT))
    if result.returncode != 0 or not out.exists():
        raise SystemExit(f"pip-compile 失败：\n{result.stderr or result.stdout}")
    return out.read_text(encoding="utf-8")


def postprocess(text: str) -> str:
    """改写成本仓库的锁文件口径（可移植、无本机信息、无镜像）。"""
    kept: list[str] = []
    for line in text.splitlines():
        # 镜像不写进锁文件（理由见模块文档）
        if line.startswith(("--index-url", "--extra-index-url")):
            continue
        # 自引用（本仓库的绝对路径）不能进锁
        if SELF_REFERENCE.match(line):
            continue
        kept.append(line)

    # pip-compile 自动生成的头部（连续的 # 行 + 命令）整体丢掉后重写：
    # 它带着本机绝对路径与用户名，而且拼写细节随 pip-tools 版本变。
    body_start = 0
    for index, line in enumerate(kept):
        if line and not line.startswith("#"):
            body_start = index
            break
    body = "\n".join(kept[body_start:])
    # `# via -c <本机绝对路径>` → 可移植占位符（这个路径每次生成都不同，
    # 留着等于把用户名与临时目录提交进仓库）
    body = _CONSTRAINT_PATH.sub(r"\1<本机 pip freeze>", body)
    if re.search(r"[A-Za-z]:[\\/]|file:///", body):
        raise SystemExit("锁文件里仍有本机绝对路径，请检查 postprocess 的清理规则")

    header = (
        "#\n"
        "# 全仓统一依赖锁（第 14 轮审计 §5.8）。四个模块的 extras 全含。\n"
        "#\n"
        "# 生成方式（必须在装了 `pip install -e \".[all]\"` 的 3.11 环境里跑）：\n"
        "#\n"
        "#    python scripts/gen_unified_lock.py          # 生成 + 补哈希 + 自检\n"
        "#\n"
        "# 它等价于 pip-compile --strip-extras --all-extras --constraint <本机 pip freeze> \\\n"
        "#            --output-file requirements.lock pyproject.toml，再加上三处必要后处理：\n"
        "# 去 index-url、去本仓库自引用、把命令头与 `# via -c` 里的本机路径换成占位符。\n"
        "#\n"
        "# 哈希由 RAG/scripts/lock_hashes.py 通过 PyPI JSON API 补齐（不下载制品）。\n"
        "# 不写 --index-url：锁文件固定的是**版本与哈希**，镜像属于本机/网络环境的选择。\n"
        "#\n"
        "# 安装：pip install --require-hashes -r requirements.lock\n"
        "# 注意：抽取链包 war_extraction 是**仓库内的包**、不在本锁内（见 pyproject 的安装说明）。\n"
        "#\n\n"
    )
    return header + body.rstrip("\n") + "\n"


def fill_hashes(lock: Path) -> None:
    result = run([sys.executable, str(HASH_SCRIPT), "--lock", str(lock)])
    if result.returncode != 0:
        raise SystemExit(f"补哈希失败：\n{result.stdout}\n{result.stderr}")
    print(result.stdout.strip())


def check() -> int:
    """CI 用：锁文件存在、带哈希、不含本机路径与镜像。"""
    if not LOCK.exists():
        print(f"缺少 {LOCK.name}")
        return 1
    text = LOCK.read_text(encoding="utf-8")
    problems = []
    if "--hash=sha256" not in text:
        problems.append("锁文件不含 --hash=sha256")
    if re.search(r"^--(extra-)?index-url", text, re.M):
        problems.append("锁文件写死了 index-url（镜像应由安装环境决定）")
    if re.search(r"[A-Za-z]:[\\/]|file:///", text):
        problems.append("锁文件含本机绝对路径")
    if SELF_REFERENCE.search(text):
        problems.append("锁文件含本仓库自身的自引用")
    for problem in problems:
        print(f"✗ {problem}")
    if problems:
        return 1
    print(f"✓ {LOCK.name} 合规（{text.count('--hash=sha256')} 个哈希）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="生成/校验全仓统一 lock")
    parser.add_argument("--no-hashes", action="store_true", help="不补哈希（仅排障用）")
    parser.add_argument("--check", action="store_true", help="只校验现有锁文件")
    args = parser.parse_args()

    if args.check:
        return check()

    constraint = Path(tempfile.mkdtemp(prefix="china-war-freeze-")) / "freeze.txt"
    constraint.write_text(freeze_constraint(), encoding="utf-8")
    print(f"约束：{sum(1 for _ in constraint.open(encoding='utf-8'))} 个已安装包")

    text = postprocess(compile_lock(constraint))
    LOCK.write_text(text, encoding="utf-8")
    print(f"已写出 {LOCK.relative_to(REPO_ROOT)}（{len(text.splitlines())} 行）")

    if not args.no_hashes:
        fill_hashes(LOCK)
    return check()


if __name__ == "__main__":
    sys.exit(main())
