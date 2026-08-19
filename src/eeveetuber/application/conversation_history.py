"""Bounded, deterministic projection of persisted recent conversation.

Conversation history is prompt data, never instruction authority.  This module is
pure: it performs no I/O and cannot invoke a model or a remote retrieval service.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from eeveetuber.storage import MessageRecord, MessageRole


@dataclass(frozen=True, slots=True)
class RecentConversationHistoryPolicy:
    max_messages: int = 12
    max_chars: int = 6_000
    max_message_chars: int = 1_500
    load_timeout_ms: int = 50

    def __post_init__(self) -> None:
        for name in ("max_messages", "max_chars", "max_message_chars", "load_timeout_ms"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
        if self.max_messages < 0 or self.max_chars < 0 or self.load_timeout_ms < 0:
            raise ValueError("history count, character, and timeout bounds cannot be negative")
        if self.max_message_chars <= 0:
            raise ValueError("max_message_chars must be positive")
        if self.max_messages > 1_000:
            raise ValueError("max_messages cannot exceed 1000")

    @property
    def enabled(self) -> bool:
        return self.max_messages > 0 and self.max_chars > 0 and self.load_timeout_ms > 0

    @property
    def fetch_limit(self) -> int:
        """Fetch a bounded surplus so malformed or ineligible rows do not dominate."""

        return min(1_000, max(self.max_messages, self.max_messages * 2))


@dataclass(frozen=True, slots=True)
class RecentHistoryMessage:
    message_id: str
    sequence: int
    generation: int
    role: MessageRole
    content: str
    truncated: bool


@dataclass(frozen=True, slots=True)
class RecentConversationHistory:
    messages: tuple[RecentHistoryMessage, ...] = ()
    rendered_context: str = ""

    @property
    def character_count(self) -> int:
        return len(self.rendered_context)

    @property
    def last_sequence(self) -> int | None:
        return self.messages[-1].sequence if self.messages else None


class RecentConversationHistoryCompiler:
    def __init__(self, policy: RecentConversationHistoryPolicy) -> None:
        self.policy = policy

    def compile(
        self,
        records: tuple[MessageRecord, ...],
        *,
        session_id: str,
        before_generation: int,
    ) -> RecentConversationHistory:
        if not self.policy.enabled:
            return RecentConversationHistory()
        eligible: list[RecentHistoryMessage] = []
        seen_ids: set[str] = set()
        for record in sorted(records, key=lambda item: (item.sequence, item.message_id)):
            if record.message_id in seen_ids or record.session_id != session_id:
                continue
            seen_ids.add(record.message_id)
            if record.role not in {MessageRole.USER, MessageRole.ASSISTANT}:
                continue
            generation = record.metadata.get("generation")
            if (
                not isinstance(generation, int)
                or isinstance(generation, bool)
                or generation < 0
                or generation >= before_generation
            ):
                continue
            content = record.content.strip()
            if not content:
                continue
            bounded = content[: self.policy.max_message_chars]
            eligible.append(
                RecentHistoryMessage(
                    message_id=record.message_id,
                    sequence=record.sequence,
                    generation=generation,
                    role=record.role,
                    content=bounded,
                    truncated=len(bounded) < len(content),
                )
            )

        selected: list[RecentHistoryMessage] = []
        for message in reversed(eligible[-self.policy.max_messages :]):
            fitted = self._fit_message(message, selected)
            if fitted is not None:
                selected.insert(0, fitted)
        if not selected:
            return RecentConversationHistory()
        rendered = _render(tuple(selected))
        return RecentConversationHistory(messages=tuple(selected), rendered_context=rendered)

    def _fit_message(
        self,
        message: RecentHistoryMessage,
        newer_messages: list[RecentHistoryMessage],
    ) -> RecentHistoryMessage | None:
        trial = (message, *newer_messages)
        if len(_render(trial)) <= self.policy.max_chars:
            return message

        low = 0
        high = len(message.content)
        best: RecentHistoryMessage | None = None
        while low <= high:
            midpoint = (low + high) // 2
            candidate = RecentHistoryMessage(
                message_id=message.message_id,
                sequence=message.sequence,
                generation=message.generation,
                role=message.role,
                content=message.content[:midpoint],
                truncated=True,
            )
            if midpoint > 0 and len(_render((candidate, *newer_messages))) <= self.policy.max_chars:
                best = candidate
                low = midpoint + 1
            else:
                high = midpoint - 1
        return best


def _render(messages: tuple[RecentHistoryMessage, ...]) -> str:
    payload = [
        {
            "message_id": message.message_id,
            "sequence": message.sequence,
            "generation": message.generation,
            "role": message.role.value,
            "content": message.content,
            "truncated": message.truncated,
        }
        for message in messages
    ]
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return "\n".join(
        (
            '<recent_conversation instruction_authority="false" data_only="true" '
            'may_be_incomplete="true">',
            serialized,
            "</recent_conversation>",
        )
    )

