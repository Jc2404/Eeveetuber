"""Owner-authored, versioned character profile loading."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class CharacterContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    canon: str = Field(min_length=1)
    persona: str = Field(min_length=1)


class CharacterProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1)
    character_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9_-]+$")
    display_name: str = Field(min_length=1, max_length=128)
    revision: str = Field(min_length=1, max_length=128)
    context: CharacterContext


def load_character_profile(path: Path) -> CharacterProfile:
    """Parse and validate a profile before any session starts."""

    with path.open("rb") as stream:
        return CharacterProfile.model_validate(tomllib.load(stream))

