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
    contender_gives: list[RosterAsset]   # 1+ assets per side
    rebuilder_gives: list[RosterAsset]
    raw_value_diff: int                  # raw sum diff (contender - rebuilder)
    effective_value_diff: float          # KTC-adjusted diff
    age_gap: float                       # avg(rebuilder ages) − avg(contender ages)
    fit_score: float                     # 0..1


def current_rosters(conn: duckdb.DuckDBPyConnection) -> dict[str, list[RosterAsset]]:
    """Return {canonical_user_id: [RosterAsset...]} from the most recent league.

    This module still anchors on FC; migrate to KTC if/when we wire it into
    the dashboard's recommendation flow.
    """
    snap = latest_snapshot(conn, source="fc")
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
    max_recs: int = 40,
    include_uneven: bool = True,
    max_per_side: int = 2,
    source: str = "fc",
) -> list[TradeRecommendation]:
    """Ranked swap ideas — both 1-for-1 and (when ``include_uneven=True``)
    1-for-2 / 2-for-1 / 2-for-2 — between contenders and rebuilders.

    Balance is measured on **effective** (KTC-raw-adjusted) values, so a
    pair of mid-tier players is correctly recognized as worth less than
    a single elite player. Fit score = 0.7 × balance + 0.3 × age gap.
    """
    from itertools import combinations

    from lddl.analysis.trade_value import (
        FC_MAX_VALUE,
        KTC_MAX_VALUE,
        effective_value,
    )

    max_v = FC_MAX_VALUE if source == "fc" else KTC_MAX_VALUE

    contenders = [
        a for a in archetypes.values() if a.archetype == ARCHETYPE_CONTENDER
    ]
    rebuilders = [
        a for a in archetypes.values() if a.archetype == ARCHETYPE_REBUILDER
    ]

    def _bundles(assets: list[RosterAsset]) -> list[tuple[RosterAsset, ...]]:
        bundles: list[tuple[RosterAsset, ...]] = [(a,) for a in assets]
        if include_uneven:
            for k in range(2, max_per_side + 1):
                if len(assets) >= k:
                    bundles.extend(combinations(assets, k))
        return bundles

    recs: list[TradeRecommendation] = []

    for c in contenders:
        c_young_pool = sorted(
            [
                a for a in rosters.get(c.user_id, [])
                if a.age is not None
                and a.age <= young_age_max
                and a.value >= min_value
            ],
            key=lambda a: -a.value,
        )[:8]  # cap to top-8 to bound combinatorial explosion

        for r in rebuilders:
            r_old_pool = sorted(
                [
                    a for a in rosters.get(r.user_id, [])
                    if a.age is not None
                    and a.age >= old_age_min
                    and a.value >= min_value
                ],
                key=lambda a: -a.value,
            )[:8]

            c_bundles = _bundles(c_young_pool)
            r_bundles = _bundles(r_old_pool)

            for c_bundle in c_bundles:
                c_vals = [a.value for a in c_bundle]
                c_raw = sum(c_vals)
                for r_bundle in r_bundles:
                    r_vals = [a.value for a in r_bundle]
                    r_raw = sum(r_vals)
                    top = max(*c_vals, *r_vals)
                    if top <= 0:
                        continue
                    c_eff = sum(
                        effective_value(v, top, max_v) for v in c_vals
                    )
                    r_eff = sum(
                        effective_value(v, top, max_v) for v in r_vals
                    )
                    high_eff = max(c_eff, r_eff)
                    if high_eff <= 0:
                        continue
                    diff_pct = abs(c_eff - r_eff) / high_eff
                    if diff_pct > balance_tolerance:
                        continue

                    c_avg_age = (
                        sum((a.age or 0) for a in c_bundle) / len(c_bundle)
                    )
                    r_avg_age = (
                        sum((a.age or 0) for a in r_bundle) / len(r_bundle)
                    )
                    age_gap = r_avg_age - c_avg_age
                    fit = (1 - diff_pct) * 0.7 + min(age_gap / 10.0, 1.0) * 0.3

                    recs.append(
                        TradeRecommendation(
                            contender_user_id=c.user_id,
                            contender_name=c.display_name,
                            rebuilder_user_id=r.user_id,
                            rebuilder_name=r.display_name,
                            contender_gives=list(c_bundle),
                            rebuilder_gives=list(r_bundle),
                            raw_value_diff=c_raw - r_raw,
                            effective_value_diff=round(c_eff - r_eff, 1),
                            age_gap=round(age_gap, 1),
                            fit_score=round(fit, 4),
                        )
                    )

    # Deduplicate near-identical recs (same parties + same first asset
    # on each side often surface multiple times via different bundle sizes).
    # Keep the highest-fit version per (contender, rebuilder, c_top, r_top).
    seen: dict[tuple, TradeRecommendation] = {}
    for rec in recs:
        c_ids = tuple(sorted(a.player_id for a in rec.contender_gives))
        r_ids = tuple(sorted(a.player_id for a in rec.rebuilder_gives))
        key = (rec.contender_user_id, rec.rebuilder_user_id, c_ids, r_ids)
        prev = seen.get(key)
        if prev is None or rec.fit_score > prev.fit_score:
            seen[key] = rec

    deduped = sorted(seen.values(), key=lambda x: -x.fit_score)
    return deduped[:max_recs]
