"""Deterministic adapters used by tests and the vertical tracer."""

from eeveetuber.adapters.fake.asr import FakeAsrRequestRecord, FakeSpeechRecognizer
from eeveetuber.adapters.fake.avatar import FakeAvatarAdapter
from eeveetuber.adapters.fake.model import FakeModelProvider
from eeveetuber.adapters.fake.speech import FakeSpeechSynthesizer

__all__ = [
    "FakeAsrRequestRecord",
    "FakeAvatarAdapter",
    "FakeModelProvider",
    "FakeSpeechRecognizer",
    "FakeSpeechSynthesizer",
]
