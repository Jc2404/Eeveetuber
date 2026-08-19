from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from eeveetuber.config import AppSettings, AvatarRendererSetting, AvatarSettings


def test_avatar_renderer_is_disabled_and_network_free_by_default() -> None:
    settings = AppSettings(_env_file=None)

    assert settings.avatar == AvatarSettings(
        enabled=False,
        renderer=AvatarRendererSetting.LIVE2D_WEB,
        asset_dir=None,
        manifest_filename="avatar.json",
        command_queue_capacity=128,
    )


def test_avatar_settings_load_from_nested_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("EEVEETUBER_AVATAR__ENABLED", "true")
    monkeypatch.setenv("EEVEETUBER_AVATAR__RENDERER", "live2d_web")
    monkeypatch.setenv("EEVEETUBER_AVATAR__ASSET_DIR", str(tmp_path))
    monkeypatch.setenv("EEVEETUBER_AVATAR__MANIFEST_FILENAME", "eevee.json")
    monkeypatch.setenv("EEVEETUBER_AVATAR__COMMAND_QUEUE_CAPACITY", "64")

    settings = AppSettings(_env_file=None)

    assert settings.avatar.enabled is True
    assert settings.avatar.renderer is AvatarRendererSetting.LIVE2D_WEB
    assert settings.avatar.asset_dir == tmp_path
    assert settings.avatar.manifest_filename == "eevee.json"
    assert settings.avatar.command_queue_capacity == 64


def test_enabled_avatar_requires_an_explicit_asset_directory() -> None:
    with pytest.raises(ValidationError, match="asset_dir is required"):
        AvatarSettings(enabled=True)


@pytest.mark.parametrize(
    "filename",
    ["", ".", "..", "nested/avatar.json", r"nested\avatar.json"],
)
def test_avatar_manifest_filename_cannot_escape_asset_root(filename: str) -> None:
    with pytest.raises(ValidationError):
        AvatarSettings(manifest_filename=filename)


@pytest.mark.parametrize("capacity", [7, 4_097])
def test_avatar_command_queue_capacity_is_bounded(capacity: int) -> None:
    with pytest.raises(ValidationError):
        AvatarSettings(command_queue_capacity=capacity)
