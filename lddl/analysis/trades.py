"""Trade enumeration + grading."""

from __future__ import annotations

import json

import duckdb

from lddl.analysis import AssetValue, SeasonRecap, Side, TradeGrade
from lddl.analysis.draft_slots import SlotResolver
from lddl.analysis.picks import pick_fc_name, pick_label
from lddl.analysis.snapshots import (
    DEFAULT_SOURCE,
    Source,
    SnapshotRef,
    latest_snapshot,
    value_by_name,
    value_by_sleeper_id,
)
from lddl.analysis.trade_value import (
    FC_MAX_VALUE,
    KTC_MAX_VALUE,
    effective_value,
)


def _max_value_for_source(source: Source) -> int:
    return FC_MAX_VALUE if source == "fc" else KTC_MAX_VALUE


def _resolve_manager(
    conn: duckdb.DuckDBPyConnection, league_id: str, roster_id: int
) -> tuple[str | None, str, str | None]:
    row = conn.execute(
        """
        SELECT lu.user_id, lu.display_name, lu.team_name
        FROM rosters r
        LEFT JOIN league_users lu
            ON lu.league_id = r.league_id AND lu.user_id = r.owner_id
        WHERE r.league_id = ? AND r.roster_id = ?
        """,
        [league_id, roster_id],
    ).fetchone()
    if not row:
        return None, f"roster {roster_id}", None
    return row[0], row[1] or f"roster {roster_id}", row[2]


def _player_label(
    conn: duckdb.DuckDBPyConnection, player_id: str
) -> str:
    row = conn.execute(
        "SELECT full_name, position, team FROM players WHERE player_id = ?",
        [player_id],
    ).fetchone()
    if not row or not row[0]:
        return f"player_id={player_id}"
    pos = row[1] or "?"
    team = row[2] or "FA"
    return f"{row[0]} ({pos} {team})"


def grade_trades_for_season(
    conn: duckdb.DuckDBPyConnection,
    season: str,
    source: Source = DEFAULT_SOURCE,
) -> SeasonRecap:
    snap = latest_snapshot(conn, source=source)
    if snap is None:
        raise RuntimeError(
            f"No {source.upper()} snapshots in DB. Run "
            f"`lddl {'ktc-snapshot' if source == 'ktc' else 'snapshot'}` first."
        )

    league = conn.execute(
        "SELECT league_id, name FROM leagues WHERE season = ? LIMIT 1",
        [season],
    ).fetchone()
    if not league:
        raise RuntimeError(f"No league for season={season}. Run `lddl ingest` first.")
    league_id, league_name = league

    fmt_label = (
        f"{'Superflex' if snap.format_num_qbs == 2 else f'{snap.format_num_qbs}QB'}, "
        f"{snap.format_ppr} PPR, {snap.format_num_teams}-team "
        f"{'dynasty' if snap.format_is_dynasty else 'redraft'}"
    )

    trades = conn.execute(
        """
        SELECT transaction_id, status_updated_at, created_at, roster_ids,
               waiver_budget
        FROM transactions
        WHERE league_id = ? AND type = 'trade' AND status = 'complete'
        ORDER BY status_updated_at NULLS LAST, created_at NULLS LAST
        """,
        [league_id],
    ).fetchall()

    slot_resolver = SlotResolver(conn)
    graded: list[TradeGrade] = []
    for tx_id, status_ts, created_ts, roster_ids_json, waiver_budget_json in trades:
        roster_ids = json.loads(roster_ids_json) if roster_ids_json else []
        wb = json.loads(waiver_budget_json) if waiver_budget_json else []
        trade_date = status_ts or created_ts
        graded.append(
            _grade_one_trade(
                conn,
                snap,
                slot_resolver,
                league_id,
                season,
                tx_id,
                trade_date,
                roster_ids,
                wb,
            )
        )

    return SeasonRecap(
        season=season,
        league_name=league_name,
        snapshot_date=snap.snapshot_date,
        snapshot_format_label=fmt_label,
        trades=graded,
    )


