"""登录限流、账号锁定与注册开关（第 13 轮整改，文档第十一节）。

三件事此前完全没有：`/api/login` 与 `/api/sign_in` 谁都能以任意频率调用，
公网部署下等于把在线爆破与批量建号两个入口都开着。用例分两层：

1. **路由层**：限流命中要返回 429（带 retry_after），注册开关关掉要返回 403；
2. **计数语义**：只有连续失败才锁定、成功即清零、停用账号即便口令正确也不放行。

阈值通过环境变量注入（`login_guard._settings()` 每次现读），因此用例不必改代码常量。

第 13 轮复核后新增一组**时钟口径**用例（见下面 "落库时间" 一节）：限流状态是落库的，
落库的时间必须是 UTC epoch 而不是 `time.monotonic()`——后者是"本机自启动以来的秒数"，
机器重启就换基准，会让账号被凭空锁住几十万秒、或者让锁定提前失效。
"""

from __future__ import annotations

import time

import login_guard
from sqlalchemy import text


def _code(response) -> int:
    """取业务码。

    登录/注册失败的既有契约是 **HTTP 200 + body.code 403**（前端按 `code` 分支），
    所以"失败"类断言要读 body；而 429 是新引入的状态，用 HTTP 状态码表达。
    """
    return response.get_json()["code"]


def _row(scope: str, key: str):
    """直接读限流表原始行：`(failures, window_start_epoch, locked_until_epoch, updated_at_epoch)`。"""
    from models import db

    return db.session.execute(
        text("SELECT failures, window_start_epoch, locked_until_epoch, updated_at_epoch "
             "FROM login_attempts WHERE scope = :scope AND key = :key"),
        {"scope": scope, "key": key},
    ).fetchone()


def _columns() -> set:
    from models import db

    return {row[1] for row in db.session.execute(
        text("PRAGMA table_info(login_attempts)")).fetchall()}


def test_同_ip_超过阈值后返回_429(client, make_user, monkeypatch):
    """一台机器对所有账号撒网：按来源 IP 限速。"""
    monkeypatch.setenv("LOGIN_RATE_LIMIT_PER_MINUTE", "3")
    make_user("someone", "viewer", password="correct-password-1")

    for _ in range(3):
        assert _code(client.post("/api/login", json={"account": "someone",
                                                     "password": "wrong-password"})) == 403

    limited = client.post("/api/login", json={"account": "someone",
                                              "password": "wrong-password"})

    assert limited.status_code == 429
    assert limited.get_json()["code"] == 429
    assert limited.get_json()["retry_after"] >= 1
    assert limited.headers.get("Retry-After")
    # 即便口令正确也被限流拦住：限流是入口门，不是口令检查的附属品
    assert client.post("/api/login", json={"account": "someone",
                                           "password": "correct-password-1"}).status_code == 429


def test_同账号连续失败达到阈值后锁定(client, make_user, monkeypatch):
    """盯着一个账号慢慢试：连续失败 N 次即锁定。"""
    monkeypatch.setenv("LOGIN_FAILURE_LOCK_THRESHOLD", "2")
    monkeypatch.setenv("LOGIN_RATE_LIMIT_PER_MINUTE", "100")
    make_user("someone", "viewer", password="correct-password-1")

    for _ in range(2):
        assert _code(client.post("/api/login", json={"account": "someone",
                                                     "password": "wrong-password"})) == 403

    limited = client.post("/api/login", json={"account": "someone",
                                              "password": "correct-password-1"})

    assert limited.status_code == 429
    assert limited.get_json()["retry_after"] >= 1


