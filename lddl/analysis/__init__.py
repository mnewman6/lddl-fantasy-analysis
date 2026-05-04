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

    def value_out_now(self) -> int:
        return sum(int(a.value_now or 0) for a in self.given)

    def value_in_now(self) -> int:
        return sum(int(a.value_now or 0) for a in self.received)

    def net_now(self) -> int:
        return self.value_in_now() - self.value_out_now()


@dataclass
class TradeGrade:
    transaction_id: str
    season: str
    trade_date: datetime | None
    sides: list[Side]
    is_faab_only: bool                # FAAB-only "trades" — flagged, not graded
    faab_movements: list[dict] = field(default_factory=list)
    n_assets_unranked: int = 0        # assets whose sleeper_id we couldn't price

    @property
    def winner(self) -> Optional[Side]:
        if self.is_faab_only or not self.sides:
            return None
        return max(self.sides, key=lambda s: s.net_now())

    @property
    def margin_now(self) -> int:
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
