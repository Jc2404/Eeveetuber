from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from eeveetuber.runtime.mailbox import (
    MailboxClosed,
    OverflowPolicy,
    PriorityMailbox,
    PutOutcome,
)


@dataclass(frozen=True)
class Item:
    name: str
    priority: int = 0
    key: str | None = None
    count: int = 1


@pytest.mark.asyncio
async def test_priority_order_and_fifo_ties_are_deterministic() -> None:
    mailbox = PriorityMailbox[Item](4)
    await mailbox.put(Item("normal-first", 10))
    await mailbox.put(Item("urgent-first", 50))
    await mailbox.put(Item("normal-second", 10))
    await mailbox.put(Item("urgent-second", 50))

    assert [
        (await mailbox.get()).name,
        (await mailbox.get()).name,
        (await mailbox.get()).name,
        (await mailbox.get()).name,
    ] == ["urgent-first", "urgent-second", "normal-first", "normal-second"]
    assert mailbox.stats.dequeued == 4
    assert mailbox.stats.high_watermark == 4


@pytest.mark.asyncio
async def test_reject_policy_reports_pressure_without_blocking() -> None:
    mailbox = PriorityMailbox[Item](1, overflow=OverflowPolicy.REJECT)
    first = await mailbox.put(Item("first"))
    second = await mailbox.put(Item("second", 100))

    assert first.outcome is PutOutcome.ACCEPTED
    assert second.outcome is PutOutcome.REJECTED_FULL
    assert not second.accepted
    assert (await mailbox.get()).name == "first"
    assert mailbox.stats.rejected == 1


@pytest.mark.asyncio
async def test_drop_lowest_only_evicts_for_strictly_higher_priority() -> None:
    mailbox = PriorityMailbox[Item](3, overflow=OverflowPolicy.DROP_LOWEST)
    await mailbox.put(Item("old-low", 1))
    await mailbox.put(Item("new-low", 1))
    await mailbox.put(Item("normal", 5))

    equal = await mailbox.put(Item("equal-low", 1))
    urgent = await mailbox.put(Item("urgent", 20))

    assert equal.outcome is PutOutcome.REJECTED_FULL
    assert urgent.outcome is PutOutcome.EVICTED_LOWEST
    assert urgent.displaced == Item("new-low", 1)
    assert [(await mailbox.get()).name for _ in range(3)] == [
        "urgent",
        "normal",
        "old-low",
    ]
    assert mailbox.stats.evicted == 1


@pytest.mark.asyncio
async def test_coalescing_replaces_matching_pending_item_and_preserves_order() -> None:
    def combine(old: Item, new: Item) -> Item:
        return Item(new.name, max(old.priority, new.priority), new.key, old.count + new.count)

    mailbox = PriorityMailbox[Item](2, overflow=OverflowPolicy.COALESCE, coalesce_key=lambda i: i.key, coalescer=combine)
    await mailbox.put(Item("partial-1", 4, "transcript", 1))
    await mailbox.put(Item("other", 4, "sensor", 1))
    result = await mailbox.put(Item("partial-2", 4, "transcript", 2))
    rejected = await mailbox.put(Item("third", 4, "different", 1))

    assert result.outcome is PutOutcome.COALESCED
    assert result.displaced == Item("partial-1", 4, "transcript", 1)
    assert rejected.outcome is PutOutcome.REJECTED_FULL
    assert await mailbox.get() == Item("partial-2", 4, "transcript", 3)
    assert (await mailbox.get()).name == "other"
    assert mailbox.stats.coalesced == 1


@pytest.mark.asyncio
async def test_close_can_drain_or_discard_and_wakes_waiters() -> None:
    draining = PriorityMailbox[Item](2)
    await draining.put(Item("low", 1))
    await draining.put(Item("high", 2))
    await draining.close()
    assert (await draining.get()).name == "high"
    assert (await draining.get()).name == "low"
    with pytest.raises(MailboxClosed):
        await draining.get()

    waiting = PriorityMailbox[Item](1)
    waiter = asyncio.create_task(waiting.get())
    await asyncio.sleep(0)
    discarded = await waiting.close(discard=True)
    assert discarded == ()
    with pytest.raises(MailboxClosed):
        await waiter
    with pytest.raises(MailboxClosed):
        await waiting.put(Item("late"))

    full = PriorityMailbox[Item](2)
    await full.put(Item("low", 1))
    await full.put(Item("high", 2))
    assert await full.close(discard=True) == (Item("high", 2), Item("low", 1))
    assert full.stats.discarded_on_close == 2


