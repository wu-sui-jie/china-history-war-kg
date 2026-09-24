"""backend 的统一日志配置。

原先 200+ 处 print 直接写 stdout：既不落文件、也没有级别，排查问题只能靠
journalctl 捞历史输出。这里收敛成两个 handler：

- 控制台（开发时肉眼可见，格式与原来接近）；
- 滚动文件 `logs/backend.log`（5 MB × 3 份，`BACKEND_LOG_DIR` 可改目录）。

用法：`logger = get_logger(__name__)`，随后用 logger.info / warning / error。
CLI 脚本（import_json_to_sqlite、sync_sqlite_to_neo4j）的终端输出仍用 print——
那是给操作者看的命令行结果，不是运行期日志。
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(os.environ.get("BACKEND_LOG_DIR") or (Path(__file__).resolve().parent / "logs"))
_configured = False

_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"
_LOGGER_ROOT = "backend"


def _configure_root() -> None:
    global _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger(_LOGGER_ROOT)
    if root.handlers:
        return

    level = (os.environ.get("BACKEND_LOG_LEVEL") or "INFO").upper()
    root.setLevel(getattr(logging, level, logging.INFO))
    # 不要向上传播到 root：免得被其它库（或 Flask 的日志配置）重复打印一遍
    root.propagate = False

    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            LOG_DIR / "backend.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError as exc:
        root.warning("日志文件不可用（%s），本次仅输出到控制台", exc)


def get_logger(name: str) -> logging.Logger:
    """取一个挂在 backend.* 命名空间下的 logger。"""
    _configure_root()
    if not name or name == "__main__":
        return logging.getLogger(_LOGGER_ROOT)
    if name.startswith(_LOGGER_ROOT + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{_LOGGER_ROOT}.{name}")
