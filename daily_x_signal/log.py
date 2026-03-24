"""daily-x-signal 结构化日志模块。

提供项目级 logger，同时输出到 stderr 和文件。
使用方式：
    from .log import logger
    logger.info("消息")
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 项目根目录下的 state/logs/
_LOG_DIR = Path(__file__).resolve().parents[1] / "state" / "logs"
_LOG_FILE = _LOG_DIR / "daily-x-signal.log"

logger = logging.getLogger("daily_x_signal")


def setup_logging(level: int = logging.INFO) -> None:
    """初始化日志，在 cli.py:main() 里调用一次即可。"""
    if logger.handlers:
        return  # 已初始化，避免重复

    logger.setLevel(level)
    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT)

    # stderr 输出（给终端看）
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)  # 终端只显示 WARNING 及以上
    stderr_handler.setFormatter(formatter)
    logger.addHandler(stderr_handler)

    # 文件输出（全量记录）
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(_LOG_FILE, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError:
        # 文件日志创建失败时不阻断主流程
        pass
