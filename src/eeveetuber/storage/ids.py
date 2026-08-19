"""Stable externally persistable identifiers."""

from __future__ import annotations

from uuid import uuid4


def new_stable_id(prefix: str) -> str:
    if not prefix or not prefix.replace("_", "").isalnum():
        raise ValueError("ID prefix must contain only letters, digits, or underscores")
    return f"{prefix}_{uuid4().hex}"


def new_session_id() -> str:
    return new_stable_id("ses")


def new_message_id() -> str:
    return new_stable_id("msg")


def new_event_id() -> str:
    return new_stable_id("evt")


def new_checkpoint_id() -> str:
    return new_stable_id("chk")


def new_candidate_id() -> str:
    return new_stable_id("memc")


def new_memory_id() -> str:
    return new_stable_id("mem")


def new_revision_id() -> str:
    return new_stable_id("memr")


def new_decision_id() -> str:
    return new_stable_id("memd")


def new_outbox_id() -> str:
    return new_stable_id("out")

