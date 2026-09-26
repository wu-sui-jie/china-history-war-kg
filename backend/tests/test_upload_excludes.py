"""上传脚本的排除规则。

## 要防的形状

`deploy/scripts/04_upload_from_local.sh` 的 `EXCLUDES` 不能带尾斜杠
（`--exclude '.git/'`）：rsync 认这种写法，**GNU tar 不认**：

```text
带尾斜杠  --exclude '.git/' --exclude 'node_modules/'
  →  ./.git/ ./.git/config ./sub/node_modules/x.js 全都进了包
不带尾斜杠 --exclude '.git' --exclude 'node_modules'
  →  ./ ./keep/ ./keep/a.txt ./sub/
```

而脚本在**没有 rsync 的机器上必然走 tar 分支**（Windows 的 Git Bash 就是这种情况），
于是 `.git`（约 108 MB，含历史里的明文口令）与两个 `node_modules`（各约 300 MB）
会被整包传到服务器——与文件头写的"代码同步时排除 node_modules、.git"正好相反。

## 这组用例怎么测

**直接从脚本里读 `EXCLUDES` 数组**喂给真 tar，而不是在测试里重抄一份模式：
重抄一份只能证明"我抄的那份是对的"，而缺陷恰恰发生在脚本里那份上。
再叠一层结构断言：模式不得以 `/` 结尾（根因），以及 `--mirror` 在 tar 分支必须报错。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "deploy" / "scripts" / "04_upload_from_local.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="未安装 bash（Windows 无 Git Bash 时跳过）")

_EXCLUDE_LINE = re.compile(r"^\s*--exclude\s+'([^']+)'\s*$", re.M)


def _exclude_patterns() -> list[str]:
    """从脚本的 EXCLUDES 数组里取出模式（数组本体，不含 rsync 分支里的两处例外）。"""
    text = SCRIPT.read_text(encoding="utf-8")
    block = text.split("EXCLUDES=(", 1)[1].split("\n)", 1)[0]
    return _EXCLUDE_LINE.findall(block)


def _synthetic_tree(tmp_path: Path) -> Path:
    """按缺陷现场造一棵小树：该被排除的与被保留的各放几个。"""
    root = tmp_path / "src"
    files = [
        ".git/config",
        ".git/objects/ab/cdef",
        "frontend/node_modules/echarts/index.js",
        "RAG/frontend/node_modules/vue/index.js",
        "RAG/data/cache/chunks.jsonl",
        "entity-event-relation/cache/chroma.sqlite3",
        "logs/backend.log",
        "__pycache__/app.cpython-311.pyc",
        ".zcode/session.json",
        "backend/.env",
        "feishu-bot/data/bot.db",
        # 必须留下来的
        "keep/a.txt",
        "backend/app.py",
        "frontend/dist/index.html",
    ]
    for rel in files:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    return root


def _tar_members(root: Path, patterns: list[str]) -> set[str]:
    """用给定模式打一次包，返回包内成员名（去掉开头的 `./`）。"""
    args = [BASH, "-c",
            'tar czf - "$@" . | tar tzf -',
            "--"] + [f"--exclude={pattern}" for pattern in patterns]
    result = subprocess.run(args, cwd=str(root), capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    assert result.returncode == 0, result.stderr
    return {line.strip().lstrip("./") for line in result.stdout.splitlines() if line.strip()}


def _members_under(members: set[str], prefix: str) -> set[str]:
    return {name for name in members if name == prefix or name.startswith(prefix + "/")}


def test_排除模式不带尾斜杠():
    """根因就写在这一条里：带尾斜杠的写法在 tar 下一条都不生效。"""
    patterns = _exclude_patterns()

    assert patterns, "没读到 EXCLUDES —— 脚本结构变了？用例需要跟着改"
    offenders = [pattern for pattern in patterns if pattern.endswith("/")]
    assert not offenders, (
        f"以下排除模式以 `/` 结尾，在 GNU tar 下不生效：{offenders}；"
        "rsync 与 tar 对尾斜杠的处理不同，见脚本里的注释"
    )


def test_tar_打包不会带上_git_与_node_modules(tmp_path):
    """缺陷的现场：`--code-only` 在无 rsync 的机器上走 tar 分支，这条必须过。"""
    root = _synthetic_tree(tmp_path)

    members = _tar_members(root, _exclude_patterns())

    for unwanted in (".git", "frontend/node_modules", "RAG/frontend/node_modules",
                     "RAG/data/cache", "entity-event-relation/cache", "logs",
                     "__pycache__", ".zcode",
                     "backend/.env", "feishu-bot/data"):
        assert not _members_under(members, unwanted), f"{unwanted} 不该进包：{sorted(members)}"
    # 反向断言：别把"什么都不打包"当成通过
    assert "keep/a.txt" in members
    assert "backend/app.py" in members
    assert "frontend/dist/index.html" in members, "dist 是刻意保留的（服务器上没 Node 也能跑）"


def test_tar_分支对_mirror_明确报错():
    """tar 删不掉服务器上多余的文件，`--mirror` 不能静默降级成"叠加"。"""
    text = SCRIPT.read_text(encoding="utf-8")

    # 两个 tar 分支（sync_path 里的与代码同步的）都要有这道拒绝
    assert text.count("tar 回退分支无法实现 --mirror") == 2, (
        "两个 tar 回退分支都必须拒绝 --mirror——静默忽略会让用户以为同步是镜像式的"
    )
    assert "MIRROR" in text


def test_rsync_分支仍支持_delete():
    """对照组：有 rsync 时 --mirror 照常工作，别在修 tar 的时候把它弄丢。"""
    text = SCRIPT.read_text(encoding="utf-8")

    assert text.count("flags+=(--delete)") == 2
