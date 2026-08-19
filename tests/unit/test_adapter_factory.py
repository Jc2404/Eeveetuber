import pytest
from pydantic import SecretStr

from eeveetuber.adapters import (
    create_model_provider,
    create_speech_recognizer,
    create_speech_synthesizer,
)
from eeveetuber.adapters.fake import (
    FakeModelProvider,
    FakeSpeechRecognizer,
    FakeSpeechSynthesizer,
)
from eeveetuber.adapters.openai_compatible import (
    OpenAICompatibleModelProvider,
    OpenAICompatibleSpeechRecognizer,
    OpenAICompatibleSpeechSynthesizer,
    ReasoningEffort,
    SpeechAudioFormat,
)
from eeveetuber.config import (
    AdapterProvider,
    AsrAdapterSettings,
    ModelAdapterSettings,
    ReasoningEffortSetting,
    SpeechAdapterSettings,
    SpeechOutputFormat,
)


def test_factory_defaults_remain_network_free() -> None:
    assert isinstance(create_model_provider(ModelAdapterSettings()), FakeModelProvider)
    assert isinstance(create_speech_synthesizer(SpeechAdapterSettings()), FakeSpeechSynthesizer)
    assert isinstance(create_speech_recognizer(AsrAdapterSettings()), FakeSpeechRecognizer)


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
    asr = create_speech_recognizer(
        AsrAdapterSettings(
            provider=AdapterProvider.OPENAI_COMPATIBLE,
            base_url="http://127.0.0.1:9000/v1",
            api_key=SecretStr("asr-secret"),
            model="local-whisper",
            language="en",
            prompt="Eevee names",
            temperature=0.15,
            request_timeout_seconds=45,
            connect_timeout_seconds=4,
            max_input_pcm_bytes=2_097_152,
            max_response_bytes=262_144,
        )
    )

    assert isinstance(model, OpenAICompatibleModelProvider)
    assert model.config.reasoning_effort is ReasoningEffort.LOW
    assert model.config.api_key == "model-secret"
    assert isinstance(speech, OpenAICompatibleSpeechSynthesizer)
    assert speech.config.response_format is SpeechAudioFormat.PCM
    assert isinstance(asr, OpenAICompatibleSpeechRecognizer)
    assert asr.config.base_url == "http://127.0.0.1:9000/v1"
    assert asr.config.api_key == "asr-secret"
    assert asr.config.model == "local-whisper"
    assert asr.config.language == "en"
    assert asr.config.prompt == "Eevee names"
    assert asr.config.temperature == 0.15
    assert asr.config.timeout_seconds == 45
    assert asr.config.connect_timeout_seconds == 4
    assert asr.config.max_input_pcm_bytes == 2_097_152
    assert asr.config.max_response_bytes == 262_144
    await model.aclose()
    await speech.aclose()
    await asr.aclose()


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
