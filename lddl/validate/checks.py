"""All 21 ingest data-quality checks.

Each check takes a DuckDB connection and returns a CheckResult. The
helper ``_R`` reduces boilerplate when constructing results.
"""

from __future__ import annotations

import functools
import json
from collections import defaultdict
from datetime import datetime, timezone

import duckdb

from lddl.validate import CheckResult, Severity, check


def _R(check_id: int, category: str, name: str):
    return functools.partial(CheckResult, id=check_id, category=category, name=name)


def _md_table(headers: list[str], rows: list[list]) -> str:
    if not rows:
        return ""
    header_row = "| " + " | ".join(headers) + " |"
    sep_row = "| " + " | ".join(["---"] * len(headers)) + " |"
    body = "\n".join(
        "| " + " | ".join("" if v is None else str(v) for v in r) + " |" for r in rows
    )
    return "\n".join([header_row, sep_row, body])


# ---------- Coverage ---------------------------------------------------------


@check(1, "coverage", "Season chain integrity")
def check_1_season_chain(conn: duckdb.DuckDBPyConnection) -> CheckResult:
    R = _R(1, "coverage", "Season chain integrity")
    rows = conn.execute(
        """
        SELECT league_id, season, previous_league_id,
               previous_league_id IS NOT NULL
                   AND previous_league_id NOT IN (SELECT league_id FROM leagues)
                   AS orphan
        FROM leagues
        ORDER BY season
        """
    ).fetchall()
    if not rows:
        return R(severity=Severity.RED, summary="no leagues in DB")
    orphans = [r for r in rows if r[3]]
    earliest = rows[0]
    table = _md_table(
        ["season", "league_id", "previous_league_id", "orphan?"],
        [[r[1], r[0], r[2] or "(none)", "YES" if r[3] else ""] for r in rows],
    )
    if orphans:
        return R(
            severity=Severity.RED,
            summary=f"{len(orphans)} season(s) point to a previous_league_id we don't have",
            details_md=table,
        )
    return R(
        severity=Severity.GREEN,
        summary=f"{len(rows)} seasons link cleanly; earliest is {earliest[1]} "
        f"with previous_league_id={earliest[2] or 'null'}",
        details_md=table,
    )


@check(2, "coverage", "Season completeness")
def check_2_season_completeness(conn: duckdb.DuckDBPyConnection) -> CheckResult:
    R = _R(2, "coverage", "Season completeness")
    rows = conn.execute(
        """
        SELECT l.season, l.league_id, l.status, l.playoff_week_start,
               COALESCE(MAX(m.week), 0) AS max_week,
               COUNT(DISTINCT m.week) AS n_weeks
        FROM leagues l LEFT JOIN matchups m USING (league_id)
        GROUP BY l.season, l.league_id, l.status, l.playoff_week_start
        ORDER BY l.season
        """
    ).fetchall()

    detail_rows: list[list] = []
    issues: list[str] = []
    for season, _lid, status, pws, max_week, n_weeks in rows:
        # Expected: weeks 1..(playoff_week_start + 2) for a 6-team playoff.
        expected = (pws or 0) + 2 if pws else None
        is_complete = status == "complete"
        if not is_complete:
            note = f"season status={status} (in-progress; partial coverage expected)"
        elif expected and max_week < expected:
            note = f"missing weeks: max_week={max_week}, expected through wk {expected}"
            issues.append(f"{season}: {note}")
        elif expected and n_weeks < expected:
            note = f"gap in weeks: have {n_weeks} distinct weeks, expected ~{expected}"
            issues.append(f"{season}: {note}")
        else:
            note = "OK"
        detail_rows.append([season, status, pws, max_week, n_weeks, expected, note])

    table = _md_table(
        ["season", "status", "playoff_wk_start", "max_week", "n_weeks", "expected", "note"],
        detail_rows,
    )
    if issues:
        return R(
            severity=Severity.RED,
            summary=f"{len(issues)} completed season(s) missing weeks",
            details_md=table,
        )
    in_progress = [r for r in rows if r[2] != "complete"]
    if in_progress:
        return R(
            severity=Severity.YELLOW,
            summary=f"{len(in_progress)} season(s) in-progress (expected); "
            "completed seasons all covered",
            details_md=table,
        )
    return R(
        severity=Severity.GREEN,
        summary=f"all {len(rows)} seasons cover expected week range",
        details_md=table,
    )


