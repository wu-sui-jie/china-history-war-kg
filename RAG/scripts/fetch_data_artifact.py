"""数据制品获取与安全解包。

只 `print` 下载 zip 的 SHA-256 就 `extractall('.')` 是不够的——
既没有和**预期哈希**比对（下载到什么都照收），也没有任何路径检查
（zip 里的 `../../` 条目可以写到仓库之外）。发布链路的输入必须可信。

本脚本的四道关（缺一不可，全部失败即退出非零）：
1. **预期哈希必需**：`--sha256` 或 `--sha256-file`，没有就不下载也不解包，
   避免"先解包再说"的习惯；
2. **可选签名校验**：给了 `--signature`（+可选 `--pubkey`）就调 gpg 验签，
   gpg 不存在直接失败（不允许静默跳过）；
3. **zip-slip 防护**：拒绝绝对路径、盘符、`..`、符号链接条目；
4. **解包范围限制**：`--allow-prefix`（默认 data/）限定顶层目录，并限制总解压体积。

用法：
    python scripts/fetch_data_artifact.py --url https://.../data.zip --sha256 <64hex>
    python scripts/fetch_data_artifact.py --from-file /tmp/data.zip --sha256 <64hex> \
        --signature /tmp/data.zip.sig --pubkey pubkey.asc --allow-prefix data/
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_CHUNK = 1 << 20
# 默认解压体积上限：数据制品约 210 MB，给 4 GB 余量足够挡住 zip bomb
DEFAULT_MAX_BYTES = 4 * 1024 * 1024 * 1024
# 解包默认只允许 data/ 下的内容（snapshot/index/eval）
DEFAULT_ALLOW_PREFIXES = ("data/",)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(_CHUNK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as resp, open(dest, "wb") as out:
        shutil.copyfileobj(resp, out, length=_CHUNK)


def verify_gpg(signature: Path, target: Path, pubkey: Path | None) -> tuple:
    """用 gpg 验签；返回 (是否通过, 说明)。gpg 不存在 → 失败（不静默跳过）。"""
    gpg = shutil.which("gpg")
    if not gpg:
        return False, "未找到 gpg：给了 --signature 就必须能验签，拒绝静默跳过"
    if pubkey:
        imported = subprocess.run([gpg, "--import", str(pubkey)],
                                  capture_output=True, text=True, timeout=60)
        if imported.returncode != 0:
            return False, f"导入公钥失败: {imported.stderr.strip()[:200]}"
    out = subprocess.run([gpg, "--verify", str(signature), str(target)],
                         capture_output=True, text=True, timeout=60)
    detail = (out.stderr or out.stdout).strip().splitlines()
    return out.returncode == 0, (detail[0] if detail else f"gpg 退出码 {out.returncode}")


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return mode == 0o120000


def plan_extraction(zip_path: Path, allow_prefixes: tuple, max_bytes: int = DEFAULT_MAX_BYTES
                    ) -> tuple:
    """先规划再解包：返回 (条目列表, 问题列表)。任何路径非法都判失败。"""
    entries: list = []
    problems: list[str] = []
    total = 0
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.filename
            if info.is_dir():
                continue
            if _is_symlink(info):
                problems.append(f"拒绝符号链接条目: {name}")
                continue
            # 统一成 POSIX 相对路径再判定；盘符（C:）与 UNC（//host）一律拒绝
            if name.startswith(("/", "\\")) or ":" in name.split("/")[0]:
                problems.append(f"拒绝绝对路径条目: {name}")
                continue
            rel = PurePosixPath(name.replace("\\", "/"))
            if any(part in ("..", "") for part in rel.parts):
                problems.append(f"拒绝包含 .. 的条目: {name}")
                continue
            if allow_prefixes and not any(str(rel).startswith(p) for p in allow_prefixes):
                problems.append(
                    f"条目超出允许范围（只允许 {', '.join(allow_prefixes)}）: {name}")
                continue
            total += info.file_size
            entries.append((info, rel))
    if total > max_bytes:
        problems.append(f"解压后体积 {total} 超过上限 {max_bytes} 字节（疑似 zip bomb）")
    return entries, problems


def extract(zip_path: Path, dest: Path, entries: list) -> int:
    """按已校验的条目逐条解包（不用 extractall：它会重走未校验的路径拼接）。"""
    count = 0
    with zipfile.ZipFile(zip_path) as zf:
        for info, rel in entries:
            target = dest / Path(*rel.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, length=_CHUNK)
            count += 1
    return count


def pack(paths: list, out_path: Path, root: Path) -> Path:
    """把给定路径（相对 RAG 根，含 data/ 前缀）打成 zip；返回 zip 路径。

    维护者本机用：生成的数据制品路径形如 `data/snapshot/<v>/...`，
    与解包端的 `--allow-prefix data/` 对齐；两端口径写在同一个脚本里，
    避免"打包一个布局、解包假设另一个布局"。
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    total = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for rel in paths:
            base = root / rel
            if base.is_file():
                files = [base]
            elif base.is_dir():
                files = [p for p in sorted(base.rglob("*")) if p.is_file()]
            else:
                raise FileNotFoundError(f"打包路径不存在: {base}")
            for path in files:
                arc = path.relative_to(root).as_posix()
                zf.write(path, arcname=arc)
                count += 1
                total += path.stat().st_size
    print(f"数据制品: {out_path}")
    print(f"  条目 {count} | 原始大小 {total / 1024 / 1024:.1f} MB "
          f"| zip 大小 {out_path.stat().st_size / 1024 / 1024:.1f} MB")
    print(f"  sha256: {sha256_file(out_path)}")
    return out_path


