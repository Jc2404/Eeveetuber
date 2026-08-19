import pytest
from pydantic import SecretStr

from eeveetuber.adapters import create_model_provider, create_speech_synthesizer
from eeveetuber.adapters.fake import FakeModelProvider, FakeSpeechSynthesizer
from eeveetuber.adapters.openai_compatible import (
    OpenAICompatibleModelProvider,
    OpenAICompatibleSpeechSynthesizer,
    ReasoningEffort,
    SpeechAudioFormat,
)
from eeveetuber.config import (
    AdapterProvider,
    ModelAdapterSettings,
    ReasoningEffortSetting,
    SpeechAdapterSettings,
    SpeechOutputFormat,
)


def test_factory_defaults_remain_network_free() -> None:
    assert isinstance(create_model_provider(ModelAdapterSettings()), FakeModelProvider)
    assert isinstance(create_speech_synthesizer(SpeechAdapterSettings()), FakeSpeechSynthesizer)


@pytest.mark.asyncio
async def test_factory_maps_openai_compatible_settings_without_network_io() -> None:
    model = create_model_provider(
        ModelAdapterSettings(
            provider=AdapterProvider.OPENAI_COMPATIBLE,
            base_url="http://127.0.0.1:11434/v1",
            api_key=SecretStr("model-secret"),
            model="local-model",
            reasoning_effort=ReasoningEffortSetting.LOW,
        )
    )
    speech = create_speech_synthesizer(
        SpeechAdapterSettings(
            provider=AdapterProvider.OPENAI_COMPATIBLE,
            base_url="http://127.0.0.1:8880/v1",
            model="local-tts",
            voice="character",
            response_format=SpeechOutputFormat.PCM,
        )
    )

    assert isinstance(model, OpenAICompatibleModelProvider)
    assert model.config.reasoning_effort is ReasoningEffort.LOW
    assert model.config.api_key == "model-secret"
    assert isinstance(speech, OpenAICompatibleSpeechSynthesizer)
    assert speech.config.response_format is SpeechAudioFormat.PCM
    await model.aclose()
    await speech.aclose()


@pytest.mark.asyncio
async def test_factory_preserves_explicit_reasoning_none() -> None:
    model = create_model_provider(
        ModelAdapterSettings(
            provider=AdapterProvider.OPENAI_COMPATIBLE,
            base_url="http://127.0.0.1:11434/v1",
            model="local-model",
            reasoning_effort=ReasoningEffortSetting.NONE,
        )
    )

    assert isinstance(model, OpenAICompatibleModelProvider)
    assert model.config.reasoning_effort is ReasoningEffort.NONE
    await model.aclose()
