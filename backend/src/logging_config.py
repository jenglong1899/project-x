import logging
import logging.config
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from src.commons import BASE_ROOT


def _env_str(name: str, default: str) -> str:
    raw = os.getenv(name, "").strip()
    return raw if raw else default


def configure_logging() -> None:
    """
    配置后端日志：
    - 控制台：INFO（默认）
    - 文件：DEBUG（默认），写入 BASE_ROOT/logs/backend.log

    注意：如果 BASE_ROOT 不可写，会抛异常并阻止启动（避免“看似启动成功但日志全丢”）。
    """
    console_level = _env_str("PROJECT_X_CONSOLE_LOG_LEVEL", "INFO").upper()
    file_level = _env_str("PROJECT_X_FILE_LOG_LEVEL", "DEBUG").upper()
    logs_dir = BASE_ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / _env_str("PROJECT_X_BACKEND_LOG_FILE", "backend.log")

    log_config: dict[str, Any] = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "%(asctime)s %(levelname)s %(name)s: %(message)s",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": console_level,
                "formatter": "default",
                "stream": "ext://sys.stdout",
            },
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": file_level,
                "formatter": "default",
                "filename": str(log_path),
                "encoding": "utf-8",
                "maxBytes": int(_env_str("PROJECT_X_BACKEND_LOG_MAX_BYTES", "10485760")),
                "backupCount": int(_env_str("PROJECT_X_BACKEND_LOG_BACKUP_COUNT", "5")),
            },
        },
        "root": {
            "level": "DEBUG",
            "handlers": ["console", "file"],
        },
        # uvicorn 的默认 logger 不是 root；显式挂到同一套 handler，避免“uvicorn 打到一套、业务打到另一套”
        "loggers": {
            "uvicorn": {"level": "INFO", "handlers": ["console", "file"], "propagate": False},
            "uvicorn.error": {"level": "INFO", "handlers": ["console", "file"], "propagate": False},
            "uvicorn.access": {"level": "INFO", "handlers": ["console", "file"], "propagate": False},
        },
    }

    logging.config.dictConfig(log_config)
    # 确保文件 handler 真正可写（RotatingFileHandler 在首次 emit 才会创建文件）
    logging.getLogger(__name__).debug("后端日志已初始化：%s", log_path)

