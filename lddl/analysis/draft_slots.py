"""Resolve traded picks to actual draft slots.

For each ``(season, round, orig_roster)`` we want to know the slot 1..N so we
can value the pick using FantasyCalc's slot-specific entry (e.g.
"2026 Pick 1.05") rather than the coarser round bucket ("2026 1st").

Strategy:
  1. Pull every actual draft pick made in that ``(season, round)`` from
     ``draft_picks`` — gives us {slot → final_picker}.
  2. Pull every traded-pick chain end-state from ``transaction_picks`` —
     gives us {orig → final_picker}.
  3. For pickers whose count of slots == count of acquired origs + 1, the
     extra slot is the picker's *own* slot.
  4. Resolve ambiguity (same picker holds N slots, all bucketable to either
     1-6 or 7-12) by using each roster's prior-season playoff status:
     playoff teams own slots in 7-12, non-playoff in 1-6. Validated on
     LDDL 2024 and 2025 — every slot resolves uniquely.

Returns ``None`` for picks we can't resolve (future drafts that haven't
happened yet); callers fall back to the round bucket in that case.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

import duckdb


def _playoff_rosters(
    conn: duckdb.DuckDBPyConnection, season: str
) -> set[int]:
    return {
        r[0]
        for r in conn.execute(
            """
            SELECT DISTINCT t1 FROM playoff_bracket pb
            JOIN leagues l USING (league_id)
            WHERE l.season = ? AND pb.bracket = 'winners' AND t1 IS NOT NULL
            UNION
            SELECT DISTINCT t2 FROM playoff_bracket pb
            JOIN leagues l USING (league_id)
            WHERE l.season = ? AND pb.bracket = 'winners' AND t2 IS NOT NULL
            """,
            [season, season],
        ).fetchall()
    }


def resolve_slots_for_round(
    conn: duckdb.DuckDBPyConnection,
    season: str,
    round_: int,
) -> dict[int, int]:
    """Return {orig_roster_id: slot} for a (season, round). Empty if there's
    no draft_picks data yet (e.g., the draft hasn't happened)."""
    pick_rows = conn.execute(
        """
        SELECT dp.draft_slot, dp.roster_id
        FROM draft_picks dp
        JOIN drafts d USING (draft_id)
        JOIN leagues l USING (league_id)
        WHERE l.season = ? AND dp.round = ?
        ORDER BY dp.draft_slot
        """,
        [season, round_],
    ).fetchall()
    if not pick_rows:
        return {}

    chain_rows = conn.execute(
        """
        WITH ranked AS (
            SELECT tp.roster_id AS orig, tp.owner_id AS final_owner,
                   ROW_NUMBER() OVER (
                       PARTITION BY tp.roster_id ORDER BY t.created_at DESC
                   ) AS rn
            FROM transaction_picks tp
            JOIN transactions t USING (transaction_id)
            WHERE tp.season = ? AND tp.round = ? AND t.status = 'complete'
              AND tp.owner_id IS NOT NULL
        )
        SELECT orig, final_owner FROM ranked WHERE rn = 1
        """,
        [season, round_],
    ).fetchall()

    prev_season = str(int(season) - 1)
    playoff_rosters = _playoff_rosters(conn, prev_season)

    picker_slots: dict[int, list[int]] = defaultdict(list)
    for slot, picker in pick_rows:
        picker_slots[picker].append(slot)
    acquired_origs: dict[int, list[int]] = defaultdict(list)
    for orig, final_owner in chain_rows:
        acquired_origs[final_owner].append(orig)

    orig_to_slot: dict[int, int] = {}
    for picker, slots in picker_slots.items():
        slots = sorted(slots)
        origs_received = acquired_origs.get(picker, [])
        # If the picker holds multiple slots OR received any traded picks,
        # we need to disambiguate which slots came from which origs.
        if len(slots) > 1 or origs_received:
            picker_in_playoffs = picker in playoff_rosters
            own_candidates = [
                s for s in slots if (s >= 7) == picker_in_playoffs
            ]
            # Picker has own + acquired iff they hold one more slot than they
            # received via trades. If they traded their own pick away too,
            # all slots came from acquisitions.
            if len(slots) > len(origs_received) and own_candidates:
                own_slot = own_candidates[0]
                orig_to_slot[picker] = own_slot
                slots = [s for s in slots if s != own_slot]
            for orig in origs_received:
                orig_in_playoffs = orig in playoff_rosters
                candidates = [
                    s for s in slots if (s >= 7) == orig_in_playoffs
                ]
                chosen = candidates[0] if candidates else (slots[0] if slots else None)
                if chosen is not None:
                    orig_to_slot[orig] = chosen
                    slots = [s for s in slots if s != chosen]
        else:
            orig_to_slot[picker] = slots[0]

    return orig_to_slot


class SlotResolver:
    """Lazy per-(season, round) cache for slot resolution within one query.

    Trade-grading processes many picks; building maps once per round and
    reusing them avoids hitting DuckDB per pick.
    """

    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn
        self._cache: dict[tuple[str, int], dict[int, int]] = {}

    def slot_for(
        self, season: str, round_: int, orig_roster: int
    ) -> Optional[int]:
        key = (season, round_)
        if key not in self._cache:
            self._cache[key] = resolve_slots_for_round(
                self.conn, season, round_
            )
        return self._cache[key].get(orig_roster)
