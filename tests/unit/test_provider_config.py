import pytest
from pydantic import ValidationError

from eeveetuber.config import (
    AdapterProvider,
    AppSettings,
    AsrAdapterSettings,
    ModelAdapterSettings,
    ReasoningEffortSetting,
    SpeechAdapterSettings,
)


def test_provider_settings_are_fake_by_default() -> None:
    model = ModelAdapterSettings()
    speech = SpeechAdapterSettings()
    asr = AsrAdapterSettings()

    assert model.provider is AdapterProvider.FAKE
    assert speech.provider is AdapterProvider.FAKE
    assert asr.provider is AdapterProvider.FAKE


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


def test_nested_asr_environment_supports_keyless_local_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("EEVEETUBER_ASR__PROVIDER", "openai_compatible")
    monkeypatch.setenv("EEVEETUBER_ASR__BASE_URL", "http://127.0.0.1:9000/v1/")
    monkeypatch.setenv("EEVEETUBER_ASR__MODEL", "local-whisper")
    monkeypatch.setenv("EEVEETUBER_ASR__LANGUAGE", "en")
    monkeypatch.setenv("EEVEETUBER_ASR__PROMPT", "Eevee, VTuber")
    monkeypatch.setenv("EEVEETUBER_ASR__TEMPERATURE", "0.2")
    monkeypatch.setenv("EEVEETUBER_ASR__REQUEST_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("EEVEETUBER_ASR__CONNECT_TIMEOUT_SECONDS", "3")
    monkeypatch.setenv("EEVEETUBER_ASR__MAX_INPUT_PCM_BYTES", "2097152")
    monkeypatch.setenv("EEVEETUBER_ASR__MAX_RESPONSE_BYTES", "262144")

    settings = AppSettings(_env_file=None)

    assert settings.asr.provider is AdapterProvider.OPENAI_COMPATIBLE
    assert settings.asr.base_url == "http://127.0.0.1:9000/v1"
    assert settings.asr.api_key is None
    assert settings.asr.model == "local-whisper"
    assert settings.asr.language == "en"
    assert settings.asr.prompt == "Eevee, VTuber"
    assert settings.asr.temperature == 0.2
    assert settings.asr.request_timeout_seconds == 45
    assert settings.asr.connect_timeout_seconds == 3
    assert settings.asr.max_input_pcm_bytes == 2_097_152
    assert settings.asr.max_response_bytes == 262_144


def test_provider_base_url_must_be_http() -> None:
    with pytest.raises(ValidationError):
        AppSettings.model_validate({"model": {"base_url": "file:///tmp/model"}})


@pytest.mark.parametrize(
    "asr",
    [
        {"base_url": "file:///tmp/asr"},
        {"base_url": "https://user:pass@example.test/v1"},
        {"base_url": "https://example.test/v1?token=secret"},
        {"api_key": " "},
        {"model": " "},
        {"language": " "},
        {"prompt": " "},
        {"temperature": 1.1},
        {"max_response_bytes": 63},
    ],
)
def test_asr_settings_reject_invalid_values(asr: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AppSettings.model_validate({"asr": asr})
