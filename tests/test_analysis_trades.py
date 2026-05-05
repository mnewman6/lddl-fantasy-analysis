"""Trade grading + report tests."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb

from lddl.analysis.picks import pick_fc_name, pick_label, round_to_ordinal
from lddl.analysis.trades import grade_trades_for_season
from lddl.reports.pdf import build_trade_recap
from lddl.store.db import init_schema


def test_round_ordinal() -> None:
    assert round_to_ordinal(1) == "1st"
    assert round_to_ordinal(2) == "2nd"
    assert round_to_ordinal(3) == "3rd"
    assert round_to_ordinal(4) == "4th"
    assert round_to_ordinal(11) == "11th"


def test_pick_names() -> None:
    assert pick_fc_name("2026", 1) == "2026 1st"
    assert pick_fc_name("2027", 2) == "2027 2nd"
    assert pick_label("2026", 1, 7) == "2026 1st (orig roster 7)"


def _seed_minimal_warehouse(db_path: Path) -> None:
    """Two-roster trade in 2024: one player + one R1 pick swap, with FC values."""
    init_schema(db_path)
    with duckdb.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO leagues VALUES (
                'L24', NULL, '2024', 'LDDL', 'complete', 'nfl', 12, 2, 15, 6,
                ?, ?, ?, ?, ?
            )
            """,
            [
                json.dumps({"type": 2, "num_teams": 12}),
                json.dumps({"rec": 0.5}),
                json.dumps(["QB", "RB"]),
                json.dumps({}),
                datetime.now(timezone.utc),
            ],
        )
        for rid, owner_id, name in [(1, "u1", "alpha"), (2, "u2", "beta")]:
            conn.execute(
                "INSERT INTO league_users VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ["L24", owner_id, name, f"{name}-team", True, False, None, json.dumps({})],
            )
            conn.execute(
                "INSERT INTO rosters (league_id, roster_id, owner_id) VALUES (?, ?, ?)",
                ["L24", rid, owner_id],
            )

        conn.execute(
            """
            INSERT INTO transactions (
                transaction_id, league_id, week, type, status, creator,
                created_at, status_updated_at, roster_ids, consenter_ids,
                waiver_budget, leg, settings, metadata
            ) VALUES (?, ?, ?, 'trade', 'complete', NULL, ?, ?, ?, NULL, NULL, NULL, NULL, NULL)
            """,
            [
                "TX1", "L24", 8,
                datetime(2024, 11, 1, tzinfo=timezone.utc),
                datetime(2024, 11, 1, tzinfo=timezone.utc),
                json.dumps([1, 2]),
            ],
        )
        for movement, rid, pid in [("drop", 1, "P1"), ("add", 2, "P1")]:
            conn.execute(
                "INSERT INTO transaction_players VALUES (?, ?, ?, ?)",
                ["TX1", pid, rid, movement],
            )
        conn.execute(
            "INSERT INTO transaction_picks VALUES (?, ?, ?, ?, ?, ?)",
            ["TX1", "2026", 1, 2, 1, 2],
        )
        conn.execute(
            """
            INSERT INTO players (player_id, full_name, position, team, fetched_at)
            VALUES ('P1', 'Test Player', 'WR', 'NYG', ?)
            """,
            [datetime.now(timezone.utc)],
        )

        snap_args = (date(2026, 5, 4), 1, 0.5, 12, True, datetime.now(timezone.utc))
        conn.execute(
            """
            INSERT INTO fc_snapshots (
                snapshot_date, fc_player_id, sleeper_id, name, position, team,
                age, value, overall_rank, position_rank, trend_30_day,
                redraft_value, combined_value, tier, trade_frequency,
                format_num_qbs, format_ppr, format_num_teams, format_is_dynasty,
                raw, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                snap_args[0], 1, "P1", "Test Player", "WR", "NYG", 25.0,
                5000, 10, 5, 0, 5000, 10000, 1, 0.01,
                snap_args[1], snap_args[2], snap_args[3], snap_args[4],
                "{}", snap_args[5],
            ],
        )
        conn.execute(
            """
            INSERT INTO fc_snapshots (
                snapshot_date, fc_player_id, sleeper_id, name, position, team,
                age, value, overall_rank, position_rank, trend_30_day,
                redraft_value, combined_value, tier, trade_frequency,
                format_num_qbs, format_ppr, format_num_teams, format_is_dynasty,
                raw, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                snap_args[0], 99, "FP_2026_1", "2026 1st", "PICK", None, None,
                3275, 50, 1, 0, 0, 3275, 7, None,
                snap_args[1], snap_args[2], snap_args[3], snap_args[4],
                "{}", snap_args[5],
            ],
        )


def test_grade_trades_basic_swap(tmp_path: Path) -> None:
    db = tmp_path / "lddl.duckdb"
    _seed_minimal_warehouse(db)
    with duckdb.connect(str(db)) as conn:
        recap = grade_trades_for_season(conn, "2024")
    assert len(recap.trades) == 1
    t = recap.trades[0]
    assert not t.is_faab_only
    assert len(t.sides) == 2
    side1 = next(s for s in t.sides if s.roster_id == 1)
    side2 = next(s for s in t.sides if s.roster_id == 2)
    # Side 1 gave Test Player (5000), got 2026 1st (3275). Raw net = -1725.
    assert side1.net_now() == -1725
    assert side2.net_now() == 1725
    assert t.winner.roster_id == 2
    # Raw margin (legacy sum-based, kept for transparency)
    assert t.raw_margin_now == 3450
    # Effective margin (KTC raw-adjusted) is smaller than the raw margin
    # because the lower-value pick gets discounted relative to the stud.
    assert 0 < t.margin_now < t.raw_margin_now
    assert side1.effective_net < 0 < side2.effective_net


def test_build_trade_recap_pdf(tmp_path: Path) -> None:
    db = tmp_path / "lddl.duckdb"
    _seed_minimal_warehouse(db)
    with duckdb.connect(str(db)) as conn:
        recap = grade_trades_for_season(conn, "2024")
    out = tmp_path / "out"
    pdf_path = build_trade_recap(recap, out)
    assert pdf_path.exists()
    assert pdf_path.stat().st_size > 1000
    # Per-trade PNG also written.
    assert (out / "trade_recap_2024_trade_01.png").exists()


def test_grade_trades_empty_season(tmp_path: Path) -> None:
    db = tmp_path / "lddl.duckdb"
    init_schema(db)
    with duckdb.connect(str(db)) as conn:
        # Need a leagues row + a snapshot for the function to not error
        conn.execute(
            """
            INSERT INTO leagues VALUES (
                'L24', NULL, '2024', 'LDDL', 'complete', 'nfl', 12, 2, 15, 6,
                ?, ?, ?, ?, ?
            )
            """,
            [json.dumps({"type": 2}), json.dumps({}), json.dumps([]),
             json.dumps({}), datetime.now(timezone.utc)],
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
        recap = grade_trades_for_season(conn, "2024")
    assert recap.trades == []
