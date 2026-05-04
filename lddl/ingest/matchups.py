"""Per-week matchup ingestion."""

from __future__ import annotations

import json

import duckdb

from lddl.clients.sleeper import SleeperClient

# Sleeper supports up to leg 18 (NFL regular + playoffs). We iterate through
# the last possible week and drop empty/zero entries; a missing week returns
# either [] or all-zero point entries depending on whether it has been played.
MAX_WEEK = 18


def ingest_matchups(
    client: SleeperClient,
    conn: duckdb.DuckDBPyConnection,
    league_id: str,
    *,
    force: bool = False,
) -> int:
    rows = []
    for week in range(1, MAX_WEEK + 1):
        entries = client.get_matchups(league_id, week, force=force)
        if not entries:
            continue
        # Skip weeks that haven't been played yet (everyone scored 0 and there
        # are no players_points data points). This avoids polluting the table
        # with future-week placeholders for an in-progress season.
        played = any((e.get("points") or 0) > 0 for e in entries)
        if not played:
            continue
        for e in entries:
            rows.append(
                [
                    league_id,
                    week,
                    e.get("matchup_id"),
                    e["roster_id"],
                    e.get("points"),
                    e.get("custom_points"),
                    json.dumps(e.get("starters") or []),
                    json.dumps(e.get("starters_points") or []),
                    json.dumps(e.get("players") or []),
                    json.dumps(e.get("players_points") or {}),
                ]
            )
    if rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO matchups (
                league_id, week, matchup_id, roster_id, points, custom_points,
                starters, starters_points, players, players_points
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)
