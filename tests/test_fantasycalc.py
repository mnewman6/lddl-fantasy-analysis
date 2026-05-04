"""FantasyCalc client + snapshot tests."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import duckdb
import httpx

from lddl.clients.fantasycalc import FantasyCalcClient
from lddl.config import Settings
from lddl.snapshot import _detect_format, take_snapshot
from lddl.store.db import init_schema

# Minimal FC payload mirroring the real shape.
SAMPLE_PAYLOAD = [
    {
        "player": {
            "id": 9833,
            "name": "Bijan Robinson",
            "sleeperId": "9509",
            "position": "RB",
            "maybeTeam": "ATL",
            "maybeAge": 24.3,
        },
        "value": 11045,
        "overallRank": 1,
        "positionRank": 1,
        "trend30Day": -8,
        "redraftValue": 10456,
        "combinedValue": 21501,
        "maybeTier": 1,
        "maybeTradeFrequency": 0.0041,
    },
    {
        "player": {
            "id": 16523,
            "name": "2026 Pick 1.01",
            "sleeperId": "DP_0_0",
            "position": "PICK",
            "maybeTeam": None,
            "maybeAge": None,
        },
        "value": 7485,
        "overallRank": 8,
        "positionRank": 1,
        "trend30Day": 738,
        "redraftValue": 0,
        "combinedValue": 7485,
        "maybeTier": 7,
        "maybeTradeFrequency": None,
    },
]


def _client(tmp_path: Path, *, payload=SAMPLE_PAYLOAD, counter=None) -> FantasyCalcClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if counter is not None:
            counter["n"] = counter.get("n", 0) + 1
        return httpx.Response(200, json=payload)

    http = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.fantasycalc.com",
    )
    return FantasyCalcClient(cache_dir=tmp_path, http=http)


def test_get_current_values_caches_per_date(tmp_path: Path) -> None:
    counter: dict = {}
    with _client(tmp_path, counter=counter) as fc:
        fc.get_current_values(snapshot_date=date(2026, 5, 4))
        fc.get_current_values(snapshot_date=date(2026, 5, 4))
    assert counter["n"] == 1


def test_force_refetches(tmp_path: Path) -> None:
    counter: dict = {}
    with _client(tmp_path, counter=counter) as fc:
        fc.get_current_values(snapshot_date=date(2026, 5, 4))
        fc.get_current_values(snapshot_date=date(2026, 5, 4), force=True)
    assert counter["n"] == 2


def test_take_snapshot_inserts_rows(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "lddl.duckdb"
    init_schema(db_path)
    # Seed a leagues row so format detection succeeds.
    with duckdb.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO leagues (
                league_id, season, name, status, total_rosters, league_type,
                playoff_week_start, playoff_teams, settings, scoring_settings,
                roster_positions, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "L1", "2026", "LDDL", "drafting", 12, 2, 15, 6,
                json.dumps({"type": 2, "num_teams": 12}),
                json.dumps({"rec": 0.5}),
                json.dumps(["QB", "RB", "RB", "WR", "WR", "TE", "FLEX"]),
                json.dumps({}),
            ],
        )

    settings = Settings(
        sleeper_league_id="L1",
        league_name="LDDL",
        data_dir=tmp_path,
        output_dir=tmp_path / "out",
    )

    # Patch the FC client used inside take_snapshot.
    from lddl import snapshot as snap_module

    class FakeFC:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def get_current_values(self, **kwargs):
            return SAMPLE_PAYLOAD

    monkeypatch.setattr(snap_module, "FantasyCalcClient", FakeFC)

    n = take_snapshot(settings, snapshot_date=date(2026, 5, 4))
    assert n == 2

    with duckdb.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT name, position, value, format_num_qbs, format_is_dynasty "
            "FROM fc_snapshots ORDER BY value DESC"
        ).fetchall()
    assert rows == [
        ("Bijan Robinson", "RB", 11045, 1, True),
        ("2026 Pick 1.01", "PICK", 7485, 1, True),
    ]


def test_take_snapshot_skips_when_today_already_taken(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "lddl.duckdb"
    init_schema(db_path)
    with duckdb.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO leagues (
                league_id, season, name, status, total_rosters, league_type,
                playoff_week_start, playoff_teams, settings, scoring_settings,
                roster_positions, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "L1", "2026", "LDDL", "drafting", 12, 2, 15, 6,
                json.dumps({"type": 2, "num_teams": 12}),
                json.dumps({"rec": 0.5}),
                json.dumps(["QB"]),
                json.dumps({}),
            ],
        )

    settings = Settings(
        sleeper_league_id="L1",
        league_name="LDDL",
        data_dir=tmp_path,
        output_dir=tmp_path / "out",
    )

    fetch_count = {"n": 0}

    class FakeFC:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def get_current_values(self, **kwargs):
            fetch_count["n"] += 1
            return SAMPLE_PAYLOAD

    from lddl import snapshot as snap_module

    monkeypatch.setattr(snap_module, "FantasyCalcClient", FakeFC)

    take_snapshot(settings, snapshot_date=date(2026, 5, 4))
    take_snapshot(settings, snapshot_date=date(2026, 5, 4))  # should skip
    assert fetch_count["n"] == 1

    take_snapshot(settings, snapshot_date=date(2026, 5, 4), force=True)
    assert fetch_count["n"] == 2


def test_detect_format_reads_league_settings(tmp_path: Path) -> None:
    db_path = tmp_path / "lddl.duckdb"
    init_schema(db_path)
    with duckdb.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO leagues (
                league_id, season, name, status, total_rosters, league_type,
                playoff_week_start, playoff_teams, settings, scoring_settings,
                roster_positions, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "L1", "2026", "LDDL", "drafting", 12, 2, 15, 6,
                json.dumps({"type": 2, "num_teams": 12}),
                json.dumps({"rec": 0.5}),
                json.dumps(["QB", "SUPER_FLEX", "RB", "WR"]),
                json.dumps({}),
            ],
        )
        fmt = _detect_format(conn)
    assert fmt.is_dynasty
    assert fmt.num_qbs == 2  # SUPER_FLEX in roster_positions → Superflex
    assert fmt.num_teams == 12
    assert fmt.ppr == 0.5
