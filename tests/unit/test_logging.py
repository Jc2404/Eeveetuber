from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from eeveetuber.observability import configure_logging, get_logger


@pytest.fixture(autouse=True)
def restore_logging() -> None:
    yield
    configure_logging("WARNING")


def _flush_handlers() -> None:
    for handler in logging.getLogger().handlers:
        handler.flush()


def test_normal_configuration_is_console_only_and_does_not_create_directory(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    candidate = tmp_path / "not-created" / "eeveetuber.log"

    assert configure_logging("INFO") is None
    get_logger(component="test").info("console_event", answer=42)
    _flush_handlers()

    assert "console_event" in capsys.readouterr().out
    assert not candidate.parent.exists()


def test_verbose_file_has_utc_timestamp_sequence_levels_and_foreign_logs(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "diagnostic.log"
    configured = configure_logging("INFO", text_log_path=path)

    logger = get_logger(component="runtime")
    logger.debug("debug_event", queue_size=2)
    logger.info("info_event", session_count=1)
    logging.getLogger("uvicorn.error").warning("foreign warning")
    _flush_handlers()

    assert configured == path.resolve()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert [re.search(r"#(\d{6})", line).group(1) for line in lines] == [  # type: ignore[union-attr]
        "000001",
        "000002",
        "000003",
    ]
    assert all(re.match(r"^\d{4}-\d{2}-\d{2}T.*Z #\d{6} ", line) for line in lines)
    assert "DEBUG" in lines[0] and "debug_event" in lines[0]
    assert "INFO" in lines[1] and "info_event" in lines[1]
    assert "WARNING" in lines[2] and "foreign warning" in lines[2]
    assert "\x1b[" not in path.read_text(encoding="utf-8")


def test_sensitive_structured_fields_are_redacted_and_lines_cannot_be_forged(tmp_path: Path) -> None:
    path = tmp_path / "safe.log"
    configure_logging("INFO", text_log_path=path)

    get_logger().info(
        "safe_event\nnot_a_second_event",
        api_key="top-secret-key",
        text="private owner sentence",
        nested={"access_token": "secret-token", "count": 3},
    )
    _flush_handlers()

    content = path.read_text(encoding="utf-8")
    assert len(content.splitlines()) == 1
    assert "top-secret-key" not in content
    assert "private owner sentence" not in content
    assert "secret-token" not in content
    assert content.count("[REDACTED]") == 3
    assert "safe_event\\nnot_a_second_event" in content
    assert "count" in content


def test_verbose_file_rotates_instead_of_growing_without_bound(tmp_path: Path) -> None:
    path = tmp_path / "bounded.log"
    configure_logging("INFO", text_log_path=path, max_bytes=256, backup_count=2)

    logger = get_logger(component="rotation-test")
    for index in range(20):
        logger.info("bounded_record", index=index, padding="safe-metadata" * 3)
    _flush_handlers()

    assert path.is_file()
    assert path.with_name("bounded.log.1").is_file()
    assert len(list(tmp_path.glob("bounded.log*"))) <= 3


@pytest.mark.parametrize(("max_bytes", "backup_count"), [(0, 1), (100, 0)])
def test_invalid_rotation_configuration_is_rejected(
    tmp_path: Path, max_bytes: int, backup_count: int
) -> None:
    with pytest.raises(ValueError):
        configure_logging(
            "INFO",
            text_log_path=tmp_path / "log.txt",
            max_bytes=max_bytes,
            backup_count=backup_count,
        )
