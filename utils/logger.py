"""Structured JSON logger backed by the standard library + rich console output."""

import logging
import sys
from typing import Optional
from rich.logging import RichHandler
from rich.console import Console


_console = Console(stderr=True)
_loggers: dict[str, logging.Logger] = {}


def get_logger(name: str, level: Optional[str] = None) -> logging.Logger:
    """Return (and cache) a named logger with rich console output.

    Args:
        name: Module name, e.g. ``__name__``.
        level: Override log level string (``"DEBUG"``, ``"INFO"``, etc.).
               Falls back to the root logger level or ``INFO``.

    Returns:
        Configured :class:`logging.Logger` instance.
    """
    if name in _loggers:
        return _loggers[name]

    logger = logging.getLogger(name)

    if not logger.handlers:
        handler = RichHandler(
            console=_console,
            show_time=True,
            show_path=False,
            markup=True,
            rich_tracebacks=True,
        )
        handler.setFormatter(logging.Formatter("%(message)s", datefmt="[%X]"))
        logger.addHandler(handler)
        logger.propagate = False

    resolved_level = level or logging.getLevelName(logging.root.level) or "INFO"
    logger.setLevel(resolved_level)

    _loggers[name] = logger
    return logger


def configure_root(level: str = "INFO") -> None:
    """Set the root log level and configure a default rich handler.

    Args:
        level: Log level string.
    """
    logging.basicConfig(
        level=level,
        handlers=[
            RichHandler(
                console=_console,
                show_time=True,
                show_path=False,
                markup=True,
                rich_tracebacks=True,
            )
        ],
        format="%(message)s",
        datefmt="[%X]",
    )