def test_登录成功清零计数(client, make_user, monkeypatch):
    """试错几次后成功登录的用户不该带着计数继续用——下一次手误就锁住了。"""
    monkeypatch.setenv("LOGIN_FAILURE_LOCK_THRESHOLD", "3")
    monkeypatch.setenv("LOGIN_RATE_LIMIT_PER_MINUTE", "100")
    make_user("someone", "viewer", password="correct-password-1")

    for _ in range(2):
        client.post("/api/login", json={"account": "someone", "password": "wrong-password"})

    assert client.post("/api/login", json={"account": "someone",
                                           "password": "correct-password-1"}
                       ).get_json()["code"] == 200

    # 计数已清零：再错两次仍不会被锁（阈值 3）
    for _ in range(2):
        assert _code(client.post("/api/login", json={"account": "someone",
                                                     "password": "wrong-password"})) == 403
    assert client.post("/api/login", json={"account": "someone",
                                           "password": "wrong-password"}).get_json()["code"] == 403


def test_停用账号即便口令正确也不能登录(client, make_user, monkeypatch):
    """封号必须立刻生效，否则"停用"只是界面上的一句话。"""
    monkeypatch.setenv("LOGIN_RATE_LIMIT_PER_MINUTE", "100")
    user = make_user("banned", "viewer", password="correct-password-1")
    from db_utils import DbUtil

    DbUtil.set_user_disabled(user.id, True)

    response = client.post("/api/login", json={"account": "banned",
                                               "password": "correct-password-1"})

    assert response.get_json()["code"] == 403


def test_注册开关关闭后返回_403(client, monkeypatch):
    monkeypatch.setenv("ALLOW_SELF_REGISTRATION", "false")

    response = client.post("/api/sign_in", json={"account": "newbie", "name": "新人",
                                                 "password": "pw-12345678"})

    assert response.status_code == 403
    assert "关闭自助注册" in response.get_json()["msg"]


def test_注册开关默认开启(client):
    """默认值必须是"和改造前一样"——一个没配的开关不该把注册入口关掉。"""
    assert login_guard.registration_allowed() is True

    response = client.post("/api/sign_in", json={"account": "newbie", "name": "新人",
                                                 "password": "pw-12345678"})

    assert response.get_json()["code"] == 200


def test_注册同样受限流保护(client, monkeypatch):
    """注册是一个比爆破更直接的入口：不设限就等于允许批量建号。"""
    monkeypatch.setenv("LOGIN_RATE_LIMIT_PER_MINUTE", "2")

    for i in range(2):
        assert client.post("/api/sign_in", json={"account": f"newbie-{i}", "name": "新人",
                                                 "password": "pw-12345678"}
                           ).get_json()["code"] == 200

    assert client.post("/api/sign_in", json={"account": "newbie-x", "name": "新人",
                                             "password": "pw-12345678"}).status_code == 429