@check(3, "coverage", "Matchup completeness")
def check_3_matchup_completeness(conn: duckdb.DuckDBPyConnection) -> CheckResult:
    R = _R(3, "coverage", "Matchup completeness")
    rows = conn.execute(
        """
        SELECT l.season, m.week, l.total_rosters,
               COUNT(DISTINCT m.roster_id) AS distinct_rosters,
               COUNT(*) AS row_count
        FROM matchups m JOIN leagues l USING (league_id)
        GROUP BY l.season, m.week, l.total_rosters
        HAVING distinct_rosters != l.total_rosters
            OR row_count != l.total_rosters
        ORDER BY l.season, m.week
        """
    ).fetchall()
    if not rows:
        n_weeks = conn.execute("SELECT COUNT(DISTINCT (league_id, week)) FROM matchups").fetchone()[
            0
        ]
        return R(
            severity=Severity.GREEN,
            summary=f"every roster appears exactly once in all {n_weeks} (season, week) pairings",
        )
    return R(
        severity=Severity.RED,
        summary=f"{len(rows)} (season, week) pairs have wrong roster counts",
        details_md=_md_table(
            ["season", "week", "expected_rosters", "distinct_rosters", "row_count"],
            [list(r) for r in rows],
        ),
    )


@check(4, "coverage", "Roster count consistency")
def check_4_roster_count_consistency(conn: duckdb.DuckDBPyConnection) -> CheckResult:
    R = _R(4, "coverage", "Roster count consistency")
    rows = conn.execute(
        """
        WITH per_week AS (
            SELECT l.season, m.league_id, m.week,
                   COUNT(DISTINCT m.roster_id) AS n
            FROM matchups m JOIN leagues l USING (league_id)
            GROUP BY l.season, m.league_id, m.week
        )
        SELECT season,
               MIN(n) AS min_n, MAX(n) AS max_n,
               COUNT(DISTINCT n) AS n_distinct
        FROM per_week
        GROUP BY season
        HAVING n_distinct > 1
        """
    ).fetchall()
    if not rows:
        return R(severity=Severity.GREEN, summary="roster count is stable within every season")
    return R(
        severity=Severity.RED,
        summary=f"{len(rows)} season(s) have shifting roster counts mid-season",
        details_md=_md_table(["season", "min_n", "max_n", "distinct_values"], [list(r) for r in rows]),
    )


@check(5, "coverage", "Transaction date coverage")
def check_5_transaction_date_coverage(conn: duckdb.DuckDBPyConnection) -> CheckResult:
    R = _R(5, "coverage", "Transaction date coverage")
    rows = conn.execute(
        """
        SELECT l.season, l.status,
               COUNT(*) AS n_transactions,
               MIN(t.week) AS first_week, MAX(t.week) AS last_week,
               MAX(t.week) - MIN(t.week) AS span,
               COUNT(DISTINCT t.week) AS distinct_weeks
        FROM transactions t JOIN leagues l USING (league_id)
        GROUP BY l.season, l.status
        ORDER BY l.season
        """
    ).fetchall()
    if not rows:
        return R(severity=Severity.RED, summary="no transactions ingested at all")
    issues = []
    for season, status, n, first, last, span, distinct_weeks in rows:
        if status == "complete" and distinct_weeks <= 1 and n > 0:
            issues.append(f"{season}: only week {first} has transactions ({n} rows)")
    table = _md_table(
        ["season", "status", "n_transactions", "first_week", "last_week", "span", "distinct_weeks"],
        [list(r) for r in rows],
    )
    if issues:
        return R(
            severity=Severity.YELLOW,
            summary="; ".join(issues),
            details_md=table,
        )
    return R(
        severity=Severity.GREEN,
        summary=f"transactions span multiple weeks in every season",
        details_md=table,
    )


# ---------- Identity --------------------------------------------------------


