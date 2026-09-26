"""回答一个反复出现的问题：**服务器上的代码是不是最新的？**

不靠时间戳猜、不靠肉眼比：把本地 HEAD 的全部受跟踪文件逐字节哈希，拿去和服务器上
`/opt/china-war` 下的实际文件比对，输出三类结论。

## 用法（两步，本机一步、服务器一步）

```bash
# 1) 本机：生成清单（必须 -X utf8，且关掉 git 的路径转义——见下方"两个坑"）
python -X utf8 scripts/compare_server_code.py --manifest > /tmp/manifest.txt

# 2) 把清单送上服务器并比对（工具本身随代码上传，位于 /opt/china-war/scripts/）
tar czf - -C /tmp manifest.txt | ssh root@<服务器> \
    'tar xzf - -C /root && python3 /opt/china-war/scripts/compare_server_code.py /root/manifest.txt'
```

## 三个输出怎么读

| 输出 | 含义 | 要做什么 |
| --- | --- | --- |
| 内容与本地不一致 | 服务器上是**旧版**（改完没上传） | 重跑 `04_upload_from_local.sh --code-only` |
| 服务器上不存在 | 新增的文件没上传（或服务器上被删了） | 同上 |
| 服务器上有、本地未跟踪 | 缓存（`.pytest_cache`）、旧上传残留（`.zcode`）、本地没提交的数据文件 | 一般可忽略；`.py` / `.sh` 出现在这里要查（可能是只在服务器上手改过的代码） |

## 两个坑（第一版都踩过，务必注意）

1. **清单必须是这份工具生成的**，别自己用 `git ls-files` 拼：本机 `git` 默认
   `core.quotepath=true`，中文文件名会被写成 `"docs/\344\277\256..."` 这种八进制转义串，
   于是本机 `os.path.isfile()` 找不到它们 → 那些文件被**静默跳过**（第一次比对只覆盖了
   611/657 个文件，却看不出来）。本工具内部传 `core.quotepath=false` 规避。
2. **本机生成清单时必须 `-X utf8`**（或在 UTF-8 终端里跑），否则 Windows 上按 GBK
   写出、服务器按 UTF-8 读，中文路径会解码成乱码 → 全部报"服务器上不存在"。

另外：清单是**生成那一刻**的快照。生成之后又改了本地文件，会比对出"不一致"——那是
清单旧了，不是服务器旧了；重新生成清单即可（这点也踩过一次）。

## 参数

- `--manifest`：本机模式，把清单写到标准输出（`<sha256> <路径>`），不连服务器。
- `<清单文件>`：服务器模式，读清单并与 `/opt/china-war` 下的文件比对（默认读取
  `/root/cwcmp/manifest.txt`）。
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys

APP_DIR = os.environ.get("APP_DIR", "/opt/china-war")

# 反方向扫描时跳过的目录名（缓存、制品、依赖、版本控制）：
# 它们要么是运行期产物，要么是第三方代码，不属于"本仓库的代码"。
SKIP_DIR_NAMES = {"node_modules", "__pycache__", ".git", "logs", "cache", "output",
                  ".pytest_cache", ".ruff_cache", ".mypy_cache", ".audit-tmp",
                  ".verify-tmp", "test-results"}
TEXT_SUFFIXES = (".py", ".sh", ".service", ".timer", ".conf", ".md", ".json", ".txt",
                 ".yml", ".yaml", ".env.example")


def build_manifest() -> int:
    """本机模式：把 HEAD 的受跟踪文件写成 `<sha256> <相对路径>`。"""
    result = subprocess.run(
        ["git", "-c", "core.quotepath=false", "ls-files"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        print(f"git ls-files 失败：{result.stderr.strip()}", file=sys.stderr)
        return 1
    count = 0
    for path in result.stdout.split("\n"):
        path = path.strip()
        if not path or not os.path.isfile(path):
            continue
        with open(path, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        print(f"{digest} {path}")
        count += 1
    print(f"# 清单 {count} 个受跟踪文件（本机工作副本）", file=sys.stderr)
    return 0


def compare(manifest_path: str) -> int:
    """服务器模式：把清单与 APP_DIR 下的实际文件比对。"""
    mismatched, missing, seen = [], [], set()
    total = 0
    with open(manifest_path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            digest, path = line.split(None, 1)
            total += 1
            seen.add(path)
            target = os.path.join(APP_DIR, path)
            if not os.path.isfile(target):
                missing.append(path)
                continue
            with open(target, "rb") as fh:
                if hashlib.sha256(fh.read()).hexdigest() != digest:
                    mismatched.append(path)

    print(f"本地 HEAD 的受跟踪文件：{total} 个")
    print(f"内容与本地不一致：{len(mismatched)}")
    for path in mismatched[:30]:
        print("   ≠", path)
    print(f"服务器上不存在：{len(missing)}")
    for path in missing[:30]:
        print("   -", path)

    # 反方向：服务器上有、清单里没有的文本文件（缓存/残留/未提交的数据）。
    server_only = []
    for root, dirs, names in os.walk(APP_DIR):
        dirs[:] = [d for d in dirs if d not in SKIP_DIR_NAMES]
        for name in names:
            if not name.endswith(TEXT_SUFFIXES):
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, APP_DIR).replace(os.sep, "/")
            if rel not in seen:
                server_only.append(rel)
    print(f"服务器上有、清单里没有的文本文件：{len(server_only)}")
    for path in sorted(server_only)[:30]:
        print("   +", path)

    return 1 if (mismatched or missing) else 0


def main() -> int:
    if "--manifest" in sys.argv:
        return build_manifest()
    manifest_path = sys.argv[1] if len(sys.argv) > 1 else "/root/cwcmp/manifest.txt"
    if not os.path.isfile(manifest_path):
        print(f"找不到清单 {manifest_path}：先在本机执行 `--manifest` 生成并传上来",
              file=sys.stderr)
        return 2
    return compare(manifest_path)


if __name__ == "__main__":
    sys.exit(main())
