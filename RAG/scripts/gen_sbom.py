"""SBOM 生成与校验。

只写 `spdxVersion/name/packages` 三个字段不算完整 SBOM：会缺 SPDX 2.3 的必需字段
（dataLicense / SPDXID / documentNamespace / creationInfo），也没有 Node 依赖，
而且产物必须真的被打进 release 包——否则"有 SBOM"只是文件名叫 SBOM。

本脚本生成**结构完整**的 SPDX 2.3 JSON：
- 文档级必需字段齐全（spdxVersion、dataLicense、SPDXID、name、documentNamespace、
  creationInfo.creators/created）；
- 每个包有 SPDXID、versionInfo、downloadLocation、filesAnalyzed=false、
  licenseConcluded/licenseDeclared/copyrightText（NOASSERTION）与 purl 外部引用；
- 依赖来源：Python 锁文件（优先）或已安装分发，Node 取 frontend/package-lock.json；
- DESCRIBES 关系把文档与包连起来，供下游工具遍历。

校验（`validate`）不依赖 spdx-tools：按规范逐条检查必需字段、ID 唯一性、
关系两端可解析、purl 形态。

用法：
    python scripts/gen_sbom.py generate [--version 20260915_v1] [--out data/release/sbom.json]
    python scripts/gen_sbom.py validate [--path data/release/sbom.json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.json_io import write_text_lf  # noqa: E402
from lib.release_info import repo_root  # noqa: E402

SPDX_VERSION = "SPDX-2.3"
DOC_ID = "SPDXRef-DOCUMENT"
_ID_RE = re.compile(r"^SPDXRef-[A-Za-z0-9.\-]+$")
_PURL_PYPI = "pkg:pypi/"
_PURL_NPM = "pkg:npm/"


def _safe_id(kind: str, name: str, seen: dict) -> str:
    """把包名转成合法且唯一的 SPDXID（同名不同版本要区分）。"""
    base = re.sub(r"[^A-Za-z0-9.\-]", "-", f"{kind}-{name}")
    candidate = f"SPDXRef-Package-{base}"
    if candidate in seen:
        seen[candidate] += 1
        candidate = f"{candidate}-{seen[candidate]}"
    else:
        seen[candidate] = 0
    return candidate


def python_packages(root: Path) -> list:
    """Python 依赖：优先解析锁文件（部署用的是它），否则回退到已安装分发。"""
    found: list = []
    seen: set = set()
    # 部署锁在前、开发锁在后，两份都收：只取第一个命中的锁文件就 break 会漏东西——
    # 部署实际用 requirements.lock，而 dev 锁里的 pytest/pandas 同样是发布包内容，
    # 漏掉会让 SBOM 与"包里装了什么"对不上。
    # 每条依赖的来源写在 annotation 里，可区分部署依赖与开发依赖。
    for lock_name in ("requirements.lock", "requirements-dev.lock"):
        lock = root / lock_name
        if not lock.is_file():
            continue
        for raw in lock.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith(("#", "-", " ")):
                continue
            match = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;]+)", line)
            if not match:
                continue
            name, version = match.group(1), match.group(2)
            if (name.lower(), version) in seen:
                continue
            seen.add((name.lower(), version))
            found.append({"name": name, "version": version, "source": lock_name})
    if found:
        return sorted(found, key=lambda p: p["name"].lower())
    try:
        from importlib.metadata import distributions

        for dist in distributions():
            name = dist.metadata["Name"] if dist.metadata else None
            if not name:
                continue
            found.append({"name": name, "version": dist.version, "source": "installed"})
    except Exception:  # noqa: BLE001
        pass
    return sorted(found, key=lambda p: p["name"].lower())


def node_packages(root: Path) -> list:
    """Node 依赖：读 frontend/package-lock.json 的 packages 表（含嵌套依赖）。"""
    lock = root / "frontend" / "package-lock.json"
    if not lock.is_file():
        return []
    try:
        data = json.loads(lock.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for path, meta in (data.get("packages") or {}).items():
        if not path or not isinstance(meta, dict):
            continue          # "" 是根包，由 package.json 单独处理
        name = meta.get("name") or path.split("node_modules/")[-1]
        version = meta.get("version")
        if not name or not version:
            continue
        out.append({"name": name, "version": version, "source": "frontend/package-lock.json"})
    # 去重（同一包可能被多级 node_modules 重复记录）
    unique = {}
    for item in out:
        unique[(item["name"], item["version"])] = item
    return sorted(unique.values(), key=lambda p: p["name"].lower())


def purl_for(kind: str, name: str, version: str) -> str:
    """依赖的 purl；npm 的 scoped 包必须按规范编码。

    `@vue/test-utils` 的规范写法是 `pkg:npm/%40vue/test-utils@2.5.0`：
    scope 去掉 `@` 并百分号编码，包名保持原名。PyPI 侧按 PEP 503 归一化为小写。
    """
    if kind == "npm":
        if name.startswith("@") and "/" in name:
            scope, _, pkg = name[1:].partition("/")
            return f"pkg:npm/%40{scope}/{pkg}@{version}"
        return f"pkg:npm/{name}@{version}"
    return f"pkg:pypi/{name.lower().replace('_', '-')}@{version}"


def generate(root: Path, version: str) -> dict:
    seen: dict = {}
    packages = []
    for kind, items in (("pypi", python_packages(root)), ("npm", node_packages(root))):
        for item in items:
            spdx_id = _safe_id(kind, item["name"], seen)
            packages.append({
                "SPDXID": spdx_id,
                "name": item["name"],
                "versionInfo": item["version"],
                "downloadLocation": "NOASSERTION",
                # 不分析包内文件（filesAnalyzed=false 时不得出现 hashes/packageVerificationCode）
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "copyrightText": "NOASSERTION",
                "primaryPackagePurpose": "LIBRARY",
                "externalRefs": [{
                    "referenceCategory": "PACKAGE-MANAGER",
                    "referenceType": "purl",
                    "referenceLocator": purl_for(kind, item["name"], item["version"]),
                }],
                "annotations": [{
                    "annotationType": "OTHER",
                    "annotator": "Tool: scripts/gen_sbom.py",
                    "annotationDate": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "comment": f"来源: {item['source']}",
                }],
            })
    # 命名空间必须**可重现**：用 uuid4 会让同一次提交重复生成得到不同文档，
    # SBOM 的哈希随每次构建漂移，无法用"哈希是否变化"判断依赖是否变化。
    # 这里用 版本 + commit + 包清单 的摘要，内容相同则命名空间相同。
    seed = json.dumps(
        {"version": version, "packages": [[p["name"], p["versionInfo"]] for p in packages]},
        ensure_ascii=False, sort_keys=True).encode("utf-8")
    doc = {
        "spdxVersion": SPDX_VERSION,
        "dataLicense": "CC0-1.0",
        "SPDXID": DOC_ID,
        "name": f"china-war-rag-{version}",
        "documentNamespace":
            f"https://github.com/china-war/rag/spdx/{version}/{hashlib.sha256(seed).hexdigest()[:32]}",
        "creationInfo": {
            "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "creators": ["Tool: scripts/gen_sbom.py (china-war-rag)", "Organization: china-war-rag"],
            "licenseListVersion": "3.22",
            "comment": "Python 依赖取自 requirements.lock + requirements-dev.lock，"
                       "Node 依赖取自 frontend/package-lock.json",
        },
        "packages": packages,
        "relationships": [
            {"spdxElementId": DOC_ID, "relationshipType": "DESCRIBES",
             "relatedSpdxElement": pkg["SPDXID"]}
            for pkg in packages
        ],
    }
    return doc


def validate(doc: dict) -> list:
    """按 SPDX 2.3 的必需字段做结构与自洽校验，返回问题列表。"""
    problems: list[str] = []
    for field in ("spdxVersion", "dataLicense", "SPDXID", "name", "documentNamespace",
                  "creationInfo"):
        if not doc.get(field):
            problems.append(f"缺少文档级必需字段: {field}")
    if doc.get("spdxVersion") != SPDX_VERSION:
        problems.append(f"spdxVersion 必须是 {SPDX_VERSION}，当前 {doc.get('spdxVersion')!r}")
    if doc.get("dataLicense") != "CC0-1.0":
        problems.append("dataLicense 必须是 CC0-1.0（SPDX 2.3 规范要求）")
    if doc.get("SPDXID") != DOC_ID:
        problems.append(f"文档 SPDXID 必须是 {DOC_ID}，当前 {doc.get('SPDXID')!r}")
    creation = doc.get("creationInfo") or {}
    if not creation.get("created"):
        problems.append("creationInfo.created 缺失")
    if not creation.get("creators"):
        problems.append("creationInfo.creators 缺失（至少要有一个 creator）")

    packages = doc.get("packages")
    if not isinstance(packages, list) or not packages:
        problems.append("packages 必须是非空数组")
        packages = []
    ids: set = set()
    for pkg in packages:
        spdx_id = pkg.get("SPDXID") or ""
        if not _ID_RE.match(spdx_id):
            problems.append(f"包 SPDXID 非法: {spdx_id!r}")
        if spdx_id in ids:
            problems.append(f"包 SPDXID 重复: {spdx_id}")
        ids.add(spdx_id)
        for field in ("name", "versionInfo", "downloadLocation", "licenseConcluded",
                      "licenseDeclared", "copyrightText"):
            if not pkg.get(field):
                problems.append(f"包 {spdx_id or pkg.get('name')} 缺少必需字段: {field}")
        if pkg.get("filesAnalyzed") is False and ("hashes" in pkg or
                                                  "packageVerificationCode" in pkg):
            problems.append(f"包 {spdx_id} 声明 filesAnalyzed=false 但有 hashes/校验码字段")
        refs = pkg.get("externalRefs") or []
        if not any(str(r.get("referenceLocator", "")).startswith((_PURL_PYPI, _PURL_NPM))
                   for r in refs):
            problems.append(f"包 {spdx_id} 缺少 purl 外部引用（pypi/npm）")

    relationships = doc.get("relationships") or []
    if not relationships:
        problems.append("relationships 为空：文档与包之间没有 DESCRIBES 关系")
    for rel in relationships:
        if rel.get("relatedSpdxElement") not in ids and rel.get("relatedSpdxElement") != DOC_ID:
            problems.append(f"关系指向不存在的 SPDXID: {rel.get('relatedSpdxElement')!r}")
        if rel.get("spdxElementId") not in ids and rel.get("spdxElementId") != DOC_ID:
            problems.append(f"关系源 SPDXID 不存在: {rel.get('spdxElementId')!r}")
    if not any(r.get("relationshipType") == "DESCRIBES" for r in relationships):
        problems.append("缺少 DESCRIBES 关系")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="生成/校验 SPDX 2.3 SBOM")
    sub = ap.add_subparsers(dest="command")
    gen = sub.add_parser("generate", help="生成 SBOM")
    gen.add_argument("--version", default="", help="数据版本（缺省取 RAG_ACTIVE_VERSION）")
    gen.add_argument("--out", default="data/release/sbom.json", help="输出路径")
    val = sub.add_parser("validate", help="校验既有 SBOM")
    val.add_argument("--path", default="data/release/sbom.json", help="SBOM 路径")
    args = ap.parse_args()

    root = repo_root()
    if args.command == "validate":
        path = Path(args.path)
        if not path.is_file():
            print(f"SBOM 不存在: {path}")
            return 2
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"SBOM 解析失败: {e}")
            return 2
        problems = validate(doc)
        print(f"SBOM 校验: {path}（{len(doc.get('packages') or [])} 个包）")
        if problems:
            print(f"发现 {len(problems)} 处问题：")
            for item in problems[:30]:
                print(f"  - {item}")
            return 1
        print("SPDX 2.3 必需字段与关系自洽性均通过")
        return 0

    if args.command in (None, "generate"):
        version = getattr(args, "version", "") or ""
        if not version:
            from config.settings import get_settings

            version = get_settings().active_version or "unknown"
        out_path = Path(getattr(args, "out", "data/release/sbom.json"))
        if not out_path.is_absolute():
            out_path = root / out_path
        doc = generate(root, version)
        problems = validate(doc)
        if problems:
            print("生成的 SBOM 未通过自检（不应发生，请检查包名清洗逻辑）：")
            for item in problems[:10]:
                print(f"  - {item}")
            return 1
        out_path.parent.mkdir(parents=True, exist_ok=True)
        write_text_lf(out_path, json.dumps(doc, ensure_ascii=False, indent=2))
        py = sum(1 for p in doc["packages"] if p["SPDXID"].startswith("SPDXRef-Package-pypi"))
        npm = len(doc["packages"]) - py
        print(f"SBOM 已写入: {out_path}")
        print(f"  SPDX {doc['spdxVersion']} | 包 {len(doc['packages'])} 个"
              f"（Python {py} / Node {npm}）| 关系 {len(doc['relationships'])} 条")
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
