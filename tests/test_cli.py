"""Smoke tests for the CLI scaffold."""

from __future__ import annotations

from typer.testing import CliRunner

from lddl.cli import app

runner = CliRunner()


def test_root_help_runs() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "lddl" in result.stdout.lower()


def test_report_help_runs() -> None:
    result = runner.invoke(app, ["report", "--help"])
    assert result.exit_code == 0
    assert "trade-recap" in result.stdout
    assert "manager-history" in result.stdout
    assert "league-state" in result.stdout


def test_top_level_commands_present() -> None:
    result = runner.invoke(app, ["--help"])
    assert "ingest" in result.stdout
    assert "snapshot" in result.stdout
    assert "report" in result.stdout
