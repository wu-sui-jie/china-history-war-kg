"""统一日志：控制台 + logs/ 滚动文件。"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"


def _formatter() -> logging.Formatter:
    return logging.Formatter(LOG_FORMAT)


def get_logger(name: str, log_dir: Path | None = None, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:  # 已初始化过
        return logger
    logger.setLevel(level)

    fmt = _formatter()

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    if log_dir is not None:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            log_dir / "rag.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    logger.propagate = False
    return logger


def setup_rag_logging(log_dir: Path | None = None, level: int = logging.INFO) -> logging.Logger:
    """给 `rag.*` 命名空间挂统一 handler（控制台 + `logs/server.log` 滚动）。

    server 侧的模块（runtime / sse / generate / llm_client）都是
    `logging.getLogger("rag.xxx")` 取值，但原先没有任何地方配置 handler：
    INFO 级日志被默认丢弃、也不落文件，排查线上问题只能看 stdout（RAG-5）。
    在服务启动时调用本函数一次，所有 `rag.*` 子 logger 都会继承这套 handler。

    与 `get_logger` 的区别：那个是给离线脚本按 logger 各自初始化的（写 `logs/rag.log`），
    这里配置的是父 logger，服务端只需要一次。
    """
    root = logging.getLogger("rag")
    if root.handlers:  # 已初始化过
        return root
    root.setLevel(level)
    # 保持向 root 传播（Python 默认行为）：pytest 的 caplog 等标准工具依赖它。
    # 不必担心重复输出——`logging.lastResort` 只在"整条祖先链一个 handler 都没有"时生效，
    # 而 rag 自己已经有 handler 了。

    fmt = _formatter()
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    if log_dir is not None:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            log_dir / "server.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)

    return root
