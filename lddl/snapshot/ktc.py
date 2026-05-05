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
# Match initials joined by periods (e.g. "D.J." → "DJ"). Apply BEFORE the
# generic punct stripper so we don't end up with "d j" (two tokens).
_INITIALS = re.compile(r"\b([a-zA-Z])\.\s*([a-zA-Z])\.")
_NAME_PUNCT = re.compile(r"[^\w\s]")

# KTC and Sleeper occasionally disagree on team abbreviations. Map both sides
# to a canonical code so team-tiebreaks don't miss real matches.
_TEAM_ALIASES = {
    "NEP": "NE", "JAC": "JAX", "LVR": "LV", "WSH": "WAS", "ARZ": "ARI",
}


def _norm_team(t: str | None) -> str:
    if not t:
        return ""
    t = t.upper().strip()
    return _TEAM_ALIASES.get(t, t)


def _normalize_name(name: str) -> str:
    s = (name or "").lower()
    s = _INITIALS.sub(lambda m: f"{m.group(1)}{m.group(2)}", s)
    s = _NAME_SUFFIXES.sub("", s)
    s = _NAME_PUNCT.sub(" ", s)
    return " ".join(s.split())


def _last_name(norm: str) -> str:
    """Last token of a normalized name (e.g. 'dj moore' → 'moore')."""
    parts = norm.split()
    return parts[-1] if parts else ""


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
) -> tuple[int, int, int, list[tuple[int, str, str]]]:
    """Populate ktc_player_map + back-fill ktc_snapshots.sleeper_id.

    Returns (n_high, n_medium, n_low, unmatched_rows) over human players
    in this snapshot.
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

    # Build sleeper lookups against normalized name (full + last) and team.
    sleepers = conn.execute(
        """
        SELECT player_id, full_name, position, team
        FROM players
        WHERE full_name IS NOT NULL
        """
    ).fetchall()
    by_name_pos: dict[tuple[str, str], list[tuple[str, str]]] = {}
    by_name: dict[str, list[tuple[str, str | None, str]]] = {}
    by_lastname_pos: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for pid, full_name, pos, team in sleepers:
        norm = _normalize_name(full_name)
        if not norm:
            continue
        nteam = _norm_team(team)
        by_name_pos.setdefault((norm, pos or ""), []).append((pid, nteam))
        by_name.setdefault(norm, []).append((pid, pos, nteam))
        ln = _last_name(norm)
        if ln:
            by_lastname_pos.setdefault((ln, pos or ""), []).append((pid, nteam))

    now = datetime.now(timezone.utc)
    high = 0
    medium = 0
    low = 0
    unmatched: list[tuple[int, str, str]] = []
    map_rows: list[list] = []

    for ktc_id, name, pos, team in ktc_players:
        norm = _normalize_name(name)
        nteam = _norm_team(team)
        sleeper_id: str | None = None
        confidence: str | None = None

        # 1) exact normalized name + position. If multiple, tiebreak on team.
        cands = by_name_pos.get((norm, pos or ""), [])
        if len(cands) == 1:
            sleeper_id = cands[0][0]
            confidence = "high"
        elif len(cands) > 1 and nteam:
            same_team = [pid for pid, t in cands if t == nteam]
            if len(same_team) == 1:
                sleeper_id = same_team[0]
                confidence = "high"

        # 2) name-only unique match (covers position oddities)
        if sleeper_id is None:
            name_cands = by_name.get(norm, [])
            if len(name_cands) == 1:
                sleeper_id = name_cands[0][0]
                confidence = "medium"

        # 3) last-name + position + team fallback (covers nickname diffs:
        #    Chig/Chigoziem, Bam/Zonovan, Gabe/Gabriel)
        if sleeper_id is None and nteam:
            ln = _last_name(norm)
            ln_cands = by_lastname_pos.get((ln, pos or ""), [])
            same_team = [pid for pid, t in ln_cands if t == nteam]
            if len(same_team) == 1:
                sleeper_id = same_team[0]
                confidence = "low"

        if sleeper_id is None:
            unmatched.append((ktc_id, name, pos or ""))
            continue

        if confidence == "high":
            high += 1
        elif confidence == "medium":
            medium += 1
        else:
            low += 1
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

    return high, medium, low, unmatched


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

        high, medium, low, unmatched = _auto_map_to_sleeper(
            conn, snapshot_date, fmt
        )
        total_human = high + medium + low + len(unmatched)
        if total_human:
            console.print(
                f"  Mapping → Sleeper: "
                f"[green]{high} high[/green] · "
                f"[yellow]{medium} medium[/yellow] · "
                f"[blue]{low} low[/blue] · "
                f"[red]{len(unmatched)} unmatched[/red] "
                f"(of {total_human} human players)"
            )
            if unmatched:
                console.print("  [red]Unmatched (likely rookies not in Sleeper "
                              "yet, or true name diffs needing manual map):[/red]")
                for ktc_id, name, pos in unmatched:
                    console.print(f"    KTC #{ktc_id} · {name} ({pos})")

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
