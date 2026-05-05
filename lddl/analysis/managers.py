"""Manager-history aggregation.

Builds one ``ManagerCard`` per user_id, collapsing every season they
participated in along with trade activity and (current-snapshot) draft
performance.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field

import duckdb

from lddl.analysis.franchises import canonical_user_id, is_predecessor
from lddl.analysis.standings import SeasonRow, season_rows
from lddl.analysis.snapshots import DEFAULT_SOURCE, Source
from lddl.analysis.trades import grade_trades_for_season


@dataclass
class TradeRecord:
    transaction_id: str
    season: str
    trade_date: object  # datetime
    net_value: int
    won: bool
    n_other_parties: int


@dataclass
class ManagerSeasonAgg:
    season: str
    league_status: str
    roster_id: int
    display_name: str
    team_name: str | None
    wins: int
    losses: int
    ties: int
    fpts: float
    fpts_against: float
    ppts: float
    expected_wins: float
    final_placement: int | None
    is_champion: bool
    is_last_place: bool
    playoff_wins: int
    playoff_losses: int
    n_trades: int
    trade_net_value: int


@dataclass
class ManagerCard:
    user_id: str
    display_name: str
    aliases: list[str]
    team_names: list[str]
    first_seen_season: str
    last_seen_season: str
    seasons: list[ManagerSeasonAgg] = field(default_factory=list)
    trades: list[TradeRecord] = field(default_factory=list)

    # ---- aggregate getters ------------------------------------------------

    @property
    def total_wins(self) -> int:
        return sum(s.wins for s in self.seasons)

    @property
    def total_losses(self) -> int:
        return sum(s.losses for s in self.seasons)

    @property
    def total_ties(self) -> int:
        return sum(s.ties for s in self.seasons)

    @property
    def total_fpts(self) -> float:
        return sum(s.fpts for s in self.seasons)

    @property
    def total_fpts_against(self) -> float:
        return sum(s.fpts_against for s in self.seasons)

    @property
    def total_expected_wins(self) -> float:
        return sum(s.expected_wins for s in self.seasons)

    @property
    def luck(self) -> float:
        return self.total_wins - self.total_expected_wins

    @property
    def championships(self) -> int:
        return sum(1 for s in self.seasons if s.is_champion)

    @property
    def last_places(self) -> int:
        return sum(1 for s in self.seasons if s.is_last_place)

    @property
    def total_playoff_wins(self) -> int:
        return sum(s.playoff_wins for s in self.seasons)

    @property
    def total_playoff_losses(self) -> int:
        return sum(s.playoff_losses for s in self.seasons)

    @property
    def n_trades(self) -> int:
        return len(self.trades)

    @property
    def trade_win_rate(self) -> float:
        if not self.trades:
            return 0.0
        return sum(1 for t in self.trades if t.won) / len(self.trades)

    @property
    def total_trade_value(self) -> int:
        return sum(t.net_value for t in self.trades)


def build_manager_cards(
    conn: duckdb.DuckDBPyConnection,
    source: Source = DEFAULT_SOURCE,
) -> list[ManagerCard]:
    # 1. Pull every manager-identity row, then merge predecessor cards into
    #    their franchise's primary card per lddl/analysis/franchises.py.
    raw_cards: dict[str, ManagerCard] = {}
    for r in conn.execute(
        """
        SELECT user_id, display_name, aliases, team_names,
               first_seen_season, last_seen_season
        FROM managers ORDER BY display_name
        """
    ).fetchall():
        uid, dn, aliases_j, teams_j, first_s, last_s = r
        raw_cards[uid] = ManagerCard(
            user_id=uid,
            display_name=dn or uid,
            aliases=json.loads(aliases_j) if aliases_j else [],
            team_names=json.loads(teams_j) if teams_j else [],
            first_seen_season=first_s,
            last_seen_season=last_s,
        )

    cards_by_uid: dict[str, ManagerCard] = {
        uid: card for uid, card in raw_cards.items()
        if not is_predecessor(uid)
    }
    for pred_uid, pred_card in raw_cards.items():
        canonical = canonical_user_id(pred_uid)
        if canonical == pred_uid:
            continue
        target = cards_by_uid.get(canonical)
        if target is None:
            target = ManagerCard(
                user_id=canonical,
                display_name=pred_card.display_name,
                aliases=[],
                team_names=[],
                first_seen_season=pred_card.first_seen_season,
                last_seen_season=pred_card.last_seen_season,
            )
            cards_by_uid[canonical] = target
        merged_aliases = set(target.aliases) | set(pred_card.aliases) | {pred_card.display_name}
        target.aliases = sorted(merged_aliases)
        target.team_names = sorted(set(target.team_names) | set(pred_card.team_names))
        if pred_card.first_seen_season < target.first_seen_season:
            target.first_seen_season = pred_card.first_seen_season
        if pred_card.last_seen_season > target.last_seen_season:
            target.last_seen_season = pred_card.last_seen_season

    # 2. Per-season standings → aggregate by canonical user_id
    rows_by_uid: dict[str, list[SeasonRow]] = defaultdict(list)
    for sr in season_rows(conn):
        canonical = canonical_user_id(sr.user_id)
        if canonical and canonical in cards_by_uid:
            rows_by_uid[canonical].append(sr)

    # 3. Per-season trade activity (recap each season once)
    trade_counts: dict[tuple[str, str], int] = defaultdict(int)
    trade_net: dict[tuple[str, str], int] = defaultdict(int)
    seasons = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT season FROM leagues ORDER BY season"
        ).fetchall()
    ]
    for season in seasons:
        recap = grade_trades_for_season(conn, season, source=source)
        for trade in recap.trades:
            if trade.is_faab_only or len(trade.sides) < 2:
                continue
            best_net = max(s.net_now() for s in trade.sides)
            for side in trade.sides:
                canonical = canonical_user_id(side.user_id)
                if not canonical or canonical not in cards_by_uid:
                    continue
                trade_counts[(canonical, season)] += 1
                trade_net[(canonical, season)] += side.net_now()
                cards_by_uid[canonical].trades.append(
                    TradeRecord(
                        transaction_id=trade.transaction_id,
                        season=trade.season,
                        trade_date=trade.trade_date,
                        net_value=side.net_now(),
                        won=(side.net_now() == best_net and best_net > 0),
                        n_other_parties=len(trade.sides) - 1,
                    )
                )

    # 4. Stitch season aggregates into the cards
    for uid, sr_list in rows_by_uid.items():
        sr_list.sort(key=lambda x: x.season)
        for sr in sr_list:
            cards_by_uid[uid].seasons.append(
                ManagerSeasonAgg(
                    season=sr.season,
                    league_status=sr.league_status,
                    roster_id=sr.roster_id,
                    display_name=sr.display_name,
                    team_name=sr.team_name,
                    wins=sr.wins,
                    losses=sr.losses,
                    ties=sr.ties,
                    fpts=sr.fpts,
                    fpts_against=sr.fpts_against,
                    ppts=sr.ppts,
                    expected_wins=sr.expected_wins,
                    final_placement=sr.final_placement,
                    is_champion=sr.is_champion,
                    is_last_place=sr.is_last_place,
                    playoff_wins=sr.playoff_wins,
                    playoff_losses=sr.playoff_losses,
                    n_trades=trade_counts.get((uid, sr.season), 0),
                    trade_net_value=trade_net.get((uid, sr.season), 0),
                )
            )

    # Drop cards with no season data (shouldn't happen, but be safe)
    cards = [c for c in cards_by_uid.values() if c.seasons]
    cards.sort(
        key=lambda c: (-c.championships, -c.total_wins, -c.total_fpts)
    )
    return cards
