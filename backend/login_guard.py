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

## 计数必须是原子的（第 13 轮复核整改 §2.3）

第一版是"SELECT failures → Python 里 +1 → UPDATE"：两个并发请求同时读到 3，
然后都写回 4——实际发生两次失败，库里只加了一次。爆破脚本要的正是"用并发把计数做废"。
现在计数、窗口滚动与锁定判定全部放进**一条 UPSERT** 的 SQL 里
（`_UPSERT_ATTEMPT`）：`ON CONFLICT ... DO UPDATE` 的 SET 表达式读的是该行的旧值，
因此整条语句是一次原子读改写，不会丢计数。Python 侧只剩两个参数（now 与阈值）。

## 限流自身故障时不能静默失效（第 13 轮复核整改 §2.4）

限流表读写失败时，第一版"记一条日志、继续放行"：数据库锁冲突、表损坏或迁移失败都会让
爆破保护**无声地消失**，而登录看起来一切正常。现在由 `LOGIN_GUARD_FAILURE_MODE` 显式选择：

| 取值 | 行为 | 适用 |
| --- | --- | --- |
| `closed`（默认） | 无法判定就拒绝本次登录：抛 `LoginGuardUnavailable`，路由回 **503** | 生产 |
| `open` | 记 `ERROR` 日志后放行（保持改造前的可用性优先行为） | 开发/内网 |

默认取 `closed` 是有意的：这是一道安全闸门，"把限流表弄坏"不该成为一种绕过它的手段。
SQLite 的临时 `busy`/`locked` 属于**已知可恢复**的故障，会先重试几次（`_BUSY_RETRIES`），
重试仍失败才按上面的策略处置——否则一次短暂的写锁就能让全站登录 503。

`record_success` 是这条规则的一个例外，理由见它的文档字符串（登录已经成功，不能因为
"没清成计数"就把用户挡回去）。
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

# 限流自身故障时的策略（第 13 轮复核整改 §2.4）。取值含义见模块文档的同名小节。
FAILURE_OPEN = "open"
FAILURE_CLOSED = "closed"
FAILURE_MODES = (FAILURE_OPEN, FAILURE_CLOSED)

# SQLite 的 busy/locked 属于"别人的写事务还没结束"，重试几次通常就好；
# 这三个数字只影响"临时锁"的处置，真正的故障仍然按 FAILURE_MODE 走。
_BUSY_RETRIES = 3
_BUSY_SLEEP_SECONDS = 0.05

Scope = str


class LoginGuardUnavailable(RuntimeError):
    """限流状态不可用，且失败策略是 closed：调用方必须拒绝本次登录（HTTP 503）。

    单独定义一个异常类型而不是让函数返回"第三种状态"：`check()` 已经有
    `(是否允许, 等待秒数)` 两个返回值，再加一个"未知"会让每个调用点都要判断它，
    而漏判的表现是"静默放行"。异常不会被忽略（Python 里没有未检查异常），
    调用方必须显式处理或让它冒泡成 500。
    """


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

    # 失败策略：只认两个取值，拼错/留空一律取 closed（安全的那一边）。
    # 与阈值不同——阈值写错最坏是限流松一点，策略写错是"保护整个消失"，
    # 所以这里不做"非法值回落成默认"，而是回落到**默认里更严的那个**。
    mode = (local_settings.get("LOGIN_GUARD_FAILURE_MODE", "") or "").strip().lower()
    if mode not in FAILURE_MODES:
        mode = FAILURE_CLOSED

    return {
        "ip_per_minute": _int("LOGIN_RATE_LIMIT_PER_MINUTE", 10),
        "lock_threshold": _int("LOGIN_FAILURE_LOCK_THRESHOLD", 5),
        "lock_seconds": _int("LOGIN_LOCK_SECONDS", 900),
        "window_seconds": _int("LOGIN_FAILURE_WINDOW_SECONDS", 900),
        "failure_mode": mode,
    }


def failure_mode() -> str:
    """当前失败策略（`/api/health` 与排障用）。"""
    return _settings()["failure_mode"]


def is_busy_error(exc: Exception) -> bool:
    """是否是 SQLite 的"临时忙"（可以通过重试恢复），而不是真故障。"""
    text_of_error = str(exc).lower()
    return "database is locked" in text_of_error or "database is busy" in text_of_error


