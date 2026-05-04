"""Sleeper /players/nfl ingestion. ~5MB; refresh weekly by default."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb

from lddl.clients.sleeper import SleeperClient

PLAYERS_CACHE_FILENAME = "players_nfl.json"
DEFAULT_MAX_AGE = timedelta(days=7)


def _cache_age(cache_path: Path) -> timedelta | None:
    if not cache_path.exists():
        return None
    mtime = datetime.fromtimestamp(cache_path.stat().st_mtime, tz=timezone.utc)
    return datetime.now(timezone.utc) - mtime


def refresh_players(
    client: SleeperClient,
    conn: duckdb.DuckDBPyConnection,
    *,
    force: bool = False,
    max_age: timedelta = DEFAULT_MAX_AGE,
) -> tuple[int, bool]:
    """Refresh players if cache is older than ``max_age`` or ``force`` is set.

    Returns (rows_in_table, fetched_fresh).
    """
    cache_path = client.cache_dir / PLAYERS_CACHE_FILENAME
    age = _cache_age(cache_path)
    fetched_fresh = force or age is None or age > max_age

    data = client.get_players_nfl(force=fetched_fresh)
    fetched_at = datetime.now(timezone.utc)

    rows = []
    for pid, p in data.items():
        if not isinstance(p, dict):
            continue
        rows.append(
            [
                str(pid),
                p.get("full_name"),
                p.get("first_name"),
                p.get("last_name"),
                p.get("position"),
                json.dumps(p.get("fantasy_positions") or []),
                p.get("team"),
                p.get("age"),
                p.get("years_exp"),
                p.get("status"),
                p.get("injury_status"),
                json.dumps(p.get("metadata") or {}),
                fetched_at,
            ]
        )
    conn.execute("DELETE FROM players")
    if rows:
        conn.executemany(
            """
            INSERT INTO players (
                player_id, full_name, first_name, last_name, position,
                fantasy_positions, team, age, years_exp, status,
                injury_status, metadata, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows), fetched_fresh
