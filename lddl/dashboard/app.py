"""Local Streamlit dashboard for the LDDL warehouse.

Run with `uv run lddl dashboard` (preferred) or
`uv run streamlit run lddl/dashboard/app.py`.

Single-file by intent — every tab uses the cached helpers at the top so the
DuckDB connection and analysis aggregations are shared across reruns.
"""

from __future__ import annotations

import json
import os

import altair as alt
import duckdb
import pandas as pd
import streamlit as st

st.set_page_config(page_title="LDDL Fantasy Analysis", layout="wide")

# Bridge Streamlit Cloud secrets → env vars so pydantic-settings picks them up.
# Locally with .env, this is a no-op (st.secrets is empty / missing).
try:
    secrets = dict(st.secrets) if hasattr(st, "secrets") else {}
except Exception:
    secrets = {}
for key in ("SLEEPER_LEAGUE_ID", "LEAGUE_NAME"):
    if key in secrets and not os.environ.get(key):
        os.environ[key] = str(secrets[key])

from lddl.analysis.drafts import aggregate_by_manager, per_pick_grades  # noqa: E402
from lddl.analysis.franchises import canonical_user_id  # noqa: E402
from lddl.analysis.managers import build_manager_cards  # noqa: E402
from lddl.analysis.snapshots import latest_snapshot  # noqa: E402
from lddl.analysis.trades import grade_trades_for_season  # noqa: E402
from lddl.config import get_settings  # noqa: E402

# ---------- Cached data accessors -----------------------------------------

settings = get_settings()


@st.cache_resource
def get_conn() -> duckdb.DuckDBPyConnection:
    if not settings.duckdb_path.exists():
        st.error(
            f"No DuckDB at {settings.duckdb_path}. "
            "Run `uv run lddl ingest` first."
        )
        st.stop()
    return duckdb.connect(str(settings.duckdb_path), read_only=True)


@st.cache_data
def cached_seasons() -> list[str]:
    conn = get_conn()
    return [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT season FROM leagues ORDER BY season"
        ).fetchall()
    ]


@st.cache_data
def cached_league_meta() -> dict:
    conn = get_conn()
    row = conn.execute(
        """
        SELECT name, season, status FROM leagues
        ORDER BY season DESC LIMIT 1
        """
    ).fetchone()
    return {"name": row[0], "season": row[1], "status": row[2]} if row else {}


@st.cache_data
def cached_snapshot_summary() -> dict:
    conn = get_conn()
    row = conn.execute(
        """
        SELECT MAX(snapshot_date), COUNT(DISTINCT snapshot_date),
               COUNT(*) FILTER (WHERE position != 'PICK'),
               COUNT(*) FILTER (WHERE position = 'PICK')
        FROM fc_snapshots
        WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM fc_snapshots)
        """
    ).fetchone()
    if not row or row[0] is None:
        return {}
    return {
        "latest_date": row[0],
        "n_dates": row[1],
        "n_players_in_latest": row[2],
        "n_picks_in_latest": row[3],
    }


@st.cache_data
def cached_manager_cards():
    conn = get_conn()
    return build_manager_cards(conn)


@st.cache_data
def cached_trade_recap(season: str):
    conn = get_conn()
    return grade_trades_for_season(conn, season)


@st.cache_data
def cached_all_trades_df() -> pd.DataFrame:
    """Flatten every season's trades into one dataframe (one row per side per trade)."""
    cards = cached_manager_cards()
    uid_to_franchise = {c.user_id: c.display_name for c in cards}
    rows = []
    for season in cached_seasons():
        recap = cached_trade_recap(season)
        for trade in recap.trades:
            for side in trade.sides:
                canonical_uid = canonical_user_id(side.user_id)
                franchise_name = uid_to_franchise.get(canonical_uid, side.display_name)
                rows.append({
                    "season": season,
                    "trade_date": trade.trade_date,
                    "transaction_id": trade.transaction_id,
                    "is_faab_only": trade.is_faab_only,
                    "n_parties": len(trade.sides),
                    # `manager`: who actually traded at the time (historical).
                    "manager": side.display_name,
                    # `franchise`: canonical name across successions.
                    "franchise": franchise_name,
                    "user_id": canonical_uid,
                    "roster_id": side.roster_id,
                    "value_in": side.value_in_now(),
                    "value_out": side.value_out_now(),
                    "net": side.net_now(),
                    "n_given": len(side.given),
                    "n_received": len(side.received),
                })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


