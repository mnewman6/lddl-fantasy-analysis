"""Trade analysis dataclasses + public API."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


@dataclass
class AssetValue:
    label: str                       # human-readable, shown verbatim in reports
    asset_type: str                  # 'player', 'pick', 'faab'
    sleeper_id: str | None
    value_now: int | None             # FC value at the latest snapshot we have
    snapshot_date_now: date | None    # date of that snapshot
    is_pre_snapshot_trade: bool       # True when this is graded against a snapshot
                                       # taken AFTER the trade itself (no at-trade
                                       # value available yet)


@dataclass
class Side:
    roster_id: int
    user_id: str | None
    display_name: str
    team_name: str | None
    given: list[AssetValue] = field(default_factory=list)
    received: list[AssetValue] = field(default_factory=list)
    # Effective (KTC-raw-adjusted) values, populated by the grader using
    # the top asset value across the entire trade. These capture the
    # 2-for-1 discount and best-player premium implicitly.
    effective_in: float = 0.0
    effective_out: float = 0.0

    def value_out_now(self) -> int:
        return sum(int(a.value_now or 0) for a in self.given)

    def value_in_now(self) -> int:
        return sum(int(a.value_now or 0) for a in self.received)

    def net_now(self) -> int:
        """Raw net (sum of received minus sum of given)."""
        return self.value_in_now() - self.value_out_now()

    @property
    def effective_net(self) -> float:
        """Adjusted net (2-for-1 discount baked in)."""
        return self.effective_in - self.effective_out


@dataclass
class TradeGrade:
    transaction_id: str
    season: str
    trade_date: datetime | None
    sides: list[Side]
    is_faab_only: bool                # FAAB-only "trades" — flagged, not graded
    faab_movements: list[dict] = field(default_factory=list)
    n_assets_unranked: int = 0        # assets whose sleeper_id we couldn't price
    top_value_in_trade: float = 0.0   # top asset value across both sides

    @property
    def winner(self) -> Optional[Side]:
        if self.is_faab_only or not self.sides:
            return None
        return max(self.sides, key=lambda s: s.effective_net)

    @property
    def margin_now(self) -> float:
        """Effective margin between best and 2nd-best side, in adjusted units."""
        if self.is_faab_only or len(self.sides) < 2:
            return 0.0
        nets = sorted([s.effective_net for s in self.sides], reverse=True)
        return nets[0] - nets[1]

    @property
    def raw_margin_now(self) -> int:
        """Raw-sum margin (kept available for transparency)."""
        if self.is_faab_only or len(self.sides) < 2:
            return 0
        nets = sorted([s.net_now() for s in self.sides], reverse=True)
        return nets[0] - nets[1]


@dataclass
class SeasonRecap:
    season: str
    league_name: str
    snapshot_date: date
    snapshot_format_label: str
    trades: list[TradeGrade]
