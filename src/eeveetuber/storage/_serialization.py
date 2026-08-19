"""Internal, lossless serialization helpers."""

from __future__ import annotations

from datetime import UTC, datetime


def encode_datetime(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def decode_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("stored timestamp is not timezone-aware")
    return parsed.astimezone(UTC)


def encode_optional_datetime(value: datetime | None) -> str | None:
    return encode_datetime(value) if value is not None else None


def decode_optional_datetime(value: str | None) -> datetime | None:
    return decode_datetime(value) if value is not None else None

