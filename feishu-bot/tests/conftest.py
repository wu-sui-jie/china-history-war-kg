"""pytest 公共装置：路径注入 + 常用假件。

测试**不依赖飞书 SDK 与网络**：dispatcher/skills/cards/session 都只依赖鸭子类型，
假件在下面各测试文件里就地定义；这里只提供路径与数据库夹具。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

BOT_ROOT = Path(__file__).resolve().parent.parent
if str(BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(BOT_ROOT))

from bot.db import Database            # noqa: E402
from bot.session import SessionStore   # noqa: E402
from config import Config              # noqa: E402


class _DropSdkCronNoise(logging.Filter):
    """丢掉 lark-oapi 的 ExpiringCache 留下的那一条 asyncio 错误日志。

    SDK 在导入时起了一个清理协程，自己从不 await 也不取消；解释器退出时 asyncio
    会为这个孤儿任务打 ERROR「Task was destroyed but it is pending!」。同一件事
    pytest 也会报一次 RuntimeWarning（已在 `pytest.ini` 里过滤）。两者都是 SDK 副作用，
    与本项目代码无关——这里按消息内容精确丢这一条，其它 asyncio 日志照常输出。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return "ExpiringCache._start_clear_cron" not in record.getMessage()


logging.getLogger("asyncio").addFilter(_DropSdkCronNoise())


@pytest.fixture
def config(tmp_path: Path) -> Config:
    """测试用配置：只覆盖必要项，其余用默认值（不读 .env）。"""
    return Config(feishu_app_id="cli_test", feishu_app_secret="secret",
                  db_path=tmp_path / "bot.db", rag_query_timeout=5.0,
                  rag_connect_timeout=2.0, subgraph_render_enabled=False)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "bot.db")
    database.connect()
    yield database
    database.close()


@pytest.fixture
def session(db: Database) -> SessionStore:
    return SessionStore(db, ttl_hours=24.0)


@pytest.fixture
def eval_answers() -> list[dict]:
    """评测 run 的真实回答样例（开发文档 10.1 要求的样例源）。"""
    import json

    path = BOT_ROOT / "tests" / "data" / "eval_answers.json"
    return json.loads(path.read_text(encoding="utf-8"))["answers"]
