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

# ---- 可动态调整的日志配置（默认值与前端约束一致：大小 1~5MB，保留 7~60 天） ----
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_ROTATION_MB = 2
DEFAULT_RETENTION_DAYS = 30
_VALID_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")

# 当前生效配置（仅 console + app.log 参与动态调整；error.log 固定 ERROR）
_log_config = {
    "level": DEFAULT_LOG_LEVEL,
    "rotation_mb": DEFAULT_ROTATION_MB,
    "retention_days": DEFAULT_RETENTION_DAYS,
}
_console_id: int | None = None
_app_file_id: int | None = None


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


def _add_console(level: str) -> int:
    return logger.add(sys.stdout, level=level, format=LOG_FMT, colorize=True)


def _add_app_file(level: str, rotation_mb: int, retention_days: int) -> int:
    return logger.add(
        str(APP_LOG),
        level=level,
        format=LOG_FMT,
        rotation=f"{rotation_mb} MB",
        compression="tar.gz",
        retention=f"{retention_days} days",
        encoding="utf-8",
        enqueue=True,
    )


def setup_logging() -> None:
    """初始化 Loguru 日志系统，接管 Uvicorn 等标准库日志。"""
    global _console_id, _app_file_id
    logger.remove()

    # 控制台 + app.log：按当前（可动态调整）配置注册，保留 handler id 以便后续重配
    _console_id = _add_console(_log_config["level"])
    _app_file_id = _add_app_file(
        _log_config["level"], _log_config["rotation_mb"], _log_config["retention_days"])

    # error.log — ERROR 级别（固定，不参与动态调整）
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


def get_log_config() -> dict:
    """返回当前生效的日志配置快照。"""
    return dict(_log_config)


def reconfigure_logging(level: str | None = None, rotation_mb: int | None = None,
                        retention_days: int | None = None) -> dict:
    """运行时重配 console + app.log（移除旧 sink → 用新参数重新 add）。

    级别与切割大小对后续写入即时生效；保留天数在下一次切割时按新值生效。
    error.log 固定 ERROR 不动。入参越界自动夹取到约束范围内。
    """
    global _console_id, _app_file_id

    if level is not None:
        level = str(level).upper()
        if level in _VALID_LEVELS:
            _log_config["level"] = level
    if rotation_mb is not None:
        _log_config["rotation_mb"] = max(1, min(5, int(rotation_mb)))
    if retention_days is not None:
        _log_config["retention_days"] = max(7, min(60, int(retention_days)))

    # 重建 console
    if _console_id is not None:
        try:
            logger.remove(_console_id)
        except ValueError:
            pass
    _console_id = _add_console(_log_config["level"])

    # 重建 app.log
    if _app_file_id is not None:
        try:
            logger.remove(_app_file_id)
        except ValueError:
            pass
    _app_file_id = _add_app_file(
        _log_config["level"], _log_config["rotation_mb"], _log_config["retention_days"])

    logger.info(f"日志配置已更新: {_log_config}")
    return dict(_log_config)
