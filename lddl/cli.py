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
def snapshot() -> None:
    """Snapshot today's FantasyCalc dynasty values into the local store."""
    rprint("[yellow]snapshot: not yet implemented (build step 3)[/yellow]")
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