def test_限流的来源地址不采信_X_Forwarded_For(client, make_user, monkeypatch):
    """XFF 是客户端自己能写的头；采信它等于把限流交给攻击者决定。"""
    monkeypatch.setenv("LOGIN_RATE_LIMIT_PER_MINUTE", "1")
    make_user("someone", "viewer", password="correct-password-1")

    assert _code(client.post("/api/login", json={"account": "someone", "password": "wrong"},
                             headers={"X-Forwarded-For": "1.1.1.1"})) == 403
    # 换一个伪造的 XFF 仍然被拦：计数按真实来源地址累计
    assert client.post("/api/login", json={"account": "someone", "password": "wrong"},
                       headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 429


def test_显式信任反代时才按_XFF_计数(client, make_user, monkeypatch):
    """前面的 nginx 是可信的场合（部署文档里的推荐拓扑）需要按真实客户端计数，
    否则所有请求都被算到 nginx 这一个来源上，一个人就能把全站锁住。"""
    monkeypatch.setenv("LOGIN_RATE_LIMIT_PER_MINUTE", "1")
    monkeypatch.setenv("LOGIN_TRUST_FORWARDED_FOR", "true")
    make_user("someone", "viewer", password="correct-password-1")

    assert _code(client.post("/api/login", json={"account": "someone", "password": "wrong"},
                             headers={"X-Forwarded-For": "1.1.1.1"})) == 403
    assert _code(client.post("/api/login", json={"account": "someone", "password": "wrong"},
                             headers={"X-Forwarded-For": "2.2.2.2"})) == 403


def test_口令强度下限生效于注册与改密码(client):
    """注册路径也要过强度检查：只在改密码时管长度等于"弱口令一次设好就永远合规"。"""
    short = client.post("/api/sign_in", json={"account": "newbie", "name": "新人",
                                              "password": "short"})

    assert short.get_json()["code"] == 400
    assert "长度" in short.get_json()["msg"]


# ------------------------------------------------------- 落库时间（第 13 轮复核整改）
#
# 限流状态是**落库**的，所以它用的时钟必须跨进程、跨重启可比。下面这组用例钉住
# "落库时间 = UTC epoch"这一条，以及由它推出的两个方向：重启后不误锁、到期能放行。


def test_落库时间接近当前_epoch(client, make_user, monkeypatch):
    """monotonic 与 epoch 相差好几个数量级（开机几天是十万量级，epoch 是十亿量级），
    所以"落库值接近 time.time()"这一条就足以把写回 monotonic 的改法直接判红。"""
    monkeypatch.setenv("LOGIN_RATE_LIMIT_PER_MINUTE", "100")
    make_user("someone", "viewer", password="correct-password-1")

    client.post("/api/login", json={"account": "someone", "password": "wrong-password"})

    row = _row("account", "someone")
    assert row is not None
    now = time.time()
    assert abs(float(row[1]) - now) < 5      # window_start_epoch
    assert abs(float(row[3]) - now) < 5      # updated_at_epoch
    assert row[2] is None                    # 阈值内不该有锁定


def test_机器重启换掉_monotonic_基准后锁定仍按配置时长生效(client, make_user, monkeypatch):
    """这是旧实现的具体故障：重启后 `monotonic=30` 与库里 `locked_until=432900` 相比，
    会得出"还要锁 432870 秒"。把 monotonic 打桩成刚开机的量级，剩余锁定时间必须不变。"""
    monkeypatch.setenv("LOGIN_FAILURE_LOCK_THRESHOLD", "1")
    monkeypatch.setenv("LOGIN_LOCK_SECONDS", "900")
    monkeypatch.setenv("LOGIN_RATE_LIMIT_PER_MINUTE", "100")
    make_user("someone", "viewer", password="correct-password-1")

    assert _code(client.post("/api/login", json={"account": "someone",
                                                 "password": "wrong-password"})) == 403

    # 模拟机器重启：monotonic 回到"刚开机"的量级
    monkeypatch.setattr(login_guard.time, "monotonic", lambda: 30.0)

    allowed, wait = login_guard.check("account", "someone")

    assert allowed is False
    assert 890 <= wait <= 900


def test_旧_monotonic_数据不会造成超长锁定(client, monkeypatch):
    """滚动升级期间旧进程可能往新列里写一行 monotonic 时间戳。

    这类值远小于当前 epoch，会被当成"早已过期"而放行——可以接受（最坏是提前解锁一次），
    绝不能反过来锁几十万秒。
    """
    monkeypatch.setenv("LOGIN_FAILURE_LOCK_THRESHOLD", "5")
    monkeypatch.setenv("LOGIN_LOCK_SECONDS", "900")
    from models import db

    db.session.execute(text(
        "INSERT INTO login_attempts (scope, key, failures, window_start_epoch, "
        "locked_until_epoch, updated_at_epoch) VALUES "
        "('account', 'someone', 5, 432000.0, 432900.0, 432000.0)"))
    db.session.commit()

    allowed, wait = login_guard.check("account", "someone")

    assert allowed is True
    assert wait == 0


def test_锁定到期后自动放行(client, monkeypatch):
    """锁定是"一段时间内不许再试"，不是"永久封禁"——到期必须能重新登录。"""
    monkeypatch.setenv("LOGIN_FAILURE_LOCK_THRESHOLD", "1")
    monkeypatch.setenv("LOGIN_LOCK_SECONDS", "900")

    login_guard.record_failure("account", "someone", now=time.time() - 1800)

    allowed, wait = login_guard.check("account", "someone")

    assert allowed is True
    assert wait == 0


def test_远期异常时间戳被丢弃(client, monkeypatch):
    """比 `now + lock_seconds` 还远的锁定只可能来自时钟跳变或人工写库。

    照单全收会让账号无端等上很久，因此按"没有可用状态"处理，并清掉这一行。
    """
    monkeypatch.setenv("LOGIN_LOCK_SECONDS", "900")
    from models import db

    db.session.execute(text(
        "INSERT INTO login_attempts (scope, key, failures, window_start_epoch, "
        "locked_until_epoch, updated_at_epoch) VALUES "
        "('account', 'someone', 5, :now, :far, :now)"),
        {"now": time.time(), "far": time.time() + 10 ** 6})
    db.session.commit()

    allowed, wait = login_guard.check("account", "someone")

    assert allowed is True
    assert wait == 0
    assert _row("account", "someone") is None


def test_IP_窗口与账号窗口各按自己的口径(client, monkeypatch):
    """两把尺子的窗口不是同一个：IP 固定 60 秒（"每分钟 N 次"的定义），
    账号取 LOGIN_FAILURE_WINDOW_SECONDS。把它们写成同一个值会让其中一边失效。"""
    monkeypatch.setenv("LOGIN_RATE_LIMIT_PER_MINUTE", "2")
    monkeypatch.setenv("LOGIN_FAILURE_LOCK_THRESHOLD", "3")
    monkeypatch.setenv("LOGIN_FAILURE_WINDOW_SECONDS", "900")
    import login_guard
    from models import db

    base = time.time()

    # IP：两次尝试把预算用光，第 61 秒滚动到新窗口即放行（而不是等 900 秒）
    for _ in range(2):
        login_guard.record_attempt("ip", "1.1.1.1", now=base)
    assert login_guard.check("ip", "1.1.1.1", now=base)[0] is False
    assert login_guard.check("ip", "1.1.1.1", now=base + 61)[0] is True

    # 账号：窗口内连续失败才累积；跨过 900 秒窗口后计数重新从 1 开始
    for _ in range(2):
        login_guard.record_failure("account", "someone", now=base)
    login_guard.record_failure("account", "someone", now=base + 901)
    db.session.commit()

    assert _row("account", "someone")[0] == 1


def test_旧口径表在升级时整表作废(client):
    """旧表（monotonic 列名）留着的都是换不了算的时间值，迁移方式是整表丢弃。

    只按列名判断：新表被误删会表现为"限流突然不生效"，比"少拦几次"更危险，
    所以判据必须来自 PRAGMA 而不是猜测。
    """
    from models import db

    db.session.execute(text("DROP TABLE IF EXISTS login_attempts"))
    db.session.execute(text(
        "CREATE TABLE login_attempts ("
        " scope TEXT NOT NULL, key TEXT NOT NULL, failures INTEGER NOT NULL DEFAULT 0,"
        " window_start REAL NOT NULL, locked_until REAL, updated_at REAL NOT NULL,"
        " PRIMARY KEY (scope, key))"))
    db.session.execute(text(
        "INSERT INTO login_attempts (scope, key, failures, window_start, locked_until, "
        "updated_at) VALUES ('account', 'someone', 5, 432000.0, 432900.0, 432000.0)"))
    db.session.commit()

    login_guard.reset_state_for_tests()   # 让本进程重新走一次建表
    login_guard.ensure_table()

    assert {"window_start_epoch", "locked_until_epoch", "updated_at_epoch"} <= _columns()
    assert _row("account", "someone") is None
