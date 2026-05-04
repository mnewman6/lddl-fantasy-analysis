"""FantasyCalc snapshot lookup helpers.

For step 4 v1 we have a single snapshot (today). All historical trades are
graded against this current snapshot and flagged as imprecise. As snapshots
accumulate, the trade grader will prefer the snapshot closest-in-time to the
trade date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import duckdb


@dataclass
class SnapshotRef:
    snapshot_date: date
    format_num_qbs: int
    format_ppr: float
    format_num_teams: int
    format_is_dynasty: bool

    def filter_clause(self, alias: str = "") -> tuple[str, list]:
        prefix = f"{alias}." if alias else ""
        clause = (
            f"{prefix}snapshot_date = ? AND {prefix}format_num_qbs = ? "
            f"AND {prefix}format_ppr = ? AND {prefix}format_num_teams = ? "
            f"AND {prefix}format_is_dynasty = ?"
        )
        binds = [
            self.snapshot_date,
            self.format_num_qbs,
            self.format_ppr,
            self.format_num_teams,
            self.format_is_dynasty,
        ]
        return clause, binds


def latest_snapshot(conn: duckdb.DuckDBPyConnection) -> SnapshotRef | None:
    """Return the most recent snapshot's (date + format dimensions)."""
    row = conn.execute(
        """
        SELECT snapshot_date, format_num_qbs, format_ppr, format_num_teams,
               format_is_dynasty
        FROM fc_snapshots
        ORDER BY snapshot_date DESC, fetched_at DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        return None
    return SnapshotRef(
        snapshot_date=row[0],
        format_num_qbs=row[1],
        format_ppr=row[2],
        format_num_teams=row[3],
        format_is_dynasty=row[4],
    )


def value_by_sleeper_id(
    conn: duckdb.DuckDBPyConnection,
    snapshot: SnapshotRef,
    sleeper_id: str,
) -> tuple[int | None, str | None]:
    """Return (value, name) for an asset by sleeper_id at a snapshot, or (None, None)."""
    where, binds = snapshot.filter_clause()
    row = conn.execute(
        f"SELECT value, name FROM fc_snapshots "
        f"WHERE {where} AND sleeper_id = ? LIMIT 1",
        binds + [sleeper_id],
    ).fetchone()
    if not row:
        return None, None
    return row[0], row[1]


def value_by_name(
    conn: duckdb.DuckDBPyConnection,
    snapshot: SnapshotRef,
    name: str,
) -> int | None:
    """Look up a named pick bucket like '2026 1st'."""
    where, binds = snapshot.filter_clause()
    row = conn.execute(
        f"SELECT value FROM fc_snapshots WHERE {where} AND name = ? LIMIT 1",
        binds + [name],
    ).fetchone()
    return row[0] if row else None
