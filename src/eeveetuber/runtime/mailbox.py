"""Deterministic bounded priority mailbox with observable overflow behavior."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass
from enum import StrEnum
from typing import cast


class OverflowPolicy(StrEnum):
    """Behavior when the bounded mailbox cannot append another item."""

    REJECT = "reject"
    DROP_LOWEST = "drop_lowest"
    COALESCE = "coalesce"


class PutOutcome(StrEnum):
    ACCEPTED = "accepted"
    COALESCED = "coalesced"
    EVICTED_LOWEST = "evicted_lowest"
    REJECTED_FULL = "rejected_full"
    CANCELLED = "cancelled"


class MailboxClosed(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PutResult[T]:
    outcome: PutOutcome
    item: T
    displaced: T | None = None

    @property
    def accepted(self) -> bool:
        return self.outcome in {
            PutOutcome.ACCEPTED,
            PutOutcome.COALESCED,
            PutOutcome.EVICTED_LOWEST,
        }


@dataclass(frozen=True, slots=True)
class MailboxStats:
    accepted: int
    dequeued: int
    coalesced: int
    rejected: int
    cancelled: int
    evicted: int
    discarded_on_close: int
    high_watermark: int


@dataclass(slots=True)
class _Entry[T]:
    item: T
    priority: int
    order: int


def _attribute_priority[T](item: T) -> int:
    value = getattr(item, "priority", 0)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("mailbox item priority must be an integer")
    return value


class PriorityMailbox[T]:
    """A small async mailbox optimized for explicit behavior, not heap throughput.

    Higher priorities are delivered first and equal priorities retain FIFO order.
    Under ``DROP_LOWEST``, a full mailbox accepts a new item only when it is
    strictly more important than the lowest queued priority; the newest item in
    that lowest tier is displaced. ``COALESCE`` combines matching pending items
    even before capacity is reached and rejects a full queue when no match exists.
    """

    def __init__(
        self,
        capacity: int,
        *,
        overflow: OverflowPolicy = OverflowPolicy.REJECT,
        priority_of: Callable[[T], int] | None = None,
        coalesce_key: Callable[[T], Hashable | None] | None = None,
        coalescer: Callable[[T, T], T] | None = None,
    ) -> None:
        if isinstance(capacity, bool) or capacity < 1:
            raise ValueError("mailbox capacity must be at least one")
        if overflow is OverflowPolicy.COALESCE and coalesce_key is None:
            raise ValueError("COALESCE overflow requires coalesce_key")
        self._capacity = capacity
        self._overflow = overflow
        self._priority_of = priority_of or _attribute_priority
        self._coalesce_key = coalesce_key
        self._coalescer = coalescer or (lambda _old, new: new)
        self._condition = asyncio.Condition()
        self._space_available = asyncio.Event()
        self._space_available.set()
        self._items: list[_Entry[T]] = []
        self._next_order = 0
        self._closed = False
        self._accepted = 0
        self._dequeued = 0
        self._coalesced = 0
        self._rejected = 0
        self._cancelled = 0
        self._evicted = 0
        self._discarded_on_close = 0
        self._high_watermark = 0

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def qsize(self) -> int:
        return len(self._items)

    @property
    def empty(self) -> bool:
        return not self._items

    @property
    def stats(self) -> MailboxStats:
        return MailboxStats(
            accepted=self._accepted,
            dequeued=self._dequeued,
            coalesced=self._coalesced,
            rejected=self._rejected,
            cancelled=self._cancelled,
            evicted=self._evicted,
            discarded_on_close=self._discarded_on_close,
            high_watermark=self._high_watermark,
        )

    async def put(self, item: T) -> PutResult[T]:
        """Attempt one immediate admission without waiting for capacity."""

        priority = self._priority_of(item)
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise TypeError("priority_of must return an integer")
        async with self._condition:
            result = self._try_put_locked(item, priority, reject_when_full=True)
            if result is None:  # pragma: no cover - immediate puts always resolve
                raise AssertionError("immediate mailbox put did not resolve")
            return result

    async def put_wait(
        self,
        item: T,
        *,
        cancel_waiter: Callable[[], Awaitable[object]] | None = None,
        accept_if: Callable[[], bool] | None = None,
        allow_priority_displacement: bool = True,
    ) -> PutResult[T]:
        """Wait losslessly for admission while remaining cancellation-aware.

        The normal coalescing and higher-priority displacement rules are applied
        before waiting unless ``allow_priority_displacement`` is false. In that mode,
        a full mailbox waits for genuine capacity instead of evicting an accepted item.
        ``accept_if`` is evaluated under the mailbox lock directly before mutation, so
        a generation check cannot race an ``await`` at commit. ``cancel_waiter`` is
        owned and cancelled by this call when no longer needed.
        """

        priority = self._priority_of(item)
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise TypeError("priority_of must return an integer")
        cancellation_task: asyncio.Future[object] | None = None
        space_task: asyncio.Task[bool] | None = None
        try:
            while True:
                if cancellation_task is not None and cancellation_task.done():
                    self._cancelled += 1
                    return PutResult(PutOutcome.CANCELLED, item)
                async with self._condition:
                    result = self._try_put_locked(
                        item,
                        priority,
                        reject_when_full=False,
                        accept_if=accept_if,
                        allow_priority_displacement=allow_priority_displacement,
                    )
                if result is not None:
                    return result

                if cancellation_task is None and cancel_waiter is not None:
                    cancellation_task = asyncio.ensure_future(cancel_waiter())
                space_task = asyncio.create_task(self._space_available.wait())
                if cancellation_task is None:
                    try:
                        await space_task
                    finally:
                        if not space_task.done():
                            space_task.cancel()
                            await asyncio.gather(space_task, return_exceptions=True)
                        space_task = None
                    continue
                try:
                    done, _pending = await asyncio.wait(
                        (space_task, cancellation_task),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    if not space_task.done():
                        space_task.cancel()
                        await asyncio.gather(space_task, return_exceptions=True)
                if cancellation_task in done:
                    self._cancelled += 1
                    return PutResult(PutOutcome.CANCELLED, item)
                space_task = None
        finally:
            if space_task is not None and not space_task.done():
                space_task.cancel()
                await asyncio.gather(space_task, return_exceptions=True)
            if cancellation_task is not None and not cancellation_task.done():
                cancellation_task.cancel()
                await asyncio.gather(cancellation_task, return_exceptions=True)

    async def get(self) -> T:
        async with self._condition:
            while not self._items:
                if self._closed:
                    raise MailboxClosed("mailbox is closed and drained")
                await self._condition.wait()
            index = max(
                range(len(self._items)),
                key=lambda candidate: (
                    self._items[candidate].priority,
                    -self._items[candidate].order,
                ),
            )
            entry = self._items.pop(index)
            self._dequeued += 1
            self._space_available.set()
            return entry.item

    async def close(self, *, discard: bool = False) -> tuple[T, ...]:
        """Close puts; optionally discard queued items and return them in delivery order."""

        async with self._condition:
            discarded: tuple[T, ...] = ()
            if discard and self._items:
                ordered = sorted(self._items, key=lambda entry: (-entry.priority, entry.order))
                discarded = tuple(entry.item for entry in ordered)
                self._discarded_on_close += len(discarded)
                self._items.clear()
            self._closed = True
            self._space_available.set()
            self._condition.notify_all()
            return discarded

    def _append(self, item: T, priority: int) -> None:
        self._items.append(_Entry(item=item, priority=priority, order=self._next_order))
        self._next_order += 1
        self._accepted += 1
        self._high_watermark = max(self._high_watermark, len(self._items))
        if len(self._items) >= self._capacity:
            self._space_available.clear()

    def _try_put_locked(
        self,
        item: T,
        priority: int,
        *,
        reject_when_full: bool,
        accept_if: Callable[[], bool] | None = None,
        allow_priority_displacement: bool = True,
    ) -> PutResult[T] | None:
        if self._closed:
            raise MailboxClosed("cannot put into a closed mailbox")
        if accept_if is not None and not accept_if():
            self._cancelled += 1
            return PutResult(PutOutcome.CANCELLED, item)

        if self._overflow is OverflowPolicy.COALESCE:
            match = self._matching_entry(item)
            if match is not None:
                previous = match.item
                combined = self._coalescer(previous, item)
                combined_priority = self._priority_of(combined)
                if isinstance(combined_priority, bool) or not isinstance(
                    combined_priority, int
                ):
                    raise TypeError("priority_of must return an integer")
                match.item = combined
                match.priority = combined_priority
                self._accepted += 1
                self._coalesced += 1
                self._condition.notify(1)
                return PutResult(PutOutcome.COALESCED, combined, displaced=previous)

        if len(self._items) < self._capacity:
            self._append(item, priority)
            self._condition.notify(1)
            return PutResult(PutOutcome.ACCEPTED, item)

        if self._overflow is OverflowPolicy.DROP_LOWEST and allow_priority_displacement:
            lowest_priority = min(entry.priority for entry in self._items)
            if priority > lowest_priority:
                candidates = [
                    (index, entry)
                    for index, entry in enumerate(self._items)
                    if entry.priority == lowest_priority
                ]
                drop_index, dropped_entry = max(candidates, key=lambda pair: pair[1].order)
                self._items.pop(drop_index)
                self._evicted += 1
                self._append(item, priority)
                self._condition.notify(1)
                return PutResult(
                    PutOutcome.EVICTED_LOWEST,
                    item,
                    displaced=dropped_entry.item,
                )

        if reject_when_full:
            self._rejected += 1
            return PutResult(PutOutcome.REJECTED_FULL, item)
        return None

    def _matching_entry(self, item: T) -> _Entry[T] | None:
        key_fn = cast(Callable[[T], Hashable | None], self._coalesce_key)
        requested_key = key_fn(item)
        if requested_key is None:
            return None
        for entry in self._items:
            if key_fn(entry.item) == requested_key:
                return entry
        return None
