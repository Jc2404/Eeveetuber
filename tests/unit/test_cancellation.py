from __future__ import annotations

import asyncio

import pytest

from eeveetuber.runtime.cancellation import (
    CancellationGeneration,
    CancellationSource,
    StaleGenerationError,
)


def test_advance_cancels_old_token_before_returning_new_generation() -> None:
    source = CancellationSource()
    old = source.token()

    current = source.advance("owner started a replacement turn")

    assert old.cancelled
    assert old.reason == "owner started a replacement turn"
    assert old.cancelled_at_monotonic_ms is not None
    assert current.generation == CancellationGeneration(1)
    assert not current.cancelled
    assert not source.accepts(old.generation)
    assert source.accepts(current.generation)


@pytest.mark.asyncio
async def test_wait_and_raise_expose_cancellation_reason() -> None:
    source = CancellationSource()
    token = source.token()
    waiter = asyncio.create_task(token.wait_cancelled())
    await asyncio.sleep(0)

    assert source.cancel_current("barge-in")
    assert not source.cancel_current("ignored duplicate")
    assert await waiter == "barge-in"
    with pytest.raises(asyncio.CancelledError, match="barge-in"):
        token.raise_if_cancelled()


def test_current_cancelled_and_old_generations_are_rejected() -> None:
    source = CancellationSource(initial_generation=4)
    old = source.token()
    current = source.advance("next turn")

    with pytest.raises(StaleGenerationError) as stale:
        source.ensure_current(old.generation)
    assert stale.value.received == CancellationGeneration(4)
    assert not stale.value.current_cancelled

    source.cancel_current("stop speech")
    with pytest.raises(StaleGenerationError) as cancelled:
        source.ensure_current(current.generation)
    assert cancelled.value.current_cancelled


def test_close_is_terminal_and_idempotently_cancels_current_token() -> None:
    source = CancellationSource()
    token = source.token()

    assert source.close("shutdown")
    assert not source.close("shutdown again")
    assert source.closed
    assert token.cancelled
    assert not source.accepts(token.generation)
    with pytest.raises(RuntimeError, match="closed"):
        source.advance("too late")


@pytest.mark.parametrize("value", [-1, True])
def test_invalid_generation_is_rejected(value: int) -> None:
    with pytest.raises(ValueError, match="generation"):
        CancellationGeneration(value)

