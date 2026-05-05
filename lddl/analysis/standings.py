"""Per-season standings and playoff-result helpers."""

from __future__ import annotations

from dataclasses import dataclass

import duckdb


@dataclass
class SeasonRow:
    season: str
    league_id: str
    league_status: str
    roster_id: int
    user_id: str | None
    display_name: str
    team_name: str | None
    wins: int
    losses: int
    ties: int
    fpts: float
    fpts_against: float
    ppts: float
    expected_wins: float          # by week-median definition
    is_champion: bool
    is_last_place: bool
    playoff_wins: int
    playoff_losses: int


def season_rows(conn: duckdb.DuckDBPyConnection) -> list[SeasonRow]:
    """One row per (season, roster) across every season we have.

    Wins / losses / ties are computed from the matchups table (regular season
    only) rather than ``rosters.wins`` because Sleeper's roster.settings.wins
    on this league appears to include consolation games, which would put it on
    a different basis than our expected_wins (regular season only) and break
    the luck metric.
    """
    base = conn.execute(
        """
        SELECT l.season, l.league_id, l.status, r.roster_id, r.owner_id,
               COALESCE(lu.display_name, '?') AS display_name,
               lu.team_name,
               COALESCE(r.fpts, 0.0), COALESCE(r.fpts_against, 0.0),
               COALESCE(r.ppts, 0.0)
        FROM rosters r
        JOIN leagues l USING (league_id)
        LEFT JOIN league_users lu
            ON lu.league_id = r.league_id AND lu.user_id = r.owner_id
        ORDER BY l.season, r.roster_id
        """
    ).fetchall()

    expected_wins = _expected_wins_by_roster(conn)
    actual_record = _regular_season_record(conn)
    champions, last_places = _bracket_finishers(conn)
    pw, pl = _playoff_record(conn)

    rows: list[SeasonRow] = []
    for r in base:
        season, league_id, status, rid, uid, dn, tn, pf, pa, ppts = r
        key = (league_id, rid)
        w, l, t = actual_record.get(key, (0, 0, 0))
        rows.append(
            SeasonRow(
                season=season,
                league_id=league_id,
                league_status=status,
                roster_id=rid,
                user_id=uid,
                display_name=dn,
                team_name=tn,
                wins=w,
                losses=l,
                ties=t,
                fpts=float(pf),
                fpts_against=float(pa),
                ppts=float(ppts),
                expected_wins=expected_wins.get(key, 0.0),
                is_champion=key in champions,
                is_last_place=key in last_places,
                playoff_wins=pw.get(key, 0),
                playoff_losses=pl.get(key, 0),
            )
        )
    return rows


def _regular_season_record(
    conn: duckdb.DuckDBPyConnection,
) -> dict[tuple[str, int], tuple[int, int, int]]:
    """Compute (W, L, T) per (league_id, roster_id) from matchups, regular
    season only (week < playoff_week_start)."""
    rows = conn.execute(
        """
        WITH reg AS (
            SELECT m.league_id, m.week, m.matchup_id, m.roster_id,
                   COALESCE(m.points, 0) AS points
            FROM matchups m JOIN leagues l USING (league_id)
            WHERE (l.playoff_week_start IS NULL OR m.week < l.playoff_week_start)
              AND m.matchup_id IS NOT NULL
        ),
        h2h AS (
            SELECT a.league_id, a.roster_id,
                   a.points AS my_pts, b.points AS opp_pts
            FROM reg a JOIN reg b
              ON a.league_id = b.league_id
             AND a.week = b.week
             AND a.matchup_id = b.matchup_id
             AND a.roster_id != b.roster_id
        )
        SELECT league_id, roster_id,
               SUM(CASE WHEN my_pts > opp_pts THEN 1 ELSE 0 END) AS w,
               SUM(CASE WHEN my_pts < opp_pts THEN 1 ELSE 0 END) AS l,
               SUM(CASE WHEN my_pts = opp_pts AND my_pts > 0 THEN 1 ELSE 0 END) AS t
        FROM h2h
        GROUP BY league_id, roster_id
        """
    ).fetchall()
    return {(r[0], r[1]): (int(r[2]), int(r[3]), int(r[4])) for r in rows}


def _expected_wins_by_roster(
    conn: duckdb.DuckDBPyConnection,
) -> dict[tuple[str, int], float]:
    """For each (league_id, roster_id), the brief's "expected wins" =
    weeks they would have beaten the league median that week. Restricted
    to regular-season weeks; playoff weeks have eliminated rosters scoring
    zero and would otherwise depress the median artificially.
    Luck = actual_wins − expected_wins."""
    rows = conn.execute(
        """
        WITH per_week AS (
            SELECT m.league_id, m.week, m.roster_id,
                   COALESCE(m.points, 0) AS points,
                   MEDIAN(COALESCE(m.points, 0))
                       OVER (PARTITION BY m.league_id, m.week) AS median_pts
            FROM matchups m JOIN leagues l USING (league_id)
            WHERE l.playoff_week_start IS NULL
               OR m.week < l.playoff_week_start
        )
        SELECT league_id, roster_id,
               SUM(CASE WHEN points > median_pts THEN 1.0
                        WHEN points = median_pts THEN 0.5
                        ELSE 0.0 END) AS expected_wins
        FROM per_week
        GROUP BY league_id, roster_id
        """
    ).fetchall()
    return {(r[0], r[1]): float(r[2] or 0.0) for r in rows}


def _bracket_finishers(
    conn: duckdb.DuckDBPyConnection,
) -> tuple[set[tuple[str, int]], set[tuple[str, int]]]:
    """Champions: winners-bracket placement=1 winner. Last-place: losers-bracket
    placement=1 loser."""
    champions: set[tuple[str, int]] = set()
    last: set[tuple[str, int]] = set()
    for league_id, winner in conn.execute(
        """
        SELECT league_id, winner FROM playoff_bracket
        WHERE bracket = 'winners' AND placement = 1 AND winner IS NOT NULL
        """
    ).fetchall():
        champions.add((league_id, winner))
    for league_id, loser in conn.execute(
        """
        SELECT league_id, loser FROM playoff_bracket
        WHERE bracket = 'losers' AND placement = 1 AND loser IS NOT NULL
        """
    ).fetchall():
        last.add((league_id, loser))
    return champions, last


def _playoff_record(
    conn: duckdb.DuckDBPyConnection,
) -> tuple[dict[tuple[str, int], int], dict[tuple[str, int], int]]:
    """Wins/losses across all playoff bracket games (winners + losers)."""
    wins: dict[tuple[str, int], int] = {}
    losses: dict[tuple[str, int], int] = {}
    for league_id, winner, loser in conn.execute(
        """
        SELECT league_id, winner, loser FROM playoff_bracket
        WHERE winner IS NOT NULL AND bracket = 'winners'
        """
    ).fetchall():
        wins[(league_id, winner)] = wins.get((league_id, winner), 0) + 1
        if loser is not None:
            losses[(league_id, loser)] = losses.get((league_id, loser), 0) + 1
    return wins, losses