@check(6, "identity", "User_id stability")
def check_6_user_id_stability(conn: duckdb.DuckDBPyConnection) -> CheckResult:
    R = _R(6, "identity", "User_id stability")
    rows = conn.execute(
        """
        SELECT lu.user_id,
               COUNT(DISTINCT l.season) AS n_seasons,
               MIN(l.season) AS first_season,
               MAX(l.season) AS last_season,
               LIST(DISTINCT lu.display_name) AS names
        FROM league_users lu JOIN leagues l USING (league_id)
        GROUP BY lu.user_id
        ORDER BY n_seasons, lu.user_id
        """
    ).fetchall()
    single_season = [r for r in rows if r[1] == 1]
    table = _md_table(
        ["user_id", "n_seasons", "first", "last", "display_names"],
        [[r[0], r[1], r[2], r[3], ", ".join(r[4]) if r[4] else ""] for r in rows],
    )
    if not single_season:
        return R(
            severity=Severity.GREEN,
            summary=f"all {len(rows)} user_ids appear in 2+ seasons",
            details_md=table,
        )
    return R(
        severity=Severity.YELLOW,
        summary=f"{len(single_season)} user_id(s) appear in only one season "
        "(could be former managers or ingest gaps)",
        details_md=table,
    )


@check(7, "identity", "Orphan roster owners")
def check_7_orphan_roster_owners(conn: duckdb.DuckDBPyConnection) -> CheckResult:
    R = _R(7, "identity", "Orphan roster owners")
    rows = conn.execute(
        """
        SELECT l.season, r.league_id, r.roster_id, r.owner_id,
               lu.user_id IS NULL AS unresolvable
        FROM rosters r JOIN leagues l USING (league_id)
        LEFT JOIN league_users lu
            ON r.league_id = lu.league_id AND r.owner_id = lu.user_id
        WHERE r.owner_id IS NULL OR lu.user_id IS NULL
        ORDER BY l.season, r.roster_id
        """
    ).fetchall()
    if not rows:
        return R(
            severity=Severity.GREEN, summary="every roster owner_id resolves to a user"
        )
    return R(
        severity=Severity.RED,
        summary=f"{len(rows)} roster(s) have null/unresolvable owner_id",
        details_md=_md_table(
            ["season", "league_id", "roster_id", "owner_id", "unresolvable"],
            [list(r) for r in rows],
        ),
    )


@check(8, "identity", "Display name churn")
def check_8_display_name_churn(conn: duckdb.DuckDBPyConnection) -> CheckResult:
    R = _R(8, "identity", "Display name churn")
    rows = conn.execute(
        """
        SELECT lu.user_id,
               COUNT(DISTINCT lu.display_name) AS n_names,
               LIST(DISTINCT lu.display_name) AS names,
               LIST(DISTINCT lu.team_name) FILTER (WHERE lu.team_name IS NOT NULL) AS teams
        FROM league_users lu
        WHERE lu.display_name IS NOT NULL
        GROUP BY lu.user_id
        ORDER BY n_names DESC, lu.user_id
        """
    ).fetchall()
    churned = [r for r in rows if r[1] > 1]
    table = _md_table(
        ["user_id", "n_display_names", "display_names", "team_names"],
        [
            [
                r[0],
                r[1],
                ", ".join(r[2]) if r[2] else "",
                ", ".join(r[3]) if r[3] else "",
            ]
            for r in rows
        ],
    )
    if not churned:
        return R(
            severity=Severity.GREEN,
            summary=f"no display-name changes across {len(rows)} user_ids",
            details_md=table,
        )
    return R(
        severity=Severity.YELLOW,
        summary=f"{len(churned)} user(s) used multiple display names — eyeball below",
        details_md=table,
    )


# ---------- Trades ----------------------------------------------------------


