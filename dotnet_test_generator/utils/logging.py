"""Logging configuration and utilities."""

import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

_loggers: dict[str, logging.Logger] = {}
_initialized = False


def setup_logging(
    level: str = "INFO",
    log_format: str = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    log_file: Path | None = None,
    rich_console: bool = True,
) -> None:
    """
    Configure logging for the application.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR)
        log_format: Format string for log messages
        log_file: Optional file path for logging
        rich_console: Use rich console handler for prettier output
    """
    global _initialized

    root_logger = logging.getLogger("dotnet_test_generator")
    root_logger.setLevel(getattr(logging, level.upper()))

    # Clear existing handlers
    root_logger.handlers.clear()

    if rich_console:
        console = Console(stderr=True)
        console_handler = RichHandler(
            console=console,
            show_time=True,
            show_path=False,
            markup=True,
            rich_tracebacks=True,
        )
        console_handler.setLevel(getattr(logging, level.upper()))
        root_logger.addHandler(console_handler)
    else:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(getattr(logging, level.upper()))
        console_handler.setFormatter(logging.Formatter(log_format))
        root_logger.addHandler(console_handler)

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)  # Always log everything to file
        file_handler.setFormatter(logging.Formatter(log_format))
        root_logger.addHandler(file_handler)

    _initialized = True


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance for a specific module.

    Args:
        name: Logger name (usually __name__)

    Returns:
        Configured logger instance
    """
    global _initialized

    if not _initialized:
        setup_logging()

    if name not in _loggers:
        if name.startswith("dotnet_test_generator"):
            logger = logging.getLogger(name)
        else:
            logger = logging.getLogger(f"dotnet_test_generator.{name}")
        _loggers[name] = logger

    return _loggers[name]


class LogContext:
    """Context manager for adding context to log messages."""

    def __init__(self, logger: logging.Logger, **context: str):
        self.logger = logger
        self.context = context
        self._old_factory: logging.LogRecordFactory | None = None

    def __enter__(self) -> "LogContext":
        self._old_factory = logging.getLogRecordFactory()

        context = self.context

        def record_factory(*args, **kwargs):
            record = self._old_factory(*args, **kwargs)
            for key, value in context.items():
                setattr(record, key, value)
            return record

        logging.setLogRecordFactory(record_factory)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._old_factory:
            logging.setLogRecordFactory(self._old_factory)
