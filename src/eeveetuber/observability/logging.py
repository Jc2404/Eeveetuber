"""Structured logging setup with safe, correlation-friendly defaults."""

from __future__ import annotations

import logging
import sys
from typing import Any, cast

import structlog


def configure_logging(level: str = "INFO", *, json: bool = False) -> None:
    """Configure stdlib and structlog once at process startup."""

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=numeric_level, force=True)
    renderer: Any = structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(**initial_values: object) -> structlog.stdlib.BoundLogger:
    """Return a structured logger without accepting unredacted prompt text by convention."""

    return cast("structlog.stdlib.BoundLogger", structlog.get_logger().bind(**initial_values))
