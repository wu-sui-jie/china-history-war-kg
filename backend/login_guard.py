"""登录限流与账号锁定（第 13 轮整改，文档第十一节）。

## 为什么需要

`/api/login` 与 `/api/sign_in` 此前没有任何节流：一个脚本可以对某个账号每秒试上千次密码，
字典攻击在日志里只表现为一串"用户名或密码错误"。限流不是要把攻击挡住（那是强口令的职责），
而是**把在线爆破的速率压到不划算**，并让这类尝试在日志里看得见。

## 两把尺子，分别管两种攻击

| 维度 | 阈值 | 计数口径 | 挡的是什么 |
| --- | --- | --- | --- |
| 同 IP | `LOGIN_RATE_LIMIT_PER_MINUTE` 次/分钟 | **每次尝试**（成功也算） | 一台机器对所有账号撒网、批量建号 |
| 同账号 | 连续失败 `LOGIN_FAILURE_LOCK_THRESHOLD` 次，锁定 `LOGIN_LOCK_SECONDS` | 只计**失败**，成功清零 | 盯着一个账号慢慢试 |

IP 维度按"每次尝试"而不是"每次失败"计数（文档的原话是"同 IP 登录限制 10 次/分钟"）：
只计失败的话，一个**每次都成功**的刷量脚本（比如反复建号）完全不消耗预算，
而那正是最该被限住的用法。账号维度则相反——只有连续失败才锁定，
否则任何一次正常登录后的手误都在往锁定阈值上走。

只按 IP 限流挡不住分布式尝试；只按账号锁定则等于给攻击者一个"把别人锁掉"的 DoS 手段。
两个一起用，锁定窗口取短（默认 15 分钟），把 DoS 的代价压到可接受。

## 为什么落在 SQLite 而不是进程内字典

锁定必须跨进程重启有效，否则攻击者只要触发一次重启（或等运维部署）就能把
失败计数清零。单机部署下 SQLite 已经是主存储，再开一张小表比引入 Redis 便宜得多；
多实例部署时这张表会退化成"每实例各自计数"，届时按文档换成 Redis（第三阶段）。

## 计数口径

- **IP 维度**：每次尝试 +1（成功也 +1），按 60 秒窗口滚动；
- **账号维度**：只计失败、成功即清零，窗口取 `LOGIN_FAILURE_WINDOW_SECONDS`。

IP 维度的窗口固定 60 秒——"每分钟 N 次"就是它的定义，写死在代码里而不是做成配置：
一个"每分钟 90 秒窗口"的配置项只会让人把语义弄混。

## 时钟口径：落库一律用 UTC epoch（第 13 轮复核后整改）

第一版把 `time.monotonic()` 写进了数据库，这是错的。`monotonic()` 是"本机自启动以来
经过的秒数"，只在**同一次开机**内可比：

    开机 5 天后        monotonic = 432000，写下的锁定截止 = 432900
    机器重启后         monotonic = 30

重启后服务比较 `30 < 432900`，判定"还没到解锁时间"，于是账号被凭空锁住约 5 天；
反向情况同样存在——运行很久的机器读到一个刚刚重启过的旧值，会把有效锁定当成早已过期，
限流直接失效。"重启一次就换一套时间基准"的计数不该落库。

现在落库的时间**一律是 `time.time()`（UTC epoch 秒）**，字段名带上 `_epoch` 后缀把单位
写进结构里，注释与列名同时约束后来者不要把 `monotonic()` 再写回来
（`monotonic()` 只适合进程内短耗时，例如单次请求计时，那类值从不落库）。

**旧数据不能与新口径混用**：升级时直接 `DROP TABLE login_attempts`（见 `_drop_legacy_table`）。
这不是业务数据，是"最近谁失败过几次"的临时安全状态，清空的最坏后果是锁定期提前放行一次；
相比之下把 monotonic 值与 epoch 值混在一列里，会让两个方向的错误都变成随机出现、
且只能靠读代码才能解释的疑难问题。
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from sqlalchemy import text

import local_settings
from logging_util import get_logger

logger = get_logger(__name__)

# 表结构：一行 = 一个 (维度, 标识) 的当前计数状态。
#
# 三个时间列都是 **UTC epoch 秒**（`time.time()`），不是 `time.monotonic()`——
# 列名里的 `_epoch` 是刻意的，它让"这个数是绝对时刻还是开机以来的秒数"在读取处
# 一眼可见（原因见模块文档的"时钟口径"一节）。
_DDL = """
CREATE TABLE IF NOT EXISTS login_attempts (
  scope              TEXT NOT NULL,   -- 'ip' / 'account'
  key                TEXT NOT NULL,
  failures           INTEGER NOT NULL DEFAULT 0,
  window_start_epoch REAL NOT NULL,   -- 当前窗口起点（UTC epoch 秒，不随进程/机器重启改变）
  locked_until_epoch REAL,            -- 锁定截止（UTC epoch 秒）；NULL = 未锁定
  updated_at_epoch   REAL NOT NULL,
  PRIMARY KEY (scope, key)
)
"""

# 第一版用过的列名（值取自 monotonic 时钟）。存在这些列 = 表是旧口径建的，整表作废：
# 两种时间基准混在一列里无法逐行区分来源，清空是唯一干净的迁移方式。
_LEGACY_COLUMNS = ("window_start", "locked_until", "updated_at")

# 判定"这个时间戳不可能是本进程写下的 epoch 值"的容差（秒）。
# 合法写入永远满足 `window_start_epoch <= now` 且 `locked_until_epoch <= now + lock_seconds`，
# 留出 5 分钟余量吸收两次读之间的正常流逝与轻微时钟回拨。
_CLOCK_SLACK_SECONDS = 300.0

# 建表只做一次（本进程内），避免每次登录都跑一遍 DDL
_ddl_done = threading.Event()
_ddl_lock = threading.Lock()

Scope = str


def _settings() -> dict:
    """从配置读阈值。非法值一律回落到默认值而不是报错——
    一个写错的限流参数不该让整套登录功能起不来（那比限流失效更糟）。"""

    def _int(name: str, default: int, low: int = 1) -> int:
        raw = local_settings.get(name, "")
        try:
            value = int(raw) if raw else default
        except (TypeError, ValueError):
            return default
        return value if value >= low else default

    return {
        "ip_per_minute": _int("LOGIN_RATE_LIMIT_PER_MINUTE", 10),
        "lock_threshold": _int("LOGIN_FAILURE_LOCK_THRESHOLD", 5),
        "lock_seconds": _int("LOGIN_LOCK_SECONDS", 900),
        "window_seconds": _int("LOGIN_FAILURE_WINDOW_SECONDS", 900),
    }


def _columns(conn) -> set:
    """表当前的列名集合；表不存在时返回空集合（PRAGMA 对不存在的表不报错）。"""
    return {row[1] for row in conn.execute(text("PRAGMA table_info(login_attempts)")).fetchall()}


def _drop_legacy_table(conn) -> bool:
    """旧口径（monotonic）的限流表整表作废，返回是否真的删了。

    只删**确认**带旧列名的表：新表被误删会让人误以为"限流没生效"，
    而误删的唯一判据来自 PRAGMA，不是猜测。
    """
    existing = _columns(conn)
    if not existing or not (set(_LEGACY_COLUMNS) & existing):
        return False
    conn.execute(text("DROP TABLE login_attempts"))
    logger.warning("登录限流表使用旧口径（monotonic 时间）建表，已整表作废重建；"
                   "期间所有计数与锁定重置（限流数据是临时安全状态，不是业务数据）")
    return True


def _ensure_table() -> None:
    if _ddl_done.is_set():
        return
    with _ddl_lock:
        if _ddl_done.is_set():
            return
        from models import db

        conn = db.session.connection()
        _drop_legacy_table(conn)
        conn.execute(text(_DDL))
        db.session.commit()
        _ddl_done.set()


def ensure_table() -> None:
    """对外暴露的建表入口（启动迁移与独立脚本用）。"""
    _ensure_table()


def _row(conn, scope: str, key: str):
    return conn.execute(
        text("SELECT failures, window_start_epoch, locked_until_epoch FROM login_attempts "
             "WHERE scope = :scope AND key = :key"),
        {"scope": scope, "key": key},
    ).fetchone()


def _delete(conn, scope: str, key: str) -> None:
    conn.execute(text("DELETE FROM login_attempts WHERE scope = :scope AND key = :key"),
                 {"scope": scope, "key": key})


def read_state(conn, scope: str, key: str, *, now: float, lock_seconds: int):
    """读一行并归一化成 `(failures, window_start, locked_until)`；无有效状态时返回 None。

    **带一次可信度检查**：列名与迁移保证了新写入的一定是 epoch，但滚动升级期间
    （旧进程还没退、新进程已起来）仍可能有旧进程写下的 monotonic 值落进这一列。
    这类值现在的效果是"提前放行"（远小于当前 epoch，被当成早已过期），不会造成
    超长锁定；反方向的异常值——比 `now + lock_seconds` 还远的"未来"时间戳——
    只可能来自时钟跳变或人为写库，把它当成有效锁定会让人无端等上很久。
    遇到就丢掉这一行的状态并记一条告警，让下一次尝试回到"从未失败"的起点。
    """
    row = _row(conn, scope, key)
    if row is None:
        return None
    failures = int(row[0])
    window_start = float(row[1])
    locked_until = None if row[2] is None else float(row[2])

    if window_start > now + _CLOCK_SLACK_SECONDS:
        logger.warning("丢弃疑似异常时间戳的限流状态：维度=%s 标识=%s "
                       "window_start=%s 晚于当前 epoch=%s，按未失败处理",
                       scope, key, window_start, now)
        _delete(conn, scope, key)
        return None
    if locked_until is not None and locked_until > now + lock_seconds + _CLOCK_SLACK_SECONDS:
        logger.warning("丢弃疑似异常时间戳的锁定状态：维度=%s 标识=%s locked_until=%s "
                       "超出当前 epoch + 配置锁定 %s 秒，按未锁定处理",
                       scope, key, locked_until, lock_seconds)
        _delete(conn, scope, key)
        return None
    return failures, window_start, locked_until


def _write(conn, scope: str, key: str, failures: int, window_start: float,
           locked_until: Optional[float], now: float) -> None:
    conn.execute(
        text("INSERT INTO login_attempts (scope, key, failures, window_start_epoch, "
             "locked_until_epoch, updated_at_epoch) VALUES (:scope, :key, :failures, "
             ":window_start, :locked_until, :now) "
             "ON CONFLICT(scope, key) DO UPDATE SET failures = :failures, "
             "window_start_epoch = :window_start, locked_until_epoch = :locked_until, "
             "updated_at_epoch = :now"),
        {"scope": scope, "key": key, "failures": failures, "window_start": window_start,
         "locked_until": locked_until, "now": now},
    )


def check(scope: Scope, key: str, *, now: Optional[float] = None) -> tuple[bool, int]:
    """是否允许这次尝试。返回 `(是否允许, 建议等待秒数)`。

    被锁定时等待秒数取"锁定剩余"，被 IP 限流时取"到下一分钟窗口的剩余"——
    调用方直接把这个数字回给客户端即可。

    `now` 是可注入的 **UTC epoch 秒**（默认 `time.time()`），用例据此构造
    "锁定已过期 / 机器换过一次时钟基准"这类确定场景，不必真的等待。
    """
    if not key:
        return True, 0
    _ensure_table()
    from models import db

    settings = _settings()
    stamp = time.time() if now is None else now
    try:
        conn = db.session.connection()
        state = read_state(conn, scope, key, now=stamp, lock_seconds=settings["lock_seconds"])
        # 状态被判为异常时上面已把它删掉，删除要落库，否则下一个请求仍读到同一行
        db.session.commit()
    except Exception as exc:  # noqa: BLE001 - 限流表读不到时宁可放行也不能把登录全锁死
        db.session.rollback()
        logger.error(f"⚠️ 登录限流表读取失败，本次不拦截：{exc}")
        return True, 0

    if state is None:
        return True, 0

    failures, window_start, locked_until = state
    if locked_until is not None and stamp < locked_until:
        return False, max(1, int(locked_until - stamp))

    if scope == "ip":
        if stamp - window_start < 60.0 and failures >= settings["ip_per_minute"]:
            return False, max(1, int(60.0 - (stamp - window_start)))
    return True, 0


def record_attempt(scope: Scope, key: str, *, now: Optional[float] = None) -> None:
    """给 `scope` 记一次尝试（不区分成败，也不触发账号锁定）。

    IP 维度用它：限流的对象是"请求频率"本身，而不是"猜错了几次"。
    账号维度**不要**用它——那里只计失败（见 `record_failure`）。
    """
    _bump(scope, key, now=now, lockable=False)


def record_failure(scope: Scope, key: str, *, now: Optional[float] = None) -> None:
    """记一次失败；账号维度达到阈值时写入锁定截止时间。

    窗口外的旧计数会被丢弃（`window_start_epoch` 重置）——"连续失败"才触发锁定，
    分散在几个小时里的零星失败不该把正常用户锁掉。
    """
    _bump(scope, key, now=now, lockable=True)


def _bump(scope: Scope, key: str, *, now: Optional[float], lockable: bool) -> None:
    """两个维度共用的计数写入。

    `lockable` 只对账号维度有效：IP 维度不写 locked_until——按来源地址"锁一段时间"
    会连带把同一个出口 NAT 后面的所有人挡在外面（公司、学校出口都是这种情况），
    而频率限制本身已经够了。

    `now` 是 UTC epoch 秒（默认 `time.time()`），写入的也是同一个基准——
    两者必须同源，否则"窗口是否已过"的判断会整体偏移（见模块的"时钟口径"）。
    """
    if not key:
        return
    _ensure_table()
    from models import db

    settings = _settings()
    window_seconds = 60.0 if scope == "ip" else float(settings["window_seconds"])
    stamp = time.time() if now is None else now
    try:
        conn = db.session.connection()
        state = read_state(conn, scope, key, now=stamp, lock_seconds=settings["lock_seconds"])
        if state is None:
            failures, window_start, locked_until = 1, stamp, None
        else:
            failures, window_start, locked_until = state
            if stamp - window_start > window_seconds:
                # 窗口已过：重新起一个窗口，而不是让计数无限累积
                failures, window_start, locked_until = 1, stamp, None
            else:
                failures += 1
        if lockable and scope == "account" and failures >= settings["lock_threshold"]:
            locked_until = stamp + settings["lock_seconds"]
            logger.warning(f"⚠️ 账号 {key} 连续登录失败 {failures} 次，已锁定 "
                           f"{settings['lock_seconds']} 秒")
        _write(conn, scope, key, failures, window_start, locked_until, stamp)
        db.session.commit()
    except Exception as exc:  # noqa: BLE001 - 记账失败不能把登录流程带崩
        db.session.rollback()
        logger.error(f"⚠️ 登录失败计数写入失败：{exc}")


def record_success(scope: Scope, key: str) -> None:
    """账号维度登录成功：清掉计数与锁定。

    必须清，否则"试错 4 次后成功登录"的用户会带着 4 次计数继续用——下一次
    手误就把自己锁住了。**IP 维度不要清**：那会让攻击者"用一个自己的合法账号
    登录一次"就重置整台的预算，限流等于没有。
    """
    if not key:
        return
    _ensure_table()
    from models import db

    try:
        conn = db.session.connection()
        _delete(conn, scope, key)
        db.session.commit()
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        logger.error(f"⚠️ 登录计数清零失败：{exc}")


def client_ip(request) -> str:
    """取来源 IP。

    **默认不看 X-Forwarded-For**：那是客户端可以自己写的头，直接采信等于把限流
    交给攻击者决定（伪造一堆 IP 就绕过了）。只有当部署方显式声明"前面有可信反代"
    （`LOGIN_TRUST_FORWARDED_FOR=1`）时才取 XFF 的第一段。
    """
    if local_settings.get("LOGIN_TRUST_FORWARDED_FOR", "") in ("1", "true", "True"):
        forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        if forwarded:
            return forwarded
    return request.remote_addr or "unknown"


def registration_allowed() -> bool:
    """是否允许自由注册。

    默认 **True**（保持既有行为，部署后不会因为一个没配的开关把注册入口关掉）。
    公网部署前必须显式设 `ALLOW_SELF_REGISTRATION=false`，改为由管理员建号
    （`python create_admin.py`）。
    """
    raw = (local_settings.get("ALLOW_SELF_REGISTRATION", "true") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def reset_state_for_tests() -> None:
    """清空计数（用例之间互相隔离用）。"""
    _ddl_done.clear()
