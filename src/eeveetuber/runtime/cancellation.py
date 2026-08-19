"""Generation-based cancellation that rejects late asynchronous results."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True, order=True, slots=True)
class CancellationGeneration:
    value: int

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or self.value < 0:
            raise ValueError("cancellation generation must be a non-negative integer")

    def next(self) -> CancellationGeneration:
        return CancellationGeneration(self.value + 1)


class StaleGenerationError(RuntimeError):
    """An adapter result no longer belongs to the current foreground turn."""

    def __init__(
        self,
        received: CancellationGeneration,
        current: CancellationGeneration,
        *,
        current_cancelled: bool,
    ) -> None:
        self.received = received
        self.current = current
        self.current_cancelled = current_cancelled
        detail = "current generation is cancelled" if current_cancelled else "generation is stale"
        super().__init__(
            f"result generation {received.value} rejected; current is {current.value} ({detail})"
        )


class _CancellationState:
    __slots__ = ("cancelled_at_monotonic_ms", "event", "reason")

    def __init__(self) -> None:
        self.event = asyncio.Event()
        self.reason: str | None = None
        self.cancelled_at_monotonic_ms: int | None = None

    def cancel(self, reason: str) -> bool:
        if self.event.is_set():
            return False
        self.reason = reason
        self.cancelled_at_monotonic_ms = time.monotonic_ns() // 1_000_000
        self.event.set()
        return True


@dataclass(frozen=True, slots=True)
class CancellationToken:
    """Read-only view passed to ASR/model/TTS/avatar adapter work."""

    generation: CancellationGeneration
    _state: _CancellationState

    @property
    def cancelled(self) -> bool:
        return self._state.event.is_set()

    @property
    def reason(self) -> str | None:
        return self._state.reason

    @property
    def cancelled_at_monotonic_ms(self) -> int | None:
        return self._state.cancelled_at_monotonic_ms

    async def wait_cancelled(self) -> str:
        await self._state.event.wait()
        return self._state.reason or "cancelled"

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise asyncio.CancelledError(self.reason or "cancelled")


class CancellationSource:
    """Single-owner generation clock with thread-safe, non-awaiting mutation."""

    __slots__ = ("_closed", "_generation", "_lock", "_state")

    def __init__(self, initial_generation: int = 0) -> None:
        self._generation = CancellationGeneration(initial_generation)
        self._state = _CancellationState()
        self._closed = False
        self._lock = Lock()

    @property
    def generation(self) -> CancellationGeneration:
        with self._lock:
            return self._generation

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def token(self) -> CancellationToken:
        with self._lock:
            return CancellationToken(self._generation, self._state)

    def advance(self, reason: str) -> CancellationToken:
        """Cancel the old generation before publishing and returning the new one."""

        if not reason.strip():
            raise ValueError("cancellation requires a non-blank reason")
        with self._lock:
            if self._closed:
                raise RuntimeError("cancellation source is closed")
            self._state.cancel(reason)
            self._generation = self._generation.next()
            self._state = _CancellationState()
            return CancellationToken(self._generation, self._state)

    def cancel_current(self, reason: str) -> bool:
        if not reason.strip():
            raise ValueError("cancellation requires a non-blank reason")
        with self._lock:
            return self._state.cancel(reason)

    def close(self, reason: str = "session closed") -> bool:
        if not reason.strip():
            raise ValueError("cancellation requires a non-blank reason")
        with self._lock:
            changed = self._state.cancel(reason)
            self._closed = True
            return changed

    def accepts(self, generation: CancellationGeneration) -> bool:
        with self._lock:
            return (
                not self._closed
                and generation == self._generation
                and not self._state.event.is_set()
            )

    def ensure_current(self, generation: CancellationGeneration) -> CancellationToken:
        with self._lock:
            cancelled = self._closed or self._state.event.is_set()
            if generation != self._generation or cancelled:
                raise StaleGenerationError(
                    generation,
                    self._generation,
                    current_cancelled=cancelled,
                )
            return CancellationToken(self._generation, self._state)
