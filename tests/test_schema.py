"""Schema initialization smoke test."""

from __future__ import annotations

from pathlib import Path

import duckdb

from lddl.store.db import init_schema

EXPECTED_TABLES = {
    "leagues",
    "league_users",
    "managers",
    "rosters",
    "matchups",
    "transactions",
    "transaction_players",
    "transaction_picks",
    "traded_picks",
    "drafts",
    "draft_picks",
    "draft_traded_picks",
    "players",
}


def test_init_schema_creates_all_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "lddl.duckdb"
    init_schema(db_path)
    init_schema(db_path)  # idempotent
    with duckdb.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()
    actual = {r[0] for r in rows}
    assert EXPECTED_TABLES.issubset(actual), f"missing tables: {EXPECTED_TABLES - actual}"