@pytest.mark.asyncio
async def test_waiting_put_backpressures_until_get_frees_capacity() -> None:
    mailbox = PriorityMailbox[Item](1, overflow=OverflowPolicy.DROP_LOWEST)
    await mailbox.put(Item("first", 10))
    waiting = asyncio.create_task(mailbox.put_wait(Item("second", 10)))
    await asyncio.sleep(0)

    assert not waiting.done()
    assert (await mailbox.get()).name == "first"
    result = await asyncio.wait_for(waiting, timeout=0.2)

    assert result.outcome is PutOutcome.ACCEPTED
    assert (await mailbox.get()).name == "second"
    assert mailbox.stats.rejected == 0


@pytest.mark.asyncio
async def test_waiting_put_is_cancellable_without_mutating_queue() -> None:
    mailbox = PriorityMailbox[Item](1)
    await mailbox.put(Item("queued"))
    cancelled = asyncio.Event()
    waiting = asyncio.create_task(
        mailbox.put_wait(Item("blocked"), cancel_waiter=cancelled.wait)
    )
    await asyncio.sleep(0)

    cancelled.set()
    result = await asyncio.wait_for(waiting, timeout=0.2)

    assert result.outcome is PutOutcome.CANCELLED
    assert not result.accepted
    assert (await mailbox.get()).name == "queued"
    assert mailbox.stats.cancelled == 1


@pytest.mark.asyncio
async def test_waiting_put_keeps_immediate_high_priority_displacement() -> None:
    mailbox = PriorityMailbox[Item](1, overflow=OverflowPolicy.DROP_LOWEST)
    await mailbox.put(Item("normal", 10))

    result = await mailbox.put_wait(Item("critical", 100))

    assert result.outcome is PutOutcome.EVICTED_LOWEST
    assert result.displaced == Item("normal", 10)
    assert (await mailbox.get()).name == "critical"


@pytest.mark.asyncio
async def test_non_displacing_wait_preserves_accepted_item_until_capacity_exists() -> None:
    mailbox = PriorityMailbox[Item](1, overflow=OverflowPolicy.DROP_LOWEST)
    await mailbox.put(Item("accepted-high", 50))
    waiting = asyncio.create_task(
        mailbox.put_wait(
            Item("waiting-critical", 100),
            allow_priority_displacement=False,
        )
    )
    await asyncio.sleep(0)

    assert not waiting.done()
    assert mailbox.qsize == 1
    assert mailbox.stats.evicted == 0
    assert (await mailbox.get()).name == "accepted-high"

    result = await asyncio.wait_for(waiting, timeout=0.2)
    assert result.outcome is PutOutcome.ACCEPTED
    assert result.displaced is None
    assert (await mailbox.get()).name == "waiting-critical"


@pytest.mark.asyncio
async def test_close_wakes_waiting_put_without_deadlock() -> None:
    mailbox = PriorityMailbox[Item](1)
    await mailbox.put(Item("queued"))
    waiting = asyncio.create_task(mailbox.put_wait(Item("blocked")))
    await asyncio.sleep(0)

    await mailbox.close()

    with pytest.raises(MailboxClosed):
        await asyncio.wait_for(waiting, timeout=0.2)


@pytest.mark.asyncio
async def test_external_task_cancellation_leaves_mailbox_usable() -> None:
    mailbox = PriorityMailbox[Item](1)
    await mailbox.put(Item("queued"))
    waiting = asyncio.create_task(mailbox.put_wait(Item("blocked")))
    await asyncio.sleep(0)

    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert (await mailbox.get()).name == "queued"
    assert (await mailbox.put(Item("replacement"))).accepted


def test_mailbox_configuration_is_validated() -> None:
    with pytest.raises(ValueError, match="capacity"):
        PriorityMailbox[Item](0)
    with pytest.raises(ValueError, match="coalesce_key"):
        PriorityMailbox[Item](1, overflow=OverflowPolicy.COALESCE)
