from __future__ import annotations

import pytest
from pydantic import ValidationError

from eeveetuber.config import AppSettings, ConversationHistorySettings


def test_history_settings_preserve_low_latency_defaults() -> None:
    settings = AppSettings(_env_file=None)

    assert settings.history == ConversationHistorySettings(
        max_messages=12,
        max_chars=6_000,
        max_message_chars=1_500,
        load_timeout_ms=50,
    )


def test_history_settings_load_from_nested_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EEVEETUBER_HISTORY__MAX_MESSAGES", "24")
    monkeypatch.setenv("EEVEETUBER_HISTORY__MAX_CHARS", "9000")
    monkeypatch.setenv("EEVEETUBER_HISTORY__MAX_MESSAGE_CHARS", "2000")
    monkeypatch.setenv("EEVEETUBER_HISTORY__LOAD_TIMEOUT_MS", "35")

    settings = AppSettings(_env_file=None)

    assert settings.history == ConversationHistorySettings(
        max_messages=24,
        max_chars=9_000,
        max_message_chars=2_000,
        load_timeout_ms=35,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [("max_messages", 1_001), ("max_chars", -1), ("max_message_chars", 0), ("load_timeout_ms", -1)],
)
def test_history_settings_reject_invalid_bounds(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        ConversationHistorySettings(**{field: value})
