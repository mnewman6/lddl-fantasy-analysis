"""Snapshot lookup helpers for both KTC (default) and FantasyCalc.

A SnapshotRef carries the source, the date, and the format dimensions needed
to scope the lookup. Callers pass the ref through to value lookups, which
dispatch to the right table (``ktc_snapshots`` vs ``fc_snapshots``).

Pick-name handling differs between the two:

* FC uses round buckets like ``"2026 1st"`` (and slot-specific
  ``"2026 Pick 1.05"`` when available, mostly for the upcoming draft).
* KTC uses tier buckets like ``"2026 Early 1st"`` / ``"2026 Mid 1st"`` /
  ``"2026 Late 1st"``.

For KTC, when callers pass an FC-style name (the existing pick_fc_name path),
we resolve it by averaging the three tier buckets for that season+round.
With a known slot we map slot 1-4 → Early, 5-8 → Mid, 9-12 → Late.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

import duckdb

Source = Literal["ktc", "fc"]
DEFAULT_SOURCE: Source = "ktc"


@dataclass
class SnapshotRef:
    snapshot_date: date
    format_num_qbs: int
    format_is_dynasty: bool
    source: Source = DEFAULT_SOURCE
    # FC-only format dimensions (irrelevant for KTC, kept for back-compat).
    format_ppr: float = 0.5
    format_num_teams: int = 12

    def filter_clause(self, alias: str = "") -> tuple[str, list]:
        """Build a WHERE clause that selects this exact snapshot."""
        prefix = f"{alias}." if alias else ""
        if self.source == "ktc":
            clause = (
                f"{prefix}snapshot_date = ? AND {prefix}format_num_qbs = ? "
                f"AND {prefix}format_is_dynasty = ?"
            )
            binds = [self.snapshot_date, self.format_num_qbs, self.format_is_dynasty]
        else:
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

    @property
    def table(self) -> str:
        return "ktc_snapshots" if self.source == "ktc" else "fc_snapshots"


def _latest_ktc(conn: duckdb.DuckDBPyConnection) -> SnapshotRef | None:
    row = conn.execute(
        """
        SELECT snapshot_date, format_num_qbs, format_is_dynasty
        FROM ktc_snapshots
        ORDER BY snapshot_date DESC, fetched_at DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        return None
    return SnapshotRef(
        snapshot_date=row[0],
        format_num_qbs=row[1],
        format_is_dynasty=row[2],
        source="ktc",
    )


def _latest_fc(conn: duckdb.DuckDBPyConnection) -> SnapshotRef | None:
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
        source="fc",
    )


def latest_snapshot(
    conn: duckdb.DuckDBPyConnection,
    source: Source = DEFAULT_SOURCE,
) -> SnapshotRef | None:
    """Return the most recent snapshot for the requested source.

    If the requested source has no snapshots, fall back to the other source.
    Inspect ``ref.source`` to see what was actually returned. Returns None
    only if both tables are empty.
    """
    if source == "ktc":
        return _latest_ktc(conn) or _latest_fc(conn)
    return _latest_fc(conn) or _latest_ktc(conn)


def value_by_sleeper_id(
    conn: duckdb.DuckDBPyConnection,
    snapshot: SnapshotRef,
    sleeper_id: str,
) -> tuple[int | None, str | None]:
    """Return (value, name) for an asset by sleeper_id at this snapshot."""
    where, binds = snapshot.filter_clause()
    row = conn.execute(
        f"SELECT value, name FROM {snapshot.table} "
        f"WHERE {where} AND sleeper_id = ? LIMIT 1",
        binds + [sleeper_id],
    ).fetchone()
    if not row:
        return None, None
    return row[0], row[1]


# Match an FC-style pick name "YYYY ORD" (e.g. "2026 1st") with optional slot
# suffix " Pick R.SS" (e.g. "2026 Pick 1.05") so we can re-route to KTC.
_PICK_FC_RE = re.compile(
    r"^(?P<season>\d{4})\s+"
    r"(?:Pick\s+(?P<round_n>\d+)\.(?P<slot>\d{2})|"
    r"(?P<round_o>1st|2nd|3rd|4th|5th))$"
)
_ORD_TO_INT = {"1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5}
_INT_TO_ORD = {v: k for k, v in _ORD_TO_INT.items()}


def _ktc_tier_for_slot(slot: int | None) -> str | None:
    if slot is None:
        return None
    if 1 <= slot <= 4:
        return "Early"
    if 5 <= slot <= 8:
        return "Mid"
    if 9 <= slot <= 12:
        return "Late"
    return None


def value_by_name(
    conn: duckdb.DuckDBPyConnection,
    snapshot: SnapshotRef,
    name: str,
) -> int | None:
    """Look up a named asset (typically a pick bucket) by display name.

    For KTC, FC-style pick names are translated to KTC tier names. If the
    caller passes an FC slot-specific name and we can derive a tier from the
    slot, we look up the exact tier bucket. Otherwise we average all three
    Early/Mid/Late values for that season+round, which is a reasonable
    estimate for an unknown-slot future pick.
    """
    where, binds = snapshot.filter_clause()

    if snapshot.source == "ktc":
        m = _PICK_FC_RE.match(name)
        if m:
            season = m.group("season")
            if m.group("round_n"):
                round_ = int(m.group("round_n"))
                slot = int(m.group("slot"))
                tier = _ktc_tier_for_slot(slot)
                if tier:
                    target = f"{season} {tier} {_INT_TO_ORD.get(round_, f'{round_}th')}"
                    row = conn.execute(
                        f"SELECT value FROM {snapshot.table} "
                        f"WHERE {where} AND name = ? LIMIT 1",
                        binds + [target],
                    ).fetchone()
                    if row:
                        return row[0]
                # fall through to round-average if tier missed
                round_str = _INT_TO_ORD.get(round_, f"{round_}th")
            else:
                round_str = m.group("round_o")
            # Average the three tier buckets for this season+round.
            avg = conn.execute(
                f"SELECT AVG(value) FROM {snapshot.table} "
                f"WHERE {where} AND name IN (?, ?, ?)",
                binds + [
                    f"{season} Early {round_str}",
                    f"{season} Mid {round_str}",
                    f"{season} Late {round_str}",
                ],
            ).fetchone()
            if avg and avg[0] is not None:
                return int(round(avg[0]))
        # Non-pick name passed for KTC — try direct match anyway.
        row = conn.execute(
            f"SELECT value FROM {snapshot.table} "
            f"WHERE {where} AND name = ? LIMIT 1",
            binds + [name],
        ).fetchone()
        return row[0] if row else None

    # FC: direct lookup
    row = conn.execute(
        f"SELECT value FROM fc_snapshots WHERE {where} AND name = ? LIMIT 1",
        binds + [name],
    ).fetchone()
    return row[0] if row else None
