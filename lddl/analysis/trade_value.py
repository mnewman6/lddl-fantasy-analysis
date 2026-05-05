"""KTC-style "raw-adjustment" trade-value formula.

Per Javelin Fantasy Football's reverse-engineering of KeepTradeCut's
JavaScript (Sept 2022), each player's contribution to a trade is a
non-linear function of their value relative to (a) the trade's top
player and (b) the value-system maximum. The result is that adding a
second mid-tier player to your side contributes far less than its
nominal sum would suggest — implicitly capturing both the 2-for-1
discount and the best-player-wins premium without ad-hoc rules.

  raw(p, t, v) = p * [
      0.184 * (p/v)**8
    + 0.368 * (p/t)**1.3
    + 0.268 * (p/(v+2000))**1.28
  ]

Where p = player value, t = top player value across BOTH sides of the
trade, v = the value-system maximum (KTC = 9999, FC ≈ 11000).

Caveats:
- Reverse-engineered from KTC's site JS, not officially published. The
  formula was stable as of Sept 2022 per Javelin's analysis; KTC may
  have updated it since.
- For non-elite players (most of the roster), the formula is fairly
  source-agnostic — the (p/v)^8 stud term contributes near-zero unless
  p is close to v. So FC vs KTC mostly differ at the very top end.
- Picks use the same formula as players. Their "value" is whatever the
  snapshot quotes (slot-specific or round-bucket).
"""

from __future__ import annotations

from collections.abc import Iterable

KTC_MAX_VALUE = 9999
FC_MAX_VALUE = 11000  # Bijan-tier players occasionally exceed 9999 in FC


def effective_value(
    value: int | float,
    top_value: int | float,
    max_value: int | float = KTC_MAX_VALUE,
) -> float:
    """Single asset's adjusted contribution to a trade."""
    if value <= 0 or top_value <= 0:
        return 0.0
    p = float(value)
    t = max(float(top_value), 1.0)
    v = float(max_value)
    return p * (
        0.184 * (p / v) ** 8
        + 0.368 * (p / t) ** 1.3
        + 0.268 * (p / (v + 2000)) ** 1.28
    )


def effective_sum(
    values: Iterable[int | float],
    top_value: int | float | None = None,
    max_value: int | float = KTC_MAX_VALUE,
) -> float:
    """Sum effective_value across a list of asset values.

    If top_value is None, uses the max of `values`. Pass top_value
    explicitly when balancing across two sides of a trade so both
    sides' assets are weighted against the same reference.
    """
    vals = [float(v) for v in values if v]
    if not vals:
        return 0.0
    if top_value is None:
        top_value = max(vals)
    return sum(effective_value(v, top_value, max_value) for v in vals)


def trade_top_value(*sides_values: Iterable[int | float]) -> float:
    """Return the maximum asset value across all sides of a trade."""
    candidates: list[float] = []
    for side in sides_values:
        for v in side:
            if v:
                candidates.append(float(v))
    return max(candidates) if candidates else 0.0
