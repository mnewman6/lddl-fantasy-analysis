"""Manager analysis + manager-history report tests."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb

from lddl.analysis.managers import build_manager_cards
from lddl.analysis.standings import season_rows
from lddl.reports.manager_history import build_manager_history_from_db
from lddl.store.db import init_schema


def _seed_two_managers_two_seasons(db_path: Path) -> None:
    """Two seasons, 2 rosters each, same managers; one champion, one last-place."""
    init_schema(db_path)
    with duckdb.connect(str(db_path)) as conn:
        # 2 seasons
        for season, league_id, status in [("2023", "L23", "complete"), ("2024", "L24", "complete")]:
            conn.execute(
                """
                INSERT INTO leagues VALUES (
                    ?, NULL, ?, 'LDDL', ?, 'nfl', 2, 2, 15, 6,
                    ?, ?, ?, ?, ?
                )
                """,
                [
                    league_id, season, status,
                    json.dumps({"type": 2, "playoff_week_start": 15}),
                    json.dumps({"rec": 0.5}),
                    json.dumps(["QB", "RB"]),
                    json.dumps({}),
                    datetime.now(timezone.utc),
                ],
            )
            for rid, owner_id, name in [(1, "u1", "alpha"), (2, "u2", "beta")]:
                conn.execute(
                    "INSERT INTO league_users VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [league_id, owner_id, name, None, True, False, None, json.dumps({})],
                )
                conn.execute(
                    "INSERT INTO rosters (league_id, roster_id, owner_id, fpts) "
                    "VALUES (?, ?, ?, ?)",
                    [league_id, rid, owner_id, 1500.0],
                )
            # Two regular-season weeks: alpha beats beta both times.
            for week in (1, 2):
                conn.execute(
                    "INSERT INTO matchups (league_id, week, matchup_id, roster_id, points) "
                    "VALUES (?, ?, 1, 1, 100)",
                    [league_id, week],
                )
                conn.execute(
                    "INSERT INTO matchups (league_id, week, matchup_id, roster_id, points) "
                    "VALUES (?, ?, 1, 2, 80)",
                    [league_id, week],
                )
        # alpha (u1) wins championship in 2024 → winners p=1 winner = 1st.
        # beta (u2) is runner-up → winners p=1 loser = 2nd. (Two-roster
        # fixture can't fully exercise the losers bracket placements; that
        # path is verified against live LDDL data manually.)
        conn.execute(
            "INSERT INTO playoff_bracket VALUES "
            "('L24', 'winners', 1, 3, 1, 1, 2, 1, 2, NULL, NULL)"
        )
        # Managers + a snapshot so trade-recap can run
        conn.execute(
            """
            INSERT INTO managers VALUES (
                'u1', 'alpha', '["alpha"]'::JSON, '[]'::JSON, '2023', '2024'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO managers VALUES (
                'u2', 'beta', '["beta"]'::JSON, '[]'::JSON, '2023', '2024'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO fc_snapshots (
                snapshot_date, fc_player_id, sleeper_id, name, position,
                value, format_num_qbs, format_ppr, format_num_teams,
                format_is_dynasty, raw, fetched_at
            ) VALUES (?, 1, 'P1', 'x', 'WR', 100, 1, 0.5, 12, true, '{}', ?)
            """,
            [date(2026, 5, 4), datetime.now(timezone.utc)],
        )


def test_season_rows_uses_matchups_for_record(tmp_path: Path) -> None:
    db = tmp_path / "lddl.duckdb"
    _seed_two_managers_two_seasons(db)
    with duckdb.connect(str(db)) as conn:
        rows = season_rows(conn)
    # alpha wins both weeks of both seasons → 2-0-0 each season, total 4-0
    alpha_rows = [r for r in rows if r.user_id == "u1"]
    beta_rows = [r for r in rows if r.user_id == "u2"]
    assert sum(r.wins for r in alpha_rows) == 4
    assert sum(r.losses for r in alpha_rows) == 0
    assert sum(r.wins for r in beta_rows) == 0
    assert sum(r.losses for r in beta_rows) == 4


def test_manager_card_aggregation(tmp_path: Path) -> None:
    db = tmp_path / "lddl.duckdb"
    _seed_two_managers_two_seasons(db)
    with duckdb.connect(str(db)) as conn:
        cards = build_manager_cards(conn)
    by_uid = {c.user_id: c for c in cards}
    assert by_uid["u1"].championships == 1
    assert by_uid["u1"].last_places == 0
    # u2 is runner-up (placement 2), not dead last.
    assert by_uid["u2"].championships == 0
    assert by_uid["u2"].last_places == 0
    # Luck balances league-wide (the metric's invariant).
    assert abs(sum(c.luck for c in cards)) < 1e-6
    # Champion is correctly placement 1; runner-up is placement 2.
    u1_2024 = next(s for s in by_uid["u1"].seasons if s.season == "2024")
    u2_2024 = next(s for s in by_uid["u2"].seasons if s.season == "2024")
    assert u1_2024.final_placement == 1
    assert u2_2024.final_placement == 2


def test_build_manager_history_pdf(tmp_path: Path) -> None:
    db = tmp_path / "lddl.duckdb"
    _seed_two_managers_two_seasons(db)
    out = tmp_path / "out"
    with duckdb.connect(str(db)) as conn:
        pdf_path = build_manager_history_from_db(conn, out)
    assert pdf_path.exists()
    assert pdf_path.stat().st_size > 1000
    assert (out / "manager_history_wins_vs_pf.png").exists()
    assert (out / "manager_history_u1.png").exists()
    assert (out / "manager_history_u2.png").exists()