@check(9, "trades", "Trade asset balance")
def check_9_trade_asset_balance(conn: duckdb.DuckDBPyConnection) -> CheckResult:
    R = _R(9, "trades", "Trade asset balance")
    trades = conn.execute(
        """
        SELECT t.transaction_id, l.season, t.created_at, t.roster_ids,
               t.waiver_budget
        FROM transactions t JOIN leagues l USING (league_id)
        WHERE t.type = 'trade' AND t.status = 'complete'
        ORDER BY t.created_at
        """
    ).fetchall()
    truly_empty: list[list] = []
    faab_only: list[list] = []
    n_trades = len(trades)
    for tx_id, season, created_at, roster_ids_json, waiver_budget_json in trades:
        roster_ids = json.loads(roster_ids_json) if roster_ids_json else []
        wb = json.loads(waiver_budget_json) if waiver_budget_json else []
        if len(roster_ids) < 2:
            truly_empty.append([tx_id, season, created_at, len(roster_ids), "<2 parties"])
            continue
        for rid in roster_ids:
            n_player = conn.execute(
                "SELECT COUNT(*) FROM transaction_players "
                "WHERE transaction_id=? AND roster_id=?",
                [tx_id, rid],
            ).fetchone()[0]
            n_pick = conn.execute(
                "SELECT COUNT(*) FROM transaction_picks "
                "WHERE transaction_id=? AND (owner_id=? OR previous_owner_id=?)",
                [tx_id, rid, rid],
            ).fetchone()[0]
            if n_player + n_pick > 0:
                continue
            faab_in = sum(b.get("amount", 0) for b in wb if b.get("receiver") == rid)
            faab_out = sum(b.get("amount", 0) for b in wb if b.get("sender") == rid)
            if faab_in or faab_out:
                faab_only.append(
                    [
                        tx_id,
                        season,
                        created_at,
                        rid,
                        f"FAAB-only: +{faab_in} / -{faab_out}",
                    ]
                )
            else:
                truly_empty.append(
                    [tx_id, season, created_at, rid, "0 players, 0 picks, 0 FAAB"]
                )

    rows = sorted(truly_empty + faab_only, key=lambda r: str(r[2]))
    table = _md_table(
        ["transaction_id", "season", "created_at", "roster", "note"], rows
    )

    if truly_empty:
        return R(
            severity=Severity.RED,
            summary=f"{len(truly_empty)} trade-party with zero assets/FAAB"
            + (f"; {len(faab_only)} FAAB-only" if faab_only else ""),
            details_md=table,
        )
    if faab_only:
        return R(
            severity=Severity.YELLOW,
            summary=f"{len(faab_only)} party-trade entries are FAAB-only swaps "
            "(no players/picks moved — schema currently doesn't model FAAB as an asset)",
            details_md=table,
        )
    return R(
        severity=Severity.GREEN,
        summary=f"all {n_trades} completed trades exchange at least one player/pick per side",
    )


@check(10, "trades", "Player asset resolution")
def check_10_player_asset_resolution(conn: duckdb.DuckDBPyConnection) -> CheckResult:
    R = _R(10, "trades", "Player asset resolution")
    rows = conn.execute(
        """
        SELECT tp.player_id,
               COUNT(*) AS n_movements,
               COUNT(DISTINCT tp.transaction_id) AS n_transactions
        FROM transaction_players tp
        LEFT JOIN players p ON tp.player_id = p.player_id
        WHERE p.player_id IS NULL
        GROUP BY tp.player_id
        ORDER BY n_movements DESC
        """
    ).fetchall()
    if not rows:
        return R(severity=Severity.GREEN, summary="every player_id in transactions resolves")
    return R(
        severity=Severity.YELLOW,
        summary=f"{len(rows)} player_id(s) in transaction history don't resolve to current "
        "players cache (likely retired / ID-changed)",
        details_md=_md_table(
            ["player_id", "n_movements", "n_transactions"], [list(r) for r in rows]
        ),
    )


@check(11, "trades", "Traded pick reconstruction")
def check_11_traded_pick_reconstruction(conn: duckdb.DuckDBPyConnection) -> CheckResult:
    R = _R(11, "trades", "Traded pick reconstruction")
    bad_tx_picks = conn.execute(
        """
        SELECT 'transaction_picks' AS source, transaction_id, season, round, roster_id,
               owner_id, previous_owner_id
        FROM transaction_picks
        WHERE season IS NULL OR round IS NULL OR roster_id IS NULL
        """
    ).fetchall()
    bad_traded = conn.execute(
        """
        SELECT 'traded_picks' AS source, league_id, season, round, roster_id,
               owner_id, previous_owner_id
        FROM traded_picks
        WHERE season IS NULL OR round IS NULL OR roster_id IS NULL
        """
    ).fetchall()
    n_total = conn.execute("SELECT COUNT(*) FROM transaction_picks").fetchone()[0] + \
        conn.execute("SELECT COUNT(*) FROM traded_picks").fetchone()[0]
    issues = list(bad_tx_picks) + list(bad_traded)
    if not issues:
        return R(
            severity=Severity.GREEN,
            summary=f"all {n_total} pick records have season + round + original roster",
        )
    return R(
        severity=Severity.RED,
        summary=f"{len(issues)} pick record(s) missing season/round/roster_id",
        details_md=_md_table(
            ["source", "id_or_tx", "season", "round", "roster", "owner", "prev_owner"],
            [list(r) for r in issues],
        ),
    )