def _with_busy_retry(action, *, description: str):
    """跑一段会碰 SQLite 的逻辑；遇 busy 短暂重试，其它异常直接上抛。

    只对 busy/locked 重试：那表示"另一个写事务正在收尾"，等几十毫秒通常就好了；
    表结构不对、磁盘满这类错误重试多少次都一样，早点暴露出来更好。
    """
    last: Optional[Exception] = None
    for attempt in range(_BUSY_RETRIES):
        try:
            return action()
        except Exception as exc:  # noqa: BLE001 - 分类后再决定是重试还是上抛
            if not is_busy_error(exc):
                raise
            last = exc
            logger.warning("登录限流表忙（第 %s/%s 次尝试，%s）：%s",
                           attempt + 1, _BUSY_RETRIES, description, exc)
            time.sleep(_BUSY_SLEEP_SECONDS * (attempt + 1))
    assert last is not None
    raise last


def _unavailable(description: str, exc: Exception, mode: str) -> None:
    """按失败策略处置"限流不可用"：closed 抛异常，open 放行。"""
    if mode == FAILURE_CLOSED:
        logger.error("⚠️ 登录限流不可用（%s），按 LOGIN_GUARD_FAILURE_MODE=closed 拒绝本次登录：%s",
                     description, exc)
        raise LoginGuardUnavailable(f"限流状态不可用：{description}") from exc
    logger.error("⚠️ 登录限流不可用（%s），按 LOGIN_GUARD_FAILURE_MODE=open 放行本次登录：%s",
                 description, exc)


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


# 计数、窗口滚动与锁定判定在**一条语句**里完成（第 13 轮复核整改 §2.3）。
#
# 为什么必须这样：`ON CONFLICT ... DO UPDATE` 的 SET 表达式读的是该行的**旧值**，
# 因此 "读 → 判断 → 写" 三步被数据库当成一次原子操作；多个并发请求各自执行这条语句时
# 不会互相覆盖计数（第一版在 Python 里 +1 再写回，并发下会丢计数）。
#
# 三处 CASE 重复了同一个"新计数"表达式：SQL 的 SET 之间不能引用刚算出的新值。
# 重复而不是用 CTE：SQLite 的 UPSERT 不支持在 DO UPDATE 里 CTE 前置，
# 而这三行的语义一眼可读——它比"引入一层临时表"更容易在 review 里核准。
_UPSERT_ATTEMPT = """
INSERT INTO login_attempts
  (scope, key, failures, window_start_epoch, locked_until_epoch, updated_at_epoch)
VALUES
  (:scope, :key, 1, :now,
   CASE WHEN :lockable = 1 AND 1 >= :threshold THEN :now + :lock_seconds ELSE NULL END,
   :now)
ON CONFLICT(scope, key) DO UPDATE SET
  failures = CASE
    WHEN :now - login_attempts.window_start_epoch > :window THEN 1
    ELSE login_attempts.failures + 1
  END,
  window_start_epoch = CASE
    WHEN :now - login_attempts.window_start_epoch > :window THEN :now
    ELSE login_attempts.window_start_epoch
  END,
  locked_until_epoch = CASE
    WHEN :lockable = 1 AND (
      CASE
        WHEN :now - login_attempts.window_start_epoch > :window THEN 1
        ELSE login_attempts.failures + 1
      END
    ) >= :threshold THEN :now + :lock_seconds
    ELSE login_attempts.locked_until_epoch
  END,
  updated_at_epoch = :now
"""


