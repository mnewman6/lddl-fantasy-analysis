"""Walk Sleeper's previous_league_id chain and persist league/user/roster rows."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import duckdb

from lddl.clients.sleeper import SleeperClient


def walk_history(
    client: SleeperClient,
    head_league_id: str,
    *,
    force_head: bool = True,
) -> list[dict]:
    """Return list of league records, oldest first.

    `head_league_id` is the most-recent season; we walk backwards via
    ``previous_league_id`` until that field is null. The head record is
    re-fetched (force=True) by default since its status/settings can change;
    older seasons use the on-disk cache when present.
    """
    seen: set[str] = set()
    chain: list[dict] = []
    lid: str | None = head_league_id
    is_head = True
    while lid:
        if lid in seen:
            raise RuntimeError(f"cycle detected at league_id={lid}")
        seen.add(lid)
        league = client.get_league(lid, force=force_head and is_head)
        if league is None:
            raise RuntimeError(f"league not found: {lid}")
        chain.append(league)
        lid = league.get("previous_league_id")
        is_head = False
    chain.reverse()
    return chain


def upsert_league(conn: duckdb.DuckDBPyConnection, league: dict) -> None:
    settings = league.get("settings") or {}
    conn.execute(
        """
        INSERT OR REPLACE INTO leagues (
            league_id, previous_league_id, season, name, status, sport,
            total_rosters, league_type, playoff_week_start, playoff_teams,
            settings, scoring_settings, roster_positions, metadata, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            league["league_id"],
            league.get("previous_league_id"),
            league.get("season"),
            league.get("name"),
            league.get("status"),
            league.get("sport"),
            league.get("total_rosters"),
            settings.get("type"),
            settings.get("playoff_week_start"),
            settings.get("playoff_teams"),
            json.dumps(settings),
            json.dumps(league.get("scoring_settings") or {}),
            json.dumps(league.get("roster_positions") or []),
            json.dumps(league.get("metadata") or {}),
            datetime.now(timezone.utc),
        ],
    )


def ingest_users(
    client: SleeperClient,
    conn: duckdb.DuckDBPyConnection,
    league_id: str,
    *,
    force: bool = False,
) -> int:
    users = client.get_users(league_id, force=force)
    rows = []
    for u in users:
        meta = u.get("metadata") or {}
        rows.append(
            [
                league_id,
                u["user_id"],
                u.get("display_name"),
                meta.get("team_name"),
                bool(u.get("is_owner")),
                bool(u.get("is_bot")),
                u.get("avatar"),
                json.dumps(meta),
            ]
        )
    if rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO league_users (
                league_id, user_id, display_name, team_name, is_owner, is_bot,
                avatar, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def ingest_rosters(
    client: SleeperClient,
    conn: duckdb.DuckDBPyConnection,
    league_id: str,
    *,
    force: bool = False,
) -> int:
    rosters = client.get_rosters(league_id, force=force)
    rows = []
    for r in rosters:
        rs = r.get("settings") or {}
        fpts = float(rs.get("fpts") or 0) + float(rs.get("fpts_decimal") or 0) / 100
        fpts_against = (
            float(rs.get("fpts_against") or 0)
            + float(rs.get("fpts_against_decimal") or 0) / 100
        )
        ppts = float(rs.get("ppts") or 0) + float(rs.get("ppts_decimal") or 0) / 100
        rows.append(
            [
                league_id,
                r["roster_id"],
                r.get("owner_id"),
                json.dumps(r.get("co_owners") or []),
                rs.get("division"),
                json.dumps(r.get("players") or []),
                json.dumps(r.get("starters") or []),
                json.dumps(r.get("taxi") or []),
                json.dumps(r.get("reserve") or []),
                json.dumps(r.get("keepers") or []),
                rs.get("wins") or 0,
                rs.get("losses") or 0,
                rs.get("ties") or 0,
                fpts,
                fpts_against,
                ppts,
                rs.get("waiver_position"),
                rs.get("waiver_budget_used"),
                rs.get("total_moves") or 0,
                json.dumps(rs),
                json.dumps(r.get("metadata") or {}),
            ]
        )
    if rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO rosters (
                league_id, roster_id, owner_id, co_owners, division, players,
                starters, taxi, reserve, keepers, wins, losses, ties, fpts,
                fpts_against, ppts, waiver_position, waiver_budget_used,
                total_moves, settings, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def rebuild_managers(conn: duckdb.DuckDBPyConnection) -> int:
    """Rebuild the deduplicated managers table from league_users + leagues."""
    conn.execute("DELETE FROM managers")
    conn.execute(
        """
        WITH ranked AS (
            SELECT
                lu.user_id,
                lu.display_name,
                lu.team_name,
                l.season,
                ROW_NUMBER() OVER (
                    PARTITION BY lu.user_id ORDER BY l.season DESC
                ) AS rn
            FROM league_users lu
            JOIN leagues l USING (league_id)
        )
        INSERT INTO managers (
            user_id, display_name, aliases, team_names,
            first_seen_season, last_seen_season
        )
        SELECT
            user_id,
            MAX(CASE WHEN rn = 1 THEN display_name END) AS display_name,
            to_json(list_distinct(list(display_name)
                FILTER (WHERE display_name IS NOT NULL))) AS aliases,
            to_json(list_distinct(list(team_name)
                FILTER (WHERE team_name IS NOT NULL))) AS team_names,
            MIN(season) AS first_seen_season,
            MAX(season) AS last_seen_season
        FROM ranked
        GROUP BY user_id
        """
    )
    return conn.execute("SELECT COUNT(*) FROM managers").fetchone()[0]