@check(12, "trades", "Trade chain consistency")
def check_12_trade_chain_consistency(conn: duckdb.DuckDBPyConnection) -> CheckResult:
    R = _R(12, "trades", "Trade chain consistency")
    rows = conn.execute(
        """
        SELECT tp.season, tp.round, tp.roster_id AS original_roster,
               tp.owner_id, tp.previous_owner_id,
               tp.transaction_id, t.created_at
        FROM transaction_picks tp JOIN transactions t USING (transaction_id)
        WHERE t.status = 'complete'
        ORDER BY tp.season, tp.round, tp.roster_id, t.created_at
        """
    ).fetchall()
    by_pick: dict[tuple, list] = defaultdict(list)
    for season, round_, original, owner, prev, tx_id, ts in rows:
        by_pick[(season, round_, original)].append(
            {"owner": owner, "prev": prev, "tx": tx_id, "ts": ts}
        )

    issues = []
    for (season, round_, original), trades in by_pick.items():
        expected_prev = original
        for trade in trades:
            if trade["prev"] != expected_prev:
                issues.append(
                    [
                        f"{season} R{round_}",
                        original,
                        trade["tx"],
                        trade["ts"],
                        expected_prev,
                        trade["prev"],
                    ]
                )
            expected_prev = trade["owner"]

    if not issues:
        return R(
            severity=Severity.GREEN,
            summary=f"chain ordering is consistent across {len(by_pick)} traded-pick lineages",
        )
    return R(
        severity=Severity.YELLOW,
        summary=f"{len(issues)} pick movement(s) where previous_owner doesn't match prior chain",
        details_md=_md_table(
            ["pick", "original_roster", "transaction_id", "ts", "expected_prev", "actual_prev"],
            issues,
        ),
    )


@check(13, "trades", "Trade timestamp sanity")
def check_13_trade_timestamp_sanity(conn: duckdb.DuckDBPyConnection) -> CheckResult:
    R = _R(13, "trades", "Trade timestamp sanity")
    rows = conn.execute(
        """
        SELECT t.transaction_id, l.season, t.created_at, t.status_updated_at,
               EXTRACT(YEAR FROM COALESCE(t.status_updated_at, t.created_at))::INTEGER AS yr,
               CAST(l.season AS INTEGER) AS season_int
        FROM transactions t JOIN leagues l USING (league_id)
        WHERE t.type = 'trade' AND t.status = 'complete'
        """
    ).fetchall()
    issues = []
    for tx_id, season, created, status_updated, yr, season_int in rows:
        if season_int is None or yr is None:
            issues.append([tx_id, season, created, status_updated, "missing timestamp"])
            continue
        if yr not in (season_int, season_int + 1):
            issues.append(
                [tx_id, season, created, status_updated, f"yr={yr}, season={season_int}"]
            )
    if not issues:
        return R(
            severity=Severity.GREEN,
            summary=f"all {len(rows)} trade timestamps fall in season's calendar window",
        )
    return R(
        severity=Severity.YELLOW,
        summary=f"{len(issues)} trade(s) have timestamps outside expected season window",
        details_md=_md_table(
            ["transaction_id", "season", "created_at", "status_updated_at", "issue"], issues
        ),
    )


# ---------- Matchups --------------------------------------------------------


@check(14, "matchups", "Score sanity")
def check_14_score_sanity(conn: duckdb.DuckDBPyConnection) -> CheckResult:
    R = _R(14, "matchups", "Score sanity")
    high = conn.execute(
        """
        SELECT l.season, m.week, m.matchup_id, m.roster_id, m.points
        FROM matchups m JOIN leagues l USING (league_id)
        WHERE m.points > 250
        ORDER BY m.points DESC
        """
    ).fetchall()
    zero_zero = conn.execute(
        """
        WITH zeros AS (
            SELECT league_id, week, matchup_id,
                   COUNT(*) AS n_rows,
                   SUM(CASE WHEN COALESCE(points, 0) = 0 THEN 1 ELSE 0 END) AS n_zero
            FROM matchups
            WHERE matchup_id IS NOT NULL
            GROUP BY league_id, week, matchup_id
        )
        SELECT l.season, z.week, z.matchup_id
        FROM zeros z JOIN leagues l USING (league_id)
        WHERE z.n_rows = 2 AND z.n_zero = 2
        """
    ).fetchall()
    issues = []
    for season, week, mid, rid, pts in high:
        issues.append([season, week, mid, rid, f"{pts:.2f}", "score >250"])
    for season, week, mid in zero_zero:
        issues.append([season, week, mid, None, "0.00", "both teams scored 0"])
    if not issues:
        return R(severity=Severity.GREEN, summary="no abnormal matchup scores")
    return R(
        severity=Severity.YELLOW,
        summary=f"{len(issues)} matchup row(s) with unusual scores "
        f"({len(high)} high, {len(zero_zero)} zero-zero pairings)",
        details_md=_md_table(
            ["season", "week", "matchup_id", "roster_id", "points", "issue"], issues
        ),
    )


