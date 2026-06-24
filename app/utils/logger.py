import logging
import sys
from pathlib import Path
from loguru import logger


LOG_DIR = Path(__file__).parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

APP_LOG = LOG_DIR / "app.log"
ERROR_LOG = LOG_DIR / "error.log"

LOG_FMT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
    "{name}:{function}:{line} | {message}"
)


class InterceptHandler(logging.Handler):
    """将标准 logging 日志桥接到 Loguru。"""

    def emit(self, record: logging.LogRecord) -> None:
        level = logger.level(record.levelname).name
        frame = logging.currentframe()
        depth = 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def setup_logging() -> None:
    """初始化 Loguru 日志系统，接管 Uvicorn 等标准库日志。"""
    logger.remove()

    # 控制台输出
    logger.add(
        sys.stdout,
        level="INFO",
        format=LOG_FMT,
        colorize=True,
    )

    # app.log — INFO 级别
    logger.add(
        str(APP_LOG),
        level="INFO",
        format=LOG_FMT,
        rotation="2 MB",
        compression="tar.gz",
        retention="30 days",
        encoding="utf-8",
        enqueue=True,
    )

    # error.log — ERROR 级别
    logger.add(
        str(ERROR_LOG),
        level="ERROR",
        format=LOG_FMT,
        rotation="2 MB",
        compression="tar.gz",
        retention="30 days",
        encoding="utf-8",
        enqueue=True,
    )

    # 接管标准库 logging
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

    # 接管 uvicorn 相关 logger
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers = [InterceptHandler()]
        lg.propagate = False

    logger.info("Loguru 日志系统初始化完成，已接管 Uvicorn 日志")
