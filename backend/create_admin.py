#!/usr/bin/env python3
"""首个管理员的一次性引导命令。

为什么需要它：注册接口一律只建 viewer（见 DbUtil.add_user），而用户管理页只有管理员
能进——全新数据库因此陷入死锁，「建第一个管理员」过去只能手写 SQLite：

    sqlite3 database "UPDATE UserInfo SET role = 'admin' WHERE account = 'someone';"

手写 SQL 的问题不是麻烦，而是**没有护栏**：谁都能在任意时刻把任意账号提成管理员，
不留痕。改成本命令后，「首个管理员」有了一个可审计的唯一入口：只在库里真的没有
管理员时才会动数据，退出码明确，且不走任何 HTTP 接口（不对外暴露引导面）。

用法：
    cd backend
    python create_admin.py --account alice              # 提升已有账号（推荐：先注册再提升）
    python create_admin.py --account alice --name 爱丽丝 --password   # 新建账号（密码交互输入）
    python create_admin.py --list                       # 只查看当前账号与角色，不改动

与启动迁移的分工见 app.py 的 ensure_user_table_schema：那条迁移把空角色回填为
**viewer**（最小权限），不再顺手造管理员；管理员一律由本命令显式产生。
"""

import argparse
import getpass
import sqlite3
import sys
from pathlib import Path

from flask import Flask
from sqlalchemy import text
from werkzeug.security import generate_password_hash

from models import UserInfo, db

APP_PATH = Path(__file__).resolve().parent
DATABASE_PATH = APP_PATH / "database"

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DATABASE_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db.init_app(app)

MIN_PASSWORD_LENGTH = 6
MAX_PASSWORD_LENGTH = 20


def _print_users():
    rows = db.session.execute(
        text("SELECT id, account, name, role FROM UserInfo ORDER BY id ASC")
    ).fetchall()
    if not rows:
        print("（当前没有任何账号）")
        return 0
    print(f"{'id':<6}{'账号':<22}{'昵称':<18}角色")
    for row in rows:
        print(f"{row[0]:<6}{row[1] or '':<22}{row[2] or '':<18}{row[3] or 'viewer'}")
    admins = [row for row in rows if (row[3] or "") == "admin"]
    print(f"\n共 {len(rows)} 个账号，其中管理员 {len(admins)} 个。")
    return 0


def _read_password() -> str:
    """交互读口令；空口令、长度越界、二次输入不一致都当场拒绝。

    口令只走终端输入（不回显、不进 shell 历史），不接受命令行参数之外的环境变量——
    本命令是一次性引导，把口令留在进程参数/环境里没有收益，只有泄露面。
    """
    password = getpass.getpass("请输入管理员口令：")
    if password != getpass.getpass("请再次输入以确认："):
        raise SystemExit("两次输入不一致，已中止（未改动数据库）。")
    if not (MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH):
        raise SystemExit(
            f"口令长度需为 {MIN_PASSWORD_LENGTH}-{MAX_PASSWORD_LENGTH} 位，已中止（未改动数据库）。"
        )
    return password


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="创建/提升首个管理员（仅在库中没有管理员时可用）")
    parser.add_argument("--account", help="账号；不存在则用 --name / 交互口令新建")
    parser.add_argument("--name", help="昵称（新建账号时必填；提升已有账号时忽略）")
    parser.add_argument(
        "--password",
        action="store_true",
        help="新建账号时交互输入口令（不会从命令行读取口令本身）",
    )
    parser.add_argument("--list", action="store_true", help="只列出账号与角色，不做任何改动")
    args = parser.parse_args(argv)

    with app.app_context():
        # 只补缺失的表（全新库连 UserInfo 都还没有）；不 drop、不改已有表
        db.create_all()

        if args.list:
            return _print_users()

        if not args.account:
            parser.error("缺少 --account（或用 --list 只查看）")

        account = args.account.strip()
        if not account:
            parser.error("--account 不能为空")

        existing_admin = db.session.execute(
            text("SELECT account FROM UserInfo WHERE role = 'admin' LIMIT 1")
        ).fetchone()
        if existing_admin:
            # 有意不做"再加一个管理员"：多管理员是日常运维动作，应该走用户管理页
            # （有界面、有操作者身份），而不是这个一次性引导命令。这里的职责边界
            # 就是"把系统从没有管理员的状态里救出来"。
            print(
                f"❌ 库中已有管理员（{existing_admin[0]}），本命令不做任何改动。\n"
                "   需要更多管理员请在管理台「用户管理」页调整角色。"
            )
            return 1

        user = UserInfo.query.filter_by(account=account).first()

        if user is None:
            # 新建：账号 + 昵称 + 口令都必需。口令必须交互输入。
            if not args.name:
                parser.error("新建账号需要 --name（昵称）")
            if not args.password:
                parser.error("新建账号需要 --password，随后会提示交互输入口令")
            user = UserInfo(
                account=account,
                name=args.name.strip(),
                password=generate_password_hash(_read_password()),
                role="admin",
            )
            db.session.add(user)
            db.session.commit()
            print(f"✅ 已新建管理员账号：{account}（id={user.id}）")
            return 0

        # 提升已有账号：口令保持不变（改口令是另一件事，不混在引导动作里）
        previous_role = user.role or "viewer"
        user.role = "admin"
        db.session.commit()
        print(
            f"✅ 已将账号 {account}（id={user.id}）的角色由 {previous_role} 提升为 admin。\n"
            "   口令未改动；建议首次登录后自行更换。"
        )
        return 0


if __name__ == "__main__":
    if not DATABASE_PATH.exists():
        # 明确说清楚"库文件不存在"而不是让 SQLAlchemy 去建一个空库再报"账号不存在"
        print(f"❌ 未找到数据库文件：{DATABASE_PATH}\n   请先启动一次后端（python app.py）建库。")
        sys.exit(1)
    try:
        sys.exit(main())
    except sqlite3.Error as exc:
        print(f"❌ 数据库操作失败：{exc}")
        sys.exit(1)
