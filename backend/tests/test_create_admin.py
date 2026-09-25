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
    """首管命令的口令强度与注册/改密码**同一份策略**（第 13 轮复核整改 §2.1）。

    原先这条用例把 `_read_password` 整体打桩成"抛 SystemExit"，于是它只证明了
    "异常会中止"，没有证明"哪些口令会被拒"——6–20 位那套数值就藏在这个桩后面。
    现在改成桩 `getpass`（真正被测的是命令自己的策略判定）。
    """
    import pytest

    from password_policy import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH

    def _answer(password):
        # getpass 会被调用两次（输入 + 确认），两次都要给同一个值
        return lambda prompt="": password

    # 9 位：低于下限，必须拒绝
    monkeypatch.setattr(create_admin.getpass, "getpass", _answer("a" * 9))
    with pytest.raises(SystemExit) as excinfo:
        create_admin.main(["--account", "first-admin", "--name", "首管", "--password"])
    assert f"{MIN_PASSWORD_LENGTH}~{MAX_PASSWORD_LENGTH}" in str(excinfo.value)
    assert UserInfo.query.count() == 0

    # 21 位：旧实现的上限是 20（会拒绝强口令），现在必须接受
    monkeypatch.setattr(create_admin.getpass, "getpass", _answer("a" * 21))
    assert create_admin.main(["--account", "first-admin", "--name", "首管", "--password"]) == 0
    assert UserInfo.query.filter_by(account="first-admin").first().role == "admin"


def test_首管口令首尾空白被拒(_app_context, monkeypatch):
    """前端不再静默 trim 口令（第 13 轮复核整改 §2.2），"带空格的输入"由策略明确答复。"""
    import pytest

    monkeypatch.setattr(create_admin.getpass, "getpass", lambda prompt="": " password1234 ")

    with pytest.raises(SystemExit) as excinfo:
        create_admin.main(["--account", "first-admin", "--name", "首管", "--password"])

    assert "空白" in str(excinfo.value) or "空格" in str(excinfo.value)
    assert UserInfo.query.count() == 0
