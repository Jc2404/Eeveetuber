"""Provider-neutral input-media ports."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from eeveetuber.media.types import AsrStreamEvent, PcmUtterance
from eeveetuber.runtime.cancellation import CancellationToken


class SpeechRecognizer(Protocol):
    """Recognize one bounded utterance.

    Implementations may yield zero or more partials, but must yield exactly one
    final event last.  Cancellation must prevent any later result from escaping.
    """

    def recognize(
        self,
        utterance: PcmUtterance,
        *,
        cancellation: CancellationToken | None = None,
    ) -> AsyncIterator[AsrStreamEvent]: ...


__all__ = ["SpeechRecognizer"]
