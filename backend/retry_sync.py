#!/usr/bin/env python3
"""重放待补偿的 Neo4j 同步任务（第 12 轮审查 P1-3 / 第 13 轮整改）。

用法：
    cd backend
    python retry_sync.py --list          # 看还有多少待补偿、哪些已放弃
    python retry_sync.py                 # 立刻重放一批（默认 50 条，忽略退避）
    python retry_sync.py --due-only      # 只重放"退避已到期"的（给定时器/后台用）
    python retry_sync.py --limit 200

为什么要有命令行入口：管理台接口（`POST /api/sync/retry`）需要有人登录去点，
而"图谱与列表对不上"这类问题常常是在部署/巡检时发现的——那时手边未必有浏览器。
两者调用的是同一个 `sync_compensation.retry_pending`，不存在两套逻辑。

**自动重试**（文档第六节第 6 条）：`deploy/systemd/china-war-outbox-retry.timer`
每分钟跑一次 `--due-only`。放在定时器而不是 web 进程的后台线程里，是因为
"重放"这件事与请求处理无关，起来一次就做完、做完就退出，比长驻线程好观察也好重启。

退出码：0 = 全部重放成功或没有待办；1 = 有失败或已放弃的任务（便于 cron/巡检判断）。
"""

import argparse
import json
import sys
from pathlib import Path

from flask import Flask

from models import db

APP_PATH = Path(__file__).resolve().parent
app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{APP_PATH / 'database'}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db.init_app(app)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="重放待补偿的 Neo4j 同步任务")
    parser.add_argument("--limit", type=int, default=50, help="本批最多重放多少条（默认 50）")
    parser.add_argument("--list", action="store_true", help="只列出待补偿任务，不做重放")
    parser.add_argument("--due-only", action="store_true",
                        help="只重放退避已到期的任务（人工立刻重试时不要加这个参数）")
    args = parser.parse_args(argv)

    with app.app_context():
        from sync_compensation import (MAX_ATTEMPTS, jobs_summary, pending_jobs,
                                       retry_pending)

        summary = jobs_summary()
        if summary.get("table_missing"):
            print("⚠️ 补偿队列表还不存在（本库从未出现过同步失败，或后端还没启动过一次）。")
            return 0

        print(f"待补偿 {summary['pending']} 条 / 已补齐 {summary['done']} 条 / "
              f"已放弃 {summary['abandoned']} 条 / 已作废 {summary['cancelled']} 条"
              f"（数据版本 {summary['dataset_version']}）")
        if summary["oldest_pending_age_seconds"] is not None:
            # 积压时长是运维最先要看的数字：它区分"偶尔抖动"与"一直在错"
            print(f"最老待办已积压 {summary['oldest_pending_age_seconds']:.0f} 秒")

        if args.list:
            jobs = pending_jobs(limit=args.limit)
            if not jobs:
                print("没有待补偿任务。")
                return 0
            print(f"\n{'id':<6}{'操作':<14}{'图谱键':<22}{'名称':<24}{'尝试':<6}最近错误")
            for job in jobs:
                print(f"{job.id:<6}{(job.operation or ''):<14}"
                      f"{(job.graph_key or ''):<22}{(job.node_name or '')[:20]:<24}"
                      f"{job.attempts:<6}{(job.last_error or '')[:50]}")
            if summary["abandoned"]:
                print(f"\n⚠️ 有 {summary['abandoned']} 条已尝试 {MAX_ATTEMPTS} 次仍未成功，"
                      "需要人工核对（先看 last_error：多为 Neo4j 不可达或权限问题）。")
            return 0

        stats = retry_pending(limit=args.limit, only_due=args.due_only)
        print(json.dumps(stats, ensure_ascii=False))
        if stats["failed"] or stats["abandoned"]:
            print("❌ 有任务未补齐；用 --list 查看原因，必要时人工核对 Neo4j 侧数据。")
            return 1
        return 0


if __name__ == "__main__":
    sys.exit(main())