@check(15, "matchups", "Playoff bracket presence")
def check_15_playoff_bracket_presence(conn: duckdb.DuckDBPyConnection) -> CheckResult:
    R = _R(15, "matchups", "Playoff bracket presence")
    has_bracket_table = conn.execute(
        """
        SELECT COUNT(*) FROM information_schema.tables
        WHERE table_schema='main'
          AND table_name IN ('winners_bracket', 'losers_bracket')
        """
    ).fetchone()[0]
    if has_bracket_table == 0:
        return R(
            severity=Severity.RED,
            summary="bracket data not ingested — /winners_bracket and /losers_bracket "
            "endpoints are not yet pulled into the warehouse",
            details_md=(
                "Sleeper exposes:\n\n"
                "- `GET /v1/league/{league_id}/winners_bracket`\n"
                "- `GET /v1/league/{league_id}/losers_bracket`\n\n"
                "Step 2 ingest does not currently fetch these. Adding them is a small "
                "extension (one new table, one new ingest module).\n\n"
                "Champion identification (check 16) depends on this."
            ),
        )
    return R(
        severity=Severity.GREEN,
        summary="bracket tables exist (further check would verify per-season data)",
    )


@check(16, "matchups", "Champion identifiable")
def check_16_champion_identifiable(conn: duckdb.DuckDBPyConnection) -> CheckResult:
    R = _R(16, "matchups", "Champion identifiable")
    has_bracket_table = conn.execute(
        """
        SELECT COUNT(*) FROM information_schema.tables
        WHERE table_schema='main' AND table_name='winners_bracket'
        """
    ).fetchone()[0]
    if has_bracket_table == 0:
        return R(
            severity=Severity.RED,
            summary="cannot determine champions — depends on bracket ingestion (see check 15)",
        )
    return R(
        severity=Severity.GREEN,
        summary="champion identifiable for all completed seasons",
    )


# ---------- Drafts ----------------------------------------------------------


@check(17, "drafts", "Draft existence")
def check_17_draft_existence(conn: duckdb.DuckDBPyConnection) -> CheckResult:
    R = _R(17, "drafts", "Draft existence")
    rows = conn.execute(
        """
        SELECT l.season, l.league_id,
               COUNT(d.draft_id) AS n_drafts
        FROM leagues l LEFT JOIN drafts d USING (league_id)
        GROUP BY l.season, l.league_id
        ORDER BY l.season
        """
    ).fetchall()
    missing = [r for r in rows if r[2] == 0]
    if missing:
        return R(
            severity=Severity.RED,
            summary=f"{len(missing)} season(s) have no draft data",
            details_md=_md_table(
                ["season", "league_id", "n_drafts"], [list(r) for r in rows]
            ),
        )
    return R(
        severity=Severity.GREEN,
        summary=f"all {len(rows)} seasons have at least one draft",
        details_md=_md_table(
            ["season", "league_id", "n_drafts"], [list(r) for r in rows]
        ),
    )


@check(18, "drafts", "Draft pick count")
def check_18_draft_pick_count(conn: duckdb.DuckDBPyConnection) -> CheckResult:
    R = _R(18, "drafts", "Draft pick count")
    rows = conn.execute(
        """
        SELECT l.season, d.draft_id, d.status, d.rounds, l.total_rosters,
               (d.rounds * l.total_rosters) AS expected_picks,
               COUNT(dp.draft_id) AS actual_picks
        FROM drafts d JOIN leagues l USING (league_id)
        LEFT JOIN draft_picks dp ON d.draft_id = dp.draft_id
        GROUP BY l.season, d.draft_id, d.status, d.rounds, l.total_rosters
        ORDER BY l.season
        """
    ).fetchall()
    incomplete_red = []
    in_progress_yellow = []
    for season, draft_id, status, rounds, teams, expected, actual in rows:
        if expected and actual != expected:
            if status == "complete":
                incomplete_red.append((season, draft_id, status, rounds, teams, expected, actual))
            else:
                in_progress_yellow.append(
                    (season, draft_id, status, rounds, teams, expected, actual)
                )
    table = _md_table(
        ["season", "draft_id", "status", "rounds", "teams", "expected_picks", "actual_picks"],
        [list(r) for r in rows],
    )
    if incomplete_red:
        return R(
            severity=Severity.RED,
            summary=f"{len(incomplete_red)} completed draft(s) missing picks",
            details_md=table,
        )
    if in_progress_yellow:
        return R(
            severity=Severity.YELLOW,
            summary=f"{len(in_progress_yellow)} draft(s) in progress with partial picks",
            details_md=table,
        )
    return R(
        severity=Severity.GREEN,
        summary=f"every completed draft has rounds × teams picks",
        details_md=table,
    )


