"""Map traded picks to FantasyCalc lookups.

Step 4 v1: every pick uses FantasyCalc's generic round bucket
(``"{season} 1st"``, ``"{season} 2nd"``, ...). Slot-specific values
(``"{season} Pick {round}.{slot:02d}"``) would be more precise but
require resolving each pick's slot from prior-season standings or from
``draft_picks``, which is ambiguous when one picker received multiple
picks of the same round in the same season — which actually happens in
LDDL. Round buckets are directionally accurate and avoid the ambiguity.
"""

from __future__ import annotations

ROUND_ORDINAL = {1: "1st", 2: "2nd", 3: "3rd"}


def round_to_ordinal(r: int) -> str:
    return ROUND_ORDINAL.get(r, f"{r}th")


def pick_fc_name(season: str, round_: int) -> str:
    """The FantasyCalc round-bucket name for a pick."""
    return f"{season} {round_to_ordinal(round_)}"


def pick_label(season: str, round_: int, orig_roster: int) -> str:
    """Human-readable pick reference for reports."""
    return f"{season} {round_to_ordinal(round_)} (orig roster {orig_roster})"