def _write(conn, scope: str, key: str, *, now: float, window_seconds: float,
           threshold: int, lock_seconds: int, lockable: bool) -> None:
    """原子地记一次尝试（计数 +1、必要时滚动窗口、必要时写锁定）。"""
    conn.execute(text(_UPSERT_ATTEMPT), {
        "scope": scope, "key": key, "now": now, "window": window_seconds,
        "threshold": threshold, "lock_seconds": lock_seconds,
        "lockable": 1 if lockable else 0,
    })


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

    def _read():
        conn = db.session.connection()
        state = read_state(conn, scope, key, now=stamp, lock_seconds=settings["lock_seconds"])
        # 状态被判为异常时上面已把它删掉，删除要落库，否则下一个请求仍读到同一行
        db.session.commit()
        return state

    try:
        state = _with_busy_retry(_read, description=f"读取 {scope} 限流状态")
    except Exception as exc:  # noqa: BLE001 - 按失败策略处置（closed 抛异常，open 放行）
        db.session.rollback()
        _unavailable(f"读取 {scope} 限流状态失败", exc, settings["failure_mode"])
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
    # 只有账号维度可写锁定：IP 维度按来源地址"锁一段时间"会连带挡住同一出口 NAT
    # 后面的所有人（公司、学校出口都是这种情况），而频率限制本身已经够了。
    writable_lock = bool(lockable and scope == "account")

    def _bump_once():
        conn = db.session.connection()
        # 先做一次可信度检查（丢弃时钟跳变/人工写库留下的"未来"时间戳），
        # 再走原子写入。这一步只清理垃圾行，判定与计数都在下面的 SQL 里。
        state = read_state(conn, scope, key, now=stamp, lock_seconds=settings["lock_seconds"])
        before = int(state[0]) if state else 0
        _write(conn, scope, key, now=stamp, window_seconds=window_seconds,
               threshold=settings["lock_threshold"], lock_seconds=settings["lock_seconds"],
               lockable=writable_lock)
        db.session.commit()
        return before

    try:
        before = _with_busy_retry(_bump_once, description=f"记录 {scope} 尝试")
    except Exception as exc:  # noqa: BLE001 - 按失败策略处置（closed 抛异常，open 放行）
        db.session.rollback()
        _unavailable(f"记录 {scope} 尝试失败", exc, settings["failure_mode"])
        return

    # 锁定是"恰好触发阈值那一次"才值得记一条：每多一次失败都记会让日志变成刷屏，
    # 而真正需要事后定位的正是"什么时候开始锁的、锁了多久"。
    if writable_lock and before + 1 == settings["lock_threshold"]:
        logger.warning("⚠️ 账号 %s 连续登录失败 %s 次，已锁定 %s 秒",
                       key, before + 1, settings["lock_seconds"])


def record_success(scope: Scope, key: str) -> None:
    """账号维度登录成功：清掉计数与锁定。

    必须清，否则"试错 4 次后成功登录"的用户会带着 4 次计数继续用——下一次
    手误就把自己锁住了。**IP 维度不要清**：那会让攻击者"用一个自己的合法账号
    登录一次"就重置整台的预算，限流等于没有。

    **这是 `LOGIN_GUARD_FAILURE_MODE` 规则的一个例外：清零失败不拒绝登录。**
    此时用户的凭证**已经验证通过**，把一次成功登录改成 503 是把服务端的记账问题
    转嫁给用户，而收益只是"计数早一点归零"——下一次成功登录会再清一次。
    风险有上限：只有"清零一直失败 **且** 期间又连续失败到阈值"才会误锁，
    那时 503 会出现在真正的写失败路径上（`_bump`），排障入口不会消失。
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

    **默认不看任何转发头**：`X-Forwarded-For` / `X-Real-IP` 都是客户端可以自己写的，
    直接采信等于把限流交给攻击者决定（伪造一堆 IP 就绕过了）。只有当部署方显式声明
    "前面有可信反代"（`LOGIN_TRUST_FORWARDED_FOR=1`）时才采信，且**取哪一段是有讲究的**：

    第 14 轮审计 P1-3：这里原先取 XFF 的**第一段**，而 nginx 用的是
    `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for`——它的语义是
    "**客户端自带的值放在最前**，真实地址追加在后"。于是第一段正是攻击者可控的那一段：
    每次换一个伪造值就把 IP 维度的限流完全绕开（只剩账号维度的 5 次锁定，换账号即可）。
    思路（不信任客户端）是对的，取错了段。

    现在的顺序：
      1. `X-Real-IP`——nginx 用 `proxy_set_header X-Real-IP $remote_addr` 覆盖下发，
         客户端伪造的值会被 nginx 覆盖掉，是这条链路上最可信的那一个；
      2. 退到 XFF 的**最右段**——那是最近一跳（我们的 nginx）追加的真实地址，
         而最左段是别人自己写进来的；
      3. 都没有才用 TCP 对端地址。
    """
    if local_settings.get("LOGIN_TRUST_FORWARDED_FOR", "") in ("1", "true", "True"):
        real_ip = (request.headers.get("X-Real-IP") or "").strip()
        if real_ip:
            return real_ip
        chain = [part.strip() for part in
                 (request.headers.get("X-Forwarded-For") or "").split(",") if part.strip()]
        if chain:
            return chain[-1]
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
