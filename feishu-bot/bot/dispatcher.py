"""事件接入：去重、入队、worker 线程与技能执行（开发文档 5.1 / 第四节）。

三条硬约束决定了这个模块的形状：
1. **回调必须毫秒级返回**：SDK 回调跑在 ws 的 event loop 线程里，在里面调 RAG 会把
   ping/重连一起卡住，也会让飞书判定"超时未应答"而重投 → 回调只做"去重 + 入队"；
2. **至少一次投递**：飞书的重试必须靠幂等兜底，所以去重要落库（进程重启后依然有效）；
3. **单 worker 串行**：会话内消息必须按顺序处理；P0 单 worker 意味着所有会话一起排队
   （开发文档第四节已明确这是起步期的并发上限）。

本模块**不导入 lark-oapi**：事件对象按鸭子类型读取属性，
这样单元测试可以用 SimpleNamespace 构造事件，不需要装 SDK、也不需要 mock 网络。
"""

from __future__ import annotations

import hashlib
import json
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from bot.cards.builder import (NOT_TEXT_MESSAGE, SEND_FAILED_TEXT, attach_feedback_button,
                               build_degraded_card, build_notice_card,
                               build_placeholder_card)
from bot.db import Database, now_ts
from bot.session import SessionStore, session_key
from bot.skills.base import Reply, SkillContext, SkillRegistry

log = logging.getLogger(__name__)

# 任务类型
TASK_MESSAGE = "message"
TASK_CARD_ACTION = "card_action"

# 队列满时的提示文案。说明"稍后再试"而不是含糊的"出错了"：用户能据此决定自己的动作。
QUEUE_BUSY_TEXT = "当前提问较多，排队已满，请稍后再试。"

# ---- 事件状态机（第 13 轮整改）----
#
# 一个事件从收到到有结论，中间要经过"认领 → 入队 → 处理"三步，每一步都可能中途
# 掉队。把状态记下来是为了让"掉在哪一步"可查，而不是只留一个 received 计数。
STATUS_RECEIVED = "received"                # 已认领，尚未确认入队
STATUS_ACCEPTED = "accepted"                # 已入队，等 worker 取
STATUS_PROCESSING = "processing"            # worker 正在处理
STATUS_DONE = "done"                        # 处理完成
STATUS_REJECTED_BUSY = "rejected_busy"      # 队列满，本次未能入队
STATUS_FAILED = "failed"                    # 处理中抛异常

# 处于这些状态的事件"已经有结论"，飞书重投一律按重复丢弃：
# - accepted / processing / done：正常路径的三档；
# - failed：处理中抛异常。**故意不让它参与重投重试**——异常很可能发生在
#   "回答已经发出去"之后（PATCH 失败、落库失败），重投会给用户再发一条回答，
#   比静默丢掉一条更难解释。
#
# 不在表里的（received 半路卡住、rejected_busy）都表示"上一轮没做成"，
# 重投可以再认领一次——这正是"队列满时消息永久消失"的修法。
_CLAIMED_STATUSES = (STATUS_ACCEPTED, STATUS_PROCESSING, STATUS_DONE, STATUS_FAILED)

# 卡住多久算"上一轮没做成"（第 14 轮审计 P1-5）。
#
# `processing` 也在 _CLAIMED_STATUSES 里，于是**进程被强杀**（SIGKILL / OOM /
# 双重 Ctrl-C 的 os._exit(0) / 停机超时后退出）会永久留下一行 status='processing'：
# 飞书重投同一 event_id 会被当成重复丢掉，那条提问就此永久消失，用户那张
# "正在检索史料…"的占位卡在 24 小时 TTL 内也不会恢复。代码原先只给 `received`
# 留了恢复口（不在 _CLAIMED_STATUSES 里），`processing` 没有。
#
# 阈值取 10 分钟：一次问答的预算是 RAG_QUERY_TIMEOUT(25s) + 连接超时 + 渲染，
# 真实在途任务不会超过一两分钟。留得宽松是为了避免把"正在跑"的任务抢回来——
# 抢回来会让同一条提问被回答两次。
STUCK_EVENT_SECONDS = 600.0


@dataclass(frozen=True)
class Claim:
    """一次事件认领的凭据。

    认领与入队之间必须能"回退"（第 13 轮整改）：队列满时若认领留在库里，飞书重投
    同一 event_id 会撞上自己的去重记录被判成重复——事件已被标记处理，消息就此永久
    消失。因此认领要等到入队成功才由 `commit()` 坐实，入队失败由 `release()` 撤销。

    `window` 为 None 表示**落库档**（processed_events 里的一行）；否则是**内存窗口档**
    （卡片回调拿不到 event_id 时的退化去重，键只活 `window` 秒）。
    """

    key: str
    window: float | None = None

    @property
    def durable(self) -> bool:
        return self.window is None


def _durable_event_key(kind: str, payload: Any) -> str | None:
    """任务在 `processed_events` 里的键；退化档（无 event_id）返回 None。

    返回 None 的任务不写状态：内存窗口档的语义是"窗口内同一次点击的重投"，
    落库就等于把一次点击永久锁死，用户再点同一个按钮会被误判成重复。
    卡片回调用 `dedupe_key()` 当**内存**键，与这里返回 None 是同一件事的两面。
    """
    if kind == TASK_MESSAGE:
        # 与 on_message 的认领键必须逐字一致，否则状态更新会打到不存在的行上
        return payload.event_id or f"msg:{payload.message_id}"
    if kind == TASK_CARD_ACTION:
        return payload.event_id or None
    return None


