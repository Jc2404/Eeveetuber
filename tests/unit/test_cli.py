from __future__ import annotations

from pathlib import Path
from typing import Any

from eeveetuber import cli
from eeveetuber.config import AppSettings


def test_normal_cli_keeps_file_logging_disabled(monkeypatch: Any, tmp_path: Path) -> None:
    calls: dict[str, Any] = {}
    settings = AppSettings(data_dir=tmp_path, _env_file=None)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "create_app", lambda configured: ("app", configured))
    monkeypatch.setattr(
        cli,
        "configure_logging",
        lambda level, **kwargs: calls.update(level=level, logging=kwargs),
    )
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kwargs: calls.update(app=app, run=kwargs))

    cli.main([])

    assert calls["logging"]["text_log_path"] is None
    assert calls["app"] == ("app", settings)
    assert calls["run"]["log_config"] is None
    assert not (tmp_path / "logs").exists()


def test_verbose_cli_uses_resolved_user_log_path(monkeypatch: Any, tmp_path: Path) -> None:
    calls: dict[str, Any] = {}
    settings = AppSettings(data_dir=tmp_path, log_filename="diagnostic.log", _env_file=None)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "create_app", lambda configured: configured)

    def configure(level: str, **kwargs: Any) -> Path:
        calls.update(level=level, logging=kwargs)
        return kwargs["text_log_path"]

    monkeypatch.setattr(cli, "configure_logging", configure)
    monkeypatch.setattr(cli, "get_logger", lambda **_values: _CapturingLogger(calls))
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kwargs: calls.update(app=app, run=kwargs))

    cli.main(["--verbose"])

    assert calls["logging"]["text_log_path"] == tmp_path / "logs" / "diagnostic.log"
    assert calls["logging"]["max_bytes"] == settings.log_max_bytes
    assert calls["logging"]["backup_count"] == settings.log_backup_count
    assert calls["logged_event"] == "verbose_file_logging_enabled"
    assert calls["run"]["log_config"] is None


class _CapturingLogger:
    def __init__(self, calls: dict[str, Any]) -> None:
        self._calls = calls

    def info(self, event: str, **values: object) -> None:
        self._calls["logged_event"] = event
        self._calls["logged_values"] = values
