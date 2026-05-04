"""Transaction ingestion: trades, waivers, free-agent moves, commissioner.

Sleeper's transactions endpoint returns all moves for a given week (leg). A
trade has multiple roster_ids and may include both player adds/drops and
draft picks; waivers/free agents have one roster_id and only player moves.
We normalize into three tables: ``transactions`` (header), ``transaction_players``
(adds + drops), and ``transaction_picks`` (picks moved as part of a trade).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import duckdb

from lddl.clients.sleeper import SleeperClient

MAX_WEEK = 18


def _ts(ms: int | None) -> datetime | None:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def ingest_transactions(
    client: SleeperClient,
    conn: duckdb.DuckDBPyConnection,
    league_id: str,
    *,
    force: bool = False,
) -> tuple[int, int, int]:
    """Return (transactions, player_movements, pick_movements)."""
    tx_rows: list[list] = []
    player_rows: list[list] = []
    pick_rows: list[list] = []
    for week in range(1, MAX_WEEK + 1):
        entries = client.get_transactions(league_id, week, force=force)
        if not entries:
            continue
        for t in entries:
            tx_id = t["transaction_id"]
            tx_rows.append(
                [
                    tx_id,
                    league_id,
                    week,
                    t.get("type"),
                    t.get("status"),
                    t.get("creator"),
                    _ts(t.get("created")),
                    _ts(t.get("status_updated")),
                    json.dumps(t.get("roster_ids") or []),
                    json.dumps(t.get("consenter_ids") or []),
                    json.dumps(t.get("waiver_budget") or []),
                    t.get("leg"),
                    json.dumps(t.get("settings") or {}),
                    json.dumps(t.get("metadata") or {}),
                ]
            )
            for player_id, roster_id in (t.get("adds") or {}).items():
                player_rows.append([tx_id, str(player_id), roster_id, "add"])
            for player_id, roster_id in (t.get("drops") or {}).items():
                player_rows.append([tx_id, str(player_id), roster_id, "drop"])
            for p in t.get("draft_picks") or []:
                pick_rows.append(
                    [
                        tx_id,
                        str(p.get("season")),
                        p.get("round"),
                        p.get("roster_id"),
                        p.get("owner_id"),
                        p.get("previous_owner_id"),
                    ]
                )
    if tx_rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO transactions (
                transaction_id, league_id, week, type, status, creator,
                created_at, status_updated_at, roster_ids, consenter_ids,
                waiver_budget, leg, settings, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            tx_rows,
        )
    # Replace child rows for any (re-)ingested transactions to avoid stale entries.
    if tx_rows:
        ids = [row[0] for row in tx_rows]
        placeholders = ",".join(["?"] * len(ids))
        conn.execute(
            f"DELETE FROM transaction_players WHERE transaction_id IN ({placeholders})",
            ids,
        )
        conn.execute(
            f"DELETE FROM transaction_picks WHERE transaction_id IN ({placeholders})",
            ids,
        )
    if player_rows:
        conn.executemany(
            """
            INSERT INTO transaction_players (
                transaction_id, player_id, roster_id, movement
            ) VALUES (?, ?, ?, ?)
            """,
            player_rows,
        )
    if pick_rows:
        conn.executemany(
            """
            INSERT INTO transaction_picks (
                transaction_id, season, round, roster_id, owner_id, previous_owner_id
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            pick_rows,
        )
    return len(tx_rows), len(player_rows), len(pick_rows)


def ingest_traded_picks(
    client: SleeperClient,
    conn: duckdb.DuckDBPyConnection,
    league_id: str,
    *,
    force: bool = False,
) -> int:
    """League-level outstanding traded picks (not yet used)."""
    picks = client.get_traded_picks(league_id, force=force)
    conn.execute("DELETE FROM traded_picks WHERE league_id = ?", [league_id])
    rows = [
        [
            league_id,
            str(p.get("season")),
            p.get("round"),
            p.get("roster_id"),
            p.get("owner_id"),
            p.get("previous_owner_id"),
        ]
        for p in picks
    ]
    if rows:
        conn.executemany(
            """
            INSERT INTO traded_picks (
                league_id, season, round, roster_id, owner_id, previous_owner_id
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)