@st.cache_data
def cached_pick_grades_df() -> pd.DataFrame:
    conn = get_conn()
    snap = latest_snapshot(conn)
    if snap is None:
        return pd.DataFrame()
    grades = per_pick_grades(conn, snap)
    cards = build_manager_cards(conn)
    uid_to_franchise_name = {c.user_id: c.display_name for c in cards}
    rows = []
    for g in grades:
        canonical_uid = canonical_user_id(g.picked_by_user_id)
        franchise_name = uid_to_franchise_name.get(
            canonical_uid, g.picked_by_display_name
        )
        rows.append({
            "season": g.season,
            "round": g.round,
            "slot": g.draft_slot,
            "pick_no": (g.round - 1) * 12 + g.draft_slot,
            # `manager`: who actually picked at the time (historical fidelity).
            "manager": g.picked_by_display_name,
            # `franchise`: canonical franchise name for cross-season aggregation.
            "franchise": franchise_name,
            "user_id": canonical_uid,
            "player": g.player_name or "(unknown)",
            "actual": g.actual_value,
            "expected": round(g.expected_value, 1),
            "delta": round(g.delta, 1),
        })
    return pd.DataFrame(rows)


@st.cache_data
def cached_top_assets_df(top_n: int = 50) -> pd.DataFrame:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT name, position, team, age, value, overall_rank, position_rank,
               trend_30_day, tier, snapshot_date
        FROM fc_snapshots
        WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM fc_snapshots)
        ORDER BY value DESC
        LIMIT ?
        """,
        [top_n],
    ).fetchall()
    return pd.DataFrame(
        rows,
        columns=[
            "name", "position", "team", "age", "value", "overall_rank",
            "position_rank", "trend_30d", "tier", "snapshot_date",
        ],
    )


# ---------- Sidebar ---------------------------------------------------------

meta = cached_league_meta()
snap = cached_snapshot_summary()
with st.sidebar:
    st.markdown("### LDDL")
    if meta:
        st.write(
            f"**{meta.get('name', 'LDDL')}** · current season {meta.get('season')} "
            f"({meta.get('status')})"
        )
    if snap:
        st.write(
            f"**Snapshot:** {snap['latest_date']}  \n"
            f"{snap['n_dates']} day(s) of FC history · "
            f"{snap['n_players_in_latest']} players, {snap['n_picks_in_latest']} picks"
        )
    else:
        st.warning("No FC snapshots yet. Run `lddl snapshot`.")
    st.caption(
        "FantasyCalc values are crowdsourced approximations. "
        "Trade and draft grades are *directional*, not authoritative."
    )

# ---------- Tabs ------------------------------------------------------------

overview, managers, trades, drafts, snapshots = st.tabs(
    ["Overview", "Managers", "Trades", "Drafts", "Snapshots"]
)

# ---------- Overview --------------------------------------------------------

with overview:
    cards = cached_manager_cards()
    seasons = cached_seasons()
    trades_df = cached_all_trades_df()

    n_trades = (
        trades_df[trades_df["is_faab_only"] == False].drop_duplicates("transaction_id").shape[0]
        if not trades_df.empty else 0
    )

    cols = st.columns(4)
    cols[0].metric("Seasons", len(seasons))
    cols[1].metric("Active managers (all-time)", len(cards))
    cols[2].metric("Trades graded", n_trades)
    cols[3].metric(
        "Latest snapshot",
        snap["latest_date"].isoformat() if snap else "none",
    )

    col_l, col_r = st.columns(2)
    with col_l:
        st.subheader("Champions")
        champ_rows = []
        for c in cards:
            for s in c.seasons:
                if s.is_champion:
                    champ_rows.append((s.season, c.display_name, s.team_name or ""))
        for season, name, team in sorted(champ_rows):
            suffix = f" · &ldquo;{team}&rdquo;" if team else ""
            st.markdown(f"**{season}** — {name}{suffix}", unsafe_allow_html=True)
    with col_r:
        st.subheader("Last place")
        last_rows = []
        for c in cards:
            for s in c.seasons:
                if s.is_last_place:
                    last_rows.append((s.season, c.display_name))
        for season, name in sorted(last_rows):
            st.markdown(f"**{season}** — {name}")

    st.subheader("All-time wins vs points-for")
    df = pd.DataFrame([
        {
            "manager": c.display_name,
            "wins": c.total_wins,
            "losses": c.total_losses,
            "pf": round(c.total_fpts, 1),
            "championships": c.championships,
            "last_places": c.last_places,
            "luck": round(c.luck, 1),
            "trades": c.n_trades,
        }
        for c in cards
    ])
    if not df.empty:
        df["color"] = df.apply(
            lambda r: "champion" if r["championships"] > 0
            else ("last_place" if r["last_places"] > 0 else "neutral"),
            axis=1,
        )
        circle = alt.Chart(df).mark_circle().encode(
            x=alt.X("pf:Q", title="All-time PF"),
            y=alt.Y("wins:Q", title="Regular-season wins"),
            size=alt.Size(
                "championships:Q", title="Championships",
                scale=alt.Scale(range=[80, 400]),
            ),
            color=alt.Color(
                "color:N",
                scale=alt.Scale(
                    domain=["champion", "last_place", "neutral"],
                    range=["#4f7cac", "#c0524a", "#888888"],
                ),
                legend=None,
            ),
            tooltip=[
                "manager", "wins", "losses", "pf", "championships",
                "last_places", "luck", "trades",
            ],
        )
        labels = alt.Chart(df).mark_text(dx=8, dy=-6, fontSize=10).encode(
            x="pf:Q", y="wins:Q", text="manager",
        )
        st.altair_chart(
            (circle + labels).properties(height=420),
            use_container_width=True,
        )

    if not trades_df.empty:
        st.subheader("Recent trades")
        recent = (
            trades_df[trades_df["is_faab_only"] == False]
            .drop_duplicates("transaction_id")
            .sort_values("trade_date", ascending=False)
            .head(8)[["trade_date", "season", "n_parties", "transaction_id"]]
        )
        st.dataframe(recent, hide_index=True, use_container_width=True)

# ---------- Managers --------------------------------------------------------

with managers:
    cards = cached_manager_cards()
    name_to_card = {c.display_name: c for c in cards}
    sel = st.selectbox("Manager", list(name_to_card.keys()))
    c = name_to_card[sel]

    a, b = st.columns([3, 2])
    with a:
        aliases = ", ".join(x for x in c.aliases if x != c.display_name) or "—"
        teams = ", ".join(c.team_names) or "—"
        st.markdown(
            f"**{c.display_name}**  \n"
            f"Aliases: {aliases}  \nTeam names: {teams}  \n"
            f"Active {c.first_seen_season}–{c.last_seen_season}"
        )
    with b:
        st.metric("Championships", c.championships)
        st.metric("Last-place finishes", c.last_places)

    cols = st.columns(6)
    cols[0].metric("Reg W-L-T", f"{c.total_wins}-{c.total_losses}-{c.total_ties}")
    cols[1].metric("Playoff W-L", f"{c.total_playoff_wins}-{c.total_playoff_losses}")
    cols[2].metric("Luck", f"{c.luck:+.0f}")
    cols[3].metric("PF", f"{c.total_fpts:.0f}")
    cols[4].metric("PA", f"{c.total_fpts_against:.0f}")
    cols[5].metric("# Trades", c.n_trades)

    st.subheader("Per-season detail")
    df = pd.DataFrame([
        {
            "season": s.season,
            "status": s.league_status,
            "W-L-T": f"{s.wins}-{s.losses}-{s.ties}",
            "PF": round(s.fpts, 1),
            "PA": round(s.fpts_against, 1),
            "Exp W": round(s.expected_wins, 1),
            "Luck": round(s.wins - s.expected_wins, 1),
            "Playoff": f"{s.playoff_wins}-{s.playoff_losses}",
            "Champ": "★" if s.is_champion else "",
            "Last": "·" if s.is_last_place else "",
            "Trades": s.n_trades,
            "Trade Δ": s.trade_net_value,
        }
        for s in c.seasons
    ])
    st.dataframe(df, hide_index=True, use_container_width=True)

    st.subheader("PF vs PA across seasons")
    if c.seasons:
        bar_df = pd.DataFrame([
            {"season": s.season, "type": "PF", "value": s.fpts}
            for s in c.seasons
        ] + [
            {"season": s.season, "type": "PA", "value": s.fpts_against}
            for s in c.seasons
        ])
        bar = alt.Chart(bar_df).mark_bar().encode(
            x=alt.X("season:O"),
            xOffset="type:N",
            y="value:Q",
            color=alt.Color(
                "type:N",
                scale=alt.Scale(domain=["PF", "PA"], range=["#4f7cac", "#c0524a"]),
            ),
            tooltip=["season", "type", "value"],
        ).properties(height=300)
        st.altair_chart(bar, use_container_width=True)

    if c.trades:
        st.subheader("Top trades by absolute net value")
        trade_df = pd.DataFrame([
            {
                "date": (t.trade_date.date() if t.trade_date else None),
                "season": t.season,
                "net Δ": t.net_value,
                "won": t.won,
                "n_other_parties": t.n_other_parties,
                "transaction_id": t.transaction_id,
            }
            for t in sorted(c.trades, key=lambda x: -abs(x.net_value))
        ])
        st.dataframe(trade_df, hide_index=True, use_container_width=True)

# ---------- Trades ----------------------------------------------------------

with trades:
    seasons = cached_seasons()
    df = cached_all_trades_df()

    if df.empty:
        st.info("No trades graded yet.")
    else:
        f1, f2, f3 = st.columns([1, 1, 2])
        season_sel = f1.multiselect("Season", seasons, default=seasons)
        all_managers = sorted(df["manager"].dropna().unique().tolist())
        manager_sel = f2.multiselect("Manager involved", all_managers, default=[])
        min_abs = int(f3.slider(
            "Min |net Δ|", 0,
            int(max(1, df["net"].abs().max())),
            value=0,
        ))

        view = df[df["season"].isin(season_sel)]
        if manager_sel:
            tx_ids = view[view["manager"].isin(manager_sel)]["transaction_id"].unique()
            view = view[view["transaction_id"].isin(tx_ids)]
        if min_abs:
            view = view[view["net"].abs() >= min_abs]

        # Per-trade aggregation: keep one row per side, but link with detail.
        st.markdown(f"**{view['transaction_id'].nunique()} trades** — "
                    f"{len(view)} sides")

        st.subheader("Trades")
        st.dataframe(
            view[
                [
                    "trade_date", "season", "n_parties", "manager",
                    "value_in", "value_out", "net", "is_faab_only",
                    "transaction_id",
                ]
            ].sort_values("trade_date", ascending=False),
            hide_index=True,
            use_container_width=True,
        )

        st.subheader("Trade-value leaderboard (filtered)")
        graded = view[view["is_faab_only"] == False]
        if not graded.empty:
            wins_per_tx = (
                graded.groupby("transaction_id")["net"]
                .max().reset_index().rename(columns={"net": "best_net"})
            )
            graded = graded.merge(wins_per_tx, on="transaction_id", how="left")
            graded["won"] = (graded["net"] == graded["best_net"]) & (graded["best_net"] > 0)
            leaderboard = (
                graded.groupby("franchise")
                .agg(
                    n_trades=("transaction_id", "nunique"),
                    total_delta=("net", "sum"),
                    avg_delta=("net", "mean"),
                    win_rate=("won", "mean"),
                )
                .reset_index()
                .sort_values("total_delta", ascending=False)
            )
            leaderboard["total_delta"] = leaderboard["total_delta"].astype(int)
            leaderboard["avg_delta"] = leaderboard["avg_delta"].round(0).astype(int)
            leaderboard["win_rate"] = (leaderboard["win_rate"] * 100).round(0).astype(int)
            leaderboard.columns = [
                "Franchise", "# trades", "Total Δ", "Avg Δ", "Win %",
            ]

            bar = alt.Chart(leaderboard).mark_bar().encode(
                x=alt.X("Total Δ:Q", title="Total trade Δ at current FC values"),
                y=alt.Y("Franchise:N", sort="-x"),
                color=alt.condition(
                    "datum['Total Δ'] >= 0",
                    alt.value("#4f7cac"),
                    alt.value("#c0524a"),
                ),
                tooltip=list(leaderboard.columns),
            ).properties(height=24 * len(leaderboard) + 40)
            st.altair_chart(bar, use_container_width=True)
            st.dataframe(leaderboard, hide_index=True, use_container_width=True)

        st.subheader("Trade volume by season")
        per_season = (
            view[view["is_faab_only"] == False]
            .drop_duplicates("transaction_id")
            .groupby("season").size().reset_index(name="n")
        )
        if not per_season.empty:
            chart = alt.Chart(per_season).mark_bar(color="#4f7cac").encode(
                x="season:O", y="n:Q",
                tooltip=["season", "n"],
            ).properties(height=240)
            st.altair_chart(chart, use_container_width=True)

        st.subheader("Trade detail")
        tx_options = (
            view.sort_values("trade_date", ascending=False)["transaction_id"]
            .drop_duplicates().tolist()
        )
        if tx_options:
            tx_sel = st.selectbox(
                "Pick a transaction to expand", tx_options,
                format_func=lambda t: (
                    f"{view[view['transaction_id']==t]['trade_date'].iloc[0]:%Y-%m-%d}"
                    f" · {view[view['transaction_id']==t]['season'].iloc[0]}"
                    f" · "
                    + " ↔ ".join(view[view["transaction_id"] == t]["manager"].tolist())
                ),
            )
            recap = cached_trade_recap(view[view["transaction_id"] == tx_sel]["season"].iloc[0])
            tg = next((x for x in recap.trades if x.transaction_id == tx_sel), None)
            if tg:
                if tg.is_faab_only:
                    st.warning("FAAB-only swap — not graded.")
                    for m in tg.faab_movements:
                        st.write(
                            f"r{m.get('sender')} → r{m.get('receiver')}: "
                            f"${m.get('amount')}"
                        )
                else:
                    cols = st.columns(len(tg.sides))
                    for col, side in zip(cols, tg.sides):
                        col.markdown(
                            f"**{side.display_name}** (r{side.roster_id})  \n"
                            f"in {side.value_in_now():,} · "
                            f"out {side.value_out_now():,} · "
                            f"**net {side.net_now():+,}**"
                        )
                        rows = [
                            {"side": "GAVE", "asset": a.label,
                             "value": a.value_now or 0}
                            for a in side.given
                        ] + [
                            {"side": "GOT", "asset": a.label,
                             "value": a.value_now or 0}
                            for a in side.received
                        ]
                        col.dataframe(
                            pd.DataFrame(rows), hide_index=True,
                            use_container_width=True,
                        )

# ---------- Drafts ----------------------------------------------------------

with drafts:
    df = cached_pick_grades_df()
    if df.empty:
        st.info("No 3-round rookie draft data yet.")
    else:
        years = sorted(df["season"].unique())
        sel_year = st.selectbox("Season", years, index=len(years) - 1)
        view = df[df["season"] == sel_year].copy().sort_values("pick_no")

        st.subheader(f"{sel_year} draft board")
        st.dataframe(
            view[
                ["pick_no", "round", "slot", "manager", "player",
                 "actual", "expected", "delta"]
            ],
            hide_index=True,
            use_container_width=True,
        )

        st.subheader("Pick value vs expected (slot median)")
        scatter = alt.Chart(view).mark_circle(size=120).encode(
            x=alt.X("pick_no:Q", title="Pick number"),
            y=alt.Y("delta:Q", title="Δ vs slot median"),
            color=alt.condition(
                "datum.delta >= 0",
                alt.value("#4f7cac"),
                alt.value("#c0524a"),
            ),
            tooltip=["pick_no", "manager", "player", "actual",
                     "expected", "delta"],
        )
        rule = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(
            color="#888"
        ).encode(y="y:Q")
        st.altair_chart(
            (scatter + rule).properties(height=340),
            use_container_width=True,
        )

        c_l, c_r = st.columns(2)
        with c_l:
            st.subheader("Steals (top +Δ)")
            st.dataframe(
                view.nlargest(10, "delta")[
                    ["pick_no", "manager", "player", "actual", "expected", "delta"]
                ],
                hide_index=True, use_container_width=True,
            )
        with c_r:
            st.subheader("Reaches (top -Δ)")
            st.dataframe(
                view.nsmallest(10, "delta")[
                    ["pick_no", "manager", "player", "actual", "expected", "delta"]
                ],
                hide_index=True, use_container_width=True,
            )

        st.subheader("Cumulative draft grade per franchise (all seasons)")
        agg = df.groupby("franchise").agg(
            picks=("pick_no", "count"),
            avg_delta=("delta", "mean"),
            total_delta=("delta", "sum"),
        ).round(1).reset_index().sort_values("avg_delta", ascending=False)
        st.dataframe(agg, hide_index=True, use_container_width=True)

# ---------- Snapshots -------------------------------------------------------

with snapshots:
    df = cached_top_assets_df(top_n=100)
    if df.empty:
        st.info("No snapshots yet — run `lddl snapshot`.")
    else:
        st.subheader(f"Top assets — snapshot {df['snapshot_date'].iloc[0]}")
        position_filter = st.multiselect(
            "Position",
            sorted(df["position"].unique()),
            default=sorted(df["position"].unique()),
        )
        view = df[df["position"].isin(position_filter)]
        st.dataframe(
            view[["overall_rank", "name", "position", "team", "age",
                  "value", "trend_30d", "tier"]],
            hide_index=True, use_container_width=True,
        )

        st.subheader("Top movers (30-day trend)")
        movers = view.dropna(subset=["trend_30d"]).copy()
        if not movers.empty:
            movers = pd.concat([
                movers.nlargest(8, "trend_30d"),
                movers.nsmallest(8, "trend_30d"),
            ])
            chart = alt.Chart(movers).mark_bar().encode(
                x=alt.X("trend_30d:Q", title="30-day trend"),
                y=alt.Y("name:N", sort="-x"),
                color=alt.condition(
                    "datum.trend_30d >= 0",
                    alt.value("#4f7cac"),
                    alt.value("#c0524a"),
                ),
                tooltip=["name", "position", "team", "value", "trend_30d"],
            ).properties(height=400)
            st.altair_chart(chart, use_container_width=True)

        if snap and snap.get("n_dates", 0) < 2:
            st.info(
                "Value-over-time charts unlock once `lddl snapshot` has run "
                "for at least two distinct dates. Right now we only have "
                f"{snap['n_dates']} day of FC history — keep the daily cron "
                "running and this section fills in over time."
            )
