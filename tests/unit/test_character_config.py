from pathlib import Path

from eeveetuber.config.character import load_character_profile


def test_loads_versioned_default_character_profile() -> None:
    profile = load_character_profile(Path("profiles/characters/default.toml"))

    assert profile.schema_version == 1
    assert profile.character_id == "default"
    assert profile.revision
    assert profile.context.canon.strip()

