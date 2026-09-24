"""SQLite 连接与建表（开发文档第七节）。

单实例 + 单 worker 起步，但有两个线程会写：
- dispatcher 线程写 `processed_events`（去重必须发生在回调里，不能等 worker）；
- worker 线程写 `sessions` / `messages` / `feedback`。
因此用**单连接 + 一把互斥锁**（开发文档第七节的写入约定）：竞争极低，
锁只为防"两个线程同时写同一连接"。

为什么不开线程池/连接池：单机个人自用到小团队场景，WAL + busy_timeout 已经够用；
多实例部署本就不在范围内（开发文档十二-7），提前上池只会让"谁在写"更难排查。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

log = logging.getLogger(__name__)

SCHEMA = """
-- 会话（TTL 软口径用 updated_at 过滤）
CREATE TABLE IF NOT EXISTS sessions (
  session_key TEXT PRIMARY KEY,          -- "{open_id}:{chat_id}"
  open_id     TEXT NOT NULL,
  chat_id     TEXT NOT NULL,
  created_at  INTEGER NOT NULL,          -- unix 秒
  updated_at  INTEGER NOT NULL
);

-- 消息历史（history 组装与反馈取数共用）
CREATE TABLE IF NOT EXISTS messages (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,  -- 同时是按钮 value 的 msg_key
  session_key    TEXT NOT NULL,
  message_id     TEXT,                   -- 收到的用户消息 id（reply 用它）
  bot_message_id TEXT,                   -- 机器人回复卡片的消息 id（P2 PATCH 更新卡片用它）
  role           TEXT NOT NULL CHECK (role IN ('user','assistant')),
  content        TEXT NOT NULL,          -- user=原始问题；assistant=answer_md 原文
  finish_reason TEXT,                    -- assistant 轮记录；user 轮为 NULL
  citations      TEXT,                   -- assistant 轮 JSON 数组；P2 反馈取数用
  created_at     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session
  ON messages (session_key, created_at);

-- 事件去重（幂等，含消息与卡片回调两类事件）
CREATE TABLE IF NOT EXISTS processed_events (
  event_id    TEXT PRIMARY KEY,          -- 飞书 event.header.event_id
  event_type  TEXT,
  received_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_processed_events_time
  ON processed_events (received_at);

-- 纠错反馈（P2）
CREATE TABLE IF NOT EXISTS feedback (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  open_id     TEXT NOT NULL,
  session_key TEXT NOT NULL,
  message_id  TEXT,
  question    TEXT NOT NULL,
  answer_md   TEXT NOT NULL,
  citations   TEXT,                      -- JSON 数组
  status      TEXT NOT NULL DEFAULT 'open',   -- open / done
  created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_status
  ON feedback (status, created_at);
"""


class Database:
    """单连接 + 互斥锁的 SQLite 封装。"""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    # ---- 生命周期 ----
    def connect(self) -> sqlite3.Connection:
        """建立连接并建表（可重复调用，幂等）。"""
        with self._lock:
            if self._conn is not None:
                return self._conn
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # check_same_thread=False：连接被 worker 与 dispatcher 两个线程共用，
            # 线程安全由本类的锁保证
            conn = sqlite3.connect(str(self.path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            # WAL：读写并发不互相阻塞（dispatcher 写去重记录时 worker 仍可读历史）
            conn.execute("PRAGMA journal_mode=WAL")
            # busy_timeout：另一个进程（如清理脚本）持锁时等待而不是立刻报 database is locked
            conn.execute("PRAGMA busy_timeout=5000")
            conn.executescript(SCHEMA)
            conn.commit()
            self._conn = conn
            log.debug("SQLite 就绪：%s", self.path)
            return conn

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn if self._conn is not None else self.connect()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ---- 语句执行（全部在锁内）----
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, tuple(params))
            self.conn.commit()
            return cur

    def executemany(self, sql: str, seq: Iterable[Iterable[Any]]) -> None:
        with self._lock:
            self.conn.executemany(sql, [tuple(p) for p in seq])
            self.conn.commit()

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(sql, tuple(params)).fetchone()

    def query_all(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self.conn.execute(sql, tuple(params)).fetchall())

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """多语句原子操作（如"去重检查 + 写入"必须在一起，否则并发回调会双写）。"""
        with self._lock:
            try:
                yield self.conn
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise


def now_ts() -> int:
    return int(time.time())
