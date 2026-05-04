"""Draft ingestion: drafts, picks, and draft-level traded picks."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import duckdb

from lddl.clients.sleeper import SleeperClient


def _ts(ms: int | None) -> datetime | None:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def ingest_drafts(
    client: SleeperClient,
    conn: duckdb.DuckDBPyConnection,
    league_id: str,
    *,
    force: bool = False,
) -> tuple[int, int, int]:
    """Return (drafts, picks, traded-picks)."""
    drafts = client.get_drafts(league_id, force=force)
    draft_rows: list[list] = []
    pick_rows: list[list] = []
    dtp_rows: list[list] = []

    for d in drafts:
        draft_id = d["draft_id"]
        settings = d.get("settings") or {}
        draft_rows.append(
            [
                draft_id,
                league_id,
                str(d.get("season")) if d.get("season") is not None else None,
                d.get("type"),
                d.get("status"),
                d.get("sport"),
                settings.get("rounds"),
                json.dumps(settings),
                json.dumps(d.get("metadata") or {}),
                _ts(d.get("start_time")),
                _ts(d.get("last_picked")),
                json.dumps(d.get("draft_order") or {}),
                json.dumps(d.get("slot_to_roster_id") or {}),
            ]
        )

        # In-progress drafts can change pick-by-pick; force-refresh those.
        force_picks = force or d.get("status") != "complete"

        for p in client.get_draft_picks(draft_id, force=force_picks):
            pick_rows.append(
                [
                    draft_id,
                    p.get("pick_no"),
                    p.get("round"),
                    p.get("draft_slot"),
                    p.get("roster_id"),
                    p.get("picked_by"),
                    str(p.get("player_id")) if p.get("player_id") else None,
                    bool(p.get("is_keeper")) if p.get("is_keeper") is not None else False,
                    json.dumps(p.get("metadata") or {}),
                ]
            )

        for tp in client.get_draft_traded_picks(draft_id, force=force_picks):
            dtp_rows.append(
                [
                    draft_id,
                    str(tp.get("season")),
                    tp.get("round"),
                    tp.get("roster_id"),
                    tp.get("owner_id"),
                    tp.get("previous_owner_id"),
                ]
            )

    if draft_rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO drafts (
                draft_id, league_id, season, type, status, sport, rounds,
                settings, metadata, start_time, last_picked, draft_order,
                slot_to_roster_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            draft_rows,
        )

    if pick_rows:
        # A pick can be re-made if the draft is still in progress; replace.
        ids = sorted({row[0] for row in pick_rows})
        placeholders = ",".join(["?"] * len(ids))
        conn.execute(
            f"DELETE FROM draft_picks WHERE draft_id IN ({placeholders})", ids
        )
        conn.executemany(
            """
            INSERT INTO draft_picks (
                draft_id, pick_no, round, draft_slot, roster_id, picked_by,
                player_id, is_keeper, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            pick_rows,
        )

    if dtp_rows:
        ids = sorted({row[0] for row in dtp_rows})
        placeholders = ",".join(["?"] * len(ids))
        conn.execute(
            f"DELETE FROM draft_traded_picks WHERE draft_id IN ({placeholders})", ids
        )
        conn.executemany(
            """
            INSERT INTO draft_traded_picks (
                draft_id, season, round, roster_id, owner_id, previous_owner_id
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            dtp_rows,
        )

    return len(draft_rows), len(pick_rows), len(dtp_rows)
