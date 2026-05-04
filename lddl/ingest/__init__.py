"""End-to-end ingest orchestrator."""

from __future__ import annotations

from rich.console import Console

from lddl.clients.sleeper import SleeperClient
from lddl.config import Settings
from lddl.ingest.brackets import ingest_brackets
from lddl.ingest.drafts import ingest_drafts
from lddl.ingest.league_history import (
    ingest_rosters,
    ingest_users,
    rebuild_managers,
    upsert_league,
    walk_history,
)
from lddl.ingest.matchups import ingest_matchups
from lddl.ingest.players import refresh_players
from lddl.ingest.transactions import ingest_traded_picks, ingest_transactions
from lddl.store.db import connect, init_schema


def run_ingest(
    settings: Settings,
    *,
    force: bool = False,
    skip_players: bool = False,
) -> None:
    console = Console()
    console.print(f"[bold]Ingesting LDDL[/bold] (head={settings.sleeper_league_id})")

    init_schema(settings.duckdb_path)
    console.print(f"  schema initialized at {settings.duckdb_path}")

    with SleeperClient(settings.raw_cache_dir) as client, connect(
        settings.duckdb_path
    ) as conn:
        console.print("\n[bold]1/3 league history[/bold]")
        chain = walk_history(client, settings.sleeper_league_id, force_head=True)
        for league in chain:
            upsert_league(conn, league)
            console.print(
                f"  {league['season']} {league['league_id']} "
                f"status={league.get('status')}"
            )

        if not skip_players:
            console.print("\n[bold]2/3 players[/bold]")
            n, fresh = refresh_players(client, conn, force=force)
            console.print(
                f"  players: {n} rows ({'fresh fetch' if fresh else 'cache hit'})"
            )

        console.print("\n[bold]3/3 per-season ingest[/bold]")
        for league in chain:
            league_id = league["league_id"]
            is_complete = league.get("status") == "complete"
            season_force = force or not is_complete
            tag = "complete" if is_complete else f"in-progress ({league.get('status')})"
            console.print(
                f"\n  [cyan]{league.get('season')} ({tag}) "
                f"force={season_force}[/cyan]"
            )

            n_users = ingest_users(client, conn, league_id, force=season_force)
            n_rosters = ingest_rosters(client, conn, league_id, force=season_force)
            n_matchups = ingest_matchups(client, conn, league_id, force=season_force)
            n_tx, n_tx_p, n_tx_pk = ingest_transactions(
                client, conn, league_id, force=season_force
            )
            n_picks = ingest_traded_picks(client, conn, league_id, force=season_force)
            n_drafts, n_dpicks, n_dtp = ingest_drafts(
                client, conn, league_id, force=season_force
            )
            n_wb, n_lb = ingest_brackets(client, conn, league_id, force=season_force)

            console.print(
                f"    users={n_users}  rosters={n_rosters}  matchups={n_matchups}\n"
                f"    transactions={n_tx} (player_moves={n_tx_p}, "
                f"pick_moves={n_tx_pk})\n"
                f"    outstanding_traded_picks={n_picks}\n"
                f"    drafts={n_drafts}  draft_picks={n_dpicks}  "
                f"draft_traded_picks={n_dtp}\n"
                f"    bracket: winners={n_wb}  losers={n_lb}"
            )

        console.print("\n[bold]rebuilding managers table[/bold]")
        n_managers = rebuild_managers(conn)
        console.print(f"  managers: {n_managers} unique user_ids")

        console.print("\n[green]ingest complete[/green]")
