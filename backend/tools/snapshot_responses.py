#!/usr/bin/env python3
"""接口响应快照：重构前后各采集一次，diff 为空即"行为未变"。

这是 P2-1（拆蓝图）与 P2-5（N+1 下推）的前置兜底——没有它就无法证明重构
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
- 不做事先归一化，逐字段精确对比。差异按 JSON 路径列出，由人判断是不是预期变化。
  唯一例外是 `--ignore` 指定的键（如耗时字段），默认忽略 `process_time` 这类运行时值。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 允许从 backend/ 目录下直接运行（脚本在 tools/ 子目录）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

# 只读请求清单：(名称, 方法, 路径, body)
READ_REQUESTS = [
    ("dashboard", "GET", "/api/dashboard/overview", None),
    ("dataset_overview", "GET", "/api/dataset/overview", None),
    ("dataset_versions", "GET", "/api/dataset/versions", None),
    ("quality_report", "GET", "/api/quality/report", None),
    ("quality_workbench", "GET", "/api/quality/workbench", None),
    ("repair_issues", "GET", "/api/repair/issues", None),
    ("timeline_overview", "GET", "/api/timeline/overview", None),
    ("timeline_events", "GET", "/api/timeline/events?limit=5", None),
    ("map_events", "GET", "/api/map/events", None),
    ("search_global", "GET", "/api/search/global?keyword=秦", None),
    ("relation_analysis", "GET", "/api/relation-analysis/query?keyword=秦", None),
    ("node_types", "GET", "/api/node_types", None),
    ("relationship_types", "GET", "/api/relationship_types", None),
    ("relationship_types_by_event", "GET", "/api/relationship_types_by_Event", None),
    ("node_by_type", "GET", "/api/node/by_type?type=Event", None),
    ("node_by_relationship", "GET", "/api/node/by_relationship?type=顺承关系", None),
    ("node_search_by_name", "GET", "/api/node/search_by_name?name=秦", None),
    ("graph_event_event", "GET", "/api/graph/event_event", None),
    ("graph_event_organization", "GET", "/api/graph/event_organization", None),
    ("graph_event_person", "GET", "/api/graph/event_person", None),
    ("graph_event_place", "GET", "/api/graph/event_place", None),
    ("graph_node_context", "GET", "/api/graph/node_context?name=秦", None),
    ("node_detail", "GET", "/api/node/detail?id=1&type=Event", None),
    ("node_relations", "GET", "/api/node/relations?id=1", None),
    ("entity_detail", "GET", "/api/entity/detail?type=Event&id=1", None),
    ("user_menu", "GET", "/user/menu", None),
    ("user_permission", "GET", "/user/permission", None),
    ("userinfo", "GET", "/api/userinfo", None),
    ("find_node_page", "POST", "/api/find_node_page", {"current": 1, "limit": 5}),
    ("find_node_page_typed", "POST", "/api/find_node_page",
     {"current": 1, "limit": 5, "type": "Event", "name": "秦"}),
    ("search_name_kg_default", "POST", "/search_name_kg", {}),
    ("search_name_kg_by_name", "POST", "/search_name_kg", {"name": "秦"}),
    ("search_name_kg_by_type", "POST", "/search_name_kg", {"node_type": "Event"}),
    # 写接口的校验失败路径：只触发参数处理，不落数据
    ("create_node_invalid", "POST", "/create_node", {}),
    ("update_node_invalid", "POST", "/update_node", {}),
    ("delete_node_invalid", "POST", "/delete_node", {}),
    ("update_properties_invalid", "POST", "/api/node/update_properties", {}),
]

# 运行时值（每次采集都可能不同），对比时忽略
DEFAULT_IGNORE_KEYS = {"process_time", "elapsed", "duration_ms"}


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


def capture(base: str, timeout: float) -> dict:
    token = make_token()
    headers = {"Token": token}
    results = []
    for name, method, path, body in READ_REQUESTS:
        url = base.rstrip("/") + path
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
    return {"base": base, "requests": results}


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
