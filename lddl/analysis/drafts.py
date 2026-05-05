"""Draft grade analysis.

For each pick made in a 3-round rookie draft, compare the player's *current*
FantasyCalc value to the league-wide median FC value at the same (round, slot).
The startup 27-round 2023 draft is excluded — its slot semantics are different.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass

import duckdb

from lddl.analysis.snapshots import SnapshotRef


@dataclass
class PickGrade:
    season: str
    round: int
    draft_slot: int
    picked_by_user_id: str | None
    picked_by_display_name: str
    player_name: str | None
    player_id: str | None
    actual_value: int
    expected_value: float
    delta: float


@dataclass
class ManagerDraftGrade:
    user_id: str
    n_picks: int
    avg_delta: float
    best_pick: PickGrade | None
    worst_pick: PickGrade | None


def per_pick_grades(
    conn: duckdb.DuckDBPyConnection, snapshot: SnapshotRef
) -> list[PickGrade]:
    """Return one PickGrade per draft pick in 3-round rookie drafts."""
    where, binds = snapshot.filter_clause("fc")
    rows = conn.execute(
        f"""
        SELECT l.season, dp.round, dp.draft_slot, dp.picked_by, dp.player_id,
               COALESCE(p.full_name, ''), COALESCE(fc.value, 0)
        FROM draft_picks dp
        JOIN drafts d USING (draft_id)
        JOIN leagues l USING (league_id)
        LEFT JOIN players p ON dp.player_id = p.player_id
        LEFT JOIN fc_snapshots fc
            ON fc.sleeper_id = dp.player_id AND {where}
        WHERE d.rounds = 3 AND d.status = 'complete'
        ORDER BY l.season, dp.pick_no
        """,
        binds,
    ).fetchall()

    by_slot: dict[tuple[int, int], list[int]] = defaultdict(list)
    for season, rd, slot, picker, pid, _name, value in rows:
        by_slot[(rd, slot)].append(int(value or 0))

    expected = {
        slot_key: statistics.median(vals) if vals else 0.0
        for slot_key, vals in by_slot.items()
    }

    picker_to_user = {
        u: name
        for u, name in conn.execute(
            "SELECT user_id, display_name FROM managers"
        ).fetchall()
    }

    grades: list[PickGrade] = []
    for season, rd, slot, picker, pid, name, value in rows:
        actual = int(value or 0)
        exp = expected.get((rd, slot), 0.0)
        grades.append(
            PickGrade(
                season=season,
                round=rd,
                draft_slot=slot,
                picked_by_user_id=picker,
                picked_by_display_name=picker_to_user.get(picker, picker or ""),
                player_name=name or None,
                player_id=str(pid) if pid else None,
                actual_value=actual,
                expected_value=exp,
                delta=actual - exp,
            )
        )
    return grades


def aggregate_by_manager(grades: list[PickGrade]) -> dict[str, ManagerDraftGrade]:
    by_user: dict[str, list[PickGrade]] = defaultdict(list)
    for g in grades:
        if g.picked_by_user_id:
            by_user[g.picked_by_user_id].append(g)

    out: dict[str, ManagerDraftGrade] = {}
    for uid, picks in by_user.items():
        if not picks:
            continue
        avg = sum(p.delta for p in picks) / len(picks)
        best = max(picks, key=lambda p: p.delta)
        worst = min(picks, key=lambda p: p.delta)
        out[uid] = ManagerDraftGrade(
            user_id=uid,
            n_picks=len(picks),
            avg_delta=avg,
            best_pick=best,
            worst_pick=worst,
        )
    return out
