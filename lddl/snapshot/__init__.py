"""FantasyCalc daily snapshot orchestrator."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone

import duckdb
from rich.console import Console
from rich.table import Table

from lddl.clients.fantasycalc import FantasyCalcClient
from lddl.config import Settings
from lddl.store.db import connect, init_schema


@dataclass
class LeagueFormat:
    is_dynasty: bool
    num_qbs: int
    num_teams: int
    ppr: float

    def __str__(self) -> str:
        kind = "dynasty" if self.is_dynasty else "redraft"
        qb = "Superflex" if self.num_qbs == 2 else f"{self.num_qbs}QB"
        return f"{qb}, {self.ppr} PPR, {self.num_teams}-team {kind}"


def _detect_format(conn: duckdb.DuckDBPyConnection) -> LeagueFormat:
    row = conn.execute(
        """
        SELECT settings, scoring_settings, roster_positions
        FROM leagues ORDER BY season DESC LIMIT 1
        """
    ).fetchone()
    if not row:
        raise RuntimeError("No leagues in DB. Run `lddl ingest` first.")
    settings = json.loads(row[0]) if row[0] else {}
    scoring = json.loads(row[1]) if row[1] else {}
    roster_positions = json.loads(row[2]) if row[2] else []

    is_dynasty = settings.get("type") == 2
    num_qbs = 2 if "SUPER_FLEX" in roster_positions else 1
    num_teams = settings.get("num_teams") or len(roster_positions) or 12
    if not isinstance(num_teams, int):
        num_teams = 12
    ppr = float(scoring.get("rec") or 0.0)

    return LeagueFormat(
        is_dynasty=is_dynasty, num_qbs=num_qbs, num_teams=num_teams, ppr=ppr
    )


def take_snapshot(
    settings: Settings,
    *,
    force: bool = False,
    snapshot_date: date | None = None,
) -> int:
    """Return number of rows written for this snapshot."""
    snapshot_date = snapshot_date or date.today()
    console = Console()

    init_schema(settings.duckdb_path)

    with connect(settings.duckdb_path) as conn:
        fmt = _detect_format(conn)
        console.print(
            f"[bold]LDDL FantasyCalc Snapshot[/bold] — {snapshot_date.isoformat()}"
        )
        console.print(f"  Format detected from leagues: {fmt}")

        existing = conn.execute(
            """
            SELECT COUNT(*) FROM fc_snapshots
            WHERE snapshot_date = ?
              AND format_num_qbs = ?
              AND format_ppr = ?
              AND format_num_teams = ?
              AND format_is_dynasty = ?
            """,
            [
                snapshot_date,
                fmt.num_qbs,
                fmt.ppr,
                fmt.num_teams,
                fmt.is_dynasty,
            ],
        ).fetchone()[0]

        if existing > 0 and not force:
            console.print(
                f"  [yellow]Already have snapshot for {snapshot_date} "
                f"({existing} rows). Use --force to refetch.[/yellow]"
            )
            return existing

        cache_dir = settings.raw_cache_dir / "fantasycalc"
        with FantasyCalcClient(cache_dir) as fc:
            values = fc.get_current_values(
                is_dynasty=fmt.is_dynasty,
                num_qbs=fmt.num_qbs,
                num_teams=fmt.num_teams,
                ppr=fmt.ppr,
                snapshot_date=snapshot_date,
                force=force,
            )

        fetched_at = datetime.now(timezone.utc)
        rows: list[list] = []
        for v in values:
            p = v.get("player") or {}
            rows.append(
                [
                    snapshot_date,
                    p.get("id"),
                    p.get("sleeperId"),
                    p.get("name"),
                    p.get("position"),
                    p.get("maybeTeam"),
                    p.get("maybeAge"),
                    v.get("value"),
                    v.get("overallRank"),
                    v.get("positionRank"),
                    v.get("trend30Day"),
                    v.get("redraftValue"),
                    v.get("combinedValue"),
                    v.get("maybeTier"),
                    v.get("maybeTradeFrequency"),
                    fmt.num_qbs,
                    fmt.ppr,
                    fmt.num_teams,
                    fmt.is_dynasty,
                    json.dumps(v),
                    fetched_at,
                ]
            )

        conn.executemany(
            """
            INSERT OR REPLACE INTO fc_snapshots (
                snapshot_date, fc_player_id, sleeper_id, name, position, team,
                age, value, overall_rank, position_rank, trend_30_day,
                redraft_value, combined_value, tier, trade_frequency,
                format_num_qbs, format_ppr, format_num_teams, format_is_dynasty,
                raw, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        n_total = len(rows)
        n_picks = sum(1 for r in rows if r[4] == "PICK")
        n_players = n_total - n_picks
        console.print(
            f"  [green]Snapshotted {n_total} values[/green] "
            f"({n_players} players, {n_picks} picks)"
        )
        _print_top_assets(console, conn, snapshot_date, fmt)
        return n_total


def _print_top_assets(
    console: Console,
    conn: duckdb.DuckDBPyConnection,
    snapshot_date: date,
    fmt: LeagueFormat,
) -> None:
    where = (
        "snapshot_date = ? AND format_num_qbs = ? AND format_ppr = ? "
        "AND format_num_teams = ? AND format_is_dynasty = ?"
    )
    binds = [snapshot_date, fmt.num_qbs, fmt.ppr, fmt.num_teams, fmt.is_dynasty]

    top_players = conn.execute(
        f"SELECT name, position, team, age, value, trend_30_day "
        f"FROM fc_snapshots WHERE {where} AND position != 'PICK' "
        f"ORDER BY value DESC LIMIT 5",
        binds,
    ).fetchall()
    pl_table = Table(title="Top 5 players")
    for col in ["Name", "Pos", "Team", "Age", "Value", "30d trend"]:
        pl_table.add_column(col)
    for r in top_players:
        pl_table.add_row(
            r[0],
            r[1],
            r[2] or "—",
            f"{r[3]:.1f}" if r[3] is not None else "—",
            f"{r[4]:,}",
            f"{r[5]:+}" if r[5] is not None else "—",
        )
    console.print(pl_table)

    top_picks = conn.execute(
        f"SELECT name, value, trend_30_day FROM fc_snapshots "
        f"WHERE {where} AND position = 'PICK' ORDER BY value DESC LIMIT 5",
        binds,
    ).fetchall()
    pk_table = Table(title="Top 5 picks")
    for col in ["Name", "Value", "30d trend"]:
        pk_table.add_column(col)
    for r in top_picks:
        pk_table.add_row(
            r[0], f"{r[1]:,}", f"{r[2]:+}" if r[2] is not None else "—"
        )
    console.print(pk_table)
