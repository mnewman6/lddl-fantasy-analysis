"""KeepTradeCut daily snapshot orchestrator.

Mirrors the FantasyCalc pipeline shape: detect league format from the
``leagues`` table, fetch via :class:`KTCClient`, normalize each row, upsert
into ``ktc_snapshots``, then auto-map KTC players → Sleeper player_ids by
normalized-name + position match against the ``players`` table.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import duckdb
from rich.console import Console
from rich.table import Table

from lddl.clients.ktc import KTCClient
from lddl.config import Settings
from lddl.store.db import connect, init_schema


@dataclass
class KTCFormat:
    is_dynasty: bool
    num_qbs: int    # 1 or 2

    def values_key(self) -> str:
        return "superflexValues" if self.num_qbs == 2 else "oneQBValues"

    def __str__(self) -> str:
        kind = "dynasty" if self.is_dynasty else "redraft"
        qb = "Superflex" if self.num_qbs == 2 else "1QB"
        return f"{qb} {kind}"


# Suffixes we strip when normalizing names.
_NAME_SUFFIXES = re.compile(r"\b(?:jr|sr|ii|iii|iv|v)\.?\b", re.IGNORECASE)
_NAME_PUNCT = re.compile(r"[^\w\s]")


def _normalize_name(name: str) -> str:
    s = (name or "").lower()
    s = _NAME_SUFFIXES.sub("", s)
    s = _NAME_PUNCT.sub(" ", s)
    return " ".join(s.split())


def _detect_format(conn: duckdb.DuckDBPyConnection) -> KTCFormat:
    row = conn.execute(
        """
        SELECT settings, roster_positions
        FROM leagues ORDER BY season DESC LIMIT 1
        """
    ).fetchone()
    if not row:
        raise RuntimeError("No leagues in DB. Run `lddl ingest` first.")
    settings = json.loads(row[0]) if row[0] else {}
    roster_positions = json.loads(row[1]) if row[1] else []

    is_dynasty = settings.get("type") == 2
    num_qbs = 2 if "SUPER_FLEX" in roster_positions else 1
    return KTCFormat(is_dynasty=is_dynasty, num_qbs=num_qbs)


def _row_for(player: dict[str, Any], fmt: KTCFormat, snapshot_date: date,
             fetched_at: datetime) -> list:
    """Flatten a KTC player record + chosen format bucket into a DB row."""
    vals = player.get(fmt.values_key()) or {}
    return [
        snapshot_date,
        player.get("playerID"),
        None,                                  # sleeper_id (filled by mapper)
        player.get("playerName"),
        player.get("position"),
        player.get("team"),
        player.get("age") or None,
        vals.get("value"),
        vals.get("rank"),
        vals.get("positionalRank"),
        vals.get("overallTrend"),
        vals.get("overall7DayTrend"),
        vals.get("positionalTrend"),
        vals.get("positional7DayTrend"),
        vals.get("overallTier"),
        vals.get("positionalTier"),
        vals.get("tradeCount"),
        fmt.num_qbs,
        fmt.is_dynasty,
        json.dumps(player),
        fetched_at,
    ]


def _auto_map_to_sleeper(
    conn: duckdb.DuckDBPyConnection,
    snapshot_date: date,
    fmt: KTCFormat,
) -> tuple[int, int, int]:
    """Populate ktc_player_map + back-fill ktc_snapshots.sleeper_id.

    Returns (n_high, n_medium, n_unmatched) over human players in this snapshot.
    """
    # Pull KTC players from this snapshot we still need to map.
    ktc_players = conn.execute(
        """
        SELECT s.ktc_player_id, s.name, s.position, s.team
        FROM ktc_snapshots s
        WHERE s.snapshot_date = ?
          AND s.format_num_qbs = ?
          AND s.format_is_dynasty = ?
          AND s.position != 'RDP'
        """,
        [snapshot_date, fmt.num_qbs, fmt.is_dynasty],
    ).fetchall()

    # Build sleeper lookup: normalized full_name + position → list of player_ids.
    sleepers = conn.execute(
        """
        SELECT player_id, full_name, position, team
        FROM players
        WHERE full_name IS NOT NULL
        """
    ).fetchall()
    by_name_pos: dict[tuple[str, str], list[tuple[str, str | None]]] = {}
    by_name: dict[str, list[tuple[str, str | None, str | None]]] = {}
    for pid, full_name, pos, team in sleepers:
        norm = _normalize_name(full_name)
        if not norm:
            continue
        by_name_pos.setdefault((norm, pos or ""), []).append((pid, team))
        by_name.setdefault(norm, []).append((pid, pos, team))

    now = datetime.now(timezone.utc)
    high = 0
    medium = 0
    unmatched: list[tuple[int, str, str]] = []
    map_rows: list[list] = []

    for ktc_id, name, pos, team in ktc_players:
        norm = _normalize_name(name)
        sleeper_id: str | None = None
        confidence: str | None = None

        # 1) exact name + position. If multiple matches, prefer same team.
        cands = by_name_pos.get((norm, pos or ""), [])
        if len(cands) == 1:
            sleeper_id = cands[0][0]
            confidence = "high"
        elif len(cands) > 1 and team:
            same_team = [pid for pid, t in cands if (t or "").upper() == (team or "").upper()]
            if len(same_team) == 1:
                sleeper_id = same_team[0]
                confidence = "high"
            else:
                # ambiguous — fall through
                pass

        # 2) fallback: name-only unique match
        if sleeper_id is None:
            name_cands = by_name.get(norm, [])
            if len(name_cands) == 1:
                sleeper_id = name_cands[0][0]
                confidence = "medium"

        if sleeper_id is None:
            unmatched.append((ktc_id, name, pos or ""))
            continue

        if confidence == "high":
            high += 1
        else:
            medium += 1
        map_rows.append([ktc_id, sleeper_id, "auto", confidence, now])

    if map_rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO ktc_player_map
                (ktc_player_id, sleeper_id, source, confidence, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            map_rows,
        )

    # Back-fill sleeper_id on ktc_snapshots from the map.
    conn.execute(
        """
        UPDATE ktc_snapshots
        SET sleeper_id = m.sleeper_id
        FROM ktc_player_map m
        WHERE ktc_snapshots.ktc_player_id = m.ktc_player_id
          AND ktc_snapshots.sleeper_id IS NULL
        """
    )

    return high, medium, len(unmatched)


def take_ktc_snapshot(
    settings: Settings,
    *,
    force: bool = False,
    snapshot_date: date | None = None,
) -> int:
    """Return number of rows written for this KTC snapshot."""
    snapshot_date = snapshot_date or date.today()
    console = Console()

    init_schema(settings.duckdb_path)

    with connect(settings.duckdb_path) as conn:
        fmt = _detect_format(conn)
        console.print(
            f"[bold]LDDL KeepTradeCut Snapshot[/bold] — {snapshot_date.isoformat()}"
        )
        console.print(f"  Format: {fmt}")

        existing = conn.execute(
            """
            SELECT COUNT(*) FROM ktc_snapshots
            WHERE snapshot_date = ?
              AND format_num_qbs = ?
              AND format_is_dynasty = ?
            """,
            [snapshot_date, fmt.num_qbs, fmt.is_dynasty],
        ).fetchone()[0]

        if existing > 0 and not force:
            console.print(
                f"  [yellow]Already have KTC snapshot for {snapshot_date} "
                f"({existing} rows). Use --force to refetch.[/yellow]"
            )
            return existing

        cache_dir = settings.raw_cache_dir / "ktc"
        with KTCClient(cache_dir) as ktc:
            data = ktc.get_dynasty_rankings(
                snapshot_date=snapshot_date, force=force
            )

        fetched_at = datetime.now(timezone.utc)
        rows = [_row_for(p, fmt, snapshot_date, fetched_at) for p in data]

        conn.executemany(
            """
            INSERT OR REPLACE INTO ktc_snapshots (
                snapshot_date, ktc_player_id, sleeper_id, name, position, team,
                age, value, overall_rank, position_rank,
                overall_trend_30d, overall_trend_7d,
                positional_trend_30d, positional_trend_7d,
                overall_tier, positional_tier, trade_count,
                format_num_qbs, format_is_dynasty, raw, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

        n_total = len(rows)
        n_picks = sum(1 for r in rows if r[4] == "RDP")
        n_players = n_total - n_picks
        console.print(
            f"  [green]Snapshotted {n_total} KTC values[/green] "
            f"({n_players} players, {n_picks} picks)"
        )

        high, medium, unmatched = _auto_map_to_sleeper(conn, snapshot_date, fmt)
        total_human = high + medium + unmatched
        if total_human:
            console.print(
                f"  Mapping → Sleeper: "
                f"[green]{high} high[/green] · "
                f"[yellow]{medium} medium[/yellow] · "
                f"[red]{unmatched} unmatched[/red] "
                f"(of {total_human} human players)"
            )

        _print_top_assets(console, conn, snapshot_date, fmt)
        return n_total


def _print_top_assets(
    console: Console,
    conn: duckdb.DuckDBPyConnection,
    snapshot_date: date,
    fmt: KTCFormat,
) -> None:
    binds = [snapshot_date, fmt.num_qbs, fmt.is_dynasty]

    top_players = conn.execute(
        """
        SELECT name, position, team, age, value, overall_trend_30d
        FROM ktc_snapshots
        WHERE snapshot_date = ? AND format_num_qbs = ? AND format_is_dynasty = ?
          AND position != 'RDP'
        ORDER BY value DESC LIMIT 5
        """,
        binds,
    ).fetchall()
    pl_table = Table(title="Top 5 players (KTC)")
    for col in ["Name", "Pos", "Team", "Age", "Value", "30d trend"]:
        pl_table.add_column(col)
    for r in top_players:
        pl_table.add_row(
            r[0], r[1], r[2] or "—",
            f"{r[3]:.1f}" if r[3] is not None else "—",
            f"{r[4]:,}" if r[4] is not None else "—",
            f"{r[5]:+}" if r[5] is not None else "—",
        )
    console.print(pl_table)

    top_picks = conn.execute(
        """
        SELECT name, value, overall_trend_30d FROM ktc_snapshots
        WHERE snapshot_date = ? AND format_num_qbs = ? AND format_is_dynasty = ?
          AND position = 'RDP'
        ORDER BY value DESC LIMIT 5
        """,
        binds,
    ).fetchall()
    pk_table = Table(title="Top 5 picks (KTC)")
    for col in ["Name", "Value", "30d trend"]:
        pk_table.add_column(col)
    for r in top_picks:
        pk_table.add_row(
            r[0],
            f"{r[1]:,}" if r[1] is not None else "—",
            f"{r[2]:+}" if r[2] is not None else "—",
        )
    console.print(pk_table)
