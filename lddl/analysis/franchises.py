"""Franchise-continuity overrides.

When a manager retires and someone else takes over their roster mid-league,
Sleeper treats them as different user_ids but the league treats it as one
continuous franchise. This module merges those identities at aggregation
time. Underlying DB rows are untouched — per-season detail still shows the
person who was actually managing that year.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Franchise:
    primary_user_id: str
    predecessor_user_ids: tuple[str, ...]
    note: str = ""


# Confirmed 2026-05-04: roster 3 in LDDL.
# jmsmeiz ("Smeisman Life") drafted in 2023, retired after that season.
# JN55 took over roster 3 from 2024 onward ("Goofy Goobers" → "50 Shades of Gadsden").
FRANCHISES: list[Franchise] = [
    Franchise(
        primary_user_id="1147348215595986944",
        predecessor_user_ids=("471027040024260608",),
        note="JN55 succeeded jmsmeiz on roster 3 after the 2023 season",
    ),
]

_MERGE_MAP: dict[str, str] = {
    pred: f.primary_user_id
    for f in FRANCHISES
    for pred in f.predecessor_user_ids
}


def canonical_user_id(user_id: str | None) -> str | None:
    """Return the franchise's primary user_id, or `user_id` unchanged."""
    if user_id is None:
        return None
    return _MERGE_MAP.get(user_id, user_id)


def is_predecessor(user_id: str | None) -> bool:
    return user_id is not None and user_id in _MERGE_MAP
