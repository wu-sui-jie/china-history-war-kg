"""首个管理员引导命令（审查报告 P2-6）。

背景：注册接口一律只建 viewer，而用户管理页只有管理员能进——全新库因此死锁，
过去只能手写 `UPDATE UserInfo SET role='admin'`。手写 SQL 的问题不是麻烦，是没有护栏：
任意时刻都能把任意账号提权，不留痕。本用例钉住引导命令的护栏：

1. 库里已有管理员时**拒绝执行**（多管理员该走管理台，不该走救急命令）；
2. 提升已有账号只改角色，不动口令；
3. 全新库能建出第一个管理员（口令来自交互输入，不走命令行参数）；
4. `--list` 是纯读操作。
"""

import create_admin

from models import UserInfo, db


def test_已有管理员时拒绝执行(_app_context, capsys):
    db.session.add(UserInfo(account="既有-admin", name="既有-admin", password="x", role="admin"))
    db.session.add(UserInfo(account="待提升", name="待提升", password="x", role="viewer"))
    db.session.commit()

    code = create_admin.main(["--account", "待提升"])

    assert code == 1
    assert UserInfo.query.filter_by(account="待提升").first().role == "viewer"
    assert "已有管理员" in capsys.readouterr().out


def test_提升已有账号只改角色不动口令(_app_context):
    db.session.add(UserInfo(account="待提升", name="昵称-甲", password="原口令哈希", role="viewer"))
    db.session.commit()

    code = create_admin.main(["--account", "待提升", "--name", "不该生效的昵称"])

    assert code == 0
    user = UserInfo.query.filter_by(account="待提升").first()
    assert user.role == "admin"
    assert user.password == "原口令哈希", "引导命令不该顺手改口令"
    assert user.name == "昵称-甲", "提升已有账号时 --name 不该覆盖昵称"


def test_全新库能建出第一个管理员(_app_context, monkeypatch):
    monkeypatch.setattr(create_admin, "_read_password", lambda: "pw-123456")

    code = create_admin.main(["--account", "first-admin", "--name", "首管", "--password"])

    assert code == 0
    user = UserInfo.query.filter_by(account="first-admin").first()
    assert user.role == "admin"
    # 口令必须是哈希落库，且原口令不再以明文出现在库里
    assert user.password != "pw-123456"
    assert user.password.startswith("pbkdf2:") or user.password.startswith("scrypt:")


def test_新建账号缺少昵称或口令开关时拒绝(_app_context):
    import pytest

    with pytest.raises(SystemExit):
        create_admin.main(["--account", "first-admin"])
    with pytest.raises(SystemExit):
        create_admin.main(["--account", "first-admin", "--name", "首管"])
    assert UserInfo.query.count() == 0


def test_list_是纯读操作(_app_context, capsys):
    db.session.add(UserInfo(account="someone", name="某人", password="x", role="viewer"))
    db.session.commit()

    code = create_admin.main(["--list"])

    assert code == 0
    out = capsys.readouterr().out
    assert "someone" in out and "viewer" in out
    assert UserInfo.query.filter_by(account="someone").first().role == "viewer"


def test_口令长度越界时中止且不落库(_app_context, monkeypatch):
    import pytest

    monkeypatch.setattr(create_admin, "_read_password",
                        lambda: (_ for _ in ()).throw(SystemExit("口令长度需为 6-20 位，已中止")))

    with pytest.raises(SystemExit):
        create_admin.main(["--account", "first-admin", "--name", "首管", "--password"])
    assert UserInfo.query.count() == 0
