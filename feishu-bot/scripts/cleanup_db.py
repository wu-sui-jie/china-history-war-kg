"""清理过期数据（开发文档 5.2 的硬口径 / 第七节）。

查询时的 24h 过滤是**软口径**（历史组装不取过期记录），本脚本负责物理删除，
两件事都做表才不会无限膨胀。建议每日执行一次（cron / 计划任务）。

删除对象：
- `messages` / `sessions`：超出 SESSION_TTL_HOURS；
- `processed_events`：超出 PROCESSED_EVENTS_TTL_HOURS（飞书重投窗口只有分钟级，
  留着一天足够；这条表增长最快，因为它每个事件一行）。

用法：
    python feishu-bot/scripts/cleanup_db.py --dry-run     # 先看会删多少
    python feishu-bot/scripts/cleanup_db.py               # 真删
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BOT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT_ROOT))

from bot.db import Database                      # noqa: E402
from bot.session import SessionStore            # noqa: E402
from config import DEFAULT_DB_PATH, ConfigError, load_config   # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="清理机器人的过期会话/事件记录")
    parser.add_argument("--db", default="", help="SQLite 路径（默认取配置或 data/bot.db）")
    parser.add_argument("--ttl-hours", type=float, default=None,
                        help="会话与消息的保留时长（默认取 SESSION_TTL_HOURS）")
    parser.add_argument("--events-hours", type=float, default=None,
                        help="事件去重记录的保留时长（默认取 PROCESSED_EVENTS_TTL_HOURS）")
    parser.add_argument("--dry-run", action="store_true", help="只统计不删除")
    args = parser.parse_args(argv)

    # 配置可能不完整（清理脚本不该要求填飞书密钥），因此缺失必填项时退化为默认值
    try:
        config = load_config(skip_dotenv=False)
        db_path = Path(args.db).expanduser() if args.db else config.database_path
        ttl_hours = args.ttl_hours if args.ttl_hours is not None else config.session_ttl_hours
        events_hours = (args.events_hours if args.events_hours is not None
                        else config.processed_events_ttl_hours)
    except ConfigError as e:
        if args.db or args.ttl_hours is not None:
            db_path = Path(args.db).expanduser() if args.db else DEFAULT_DB_PATH
            ttl_hours = args.ttl_hours if args.ttl_hours is not None else 24.0
            events_hours = args.events_hours if args.events_hours is not None else 24.0
            print(f"[i] 配置不完整（{e}），按命令行参数/默认值执行")
        else:
            print(f"[feishu-bot] 无法确定清理参数：{e}\n"
                  f"请用 --db/--ttl-hours/--events-hours 显式指定，或先补好 .env。",
                  file=sys.stderr)
            return 2

    if not db_path.exists():
        print(f"[i] 数据库不存在，无需清理：{db_path}")
        return 0

    db = Database(db_path)
    db.connect()
    try:
        store = SessionStore(db, ttl_hours=ttl_hours)
        counts = store.cleanup(events_ttl_hours=events_hours, dry_run=args.dry_run)
    finally:
        db.close()

    action = "将删除" if args.dry_run else "已删除"
    print(f"[i] 数据库：{db_path}")
    print(f"[i] 口径：会话/消息 {ttl_hours:g}h 之前、事件去重 {events_hours:g}h 之前")
    for name, count in counts.items():
        print(f"    {action} {name}: {count} 行")
    if args.dry_run:
        print("[i] 这是预演（--dry-run），未做任何删除")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
