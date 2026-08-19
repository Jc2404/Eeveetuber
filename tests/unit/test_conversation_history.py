from __future__ import annotations

import json
from datetime import UTC, datetime

from eeveetuber.application.conversation_history import (
    RecentConversationHistoryCompiler,
    RecentConversationHistoryPolicy,
)
from eeveetuber.storage import MessageRecord, MessageRole

NOW = datetime(2026, 8, 20, 8, tzinfo=UTC)


def message(
    sequence: int,
    role: MessageRole,
    content: str,
    *,
    session_id: str = "session-a",
    generation: object = 1,
) -> MessageRecord:
    return MessageRecord(
        message_id=f"message-{session_id}-{sequence}",
        session_id=session_id,
        sequence=sequence,
        role=role,
        content=content,
        created_at=NOW,
        metadata={"generation": generation},
    )


def payload(rendered: str) -> list[dict[str, object]]:
    return json.loads(rendered.splitlines()[1])  # type: ignore[no-any-return]


def test_history_filters_session_role_and_generation_then_preserves_order() -> None:
    compiler = RecentConversationHistoryCompiler(
        RecentConversationHistoryPolicy(max_messages=4, max_chars=2_000)
    )
    records = (
        message(5, MessageRole.ASSISTANT, "future", generation=3),
        message(2, MessageRole.ASSISTANT, "prior answer", generation=1),
        message(1, MessageRole.USER, "prior question", generation=1),
        message(3, MessageRole.TOOL, "tool internals", generation=1),
        message(4, MessageRole.USER, "other session", session_id="session-b", generation=1),
    )

    history = compiler.compile(records, session_id="session-a", before_generation=2)

    assert [item.sequence for item in history.messages] == [1, 2]
    assert [item.role for item in history.messages] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    assert [item["content"] for item in payload(history.rendered_context)] == [
        "prior question",
        "prior answer",
    ]
    assert 'instruction_authority="false"' in history.rendered_context
    assert 'data_only="true"' in history.rendered_context


def test_history_favors_newest_messages_and_honors_exact_character_cap() -> None:
    policy = RecentConversationHistoryPolicy(
        max_messages=2,
        max_chars=430,
        max_message_chars=50,
    )
    history = RecentConversationHistoryCompiler(policy).compile(
        tuple(
            message(sequence, MessageRole.USER, f"turn-{sequence} " + "x" * 180)
            for sequence in range(1, 5)
        ),
        session_id="session-a",
        before_generation=2,
    )

    assert len(history.messages) <= 2
    assert history.messages[-1].sequence == 4
    assert len(history.rendered_context) <= policy.max_chars
    assert any(item.truncated for item in history.messages)


def test_history_can_be_disabled_without_touching_records() -> None:
    history = RecentConversationHistoryCompiler(
        RecentConversationHistoryPolicy(max_messages=0)
    ).compile(
        (message(1, MessageRole.USER, "not loaded"),),
        session_id="session-a",
        before_generation=2,
    )
    assert history.messages == ()
    assert history.rendered_context == ""
