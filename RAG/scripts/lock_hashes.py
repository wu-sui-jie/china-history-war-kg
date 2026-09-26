"""给锁文件补 `--hash=`。

为什么不用 `pip-compile --generate-hashes`：它要为每个固定版本下载**所有平台**的
wheel/sdist 才能算出哈希（chromadb 依赖树实测上 GB），本机两次因镜像传输中断失败——
"锁文件没有哈希"就是这么来的。

本脚本改走 PyPI 的 JSON API（`/pypi/<name>/<version>/json`）：该接口直接返回每个文件
的 `digests.sha256`，**不需要下载任何制品**，因此几十秒内就能把 90+ 个包补齐。
哈希是文件内容的摘要，与从哪个镜像下载无关：锁文件仍可配 `--index-url` 指向镜像，
`pip install --require-hashes` 会逐文件校验。

用法：
    python scripts/lock_hashes.py                          # 处理 requirements*.lock
    python scripts/lock_hashes.py --lock requirements-dev.lock
    python scripts/lock_hashes.py --check                  # 只检查是否带哈希（CI 用）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.release_info import repo_root  # noqa: E402

DEFAULT_LOCKS = ("requirements-dev.lock", "requirements.lock")
PYPI_API = "https://pypi.org/pypi/{name}/{version}/json"
_REQ = re.compile(r"^([A-Za-z0-9_.\-]+)(\[[^\]]+\])?==([^\s;\\]+)")


def parse_lock(text: str) -> list:
    """把锁文件拆成块：(需求行, 缩进注释行列表)，并记录每条需求带了几行 `--hash=`。

    只做**结构**解析：选项行（--index-url 等）与空行原样保留，
    需求行与其后的 `# via` 注释分组，便于原地重写而不破坏 pip-compile 的排版。
    """
    blocks: list = []
    for raw in text.splitlines():
        if raw and not raw[0].isspace() and not raw.startswith("#") \
                and not raw.startswith("-"):
            match = _REQ.match(raw)
            if match:
                blocks.append({"kind": "req", "name": match.group(1),
                               "version": match.group(3), "raw": raw,
                               "comments": [], "hashes": 0})
                continue
        if blocks and blocks[-1]["kind"] == "req" and raw.startswith("    #"):
            blocks[-1]["comments"].append(raw)
            continue
        if blocks and blocks[-1]["kind"] == "req" and raw.strip().startswith("--hash="):
            blocks[-1]["hashes"] += 1     # 逐条计数，供 check 判定"这条需求有没有哈希"
            continue
        blocks.append({"kind": "raw", "raw": raw})
    return blocks


def fetch_hashes(name: str, version: str, attempts: int = 4) -> list:
    """取该版本所有发布文件的 sha256（含各平台 wheel 与 sdist）。"""
    url = PYPI_API.format(name=name, version=version)
    last: Exception | None = None
    for i in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            hashes = {f["digests"]["sha256"] for f in data.get("urls", [])
                      if f.get("digests", {}).get("sha256")}
            if hashes:
                return sorted(hashes)
            raise RuntimeError(f"{name}=={version} 的发布文件没有 sha256 摘要")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise RuntimeError(f"{name}=={version} 在 PyPI 上不存在（404）") from e
            last = e
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"{name}=={version} 取哈希失败: {last}")


def render(blocks: list, hashes_by_req: dict, quiet: bool = False) -> str:
    out: list = []
    for block in blocks:
        if block["kind"] != "req":
            out.append(block["raw"])
            continue
        key = (block["name"], block["version"])
        hashes = hashes_by_req.get(key) or []
        if not hashes:
            out.append(block["raw"])
            out.extend(block["comments"])
            continue
        lines = [f"{block['name']}=={block['version']} \\"]
        for i, digest in enumerate(hashes):
            suffix = " \\" if i < len(hashes) - 1 else ""
            lines.append(f"    --hash=sha256:{digest}{suffix}")
        out.extend(lines)
        out.extend(block["comments"])
    return "\n".join(out) + "\n"


def process(path: Path, quiet: bool = False) -> int:
    text = path.read_text(encoding="utf-8")
    blocks = parse_lock(text)
    reqs = [(b["name"], b["version"]) for b in blocks if b["kind"] == "req"]
    hashes_by_req: dict = {}
    for i, (name, version) in enumerate(reqs, start=1):
        hashes_by_req[(name, version)] = fetch_hashes(name, version)
        if not quiet:
            print(f"  [{i}/{len(reqs)}] {name}=={version} → {len(hashes_by_req[(name, version)])} 个哈希")
    path.write_text(render(blocks, hashes_by_req), encoding="utf-8")
    print(f"已写入: {path}（{len(reqs)} 个需求，全部带 --hash）")
    return 0


def check(path: Path) -> list:
    """逐条检查需求是否带哈希（CI/门禁用，不联网）。

    必须**按需求逐条**判定：早期实现比较"文件里 --hash 行数 ≥ 需求数"，
    于是"一个包带 2 个哈希、另一个包 0 个"会误判为通过——总数相等掩盖了缺口。
    """
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if not text:
        return [f"{path.name}: 不存在或为空"]
    blocks = parse_lock(text)
    reqs = [b for b in blocks if b["kind"] == "req"]
    if not reqs:
        return [f"{path.name}: 没有解析到任何 `name==version` 需求行（格式异常）"]
    missing = [f"{b['name']}=={b['version']}" for b in reqs if not b.get("hashes")]
    if missing:
        return [f"{path.name}: {len(missing)}/{len(reqs)} 个需求缺少 --hash"
                f"（{', '.join(missing[:5])}{'…' if len(missing) > 5 else ''}）"]
    return []


def main() -> int:
    ap = argparse.ArgumentParser(description="给 Python 锁文件补 --hash（PyPI JSON API）")
    ap.add_argument("--lock", action="append", default=None,
                    help="锁文件路径（可重复；缺省处理 requirements-dev.lock 与 requirements.lock）")
    ap.add_argument("--check", action="store_true", help="只检查是否带哈希，不联网改写")
    ap.add_argument("--quiet", action="store_true", help="少打印")
    args = ap.parse_args()

    root = repo_root()
    paths = [Path(p) for p in (args.lock or DEFAULT_LOCKS)]
    paths = [p if p.is_absolute() else root / p for p in paths]

    if args.check:
        problems: list = []
        for path in paths:
            problems.extend(check(path))
        for item in problems:
            print(f"  - {item}")
        if problems:
            print("锁文件缺少 --hash：`pip install --require-hashes` 会失败")
            return 1
        print("锁文件均带完整 --hash")
        return 0

    for path in paths:
        if not path.is_file():
            print(f"跳过（不存在）: {path}")
            continue
        rc = process(path, quiet=args.quiet)
        if rc:
            return rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