def _grade_one_trade(
    conn: duckdb.DuckDBPyConnection,
    snap: SnapshotRef,
    slot_resolver: SlotResolver,
    league_id: str,
    season: str,
    tx_id: str,
    trade_date,
    roster_ids: list[int],
    waiver_budget: list[dict],
) -> TradeGrade:
    sides_by_roster: dict[int, Side] = {}
    for rid in roster_ids:
        user_id, display_name, team_name = _resolve_manager(conn, league_id, rid)
        sides_by_roster[rid] = Side(
            roster_id=rid,
            user_id=user_id,
            display_name=display_name,
            team_name=team_name,
        )

    n_unranked = 0
    is_pre = (
        snap.snapshot_date
        and trade_date
        and snap.snapshot_date.isoformat() > str(trade_date)[:10]
    )

    # Player movements
    for movement, roster_id, player_id in conn.execute(
        """
        SELECT movement, roster_id, player_id
        FROM transaction_players WHERE transaction_id = ?
        """,
        [tx_id],
    ).fetchall():
        if roster_id not in sides_by_roster:
            continue
        label = _player_label(conn, player_id)
        value, fc_name = value_by_sleeper_id(conn, snap, str(player_id))
        if value is None:
            n_unranked += 1
        asset = AssetValue(
            label=label,
            asset_type="player",
            sleeper_id=str(player_id),
            value_now=value,
            snapshot_date_now=snap.snapshot_date if value is not None else None,
            is_pre_snapshot_trade=bool(is_pre),
        )
        if movement == "drop":
            sides_by_roster[roster_id].given.append(asset)
        else:
            sides_by_roster[roster_id].received.append(asset)

    # Pick movements (each row = one pick swap event in this trade)
    for pick_season, round_, orig_roster, owner, prev_owner in conn.execute(
        """
        SELECT season, round, roster_id, owner_id, previous_owner_id
        FROM transaction_picks WHERE transaction_id = ?
        """,
        [tx_id],
    ).fetchall():
        slot = slot_resolver.slot_for(str(pick_season), round_, orig_roster)
        fc_name = pick_fc_name(str(pick_season), round_, slot)
        value = value_by_name(conn, snap, fc_name)
        # If slot-specific lookup misses, fall back to the round bucket so a
        # known-slot pick still gets *some* value rather than None.
        if value is None and slot is not None:
            fallback_name = pick_fc_name(str(pick_season), round_)
            value = value_by_name(conn, snap, fallback_name)
        if value is None:
            n_unranked += 1
        label = pick_label(str(pick_season), round_, orig_roster, slot)
        asset = AssetValue(
            label=label,
            asset_type="pick",
            sleeper_id=None,
            value_now=value,
            snapshot_date_now=snap.snapshot_date if value is not None else None,
            is_pre_snapshot_trade=bool(is_pre),
        )
        if owner in sides_by_roster:
            sides_by_roster[owner].received.append(asset)
        if prev_owner in sides_by_roster:
            sides_by_roster[prev_owner].given.append(asset)

    sides = [sides_by_roster[rid] for rid in roster_ids if rid in sides_by_roster]

    # Detect FAAB-only "trades": no players, no picks, only waiver_budget rows.
    has_assets = any(s.given or s.received for s in sides)
    is_faab_only = (not has_assets) and bool(waiver_budget)

    # KTC raw-adjusted values: top value is across BOTH sides of the trade,
    # so multi-asset sides get penalized correctly relative to a stud.
    top_value = 0.0
    for s in sides:
        for a in s.given + s.received:
            v = a.value_now or 0
            if v > top_value:
                top_value = float(v)
    max_v = _max_value_for_source(snap.source)
    if top_value > 0:
        for s in sides:
            s.effective_in = sum(
                effective_value(a.value_now or 0, top_value, max_v)
                for a in s.received
            )
            s.effective_out = sum(
                effective_value(a.value_now or 0, top_value, max_v)
                for a in s.given
            )

    return TradeGrade(
        transaction_id=tx_id,
        season=season,
        trade_date=trade_date,
        sides=sides,
        is_faab_only=is_faab_only,
        faab_movements=waiver_budget if is_faab_only else [],
        n_assets_unranked=n_unranked,
        top_value_in_trade=top_value,
    )
