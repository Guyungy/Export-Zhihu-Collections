# -*- coding: utf-8 -*-
"""日志初始化与强制刷新。

原实现里有 ``setup_debug_logging`` 与 ``reconfigure_logging`` 两份几乎一样的
代码（差别只是日志目录）。这里合并成一次调用：日志目录确定后再初始化即可。
"""

from __future__ import annotations

import logging
import os
import pathlib
import sys
from datetime import datetime
from typing import Optional

LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"


def ensure_dir(path) -> pathlib.Path:
    """确保目录存在并返回 ``pathlib.Path``。"""
    directory = pathlib.Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def setup_logging(
    logs_dir,
    prefix: str = "debug",
    console_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
) -> str:
    """初始化根 logger，同时输出到文件与控制台。

    :param logs_dir: 日志目录
    :param prefix: 日志文件名前缀，如 ``debug`` / ``openCollection``
    :return: 日志文件的绝对路径
    """
    logs_dir = ensure_dir(logs_dir)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = logs_dir / ("%s_%s.log" % (prefix, timestamp))

    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        _close_handler(handler)
        root_logger.removeHandler(handler)

    formatter = logging.Formatter(LOG_FORMAT)

    file_handler = logging.FileHandler(log_file, encoding="utf-8", mode="w")
    file_handler.setLevel(file_level)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(formatter)

    root_logger.setLevel(min(file_level, console_level))
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    logging.info("日志系统初始化完成，日志文件: %s", log_file)
    flush_logs()
    return str(log_file)


def _close_handler(handler: logging.Handler) -> None:
    """尽力关闭并刷新一个 handler。"""
    try:
        handler.flush()
    except Exception:  # noqa: BLE001 - 关闭阶段不应抛错
        pass


def flush_logs() -> None:
    """强制刷新所有日志处理器与标准输出。

    多线程 + 长任务场景下，日志可能还在缓冲区里，出问题时看不到现场。
    """
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        try:
            if hasattr(handler, "flush"):
                handler.flush()
            stream = getattr(handler, "stream", None)
            if stream is not None and hasattr(stream, "flush"):
                stream.flush()
                if hasattr(stream, "fileno"):
                    try:
                        os.fsync(stream.fileno())
                    except Exception:  # noqa: BLE001 - 部分流不支持 fsync
                        pass
        except Exception:  # noqa: BLE001 - 刷新失败不应影响主流程
            pass

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:  # noqa: BLE001
            pass


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """获取一个子 logger（统一走根 logger 的 handler）。"""
    return logging.getLogger(name) if name else logging.getLogger()
