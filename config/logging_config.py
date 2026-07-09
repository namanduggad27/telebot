import logging
import sys
from typing import Optional
from config.settings import settings


class StructuredFormatter(logging.Formatter):
    """Custom formatter to output cleanly structured log records."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = self.formatTime(record, self.datefmt)
        level = record.levelname.ljust(8)
        logger_name = record.name
        message = record.getMessage()
        if record.exc_info:
            exc_text = self.formatException(record.exc_info)
            message = f"{message}\n{exc_text}"
        return f"[{timestamp}] [{level}] [{logger_name}] - {message}"


def configure_logging(level: Optional[str] = None) -> None:
    """Initialize structured console logging across all pipeline workers and services."""
    log_level = level or settings.LOG_LEVEL
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Clear existing handlers
    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    formatter = StructuredFormatter(datefmt="%Y-%m-%d %H:%M:%S")
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Lower noisy third-party library logs unless explicitly debugging
    logging.getLogger("hydrogram").setLevel(logging.WARNING)
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