@dataclass
class MessageEvent:
    """从 SDK 事件里提取出的最小字段集（开发文档 5.1 的字段表）。"""

    event_id: str
    message_id: str
    chat_id: str
    chat_type: str          # p2p / group
    open_id: str
    text: str
    message_type: str = "text"
    mentions: list[dict] = field(default_factory=list)
    raw_text: str = ""      # 未剥离 @ 的原文，仅用于诊断

    @property
    def is_group(self) -> bool:
        return self.chat_type == "group"

    @property
    def session_key(self) -> str:
        return session_key(self.open_id, self.chat_id)


@dataclass
class CardAction:
    """卡片按钮回调（card.action.trigger）。"""

    event_id: str
    open_id: str
    chat_id: str
    message_id: str          # 卡片所在消息（context.open_message_id）
    action: str              # value.action：ask / report_error
    value: dict = field(default_factory=dict)

    def dedupe_key(self) -> str:
        """缺少 event_id 时的退化去重键：动作身份（不含时间，时间窗在调用处加）。

        只用来压制"同一次点击的重投"，因此必须把按钮的全部区分信息纳入：
        消息 + 操作人 + 组件 tag + value 摘要——连点两次同一个按钮会被窗口内误杀，
        这正是窗口取得极小（默认 2s）的原因。
        """
        digest = hashlib.sha1(
            json.dumps(self.value, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        return f"card:{self.message_id}:{self.open_id}:{self.action}:{digest}"


# ---- 事件解析（鸭子类型，不依赖 SDK）----


def _attr(obj: Any, *path: str, default: Any = None) -> Any:
    """按属性路径安全取值：_attr(event, "event", "message", "chat_id")。"""
    cur = obj
    for name in path:
        if cur is None:
            return default
        cur = getattr(cur, name, None)
    return default if cur is None else cur


def _parse_content_text(content: Any) -> str:
    """message.content 是 JSON 字符串（如 '{"text": "你好"}'）。"""
    if not content:
        return ""
    if isinstance(content, dict):
        return str(content.get("text") or "")
    try:
        parsed = json.loads(content)
    except Exception:  # noqa: BLE001 - 非 JSON 内容按空文本处理，走"暂只支持文字提问"
        return ""
    return str(parsed.get("text") or "") if isinstance(parsed, dict) else ""


def _extract_mentions(message: Any) -> list[dict]:
    """mentions 元素为 MentionEvent：key / name / id.open_id（id 是对象，不是字符串）。

    同时兼容 dict 形态（测试与事件回放帧用），避免解析层被"事件对象 vs 字典"这种
    表示差异绊住——两种形态的字段名是一样的。
    """
    out: list[dict] = []
    for m in getattr(message, "mentions", None) or []:
        if isinstance(m, dict):
            mention_id = m.get("id") if isinstance(m.get("id"), dict) else {}
            open_id = m.get("open_id") or (mention_id or {}).get("open_id") or ""
            out.append({"key": m.get("key") or "", "name": m.get("name") or "",
                        "open_id": open_id})
        else:
            out.append({
                "key": getattr(m, "key", "") or "",
                "name": getattr(m, "name", "") or "",
                "open_id": _attr(m, "id", "open_id", default="") or "",
            })
    return out


def parse_message_event(data: Any) -> MessageEvent | None:
    """P2ImMessageReceiveV1 → MessageEvent；结构不符时返回 None（记日志后静默丢弃）。"""
    header = getattr(data, "header", None)
    event = getattr(data, "event", None)
    message = getattr(event, "message", None) if event is not None else None
    if header is None or message is None:
        log.warning("消息事件结构不符合预期，已忽略：%r", type(data).__name__)
        return None

    message_id = getattr(message, "message_id", "") or ""
    chat_id = getattr(message, "chat_id", "") or ""
    open_id = _attr(event, "sender", "sender_id", "open_id", default="") or ""
    if not message_id or not chat_id or not open_id:
        log.warning("消息事件缺少必要字段，已忽略：message_id=%r chat_id=%r open_id=%r",
                    message_id, chat_id, open_id)
        return None

    return MessageEvent(
        event_id=getattr(header, "event_id", "") or "",
        message_id=message_id,
        chat_id=chat_id,
        chat_type=getattr(message, "chat_type", "") or "",
        open_id=open_id,
        text=_parse_content_text(getattr(message, "content", None)),
        message_type=getattr(message, "message_type", "") or "",
        mentions=_extract_mentions(message),
    )


def parse_card_action(data: Any) -> CardAction | None:
    """P2CardActionTrigger → CardAction。"""
    header = getattr(data, "header", None)
    event = getattr(data, "event", None)
    if header is None or event is None:
        log.warning("卡片回调结构不符合预期，已忽略：%r", type(data).__name__)
        return None
    value = getattr(getattr(event, "action", None), "value", None) or {}
    if not isinstance(value, dict):
        value = {}
    return CardAction(
        event_id=getattr(header, "event_id", "") or "",
        open_id=_attr(event, "operator", "open_id", default="") or "",
        chat_id=_attr(event, "context", "open_chat_id", default="") or "",
        message_id=_attr(event, "context", "open_message_id", default="") or "",
        action=str(value.get("action") or ""),
        value=value,
    )


def _bot_mention_keys(text: str, mentions: list[dict],
                      bot_open_id: str | None) -> list[str]:
    """挑出属于**本机器人**的 mention key。

    拿不到 bot open_id 时退化为"只认出现在文本开头的那个 key"——与
    `mentioned_bot` 的兜底口径一致，且宁可少剥（问题里多一个 `@_user_1` 残留）
    也不要多剥（把问题内容吃掉）。
    """
    if bot_open_id:
        return [m.get("key") or "" for m in mentions
                if (m.get("open_id") or "") == bot_open_id and (m.get("key") or "")]
    head = (text or "").lstrip()
    for mention in mentions:
        key = mention.get("key") or ""
        if key and head.startswith(key):
            return [key]
    return []


def strip_mentions(text: str, mentions: list[dict],
                   bot_open_id: str | None = None) -> str:
    """剥离文本中的 @机器人 片段（mention key 形如 `@_user_1`）。

    **只剥本机器人自己的 mention**（开发文档 5.1）：群里 @ 别人也是问题语义的一部分，
    "介绍一下 @张三 提到的赤壁之战"里 @张三 必须留在问题里。
    只在群聊里调用：单聊没有 @ 语义，文本原样作为问题。
    """
    out = text
    for key in _bot_mention_keys(text, mentions, bot_open_id):
        out = out.replace(key, " ")
    return " ".join(out.split())


def mentioned_bot(event: MessageEvent, bot_open_id: str | None) -> bool:
    """群聊是否 @ 了本机器人。

    优先比对 mention 的 open_id 与机器人自己的 open_id；拿不到 bot open_id 时
    退化为"文本开头出现任意 mention key"（开发文档 5.1 给的两条口径）。
    P0-4 实测已确认事件里 mention.id.open_id 可用，退化路径只是兜底。
    """
    keys = {(m.get("key") or "") for m in event.mentions}
    keys.discard("")
    if not keys:
        return False
    if bot_open_id:
        return any((m.get("open_id") or "") == bot_open_id for m in event.mentions)
    head = event.text.strip()
    return any(head.startswith(key) for key in keys)


class Dispatcher:
    """事件接入与 worker。"""

    # worker 取任务的轮询间隔（秒）。取值只影响"停机最多慢多久"：
    # 0.5s 意味着 `stop()` 最坏多等半秒，换来的是"停机不依赖往队列里塞哨兵消息"。
    _POLL_SECONDS = 0.5

    # 队列观测的打印间隔（秒）。太长看不到积压的实时变化，太短会把日志淹掉。
    _MONITOR_SECONDS = 60.0

    def __init__(self, *, db: Database, session: SessionStore, skills: list,
                 feishu, config, clock: Callable[[], float] = time.time):
        self.db = db
        self.session = session
        self.skills = skills
        self.registry = SkillRegistry(skills)     # 分流顺序的唯一实现处
        self.feishu = feishu
        self.config = config
        self.clock = clock
        # 有界队列（第 12 轮审查 P2-5）：原先是无界 queue.Queue()，消息突发时会一路吃内存，
        # 直到进程被 OOM 杀掉——而触发条件是外部的，运维看不到任何预兆。
        # 上限与拒绝行为见 _enqueue。
        self.queue: queue.Queue = queue.Queue(maxsize=max(1, int(config.queue_max_size)))
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self.command_bot_open_id: str | None = None
        # 卡片回调的退化去重窗口：{dedupe_key: 上次处理时刻}
        self._card_seen: dict[str, float] = {}
        # 繁忙提示的冷却记录：{session_key: 上次提示时刻}，避免同一会话被提示刷屏
        self._busy_notified: dict[str, float] = {}
        # 观测计数（日志与测试断言共用）
        self.stats = {"received": 0, "duplicate": 0, "enqueued": 0, "handled": 0,
                      "failed": 0, "send_failed": 0, "rejected_full": 0,
                      "rejected_not_allowed": 0}
        # 上次打印队列观测的时刻（monotonic 秒），见 _log_queue_stats_periodically
        self._last_stats_log = 0.0

    # ---- 生命周期 ----
    def recover_stuck_events(self, *, stale_seconds: float = STUCK_EVENT_SECONDS) -> int:
        """把"上一轮没做成"的事件扫回可重认领状态，返回改动行数（第 14 轮审计 P1-5）。

        要恢复的有两类：

        - `processing`：worker 正在处理时进程被强杀（SIGKILL / OOM / os._exit），
          这一行会永久留着，而它在 `_CLAIMED_STATUSES` 里 → 飞书重投被当重复丢弃，
          那条提问就此永久消失；
        - `received`：认领成功但还没来得及入队（进程在同一瞬间被杀），
          `received` 本来就可重认领，但**只有超时才动**——刚落库的正常在途认领不该被抢。

        为什么用时间窗而不是"一律恢复 processing"：worker 可能**正在**处理一条
        耗时任务，无条件改状态会让同一条提问被回答两次（用户在飞书里收到两条回答，
        比晚几秒看到结果更难解释）。10 分钟窗口把这两种情况分开。

        什么时候调用：进程启动、worker 起来**之前**（见 main.run）。放在停机路径上
        没有用——强杀的进程没有停机路径可走。
        """
        if stale_seconds <= 0:
            return 0
        cutoff = self.clock() - stale_seconds
        try:
            with self.db.transaction() as conn:
                cursor = conn.execute(
                    "UPDATE processed_events SET status = ?, received_at = ? "
                    "WHERE status IN (?, ?) AND received_at < ?",
                    (STATUS_RECEIVED, self.clock(), STATUS_PROCESSING, STATUS_RECEIVED,
                     cutoff),
                )
                changed = cursor.rowcount or 0
        except Exception as exc:  # noqa: BLE001 - 恢复失败不该挡住机器人启动
            log.error("卡住事件恢复失败（不影响启动，重投仍会走正常去重）：%s", exc)
            return 0
        if changed:
            # 这条日志是"进程被杀过"的证据：正常运行不会出现
            log.warning("已恢复 %d 条卡住的事件（processing/received 超过 %.0fs）："
                        "它们之前因为进程被强杀而无法被飞书重投认领", changed, stale_seconds)
        return changed

    def start(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        # 先恢复卡住的事件，再启动 worker：顺序反了的话，刚恢复的行可能被
        # 正在运行的 worker 与新到达的消息同时看到（虽然也能自洽，但没有必要）。
        self.recover_stuck_events()
        self._stop.clear()
        self._worker = threading.Thread(target=self.worker_loop, name="bot-worker",
                                        daemon=True)
        self._worker.start()
        log.info("worker 线程已启动（单 worker：所有会话串行处理）")

    def stop(self, timeout: float = 5.0) -> None:
        """停止 worker；`timeout` 为等待在途任务收尾的秒数。

        **不再用哨兵消息唤醒 worker**（第 13 轮整改）。原先的 `queue.put(None)`
        在队列已满时会阻塞，而 worker 此刻可能正卡在一次长 RAG 查询上——
        于是 `stop()` 永久阻塞，`main.py` 的停机流程再也走不到 `close()`，
        表现为"按了 Ctrl-C、日志说正在停止、进程却退不出来"（本地诊断结果：
        `stop_thread_alive_with_full_queue = True`）。
        worker 改成 `get(timeout=...)` 轮询 `_stop`，停机就只依赖一个 Event。

        默认 5s 只够等短任务；在途的 RAG 查询最长要 `RAG_QUERY_TIMEOUT`，
        所以 `main.py` 的停机路径传的是 RAG 预算——否则 `app.close()` 会先把
        httpx/SQLite 关掉，在途任务抛异常（进程即将退出、无实害，但日志有吓人堆栈）。
        第二下 Ctrl-C 的 `os._exit(0)` 仍是"等太久"时的逃生口。
        """
        self._stop.set()
        if self._worker is not None:
            self._worker.join(timeout=timeout)
            if self._worker.is_alive():
                # 超时不是错误：在途任务还在跑，进程退出时它会随守护线程一起结束。
                # 记一条 WARNING 是为了让"停机用了多久、丢了几条"在日志里可见。
                log.warning("worker 未在 %.1fs 内退出（在途任务仍在跑）；队列剩余 %d 条，"
                            "将随进程退出丢弃", timeout, self.queue.qsize())
            else:
                self._worker = None
        log.info("worker 已停止")

    # ---- 去重（开发文档 5.1 / 风险 4）----
    #
    # 认领（claim）→ 入队（enqueue）→ 坐实（commit）是三步，中间留了 release() 这个
    # 回退口：把"认领"和"最终处理"压成一步，队列满时被拒的消息会被自己的去重记录
    # 永久挡在门外（第 13 轮整改修正的正是这一点）。

    def _set_event_status(self, event_id: str, status: str) -> None:
        """更新事件状态；事件不在表里就静默跳过（UPDATE 命中 0 行不是错误）。"""
        if not event_id:
            return
        with self.db.transaction() as conn:
            conn.execute("UPDATE processed_events SET status = ? WHERE event_id = ?",
                         (status, event_id))

    def _claim_event(self, event_id: str, event_type: str) -> Claim | None:
        """认领一个事件；已有结论（见 _CLAIMED_STATUSES）时返回 None。

        读取与写入在同一事务里，不存在"先查后写"的竞态窗口（飞书的重投可能落在
        不同线程）。上一轮没做成的行（received 卡住 / rejected_busy）会被改回
        received，让重投还能再试一次；`received_at` 同时刷新，否则 TTL 清理会先把
        它删掉，"重投仍可进入"就无从谈起。
        """
        if not event_id:
            return Claim(key="")
        now = now_ts()
        with self.db.transaction() as conn:
            row = conn.execute("SELECT status FROM processed_events WHERE event_id = ?",
                               (event_id,)).fetchone()
            if row is not None and row["status"] in _CLAIMED_STATUSES:
                return None
            if row is None:
                conn.execute(
                    "INSERT INTO processed_events (event_id, event_type, status, received_at) "
                    "VALUES (?, ?, ?, ?)",
                    (event_id, event_type, STATUS_RECEIVED, now),
                )
            else:
                conn.execute(
                    "UPDATE processed_events SET status = ?, received_at = ? WHERE event_id = ?",
                    (STATUS_RECEIVED, now, event_id),
                )
        return Claim(key=event_id)

    def _claim_card_action(self, action: CardAction) -> Claim | None:
        """卡片回调去重：优先 event_id（落库、长期）；缺失时退化为动作身份 + 短时间窗。"""
        if action.event_id:
            return self._claim_event(action.event_id, "card.action.trigger")
        key = action.dedupe_key()
        now = self.clock()
        window = self.config.card_dedupe_window_seconds
        last = self._card_seen.get(key)
        if last is not None and now - last < window:
            return None
        self._card_seen[key] = now
        # 顺手清理过期键，避免长跑进程里字典无限增长
        if len(self._card_seen) > 512:
            self._card_seen = {k: t for k, t in self._card_seen.items()
                               if now - t < window}
        return Claim(key=key, window=window)

    def _commit_claim(self, claim: Claim) -> None:
        """入队成功 → 坐实认领。"""
        if not claim.durable:
            return      # 内存档：认领时已记录时刻，窗口本身就是它的全部状态
        self._set_event_status(claim.key, STATUS_ACCEPTED)

    def _release_claim(self, claim: Claim) -> None:
        """入队失败 → 撤销认领（第 13 轮整改）。

        队列满不等于"这条消息不该被处理"：飞书对未及时应答的事件会重投，用户也可能
        自己再发一次。若认领留在库里，重投会撞上自己的去重记录被静默丢弃——这就是
        "队列满后消息永久丢失"的成因。两档撤销方式不同：
        - 落库档：改成 rejected_busy（不在 _CLAIMED_STATUSES 里，重投可再认领）；
        - 内存档：删掉窗口键，下一次同样的点击仍然算数。
        """
        if not claim.key:
            return
        if not claim.durable:
            self._card_seen.pop(claim.key, None)
            return
        self._set_event_status(claim.key, STATUS_REJECTED_BUSY)

    # ---- 使用范围白名单（第 12 轮审查 P2-5）----
    def is_allowed(self, chat_id: str, open_id: str) -> bool:
        """该会话是否在允许范围内。

        两个白名单都为空 = 不限（内网默认，行为与改造前完全一致）；
        任一非空时**命中任一即可放行**；都非空则"在群名单里"或"在用户名单里"均通过。
        判定放在服务端：界面藏入口只是体验，判断必须在收到消息的这一刻做。
        """
        allowed_chats = tuple(getattr(self.config, "feishu_allowed_chat_ids", ()) or ())
        allowed_users = tuple(getattr(self.config, "feishu_allowed_open_ids", ()) or ())
        if not allowed_chats and not allowed_users:
            return True
        return chat_id in allowed_chats or open_id in allowed_users

    def _reject_not_allowed(self, chat_id: str, open_id: str) -> None:
        """不在白名单内：不回消息、只记日志与计数。

        为什么不回一句"无权使用"：那等于把一个能探测机器人存在性的接口开放给任何人，
        而且白名单本来就是"不想让这些人用"，回执只会让他们继续试。
        """
        self.stats["rejected_not_allowed"] += 1
        log.warning("会话不在白名单内，已忽略：chat_id=%s open_id=%s", chat_id, open_id)

    # ---- 观测 ----
    def queue_snapshot(self) -> dict:
        """队列与处理计数的快照（日志、健康检查、诊断脚本共用）。

        为什么要专门提供它：故障现象是"机器人不回话了"，而运维在日志里只看到
        "队列已满，本次消息已丢弃"的单行记录，拼不出"现在积压多少、丢了多少"的全貌。
        一个可打印、可断言的快照能让这件事一眼可见。
        """
        return {
            "queue_size": self.queue.qsize(),
            "queue_max": self.queue.maxsize,
            "in_flight": self.queue.unfinished_tasks,
            "worker_alive": bool(self._worker is not None and self._worker.is_alive()),
            **self.stats,
        }

    def _log_queue_stats_periodically(self) -> None:
        """每 `_MONITOR_SECONDS` 打一条队列观测；无异常且空闲时降为 DEBUG。

        用 `time.monotonic` 而不是注入的 `clock`：后者是秒级时间戳，供业务断言使用，
        观测节流不该被用例替换的时钟影响。
        """
        now = time.monotonic()
        if now - self._last_stats_log < self._MONITOR_SECONDS:
            return
        self._last_stats_log = now
        snapshot = self.queue_snapshot()
        if snapshot["queue_size"] or snapshot["rejected_full"]:
            log.warning("队列观测：%s", snapshot)
        else:
            log.debug("队列观测：%s", snapshot)

    # ---- 入队（带容量上限）----
    def _enqueue(self, item, *, session_key: str, chat_id: str, reply_to: str | None) -> bool:
        """非阻塞入队；队列满时快速拒绝并（可选）提示，返回是否入队成功。

        必须非阻塞：本方法跑在飞书 SDK 的 ws 事件循环线程里，`put()` 阻塞会把
        ping/重连一起卡住，触发飞书判定"未应答"而重投——比丢消息更糟。
        """
        try:
            self.queue.put_nowait(item)
        except queue.Full:
            self.stats["rejected_full"] += 1
            log.error("队列已满（上限 %s），本次消息已丢弃：session=%s",
                      self.queue.maxsize, session_key)
            self._notify_busy(session_key, chat_id, reply_to)
            return False
        self.stats["enqueued"] += 1
        return True

    def _notify_busy(self, session_key: str, chat_id: str, reply_to: str | None) -> None:
        """在后台线程发一条"当前繁忙"，不阻塞回调线程。

        - 用独立线程：发消息是 HTTP 调用（几百毫秒），在回调线程里做会把 ws 心跳拖住；
        - 按会话冷却：连点/猛发时只提示一次，否则提示本身就成了新的消息风暴；
        - 失败只记日志：这是尽力而为的提示，不能因为提示发不出去而影响主流程。
        """
        if not getattr(self.config, "busy_notice_enabled", True):
            return
        now = self.clock()
        cooldown = float(getattr(self.config, "busy_notice_cooldown_seconds", 30.0))
        last = self._busy_notified.get(session_key)
        if last is not None and now - last < cooldown:
            return
        self._busy_notified[session_key] = now
        # 顺手清理过期键，避免长跑进程里字典无限增长
        if len(self._busy_notified) > 512:
            self._busy_notified = {k: t for k, t in self._busy_notified.items()
                                   if now - t < cooldown}

        card = build_notice_card(QUEUE_BUSY_TEXT)

        def _send() -> None:
            try:
                if reply_to:
                    self.feishu.reply_card(reply_to, card)
                else:
                    self.feishu.send_card(chat_id, card)
            except Exception as e:  # noqa: BLE001 - 提示失败不影响任何主流程
                log.warning("繁忙提示发送失败：chat_id=%s err=%s", chat_id, e)

        threading.Thread(target=_send, name="busy-notice", daemon=True).start()

    # ---- SDK 回调入口（只做去重 + 入队）----
    def on_message(self, data: Any) -> None:
        """im.message.receive_v1 回调。必须毫秒级返回（开发文档 5.1）。"""
        event = parse_message_event(data)
        if event is None:
            return
        self.stats["received"] += 1
        if not self.is_allowed(event.chat_id, event.open_id):
            self._reject_not_allowed(event.chat_id, event.open_id)
            return

        # 消息事件的退化去重键用 message_id：同一条消息的重投 message_id 必然相同，
        # 且用户不可能"再发一条 message_id 相同的新消息"，因此不需要时间窗。
        key = event.event_id or f"msg:{event.message_id}"
        claim = self._claim_event(key, "im.message.receive_v1")
        if claim is None:
            self.stats["duplicate"] += 1
            log.info("duplicate 事件已丢弃：event_id=%s message_id=%s",
                     event.event_id, event.message_id)
            return

        if not self._enqueue((TASK_MESSAGE, event), session_key=event.session_key,
                            chat_id=event.chat_id, reply_to=event.message_id):
            # 队列满：必须撤销认领，否则飞书重投会被刚写下的去重记录挡掉，
            # 这条消息就再也进不来了（见 _release_claim）。
            self._release_claim(claim)
            return
        self._commit_claim(claim)

    def on_card_action(self, data: Any) -> dict | None:
        """card.action.trigger 回调。

        返回给 SDK 的即时回执（toast）。**业务处理一律入队**：
        卡片回调超时同样会重投，而组卡片/发工单都是慢操作。
        """
        action = parse_card_action(data)
        if action is None:
            return None
        self.stats["received"] += 1
        if not self.is_allowed(action.chat_id, action.open_id):
            # 卡片回调同样过白名单：否则被移出名单的群仍能通过点按钮继续提问
            self._reject_not_allowed(action.chat_id, action.open_id)
            return None
        if not action.action:
            log.warning("卡片回调缺少 value.action，已忽略：value=%r", action.value)
            return None
        claim = self._claim_card_action(action)
        if claim is None:
            self.stats["duplicate"] += 1
            log.info("duplicate 卡片回调已丢弃：event_id=%s action=%s message_id=%s",
                     action.event_id, action.action, action.message_id)
            return None

        enqueued = self._enqueue((TASK_CARD_ACTION, action), session_key=session_key(
            action.open_id, action.chat_id), chat_id=action.chat_id,
            reply_to=action.message_id)
        if not enqueued:
            self._release_claim(claim)
            # 队列满时如实回"繁忙"，不要回"正在查询…"：后者是承诺已开始受理，
            # 而这次点击其实没被受理（第 13 轮整改）。
            return {"toast": {"type": "warning", "content": QUEUE_BUSY_TEXT}}
        self._commit_claim(claim)
        # 立即回执：toast 只是"已收到"，不承诺处理结果（处理结果由 worker 发卡片）
        toast = {
            "ask": "正在查询…",
            "report_error": "已收到反馈，谢谢！",
        }.get(action.action, "已收到")
        return {"toast": {"type": "info", "content": toast}}

    # ---- worker ----
    def worker_loop(self) -> None:
        log.info("worker 开始取任务")
        while not self._stop.is_set():
            # 轮询式取任务（第 13 轮整改）：不再靠哨兵消息唤醒，停机只依赖 _stop。
            # 哨兵路径的问题见 `stop()`——队列满时 `put(None)` 会阻塞在那里。
            try:
                item = self.queue.get(timeout=self._POLL_SECONDS)
            except queue.Empty:
                self._log_queue_stats_periodically()
                continue
            kind, payload = item
            key = _durable_event_key(kind, payload)
            try:
                if key:
                    self._set_event_status(key, STATUS_PROCESSING)
                if kind == TASK_MESSAGE:
                    self.handle_message(payload)
                elif kind == TASK_CARD_ACTION:
                    self.handle_card_action(payload)
                else:
                    log.warning("未知任务类型，已忽略：%r", kind)
            except Exception as e:  # noqa: BLE001 - worker 绝不能静默死掉
                self.stats["failed"] += 1
                if key:
                    self._set_event_status(key, STATUS_FAILED)
                log.exception("任务处理失败：%s", e)
            else:
                if key:
                    self._set_event_status(key, STATUS_DONE)
            finally:
                self.queue.task_done()
        log.info("worker 已退出")

    # ---- 业务处理 ----
    def handle_message(self, event: MessageEvent) -> None:
        if event.is_group:
            if not mentioned_bot(event, self.command_bot_open_id):
                log.info("群聊消息未 @ 机器人，忽略：chat_id=%s", event.chat_id)
                return
            question = strip_mentions(event.text, event.mentions,
                                      self.command_bot_open_id)
        else:
            question = event.text.strip()

        if event.message_type and event.message_type != "text":
            # P0 采用"回复提示"而不是静默忽略：避免用户以为机器人坏了（开发文档 5.1）
            self._reply_card(event, build_notice_card(NOT_TEXT_MESSAGE))
            return

        if not question:
            if event.is_group:
                log.info("剥离 @ 后问题为空，忽略：chat_id=%s", event.chat_id)
                return
            self._reply_card(event, build_notice_card("没有收到问题内容，请再发一次。"))
            return

        log.info("处理提问：event_id=%s session=%s question=%r",
                 event.event_id, event.session_key, question[:60])
        ctx = self._build_context(event, question)
        skill = self._resolve_skill(ctx)
        # 两段式回复（批次③-1）：慢技能先回占位卡，跑完再 PATCH 成最终卡
        placeholder_id = self._send_placeholder(event, skill)
        reply = self._run_skill(ctx, skill)
        user_id, msg_key = self._record_turn(ctx, reply)
        if msg_key is not None:
            # 卡片在发送前才补"反馈有误"按钮：value 要带 messages.id，而 id 先落库才有
            attach_feedback_button(reply.card, msg_key)
        sent_id = self._deliver(event, reply, placeholder_id)
        if msg_key is not None:
            if sent_id:
                self.session.set_bot_message_id(msg_key, sent_id)
            else:
                # 发送失败：回答没送达，刚落库的两行必须撤回（见 _discard_unsent_turn）
                self._discard_unsent_turn(ctx, reply, user_id, msg_key)
        self.stats["handled"] += 1

    def handle_card_action(self, action: CardAction) -> None:
        handlers = self._card_handlers()
        skill = handlers.get(action.action)
        if skill is None:
            log.warning("未知的卡片动作：%r（value=%r）", action.action, action.value)
            return
        skill.handle_card_action(action, self)
        self.stats["handled"] += 1

    def _card_handlers(self) -> dict:
        """{action: skill}：技能用类属性 `card_action` 声明自己处理哪个按钮动作。"""
        handlers: dict[str, Any] = {}
        for skill in self.skills:
            action = getattr(skill, "card_action", "")
            if action:
                handlers[action] = skill
        return handlers

    # ---- 内部：上下文 / 技能 / 落库 ----

    def build_synthetic_event(self, action: CardAction, question: str) -> MessageEvent:
        """把卡片点击（value.action=ask）转成一个"新提问"事件。

        不依赖原消息内容（开发文档 5.5.3）：chat_type 置空以便走单聊分支——
        卡片点击不存在 @ 语义，问题文本无需剥离，也不该被群聊规则挡下。
        """
        return MessageEvent(
            event_id=action.event_id or action.dedupe_key(),
            message_id=action.message_id,
            chat_id=action.chat_id,
            chat_type="",
            open_id=action.open_id,
            text=question,
            message_type="text",
        )

    def _build_context(self, event: MessageEvent, question: str) -> SkillContext:
        """会话 touch + 历史组装。

        **不在这里记录本轮提问**：用户行与本轮回答一起写，见 `_record_turn` 的说明。
        历史必须在记录之前取，否则当前问题会重复进入自己的上下文。
        """
        self.session.touch(event.session_key, event.open_id, event.chat_id)
        history = self.session.history_for_rag(event.session_key)
        return SkillContext(event=event, question=question, session_key=event.session_key,
                            history=history)

    def _record_turn(self, ctx: SkillContext,
                     reply: Reply) -> tuple[int | None, int | None]:
        """写本轮问答（user 行 + assistant 行），返回 `(user 行 id, assistant 行 id)`。

        三条规则叠在一起决定了这个函数的位置与顺序：

        1. **两行一起写、且只有"能进历史"的轮次才写**。反过来（提问先落库、回答失败也留着）
           会让 `/help` 这类命令进入 RAG 上下文——**更糟的是历史参与回答缓存的键**：
           实测同一个"介绍一下长平之战"因为多了一条 `/help` 历史，从 34ms 的缓存命中
           变成 16–25s 的真生成，直接顶穿机器人 25s 预算（真机踩过）。
           降级/失败轮次同理：没有回答的孤立提问对模型没有价值。
        2. **用户的提问行必须先于 assistant 行写入**：反馈工单按"紧邻在此之前的那次提问"
           取问题（`question_before_assistant` 用自增 id 定序），顺序反了工单会带错问题。
        3. 返回 `(None, None)` 表示"这轮不落库"，调用方据此既不补反馈按钮、也不回填消息 id。

        第二个 id 是卡片按钮的 msg_key；第一个 id 只在**发送失败撤回本轮**时用得到
        （`_discard_unsent_turn`）：两行是一起写的，撤回也必须一起撤。
        """
        turn = reply.assistant_turn
        if turn is None:
            return None, None
        if not turn.is_history_eligible:
            log.info("终态 %s 不写入历史（按开发文档 5.2）", turn.finish_reason)
            return None, None
        user_id = self.session.record_user(ctx.session_key, ctx.event.message_id,
                                          ctx.question)
        assistant_id = self.session.record_assistant(
            ctx.session_key, bot_message_id=None, content=turn.content,
            finish_reason=turn.finish_reason, citations=turn.citations,
        )
        return user_id, assistant_id

    def _discard_unsent_turn(self, ctx: SkillContext, reply: Reply,
                             user_id: int | None, assistant_id: int | None) -> None:
        """发送失败：撤回刚落库的 user + assistant 两行。

        为什么必须撤（2026-09-24 线上实测：飞书侧 TLS 抖动导致回答未送达）：
        这轮问答已经进了历史，而下一轮的上下文与 **RAG 回答缓存的键**都会带着
        一条用户从未见过的回答——查询缓存因此永远命中不了，本该毫秒返回的问题
        退化成十几秒的真生成。与"成对写入"是同一条原则的两面：只写能入历史的轮次，
        不保留没有送达的回答。
        答案全文打进 ERROR 日志，供运营侧手工补偿。
        两段式回复下同样适用：占位卡送达但最终卡没 PATCH 上去时，用户看到的是
        "发送失败"提示（`_patch_placeholder_failed`），回答本身并未送达。
        """
        removed = self.session.delete_messages([user_id, assistant_id])
        self.stats["send_failed"] += 1
        turn = reply.assistant_turn
        log.error("回复发送失败：已撤回本轮历史 %d 行（session=%s question=%r）。"
                  "回答全文如下，供手工补偿：%s",
                  removed, ctx.session_key, ctx.question,
                  (turn.content if turn is not None else ""))

    def _resolve_skill(self, ctx: SkillContext) -> Any | None:
        """按注册顺序解析技能（顺序规则见 skills/base.py::SkillRegistry）。

        分流本身抛异常也要有个结果：占位卡此时可能已经发出去了，必须继续走到
        "给用户一张降级卡"这一步，而不是让它留在"正在检索…"。
        """
        try:
            return self.registry.resolve(ctx)
        except Exception as e:  # noqa: BLE001
            log.exception("技能分流异常：%s", e)
            return None

    def _run_skill(self, ctx: SkillContext, skill: Any | None) -> Reply:
        """执行已解析的技能。"""
        if skill is None:
            log.error("没有技能命中且无兜底技能，返回降级卡片")
            return Reply(kind="card", card=build_degraded_card(reason="internal"))
        try:
            log.debug("技能命中：%s", skill.name)
            return skill.run(ctx)
        except Exception as e:  # noqa: BLE001
            log.exception("技能 %s 执行异常：%s", skill.name, e)
            return Reply(kind="card", card=build_degraded_card(reason="internal"))

    def _send_placeholder(self, event: MessageEvent, skill: Any | None) -> str | None:
        """两段式回复第一步：慢技能动手之前先回一张"正在检索…"占位卡（批次③-1）。

        为什么要占位：知识问答同步等 RAG，介绍类长回答实测 12–25s，不给任何反馈用户
        只能干等、还会怀疑机器人没反应。占位卡发出去后最终卡由 PATCH 整卡替换（`_deliver`），
        消息 id 不变、不新增消息。

        只有技能自己声明了 `wants_placeholder` 才发（当前只有 knowledge_qa）：
        `/help`、`/new` 这类命令与提示卡片本来就秒回，不需要两段式。
        占位卡发失败不算致命（传输层抖动是常见现象）：退回单段式，跑完后按普通 reply 发送。
        """
        if skill is None or not getattr(skill, "wants_placeholder", False):
            return None
        try:
            return self.feishu.reply_card(event.message_id, build_placeholder_card())
        except Exception as e:  # noqa: BLE001
            log.error("占位卡发送失败（退回单段式）：message_id=%s err=%s",
                      event.message_id, e)
            return None

    def _deliver(self, event: MessageEvent, reply: Reply,
                 placeholder_id: str | None) -> str | None:
        """把最终回复送到用户眼前；返回送达消息的 id，失败返回 None。

        有占位卡就 PATCH 它（整卡替换、消息 id 不变）；PATCH 失败时占位卡已经躺在用户
        眼前了，**不能让它一直转圈**——再尽力 PATCH 一次"发送失败"提示卡，本轮历史照旧
        撤回（回答确实没送达，与"成对写入"口径一致）。
        """
        if not placeholder_id:
            return self._send_reply(event, reply)
        # kind=text 是兜底路径（当前没有技能这么返回）：占位卡已送达，PATCH 成同等内容的
        # 卡片比再补发一条消息干净，用户不会看到两张卡
        card = reply.card or build_notice_card(reply.text or "")
        try:
            if self.feishu.patch_card(placeholder_id, card):
                return placeholder_id
            log.error("最终卡片更新失败（飞书返回业务错误）：message_id=%s", placeholder_id)
        except Exception as e:  # noqa: BLE001 - 重试后仍失败，见 feishu_client
            log.error("最终卡片更新失败（传输层）：message_id=%s err=%s", placeholder_id, e)
        self._patch_placeholder_failed(placeholder_id)
        return None

    def _patch_placeholder_failed(self, placeholder_id: str) -> None:
        """占位卡收尾：PATCH 成"发送失败"提示（尽力而为，失败只记日志）。"""
        try:
            if self.feishu.patch_card(placeholder_id, build_notice_card(SEND_FAILED_TEXT)):
                log.warning("占位卡已更新为发送失败提示：message_id=%s", placeholder_id)
            else:
                log.error("失败提示也没能 PATCH 上去（用户会停留在占位卡上）：message_id=%s",
                          placeholder_id)
        except Exception as e:  # noqa: BLE001
            log.error("失败提示没能更新（用户会停留在占位卡上）：message_id=%s err=%s",
                      placeholder_id, e)

    def _send_reply(self, event: MessageEvent, reply: Reply) -> str | None:
        """发送回复并返回机器人消息 id；**发送失败返回 None**。

        传输层异常（TLS 抖动等）不能让 worker_loop 的兜底 catch 收走：feishu_client
        已经重试过（见该模块的传输层重试），走到这里就是"这次真没发出去"——按发送失败
        返回 None，调用方才有机会把这轮历史撤干净（否则用户什么也没收到，历史里却留着
        一条他从未见过的回答）。
        """
        try:
            if reply.kind == "text" and reply.text:
                return self.feishu.reply_text(event.message_id, reply.text)
            card = reply.card or build_degraded_card(reason="internal")
            return self.feishu.reply_card(event.message_id, card)
        except Exception as e:  # noqa: BLE001 - 发送失败是必经路径（网络抖动）
            log.error("回复发送失败（传输层，重试后仍未成功）：message_id=%s err=%s",
                      event.message_id, e)
            return None

    def _reply_card(self, event: MessageEvent, card: dict) -> str | None:
        """直接回一张卡片（非文本消息提示、空问题提示等，不经过技能）。"""
        try:
            return self.feishu.reply_card(event.message_id, card)
        except Exception as e:  # noqa: BLE001 - 提示类卡片没有历史要撤，失败只记日志
            log.error("提示卡片发送失败：message_id=%s err=%s", event.message_id, e)
            return None

    def reply_card_to_chat(self, chat_id: str, card: dict) -> str | None:
        """主动发卡片（示例问题/工单等不依附原消息的场景）。"""
        return self.feishu.send_card(chat_id, card)
