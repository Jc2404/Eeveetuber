"""Bounded lexical-query normalization shared by SQLite FTS repositories."""

from __future__ import annotations

import re


def safe_fts_query(value: str, *, max_terms: int = 16) -> str:
    if max_terms < 1:
        raise ValueError("max_terms must be positive")
    terms = re.findall(r"[^\W_]+", value, flags=re.UNICODE)
    return " AND ".join(f'"{term}"' for term in terms[:max_terms])
