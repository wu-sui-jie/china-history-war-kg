#!/usr/bin/env python3
"""接口响应快照：重构前后各采集一次，diff 为空即"行为未变"。

这是拆蓝图与 N+1 下推的前置兜底——没有它就无法证明重构
只改了结构、没改行为。用法：

    # 1) 重构前，后端在跑的情况下采集基线
    python tools/snapshot_responses.py capture --out /tmp/before.json

    # 2) 改完代码、重启后端，再采一次
    python tools/snapshot_responses.py capture --out /tmp/after.json

    # 3) 对比
    python tools/snapshot_responses.py diff /tmp/before.json /tmp/after.json

设计取舍：

- **只覆盖只读接口**，以及写接口的"参数校验失败"路径（不落数据）。
  跑在真实库上，绝不能因为对照脚本改坏数据。
- 会调用大模型/外部服务的接口（`/api/ai/inference*`、`/api/extract/entities-events`）
  不在清单里：它们的结果本身不确定，放进来只会制造噪音。
  **这个缺口由别的层兜住**：抽取端点的提示词内容有
  `tests/test_extract_prompt.py`（桩 LLM），序列化与后处理有
  `tests/test_extract_replay.py`（真实运行录制回放，夹具
  `tests/fixtures/extract_replay.json`，重录脚本 `tools/record_extract_replay.py`）。
  两者都是确定性的、可在 CI 跑；"抽取质量"只能靠真跑 + 人工看，进不了 CI。
- 不做事先归一化，逐字段精确对比。差异按 JSON 路径列出，由人判断是不是预期变化。
  唯一例外是 `--ignore` 指定的键（如耗时字段），默认忽略 `process_time` 这类运行时值。
- 路径里可写 `{version_id}` / `{node_id}` 占位符，采集时由 `probe_dynamic()` 现取
  （版本号、非 1 号节点 id）。取不到就跳过该请求并提示，不用写死的假值充数。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# 允许从 backend/ 目录下直接运行（脚本在 tools/ 子目录）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

# 只读请求清单：(名称, 方法, 路径, body)
READ_REQUESTS = [
    # ---- 总览与数据集 ----
    ("dashboard", "GET", "/api/dashboard/overview", None),
    ("dataset_overview", "GET", "/api/dataset/overview", None),
    ("dataset_versions", "GET", "/api/dataset/versions", None),
    ("quality_report", "GET", "/api/quality/report", None),
    ("quality_workbench", "GET", "/api/quality/workbench", None),
    ("repair_issues", "GET", "/api/repair/issues", None),
    # ---- 时间轴与地图 ----
    ("timeline_overview", "GET", "/api/timeline/overview", None),
    ("timeline_overview_dynasty", "GET", "/api/timeline/overview?dynasty=秦", None),
    ("timeline_overview_participant", "GET", "/api/timeline/overview?participant=秦", None),
    # 注：/api/timeline/events 当前不读 limit（只读 keyword/dynasty/participant/event_type/
    # only_issues），这个参数是空转的。特意留着：哪天路由真的接上 limit，这里会立刻报差异。
    ("timeline_events", "GET", "/api/timeline/events?limit=5", None),
    ("timeline_events_only_issues", "GET", "/api/timeline/events?only_issues=1", None),
    ("timeline_events_participant", "GET", "/api/timeline/events?participant=秦", None),
    ("map_events", "GET", "/api/map/events", None),
    ("map_events_filtered", "GET", "/api/map/events?keyword=秦&dynasty=秦", None),
    ("map_events_dynasty", "GET", "/api/map/events?dynasty=秦", None),
    # ---- 搜索与关系分析 ----
    ("search_global", "GET", "/api/search/global?keyword=秦", None),
    ("relation_analysis", "GET", "/api/relation-analysis/query?keyword=秦", None),
    ("relation_analysis_empty", "GET", "/api/relation-analysis/query?keyword=", None),
    # ---- 类型与节点查询 ----
    ("node_types", "GET", "/api/node_types", None),
    ("relationship_types", "GET", "/api/relationship_types", None),
    ("relationship_types_by_event", "GET", "/api/relationship_types_by_Event", None),
    ("node_by_type", "GET", "/api/node/by_type?type=Event", None),
    ("node_by_relationship", "GET", "/api/node/by_relationship?type=顺承关系", None),
    ("node_search_by_name", "GET", "/api/node/search_by_name?name=秦", None),
    # /api/node/search_by_name 的 limit 走 request.args.get(..., type=int)：缺省、0、
    # 负数、超大、非法值各有不同落点，逐个列出来钉住边界
    ("search_by_name_limit_1", "GET", "/api/node/search_by_name?name=秦&limit=1", None),
    ("search_by_name_limit_0", "GET", "/api/node/search_by_name?name=秦&limit=0", None),
    ("search_by_name_limit_negative", "GET", "/api/node/search_by_name?name=秦&limit=-1", None),
    ("search_by_name_limit_huge", "GET", "/api/node/search_by_name?name=秦&limit=100000", None),
    ("search_by_name_limit_invalid", "GET", "/api/node/search_by_name?name=秦&limit=abc", None),
    ("search_by_name_no_name", "GET", "/api/node/search_by_name?limit=1", None),
    # ---- 图谱 ----
    ("graph_event_event", "GET", "/api/graph/event_event", None),
    ("graph_event_organization", "GET", "/api/graph/event_organization", None),
    ("graph_event_person", "GET", "/api/graph/event_person", None),
    ("graph_event_place", "GET", "/api/graph/event_place", None),
    ("graph_node_context", "GET", "/api/graph/node_context?name=秦", None),
    ("graph_event_event_filtered", "GET", "/api/graph/event_event?name=秦&rel_type=顺承关系", None),
    # ---- 节点详情（1 号节点 + 动态取到的非 1 号节点）----
    ("node_detail", "GET", "/api/node/detail?id=1&type=Event", None),
    ("node_detail_non_first", "GET", "/api/node/detail?id={node_id}&type=Event", None),
    ("node_relations", "GET", "/api/node/relations?id=1", None),
    ("node_relations_non_first", "GET", "/api/node/relations?id={node_id}", None),
    ("entity_detail", "GET", "/api/entity/detail?type=Event&id=1", None),
    ("entity_detail_non_first", "GET", "/api/entity/detail?type=Event&id={node_id}", None),
    ("node_detail_missing_id", "GET", "/api/node/detail?type=Event", None),
    ("node_detail_unknown_type", "GET", "/api/node/detail?id=1&type=NotAType", None),
    # ---- 数据集版本详情（动态版本号 + 未知版本号的回退分支）----
    ("dataset_version_detail", "GET", "/api/dataset/version_detail", None),
    ("dataset_version_detail_dynamic", "GET", "/api/dataset/version_detail?id={version_id}", None),
    ("dataset_version_detail_unknown", "GET", "/api/dataset/version_detail?id=__no_such_version__", None),
    # ---- 用户 ----
    ("user_menu", "GET", "/user/menu", None),
    ("user_permission", "GET", "/user/permission", None),
    ("userinfo", "GET", "/api/userinfo", None),
    # ---- POST：分页（真实参数名是 pageNum / pageSize，不是 current / limit）----
    ("find_node_page", "POST", "/api/find_node_page", {"pageNum": 1, "pageSize": 5}),
    ("find_node_page_default", "POST", "/api/find_node_page", {}),
    ("find_node_page_size1", "POST", "/api/find_node_page", {"pageNum": 1, "pageSize": 1}),
    ("find_node_page_size0", "POST", "/api/find_node_page", {"pageNum": 1, "pageSize": 0}),
    ("find_node_page_page0", "POST", "/api/find_node_page", {"pageNum": 0, "pageSize": 5}),
    ("find_node_page_page_negative", "POST", "/api/find_node_page", {"pageNum": -1, "pageSize": 5}),
    ("find_node_page_size_huge", "POST", "/api/find_node_page",
     {"pageNum": 1, "pageSize": 100000, "name": "秦"}),
    ("find_node_page_typed", "POST", "/api/find_node_page",
     {"pageNum": 1, "pageSize": 5, "node_type": "Event", "name": "秦"}),
    ("find_node_page_typed_size0", "POST", "/api/find_node_page",
     {"pageNum": 1, "pageSize": 0, "node_type": "Event"}),
    # 非 typed 分支的负 pageSize 会命中切片 [0:-1]（返回几乎全表），加 name 过滤压住响应体
    ("find_node_page_size_negative_named", "POST", "/api/find_node_page",
     {"pageNum": 1, "pageSize": -1, "name": "秦"}),
    # ---- POST：名称检索 ----
    ("search_name_kg_default", "POST", "/search_name_kg", {}),
    ("search_name_kg_by_name", "POST", "/search_name_kg", {"name": "秦"}),
    ("search_name_kg_by_type", "POST", "/search_name_kg", {"node_type": "Event"}),
    # ---- 写接口的校验失败路径：只触发参数处理，不落数据 ----
    ("create_node_invalid", "POST", "/create_node", {}),
    ("update_node_invalid", "POST", "/update_node", {}),
    ("delete_node_invalid", "POST", "/delete_node", {}),
    ("update_properties_invalid", "POST", "/api/node/update_properties", {}),
]

# 运行时值（每次采集都可能不同），对比时忽略
DEFAULT_IGNORE_KEYS = {"process_time", "elapsed", "duration_ms"}

# 路径占位符：{version_id} / {node_id}，采集时由 probe_dynamic() 解析
PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")


def make_token() -> str:
    """用 admin 账号（id 最小的 admin）签发一个 token，省得脚本里存口令。"""
    import jwt_util
    from models import UserInfo, db
    import app as app_module  # noqa: F401  仅为触发 db 初始化

    with app_module.app.app_context():
        user = UserInfo.query.filter(UserInfo.role == "admin").order_by(UserInfo.id).first()
        if user is None:
            raise SystemExit("库里没有 admin 账号，无法签发 token")
        return jwt_util.encode(user.id)


def probe_dynamic(base: str, headers: dict, timeout: float) -> dict:
    """解析路径占位符需要的运行时值。

    - `version_id`：数据集版本列表的第一项 id（版本号是数据派生的，不能写死）。
    - `node_id`：Event 类型里第一个 id != 1 的节点（1 号节点已在清单里单列，
      这里要的是"非 1 号"那条路径：字段映射、关系聚合在别的主键上是否一致）。

    取不到就不放进结果，对应请求在采集时跳过并提示——宁可少采一条，也不用写死的
    假 id 充数：假 id 采到的是稳定的"节点不存在"，对照时看不出真实行为变化。
    """
    values = {}
    root = base.rstrip("/")

    try:
        resp = requests.get(root + "/api/dataset/versions", headers=headers, timeout=timeout)
        versions = (resp.json() or {}).get("data") or []
        if versions:
            version_id = str(versions[0].get("id") or "")
            if version_id:
                values["version_id"] = version_id
    except Exception as exc:  # noqa: BLE001
        print(f"  探测数据集版本失败：{type(exc).__name__}: {exc}", file=sys.stderr)

    try:
        resp = requests.post(root + "/api/find_node_page", headers=headers,
                             json={"pageNum": 1, "pageSize": 20, "node_type": "Event"},
                             timeout=timeout)
        records = ((resp.json() or {}).get("data") or {}).get("records") or []
        for record in records:
            if record.get("id") not in (None, 1):
                values["node_id"] = str(record["id"])
                break
        if "node_id" not in values:
            print("  库里没有 id != 1 的 Event 节点，非 1 号节点的请求会跳过", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"  探测非 1 号节点失败：{type(exc).__name__}: {exc}", file=sys.stderr)

    return values


def capture(base: str, timeout: float) -> dict:
    token = make_token()
    headers = {"Token": token}
    dynamic = probe_dynamic(base, headers, timeout)
    if dynamic:
        print(f"  占位符已解析：{dynamic}", file=sys.stderr)
    results = []
    skipped = []
    for name, method, path, body in READ_REQUESTS:
        pending = [k for k in PLACEHOLDER_RE.findall(path) if not dynamic.get(k)]
        if pending:
            skipped.append(f"{name}（{','.join(pending)} 未解析）")
            print(f"  {name:28s} 跳过：占位符未解析", file=sys.stderr)
            continue
        url = base.rstrip("/") + path.format(**dynamic)
        try:
            if method == "GET":
                resp = requests.get(url, headers=headers, timeout=timeout)
            else:
                resp = requests.post(url, headers=headers, json=body, timeout=timeout)
            try:
                parsed = resp.json()
            except ValueError:
                parsed = resp.text[:2000]
            entry = {"name": name, "method": method, "path": path,
                     "status": resp.status_code, "body": parsed}
        except Exception as exc:  # noqa: BLE001
            entry = {"name": name, "method": method, "path": path,
                     "status": None, "error": f"{type(exc).__name__}: {exc}"}
        results.append(entry)
        print(f"  {name:28s} {entry['status']}", file=sys.stderr)
    snapshot = {"base": base, "requests": results}
    if dynamic:
        snapshot["dynamic"] = dynamic
    if skipped:
        snapshot["skipped"] = skipped
    return snapshot


def diff_value(a, b, path: str, ignore_keys: set, out: list) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            if key in ignore_keys:
                continue
            sub = f"{path}.{key}" if path else key
            if key not in a:
                out.append(f"+ {sub}: 新增 = {json.dumps(b[key], ensure_ascii=False)[:120]}")
            elif key not in b:
                out.append(f"- {sub}: 缺失（原 = {json.dumps(a[key], ensure_ascii=False)[:120]}）")
            else:
                diff_value(a[key], b[key], sub, ignore_keys, out)
        return
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"! {path}: 长度 {len(a)} -> {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            diff_value(x, y, f"{path}[{i}]", ignore_keys, out)
        return
    if a != b:
        out.append(f"! {path}: {json.dumps(a, ensure_ascii=False)[:120]} -> "
                   f"{json.dumps(b, ensure_ascii=False)[:120]}")


def compare(before: dict, after: dict, ignore_keys: set) -> int:
    b_by_name = {r["name"]: r for r in before["requests"]}
    a_by_name = {r["name"]: r for r in after["requests"]}
    total_diffs = 0

    for name in sorted(set(b_by_name) | set(a_by_name)):
        b, a = b_by_name.get(name), a_by_name.get(name)
        if b is None or a is None:
            print(f"[{name}] 只在一侧存在")
            total_diffs += 1
            continue

        lines: list[str] = []
        if b.get("path") != a.get("path"):
            # 占位符解析值变了（如动态 node_id 指向另一个节点）：数据变了，必须报
            lines.append(f"! 路径: {b.get('path')} -> {a.get('path')}")
        if b.get("status") != a.get("status"):
            lines.append(f"! HTTP 状态: {b.get('status')} -> {a.get('status')}")
        if b.get("error") or a.get("error"):
            if b.get("error") != a.get("error"):
                lines.append(f"! 请求错误: {b.get('error')} -> {a.get('error')}")
        else:
            diff_value(b.get("body"), a.get("body"), "", ignore_keys, lines)

        if lines:
            total_diffs += len(lines)
            print(f"\n[{name}] {len(lines)} 处差异")
            for line in lines[:20]:
                print(f"  {line}")
            if len(lines) > 20:
                print(f"  …… 另有 {len(lines) - 20} 处")
        else:
            print(f"[{name}] 一致")

    for side, snapshot in (("before", before), ("after", after)):
        for item in snapshot.get("skipped") or []:
            print(f"[跳过] {side}: {item}")

    print(f"\n{'完全一致' if total_diffs == 0 else f'共 {total_diffs} 处差异'}")
    return 1 if total_diffs else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="接口响应快照采集与对比")
    sub = parser.add_subparsers(dest="command", required=True)

    cap = sub.add_parser("capture", help="采集一份快照")
    cap.add_argument("--out", required=True)
    cap.add_argument("--base", default="http://127.0.0.1:5000")
    cap.add_argument("--timeout", type=float, default=60.0)

    cmp_ = sub.add_parser("diff", help="对比两份快照")
    cmp_.add_argument("before")
    cmp_.add_argument("after")
    cmp_.add_argument("--ignore", default=",".join(sorted(DEFAULT_IGNORE_KEYS)),
                      help="忽略的键名（逗号分隔）")

    args = parser.parse_args()

    if args.command == "capture":
        snapshot = capture(args.base, args.timeout)
        Path(args.out).write_text(json.dumps(snapshot, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        print(f"已写入 {args.out}（{len(snapshot['requests'])} 个请求）")
        return 0

    before = json.loads(Path(args.before).read_text(encoding="utf-8"))
    after = json.loads(Path(args.after).read_text(encoding="utf-8"))
    ignore = {k.strip() for k in args.ignore.split(",") if k.strip()}
    return compare(before, after, ignore)


if __name__ == "__main__":
    raise SystemExit(main())
