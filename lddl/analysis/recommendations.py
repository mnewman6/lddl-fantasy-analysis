"""Trade-recommendation engine.

Classifies each manager as Contender / Rebuilder / Middler based on roster
age (value-weighted) and last completed season's regular-season W-L, then
generates 1-for-1 swap ideas that:
  - move OLDER, immediate-value veterans from rebuilders → contenders
  - move YOUNGER, upside players from contenders → rebuilders
with FC values balanced within a configurable tolerance. Picks are not
included in this v1 (player-only swaps).
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass

import duckdb

from lddl.analysis.franchises import canonical_user_id
from lddl.analysis.snapshots import latest_snapshot

ARCHETYPE_CONTENDER = "Contender"
ARCHETYPE_REBUILDER = "Rebuilder"
ARCHETYPE_MIDDLER = "Middler"


@dataclass
class RosterAsset:
    user_id: str
    display_name: str
    player_id: str
    name: str
    position: str
    team: str | None
    age: float | None
    value: int


@dataclass
class ManagerArchetype:
    user_id: str
    display_name: str
    archetype: str
    avg_age_weighted: float
    recent_wins: int
    recent_losses: int
    total_roster_value: int
    n_rostered: int


@dataclass
class TradeRecommendation:
    contender_user_id: str
    contender_name: str
    rebuilder_user_id: str
    rebuilder_name: str
    contender_gives: RosterAsset
    rebuilder_gives: RosterAsset
    value_diff: int          # contender_gives.value − rebuilder_gives.value
    age_gap: float           # rebuilder_gives.age − contender_gives.age
    fit_score: float         # 0..1


def current_rosters(conn: duckdb.DuckDBPyConnection) -> dict[str, list[RosterAsset]]:
    """Return {canonical_user_id: [RosterAsset...]} from the most recent league."""
    snap = latest_snapshot(conn)
    if snap is None:
        return {}
    where, binds = snap.filter_clause()
    fc_lookup: dict[str, tuple[int, float | None, str, str, str | None]] = {}
    for sid, value, age, position, name, team in conn.execute(
        f"""
        SELECT sleeper_id, value, age, position, name, team
        FROM fc_snapshots WHERE {where}
        """,
        binds,
    ).fetchall():
        fc_lookup[sid] = (int(value or 0), age, position, name, team)

    name_lookup = {
        r[0]: r[1] for r in conn.execute(
            "SELECT user_id, display_name FROM managers"
        ).fetchall()
    }

    rows = conn.execute(
        """
        SELECT r.owner_id, r.players
        FROM rosters r JOIN leagues l USING (league_id)
        WHERE l.season = (SELECT MAX(season) FROM leagues)
          AND r.owner_id IS NOT NULL
        """
    ).fetchall()

    out: dict[str, list[RosterAsset]] = defaultdict(list)
    for owner_id, players_json in rows:
        canonical = canonical_user_id(owner_id)
        if canonical is None:
            continue
        try:
            player_ids = json.loads(players_json) if players_json else []
        except (TypeError, json.JSONDecodeError):
            player_ids = []
        for pid in player_ids:
            if pid is None:
                continue
            pid_str = str(pid)
            fc = fc_lookup.get(pid_str)
            if fc is None:
                continue
            value, age, position, name, team = fc
            if position == "PICK":
                continue
            out[canonical].append(
                RosterAsset(
                    user_id=canonical,
                    display_name=name_lookup.get(canonical, canonical),
                    player_id=pid_str,
                    name=name or pid_str,
                    position=position or "?",
                    team=team,
                    age=age,
                    value=value,
                )
            )
    return dict(out)


def _last_completed_season(conn: duckdb.DuckDBPyConnection) -> str | None:
    row = conn.execute(
        "SELECT MAX(season) FROM leagues WHERE status = 'complete'"
    ).fetchone()
    return row[0] if row and row[0] else None


def _recent_records(
    conn: duckdb.DuckDBPyConnection, season: str
) -> dict[str, tuple[int, int]]:
    """{canonical_user_id: (wins, losses)} from regular season of `season`."""
    rows = conn.execute(
        """
        WITH reg AS (
            SELECT m.league_id, m.week, m.matchup_id, m.roster_id, m.points
            FROM matchups m JOIN leagues l USING (league_id)
            WHERE l.season = ? AND m.matchup_id IS NOT NULL
              AND m.week < l.playoff_week_start
        ),
        h2h AS (
            SELECT a.roster_id, a.points AS my_pts, b.points AS opp_pts
            FROM reg a JOIN reg b
              ON a.week = b.week AND a.matchup_id = b.matchup_id
              AND a.roster_id != b.roster_id
        ),
        per_roster AS (
            SELECT roster_id,
                   SUM(CASE WHEN my_pts > opp_pts THEN 1 ELSE 0 END) AS w,
                   SUM(CASE WHEN my_pts < opp_pts THEN 1 ELSE 0 END) AS l
            FROM h2h GROUP BY roster_id
        )
        SELECT lu.user_id, pr.w, pr.l
        FROM per_roster pr
        JOIN rosters r USING (roster_id)
        JOIN leagues lg ON r.league_id = lg.league_id AND lg.season = ?
        LEFT JOIN league_users lu
          ON lu.league_id = r.league_id AND lu.user_id = r.owner_id
        """,
        [season, season],
    ).fetchall()
    out: dict[str, tuple[int, int]] = {}
    for uid, w, l in rows:
        if not uid:
            continue
        canonical = canonical_user_id(uid)
        if canonical is None:
            continue
        out[canonical] = (int(w or 0), int(l or 0))
    return out


def classify_managers(
    conn: duckdb.DuckDBPyConnection,
    rosters: dict[str, list[RosterAsset]] | None = None,
) -> dict[str, ManagerArchetype]:
    rosters = rosters if rosters is not None else current_rosters(conn)
    if not rosters:
        return {}

    last_season = _last_completed_season(conn)
    records = _recent_records(conn, last_season) if last_season else {}

    archetypes: dict[str, ManagerArchetype] = {}
    for uid, assets in rosters.items():
        valued_aged = [
            a for a in assets if a.age is not None and a.age > 0 and a.value > 0
        ]
        total_v = sum(a.value for a in valued_aged)
        weighted_age = (
            sum(a.age * a.value for a in valued_aged) / total_v
            if total_v > 0 else 26.0
        )
        total_value = sum(a.value for a in assets)
        w, l = records.get(uid, (0, 0))
        archetypes[uid] = ManagerArchetype(
            user_id=uid,
            display_name=assets[0].display_name if assets else uid,
            archetype="",  # filled below
            avg_age_weighted=round(weighted_age, 2),
            recent_wins=w,
            recent_losses=l,
            total_roster_value=total_value,
            n_rostered=len(assets),
        )

    if not archetypes:
        return {}

    # Combined rank: oldest team gets age_rank 0, most wins gets win_rank 0.
    # Lower combined rank ⇒ older + more wins ⇒ Contender.
    sorted_age = sorted(
        archetypes.values(), key=lambda x: x.avg_age_weighted, reverse=True
    )
    sorted_wins = sorted(
        archetypes.values(), key=lambda x: x.recent_wins, reverse=True
    )
    age_rank = {a.user_id: i for i, a in enumerate(sorted_age)}
    win_rank = {a.user_id: i for i, a in enumerate(sorted_wins)}
    combined = sorted(
        archetypes.values(),
        key=lambda x: age_rank[x.user_id] + win_rank[x.user_id],
    )

    n = len(combined)
    third = max(1, n // 3)
    contender_ids = {a.user_id for a in combined[:third]}
    rebuilder_ids = {a.user_id for a in combined[-third:]}

    for uid, arch in archetypes.items():
        if uid in contender_ids:
            arch.archetype = ARCHETYPE_CONTENDER
        elif uid in rebuilder_ids:
            arch.archetype = ARCHETYPE_REBUILDER
        else:
            arch.archetype = ARCHETYPE_MIDDLER
    return archetypes


def recommend_trades(
    rosters: dict[str, list[RosterAsset]],
    archetypes: dict[str, ManagerArchetype],
    *,
    young_age_max: float = 25.0,
    old_age_min: float = 28.0,
    min_value: int = 1500,
    balance_tolerance: float = 0.20,
    max_recs: int = 30,
) -> list[TradeRecommendation]:
    """Generate ranked 1-for-1 swap ideas between contenders and rebuilders.

    Each rec has a fit score 0..1: 70% from how balanced the values are,
    30% from the age gap (bigger swap = better strategic fit).
    """
    contenders = [
        a for a in archetypes.values() if a.archetype == ARCHETYPE_CONTENDER
    ]
    rebuilders = [
        a for a in archetypes.values() if a.archetype == ARCHETYPE_REBUILDER
    ]

    recs: list[TradeRecommendation] = []
    for c in contenders:
        c_young = [
            a
            for a in rosters.get(c.user_id, [])
            if a.age is not None
            and a.age <= young_age_max
            and a.value >= min_value
        ]
        for r in rebuilders:
            r_old = [
                a
                for a in rosters.get(r.user_id, [])
                if a.age is not None
                and a.age >= old_age_min
                and a.value >= min_value
            ]
            for cy in c_young:
                for ro in r_old:
                    high = max(cy.value, ro.value)
                    if high == 0:
                        continue
                    diff_pct = abs(cy.value - ro.value) / high
                    if diff_pct > balance_tolerance:
                        continue
                    age_gap = (ro.age or 0) - (cy.age or 0)
                    fit = (1 - diff_pct) * 0.7 + min(age_gap / 10.0, 1.0) * 0.3
                    recs.append(
                        TradeRecommendation(
                            contender_user_id=c.user_id,
                            contender_name=c.display_name,
                            rebuilder_user_id=r.user_id,
                            rebuilder_name=r.display_name,
                            contender_gives=cy,
                            rebuilder_gives=ro,
                            value_diff=cy.value - ro.value,
                            age_gap=age_gap,
                            fit_score=round(fit, 4),
                        )
                    )

    recs.sort(key=lambda x: -x.fit_score)
    return recs[:max_recs]
