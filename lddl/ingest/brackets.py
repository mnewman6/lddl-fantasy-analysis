"""Playoff bracket ingestion (winners + losers)."""

from __future__ import annotations

import json

import duckdb

from lddl.clients.sleeper import SleeperClient


def _rows_for(bracket: str, league_id: str, games: list[dict]) -> list[list]:
    rows = []
    for g in games:
        rows.append(
            [
                league_id,
                bracket,
                g.get("m"),
                g.get("r"),
                g.get("p"),
                g.get("t1"),
                g.get("t2"),
                g.get("w"),
                g.get("l"),
                json.dumps(g.get("t1_from")) if g.get("t1_from") else None,
                json.dumps(g.get("t2_from")) if g.get("t2_from") else None,
            ]
        )
    return rows


def ingest_brackets(
    client: SleeperClient,
    conn: duckdb.DuckDBPyConnection,
    league_id: str,
    *,
    force: bool = False,
) -> tuple[int, int]:
    """Return (winners_games, losers_games)."""
    winners = client.get_winners_bracket(league_id, force=force)
    losers = client.get_losers_bracket(league_id, force=force)

    rows = _rows_for("winners", league_id, winners) + _rows_for("losers", league_id, losers)
    conn.execute("DELETE FROM playoff_bracket WHERE league_id = ?", [league_id])
    if rows:
        conn.executemany(
            """
            INSERT INTO playoff_bracket (
                league_id, bracket, match_id, round, placement,
                t1, t2, winner, loser, t1_from, t2_from
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(winners), len(losers)
