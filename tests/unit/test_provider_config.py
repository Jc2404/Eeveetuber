import pytest
from pydantic import ValidationError

from eeveetuber.config import (
    AdapterProvider,
    AppSettings,
    ModelAdapterSettings,
    ReasoningEffortSetting,
    SpeechAdapterSettings,
)


def test_provider_settings_are_fake_by_default() -> None:
    model = ModelAdapterSettings()
    speech = SpeechAdapterSettings()

    assert model.provider is AdapterProvider.FAKE
    assert speech.provider is AdapterProvider.FAKE


def test_nested_environment_shape_supports_local_openai_endpoint() -> None:
    settings = AppSettings.model_validate(
        {
            "model": {
                "provider": "openai_compatible",
                "base_url": "http://127.0.0.1:11434/v1/",
                "model": "local-model",
            }
        }
    )

    assert settings.model.provider is AdapterProvider.OPENAI_COMPATIBLE
    assert settings.model.base_url == "http://127.0.0.1:11434/v1"
    assert settings.model.api_key is None


def test_reasoning_none_is_a_first_class_environment_setting(monkeypatch) -> None:
    monkeypatch.setenv("EEVEETUBER_MODEL__REASONING_EFFORT", "none")

    settings = AppSettings(_env_file=None)

    assert settings.model.reasoning_effort is ReasoningEffortSetting.NONE


def test_provider_base_url_must_be_http() -> None:
    with pytest.raises(ValidationError):
        AppSettings.model_validate({"model": {"base_url": "file:///tmp/model"}})