@check(19, "drafts", "Keeper marking")
def check_19_keeper_marking(conn: duckdb.DuckDBPyConnection) -> CheckResult:
    R = _R(19, "drafts", "Keeper marking")
    rows = conn.execute(
        """
        SELECT l.season, d.draft_id, d.status,
               COUNT(*) AS total_picks,
               SUM(CASE WHEN dp.is_keeper THEN 1 ELSE 0 END) AS keepers,
               ROUND(100.0 * SUM(CASE WHEN dp.is_keeper THEN 1 ELSE 0 END) / COUNT(*), 1)
                   AS keeper_pct
        FROM drafts d JOIN leagues l USING (league_id)
        JOIN draft_picks dp USING (draft_id)
        GROUP BY l.season, d.draft_id, d.status
        ORDER BY l.season
        """
    ).fetchall()
    table = _md_table(
        ["season", "draft_id", "status", "total_picks", "keepers", "keeper_%"],
        [list(r) for r in rows],
    )
    return R(
        severity=Severity.YELLOW,
        summary="informational — eyeball whether keeper fractions look right per season",
        details_md=table,
    )


# ---------- Hygiene ---------------------------------------------------------


@check(20, "hygiene", "Players cache age")
def check_20_players_cache_age(conn: duckdb.DuckDBPyConnection) -> CheckResult:
    R = _R(20, "hygiene", "Players cache age")
    row = conn.execute(
        "SELECT COUNT(*), MAX(fetched_at) FROM players"
    ).fetchone()
    n_rows, fetched_at = row
    if n_rows == 0 or fetched_at is None:
        return R(severity=Severity.RED, summary="players table is empty")
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - fetched_at
    age_days = age.total_seconds() / 86400
    if age_days > 7:
        return R(
            severity=Severity.YELLOW,
            summary=f"players cache is {age_days:.1f} days old — refresh recommended",
        )
    return R(
        severity=Severity.GREEN,
        summary=f"players cache refreshed {age_days:.1f} days ago ({n_rows} rows)",
    )


@check(21, "hygiene", "Row counts per table")
def check_21_row_counts(conn: duckdb.DuckDBPyConnection) -> CheckResult:
    R = _R(21, "hygiene", "Row counts per table")
    tables = [
        "leagues", "league_users", "managers", "rosters", "matchups",
        "transactions", "transaction_players", "transaction_picks",
        "traded_picks", "drafts", "draft_picks", "draft_traded_picks", "players",
    ]
    counts = []
    for t in tables:
        n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        counts.append([t, n])
    table = _md_table(["table", "row_count"], counts)
    return R(
        severity=Severity.GREEN,
        summary=f"{sum(c[1] for c in counts)} total rows across {len(tables)} tables",
        details_md=table,
    )


# ---------------------------------------------------------------------------


ALL_CHECKS = [
    check_1_season_chain,
    check_2_season_completeness,
    check_3_matchup_completeness,
    check_4_roster_count_consistency,
    check_5_transaction_date_coverage,
    check_6_user_id_stability,
    check_7_orphan_roster_owners,
    check_8_display_name_churn,
    check_9_trade_asset_balance,
    check_10_player_asset_resolution,
    check_11_traded_pick_reconstruction,
    check_12_trade_chain_consistency,
    check_13_trade_timestamp_sanity,
    check_14_score_sanity,
    check_15_playoff_bracket_presence,
    check_16_champion_identifiable,
    check_17_draft_existence,
    check_18_draft_pick_count,
    check_19_keeper_marking,
    check_20_players_cache_age,
    check_21_row_counts,
]