def default_pack_paths() -> list:
    """默认打包内容：活跃版本（或最新一致版本）的 snapshot / index / eval。"""
    from config.settings import get_settings
    from lib import versions

    settings = get_settings()
    version = settings.active_version
    if not version:
        for candidate in versions.list_versions(settings.snapshot_dir):
            if (settings.index_dir / candidate).exists():
                version = candidate
                break
    if not version:
        raise SystemExit("未指定版本且未找到一致的快照/索引目录：请用 --pack 显式给出路径")
    return [f"data/snapshot/{version}", f"data/index/{version}", f"data/eval/{version}"]


def main() -> int:
    ap = argparse.ArgumentParser(description="数据制品打包 / 下载 / 校验 / 安全解包")
    ap.add_argument("--pack", nargs="*", default=None, metavar="PATH",
                    help="打包模式：把给定路径（相对 RAG 根，如 data/snapshot/20260915_v1）"
                         "打成 zip；不带参数则打包活跃版本的 snapshot/index/eval")
    ap.add_argument("--pack-out", default=".tmp/data.zip", help="打包输出路径")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--url", help="下载地址")
    src.add_argument("--from-file", help="本地已有 zip（离线场景）")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--sha256", help="预期 SHA-256（64 位十六进制）")
    g.add_argument("--sha256-file", help="含预期 SHA-256 的文本文件（sha256sum 格式或纯哈希）")
    ap.add_argument("--dest", default=".", help="解包目标目录（默认当前目录）")
    ap.add_argument("--signature", default="", help="gpg 签名文件（给了就必须验签通过）")
    ap.add_argument("--pubkey", default="", help="用于验签的公钥文件")
    ap.add_argument("--allow-prefix", default=",".join(DEFAULT_ALLOW_PREFIXES),
                    help="允许解包的顶层前缀，逗号分隔（默认 data/）")
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES,
                    help="解压后总字节上限")
    ap.add_argument("--keep", action="store_true", help="保留下载的临时 zip")
    args = ap.parse_args()

    if args.pack is not None:
        from lib.release_info import repo_root

        paths = args.pack or default_pack_paths()
        pack(paths, Path(args.pack_out), repo_root())
        return 0

    if not (args.url or args.from_file):
        print("需要 --url 或 --from-file（或用 --pack 打包）；解包必须有预期哈希")
        return 2

    expected = (args.sha256 or "").strip().lower()
    if args.sha256_file:
        text = Path(args.sha256_file).read_text(encoding="utf-8").strip()
        for line in text.splitlines():
            token = line.split()[0] if line.split() else ""
            if len(token) == 64:
                expected = token.lower()
                break
    if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        print(f"::error:: --sha256 必须是 64 位十六进制，当前 {expected!r}")
        return 2

    tmp_dir = Path(tempfile.mkdtemp(prefix="rag-data-"))
    zip_path = Path(args.from_file) if args.from_file else tmp_dir / "data.zip"
    try:
        if args.url:
            print(f"下载数据制品: {args.url}")
            download(args.url, zip_path)
        actual = sha256_file(zip_path)
        print(f"制品 sha256: {actual}")
        if actual != expected:
            print(f"::error:: 哈希不匹配：预期 {expected}，实际 {actual}（拒绝解包）")
            return 3
        print("哈希校验通过")

        if args.signature:
            ok, detail = verify_gpg(Path(args.signature), zip_path,
                                    Path(args.pubkey) if args.pubkey else None)
            if not ok:
                print(f"::error:: 签名校验失败: {detail}")
                return 4
            print(f"签名校验通过（{detail}）")

        allow = tuple(p.strip() for p in args.allow_prefix.split(",") if p.strip())
        entries, problems = plan_extraction(zip_path, allow, args.max_bytes)
        if problems:
            print(f"::error:: 解包计划被拒绝（{len(problems)} 条）：")
            for item in problems[:20]:
                print(f"  - {item}")
            return 5
        count = extract(zip_path, Path(args.dest), entries)
        print(f"解包完成：{count} 个文件 → {Path(args.dest).resolve()}")
        if args.keep and args.url:
            kept = Path(args.dest) / zip_path.name
            shutil.copy2(zip_path, kept)
            print(f"已保留 zip 副本: {kept}")
        return 0
    finally:
        if not (args.keep and args.url):
            shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
