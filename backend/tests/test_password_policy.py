"""口令策略的唯一口径。

数值一旦分成两套（例如注册/改密码 10–64，而首个管理员命令 6–20），后果不是"数值不美观"，
而是**权限最高的账号允许最弱的密码**，同时 21–64 位的强口令反而设不上。

本文件钉四件事：

1. 边界：9 位拒绝、10 位与 64 位接受、65 位拒绝；
2. 首尾空白：明确报错（而不是静默去掉）；
3. **登录路径不查强度**：策略收紧之前建的短口令账号必须还能登录；
4. 首管命令与注册/改密码用的是**同一个函数**（不是"看起来一样的两份实现"）。
"""

from __future__ import annotations

import pytest

import password_policy
from password_policy import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH, password_problem


@pytest.mark.parametrize("password,ok", [
    ("a" * 9, False),
    ("a" * 10, True),
    ("a" * 20, True),
    ("a" * MAX_PASSWORD_LENGTH, True),
    ("a" * (MAX_PASSWORD_LENGTH + 1), False),
    ("", False),
])
def test_长度边界(password, ok):
    problem = password_problem(password)
    assert (problem is None) is ok
    if not ok:
        # 文案里必须给出实际长度，否则用户不知道自己填了多少位
        assert f"{len(password)} 位" in problem


def test_首尾空白报错而不是被静默去掉():
    """不能 `password.trim()`——那是"静默改用户输入"，本模块的存在就是为了取代它。"""
    for password in (" password123 ", "\tpassword123", "password123\n"):
        problem = password_problem(password)
        assert problem is not None
        assert "空白" in problem or "空格" in problem


def test_中间的空格是允许的():
    """口令里的空格是合法字符（口令短语），只有首尾空白属于输入失误。"""
    assert password_problem("correct horse battery") is None


def test_不修改输入值():
    """策略只回答"合不合规"：规范化会让"用户输入"与"落库值"再次不一致。"""
    raw = " password 1234 "
    password_problem(raw)
    assert raw == " password 1234 ", "本模块不得就地改写调用方传进来的字符串"


def test_短口令的历史账号仍可登录(client, make_user):
    """长度策略的语义是"不许**设置**弱口令"，不是"不许用弱口令登录"。

    提高下限之前建的 6 位口令账号必须继续能登录，否则一次策略调整就把老用户全锁在
    门外——而他们没有任何自助恢复手段（改密码要先登录）。
    """
    from werkzeug.security import generate_password_hash

    make_user("legacy", "viewer", password="short")
    # 直接落一个"当年允许的短口令"哈希，绕开注册路径的策略检查
    from models import UserInfo, db

    user = UserInfo.query.filter_by(account="legacy").first()
    user.password = generate_password_hash("123456")
    db.session.commit()

    response = client.post("/api/login", json={"account": "legacy", "password": "123456"})

    assert response.get_json()["code"] == 200, "老账号的短口令必须还能登录"
    assert MIN_PASSWORD_LENGTH > 6, "本用例的前提是策略下限已高于 6"


def test_注册与改密码走的是同一个策略(client, make_user):
    """两条写路径都必须用 `password_problem`，不能各留一份实现。"""
    from db_utils import DbUtil

    short = client.post("/api/sign_in", json={"account": "newbie", "name": "新人",
                                             "password": "123456789"})
    assert short.get_json()["code"] == 400
    assert "10~64" in short.get_json()["msg"]

    user = make_user("someone", "viewer", password="old-password-1")
    result = DbUtil().change_password(user.id, "old-password-1", "123456789")
    assert result["code"] == 400
    assert "10~64" in result["msg"]

    # 首尾空白同样由同一条策略拦下（而不是前端 trim 掉）
    spaced = client.post("/api/sign_in", json={"account": "newbie2", "name": "新人",
                                              "password": " spaced1234 "})
    assert spaced.get_json()["code"] == 400
    assert "空白" in spaced.get_json()["msg"]


def test_首管命令与注册共用同一函数(monkeypatch):
    """钉住"同一份实现"而不是"两份看起来一样的实现"。"""
    import create_admin

    calls: list[str] = []

    def spy(password):
        calls.append(password)
        return password_policy.password_problem(password)

    monkeypatch.setattr(create_admin, "password_problem", spy)
    monkeypatch.setattr(create_admin.getpass, "getpass", lambda prompt="": "a" * 9)
    from models import db

    db.session.query(__import__("models").UserInfo).delete()
    db.session.commit()

    with pytest.raises(SystemExit):
        create_admin._read_password()

    assert calls == ["a" * 9], "首管命令必须调用统一策略函数"
