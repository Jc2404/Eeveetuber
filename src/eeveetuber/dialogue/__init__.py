"""Incremental foreground dialogue contracts and orchestration."""

from eeveetuber.dialogue.assembler import IncrementalUtteranceAssembler
from eeveetuber.dialogue.pipeline import DialoguePipeline
from eeveetuber.dialogue.ports import ModelProvider, SpeechSynthesizer
from eeveetuber.dialogue.types import (
    AudioChunk,
    DialogueRequest,
    DialogueStreamEvent,
    ModelCompleted,
    ModelStreamEvent,
    ModelTextDelta,
    SegmentAudioReady,
    SegmentReady,
    UtteranceCompleted,
    UtterancePlan,
    UtteranceSegment,
)

__all__ = [
    "AudioChunk",
    "DialoguePipeline",
    "DialogueRequest",
    "DialogueStreamEvent",
    "IncrementalUtteranceAssembler",
    "ModelCompleted",
    "ModelProvider",
    "ModelStreamEvent",
    "ModelTextDelta",
    "SegmentAudioReady",
    "SegmentReady",
    "SpeechSynthesizer",
    "UtteranceCompleted",
    "UtterancePlan",
    "UtteranceSegment",
]

