"""Smoke tests for the validate module — every check should run without error
on an empty (schema-only) DuckDB.
"""

from __future__ import annotations

from pathlib import Path

from lddl.store.db import init_schema
from lddl.validate import Severity, run_validation
from lddl.validate.checks import ALL_CHECKS


def test_all_checks_execute_on_empty_db(tmp_path: Path) -> None:
    db_path = tmp_path / "empty.duckdb"
    init_schema(db_path)
    results = run_validation(db_path, output_path=None)
    assert len(results) == len(ALL_CHECKS)
    # No check should crash and report itself as a generic exception
    for r in results:
        assert "check raised" not in r.summary, f"check {r.id} crashed: {r.summary}"


def test_summary_table_has_all_categories(tmp_path: Path) -> None:
    db_path = tmp_path / "empty.duckdb"
    init_schema(db_path)
    results = run_validation(db_path, output_path=None)
    cats = {r.category for r in results}
    assert cats == {"coverage", "identity", "trades", "matchups", "drafts", "hygiene"}


def test_writes_markdown_when_path_given(tmp_path: Path) -> None:
    db_path = tmp_path / "empty.duckdb"
    init_schema(db_path)
    out = tmp_path / "report.md"
    run_validation(db_path, output_path=out)
    text = out.read_text()
    assert "LDDL Ingest Validation Report" in text
    assert "## Summary" in text
    assert "## Coverage" in text


def test_severity_enum_values() -> None:
    assert Severity.GREEN.value == "GREEN"
    assert Severity.YELLOW.value == "YELLOW"
    assert Severity.RED.value == "RED"
