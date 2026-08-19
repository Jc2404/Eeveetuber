"""Structured logging setup with safe, correlation-friendly defaults."""

from __future__ import annotations

import logging
import sys
from collections.abc import Mapping, MutableMapping, Sequence
from itertools import count
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Lock
from typing import Any, cast

import structlog

_REDACTED = "[REDACTED]"
_SENSITIVE_FIELDS = frozenset(
    {
        "api_key",
        "audio_base64",
        "authorization",
        "chain_of_thought",
        "content",
        "cookie",
        "display_text",
        "messages",
        "password",
        "private_reasoning",
        "prompt",
        "raw_reasoning",
        "refresh_token",
        "response_text",
        "secret",
        "set_cookie",
        "speakable_text",
        "text",
        "user_text",
    }
)


class _SequenceFilter(logging.Filter):
    """Assign one process-local order before any handler formats the record."""

    def __init__(self) -> None:
        super().__init__()
        self._values = count(1)
        self._lock = Lock()

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "eeveetuber_sequence", None) is None:
            with self._lock:
                if getattr(record, "eeveetuber_sequence", None) is None:
                    record.eeveetuber_sequence = next(self._values)
        return True


class _HumanFileRenderer:
    """Render one escaped physical line with an obvious UTC time and sequence."""

    def __call__(
        self,
        _logger: Any,
        _method_name: str,
        event_dict: MutableMapping[str, Any],
    ) -> str:
        timestamp = str(event_dict.pop("timestamp", "unknown-time"))
        sequence = int(event_dict.pop("sequence", 0))
        level = str(event_dict.pop("level", "info")).upper()
        event = _one_line(event_dict.pop("event", ""))
        details = " ".join(
            f"{key}={_render_value(value)}" for key, value in sorted(event_dict.items())
        )
        prefix = f"{timestamp} #{sequence:06d} {level:<8} {event}"
        return f"{prefix} {details}" if details else prefix


def configure_logging(
    level: str = "INFO",
    *,
    json: bool = False,
    text_log_path: Path | None = None,
    file_level: str = "DEBUG",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 3,
) -> Path | None:
    """Configure console logging and an optional rotating human-readable file.

    The file sink is absent unless ``text_log_path`` is explicitly supplied.
    Its lower default level makes ``--verbose`` useful without making the normal
    console noisy. Structured content and credential fields are redacted before
    reaching either sink.
    """

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    numeric_file_level = getattr(logging, file_level.upper(), logging.DEBUG)
    root_level = min(numeric_level, numeric_file_level) if text_log_path else numeric_level
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        _redact_sensitive_values,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    sequence_filter = _SequenceFilter()
    console_renderer: Any = (
        structlog.processors.JSONRenderer()
        if json
        else structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())
    )
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.addFilter(sequence_filter)
    console_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.processors.TimeStamper(fmt="iso", utc=True),
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                console_renderer,
            ],
            foreign_pre_chain=shared_processors,
        )
    )

    handlers: list[logging.Handler] = [console_handler]
    resolved_path: Path | None = None
    if text_log_path is not None:
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        if backup_count < 1:
            raise ValueError("backup_count must be positive")
        resolved_path = text_log_path.expanduser().resolve()
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            resolved_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(numeric_file_level)
        file_handler.addFilter(sequence_filter)
        file_handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                processors=[
                    structlog.processors.TimeStamper(fmt="iso", utc=True),
                    _add_record_sequence,
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    _HumanFileRenderer(),
                ],
                foreign_pre_chain=shared_processors,
            )
        )
        handlers.append(file_handler)

    logging.basicConfig(level=root_level, handlers=handlers, force=True)
    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.make_filtering_bound_logger(root_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )
    return resolved_path


def get_logger(**initial_values: object) -> structlog.stdlib.BoundLogger:
    """Return a structured logger without accepting unredacted prompt text by convention."""

    return cast("structlog.stdlib.BoundLogger", structlog.get_logger().bind(**initial_values))


def _add_record_sequence(
    _logger: Any,
    _method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    record = event_dict.get("_record")
    event_dict["sequence"] = (
        int(getattr(record, "eeveetuber_sequence", 0))
        if isinstance(record, logging.LogRecord)
        else 0
    )
    return event_dict


def _redact_sensitive_values(
    _logger: Any,
    _method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    for key in tuple(event_dict):
        event_dict[key] = _redact_value(key, event_dict[key])
    return event_dict


def _redact_value(key: str, value: object) -> object:
    normalized = key.lower().replace("-", "_")
    if (
        normalized in _SENSITIVE_FIELDS
        or normalized.endswith("_password")
        or normalized.endswith("_secret")
        or normalized.endswith("_token")
        or normalized.endswith("_api_key")
    ):
        return _REDACTED
    if isinstance(value, Mapping):
        return {
            str(child_key): _redact_value(str(child_key), child)
            for child_key, child in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_redact_value("item", child) for child in value]
    return value


def _one_line(value: object) -> str:
    return str(value).replace("\r", "\\r").replace("\n", "\\n")


def _render_value(value: object) -> str:
    return repr(value).replace("\r", "\\r").replace("\n", "\\n")
