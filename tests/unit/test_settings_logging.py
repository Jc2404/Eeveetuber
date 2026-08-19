from pathlib import Path

import pytest
from pydantic import ValidationError

from eeveetuber.config import AppSettings


def test_default_log_path_follows_configured_data_directory(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path, _env_file=None)

    assert settings.verbose_log_path == tmp_path / "logs" / "eeveetuber.log"
    assert not settings.verbose_log_path.parent.exists()


def test_explicit_log_directory_and_filename_are_respected(tmp_path: Path) -> None:
    settings = AppSettings(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "diagnostics",
        log_filename="session.log",
        _env_file=None,
    )

    assert settings.verbose_log_path == tmp_path / "diagnostics" / "session.log"


@pytest.mark.parametrize("filename", ["../escape.log", "nested/log.txt", ".", "..", " "])
def test_log_filename_cannot_escape_log_directory(filename: str) -> None:
    with pytest.raises(ValidationError, match="plain filename"):
        AppSettings(log_filename=filename, _env_file=None)
