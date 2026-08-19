"""Validated application and character configuration."""

from eeveetuber.config.character import CharacterProfile, load_character_profile
from eeveetuber.config.providers import (
    AdapterProvider,
    AsrAdapterSettings,
    ModelAdapterSettings,
    ReasoningEffortSetting,
    SpeechAdapterSettings,
    SpeechOutputFormat,
)
from eeveetuber.config.settings import (
    AppSettings,
    AvatarRendererSetting,
    AvatarSettings,
    ContextBudgetSettings,
    ConversationHistorySettings,
    VoiceInputSettings,
    get_settings,
)

__all__ = [
    "AdapterProvider",
    "AppSettings",
    "AsrAdapterSettings",
    "AvatarRendererSetting",
    "AvatarSettings",
    "CharacterProfile",
    "ContextBudgetSettings",
    "ConversationHistorySettings",
    "ModelAdapterSettings",
    "ReasoningEffortSetting",
    "SpeechAdapterSettings",
    "SpeechOutputFormat",
    "VoiceInputSettings",
    "get_settings",
    "load_character_profile",
]
