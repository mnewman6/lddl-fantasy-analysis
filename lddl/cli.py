"""LDDL CLI entry point."""

from __future__ import annotations

import typer
from rich import print as rprint

app = typer.Typer(
    name="lddl",
    help="Local analysis tool for the LDDL dynasty fantasy football league.",
    no_args_is_help=True,
    add_completion=False,
)

report_app = typer.Typer(
    name="report",
    help="Generate shareable PDF reports.",
    no_args_is_help=True,
)
app.add_typer(report_app, name="report")

validate_app = typer.Typer(
    name="validate",
    help="Data-quality checks against the local warehouse.",
    no_args_is_help=True,
)
app.add_typer(validate_app, name="validate")


@app.command()
def ingest(
    force: bool = typer.Option(
        False, "--force", help="Refetch every endpoint, ignoring on-disk caches."
    ),
    skip_players: bool = typer.Option(
        False, "--skip-players", help="Skip the /players/nfl refresh."
    ),
) -> None:
    """Pull full league history from Sleeper into the local DuckDB store."""
    from lddl.config import get_settings
    from lddl.ingest import run_ingest

    settings = get_settings()
    if not settings.sleeper_league_id:
        rprint(
            "[red]SLEEPER_LEAGUE_ID is not set.[/red] "
            "Copy .env.example to .env and fill it in."
        )
        raise typer.Exit(code=2)
    run_ingest(settings, force=force, skip_players=skip_players)


@app.command()
def snapshot(
    force: bool = typer.Option(
        False, "--force", help="Refetch FantasyCalc even if today's snapshot exists."
    ),
) -> None:
    """Snapshot today's FantasyCalc dynasty values into the local store."""
    from lddl.config import get_settings
    from lddl.snapshot import take_snapshot

    settings = get_settings()
    if not settings.duckdb_path.exists():
        rprint(
            f"[red]No DuckDB file at {settings.duckdb_path}.[/red] "
            "Run `lddl ingest` first so we can detect your league format."
        )
        raise typer.Exit(code=2)
    take_snapshot(settings, force=force)


@validate_app.command("ingest")
def validate_ingest_cmd() -> None:
    """Run all 21 ingest data-quality checks against the local DuckDB store."""
    from lddl.config import get_settings
    from lddl.validate import Severity, run_validation

    settings = get_settings()
    if not settings.duckdb_path.exists():
        rprint(
            f"[red]No DuckDB file at {settings.duckdb_path}.[/red] "
            "Run `lddl ingest` first."
        )
        raise typer.Exit(code=2)
    output_path = settings.output_dir / "validation_report.md"
    results = run_validation(settings.duckdb_path, output_path)
    rprint(f"\n[dim]Markdown report: {output_path}[/dim]")
    if any(r.severity == Severity.RED for r in results):
        raise typer.Exit(code=1)


@report_app.command("league-state")
def report_league_state() -> None:
    """Current-season snapshot: power rankings, recent trades, waivers, standings."""
    rprint("[yellow]report league-state: not yet implemented (build step 5+)[/yellow]")
    raise typer.Exit(code=1)


@report_app.command("trade-recap")
def report_trade_recap(
    season: int = typer.Option(..., "--season", help="Season year, e.g. 2024"),
) -> None:
    """Every trade from the given season, graded then and now."""
    rprint(
        f"[yellow]report trade-recap --season {season}: "
        "not yet implemented (build step 4)[/yellow]"
    )
    raise typer.Exit(code=1)


@report_app.command("manager-history")
def report_manager_history() -> None:
    """Multi-page deep dive on every manager across all seasons."""
    rprint("[yellow]report manager-history: not yet implemented (build step 5)[/yellow]")
    raise typer.Exit(code=1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
