"""Matplotlib chart factories for the trade recap report.

Muted palette, dark-mode friendly, with source attribution in the footer.
Each factory returns the path of the saved PNG so the same image can be
embedded in the PDF *and* shared standalone in iMessage.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from lddl.analysis import TradeGrade  # noqa: E402

# Muted palette — readable in both light and dark group chats.
PALETTE = [
    "#4f7cac",  # muted blue
    "#c0524a",  # muted brick
    "#6f9b6c",  # sage
    "#9a7ba1",  # dusty plum
    "#caa770",  # khaki
    "#5e8b87",  # teal
]
NEUTRAL = "#444"
GRID = "#dddddd"


def _attribution(snapshot_date: date) -> str:
    return f"Data: Sleeper + FantasyCalc · Snapshot {snapshot_date.isoformat()}"


def trade_chart(
    trade: TradeGrade,
    output_path: Path,
    *,
    snapshot_date: date,
) -> Path:
    """Per-trade horizontal bar chart of each side's net delta at current snapshot."""
    if trade.is_faab_only:
        return _faab_only_chart(trade, output_path, snapshot_date=snapshot_date)

    sides = trade.sides
    labels = [f"r{s.roster_id} · {s.display_name}" for s in sides]
    nets = [s.net_now() for s in sides]

    fig_height = max(2.4, 0.6 * len(sides) + 1.4)
    fig, ax = plt.subplots(figsize=(8, fig_height), dpi=150)

    colors = [PALETTE[i % len(PALETTE)] for i in range(len(sides))]
    ax.barh(labels, nets, color=colors, edgecolor="none", height=0.55)
    ax.axvline(0, color=NEUTRAL, linewidth=0.8)

    max_abs = max((abs(n) for n in nets), default=1) or 1
    pad = max_abs * 0.04
    for i, net in enumerate(nets):
        ha = "left" if net >= 0 else "right"
        x = net + (pad if net >= 0 else -pad)
        ax.text(x, i, f"{net:+,}", va="center", ha=ha, fontsize=10, color=NEUTRAL)

    ax.set_xlabel("Net value delta · FC dynasty units")
    title = f"Trade · {trade.trade_date.strftime('%Y-%m-%d') if trade.trade_date else trade.season}"
    ax.set_title(title, fontsize=11, color=NEUTRAL)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(GRID)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=NEUTRAL)
    ax.set_xlim(min(0, min(nets)) - max_abs * 0.18, max(0, max(nets)) + max_abs * 0.18)

    fig.text(
        0.99, 0.01, _attribution(snapshot_date),
        ha="right", fontsize=7, color="#888",
    )
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return output_path


def wins_vs_pf_scatter(cards, output_path: Path, *, snapshot_date: date) -> Path:
    """League-wide scatter: regular-season wins vs all-time PF, one dot per manager."""
    fig, ax = plt.subplots(figsize=(8, 5.5), dpi=150)
    xs = [c.total_fpts for c in cards]
    ys = [c.total_wins for c in cards]
    sizes = [60 + 28 * c.championships for c in cards]
    colors = []
    for c in cards:
        if c.championships > 0:
            colors.append(PALETTE[0])
        elif c.last_places > 0:
            colors.append(PALETTE[1])
        else:
            colors.append("#888")
    ax.scatter(xs, ys, s=sizes, c=colors, edgecolor="white", linewidth=1.0, alpha=0.85)
    for c in cards:
        ax.annotate(
            c.display_name,
            (c.total_fpts, c.total_wins),
            xytext=(5, 5), textcoords="offset points",
            fontsize=8, color=NEUTRAL,
        )

    if xs and ys:
        ax.axhline(sum(ys) / len(ys), color=GRID, linewidth=0.6, linestyle="--")
        ax.axvline(sum(xs) / len(xs), color=GRID, linewidth=0.6, linestyle="--")

    ax.set_xlabel("All-time points for")
    ax.set_ylabel("Regular-season wins")
    ax.set_title("Wins vs PF — circle size = championships", fontsize=11, color=NEUTRAL)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(GRID)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=NEUTRAL)
    fig.text(
        0.99, 0.01, _attribution(snapshot_date),
        ha="right", fontsize=7, color="#888",
    )
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return output_path


def manager_seasons_chart(card, output_path: Path, *, snapshot_date: date) -> Path:
    """Per-manager paired bars: PF vs PA across seasons."""
    seasons = [s.season for s in card.seasons]
    pf = [s.fpts for s in card.seasons]
    pa = [s.fpts_against for s in card.seasons]
    if not seasons:
        # Empty placeholder
        fig, ax = plt.subplots(figsize=(7, 2), dpi=150)
        ax.axis("off")
        ax.text(0.5, 0.5, "No season data", ha="center", va="center",
                fontsize=10, color=NEUTRAL)
        fig.savefig(output_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return output_path

    fig, ax = plt.subplots(figsize=(7, 2.6), dpi=150)
    import numpy as np  # local; numpy already pulled in by pandas
    x = np.arange(len(seasons))
    w = 0.38
    ax.bar(x - w / 2, pf, w, label="PF", color=PALETTE[0], edgecolor="none")
    ax.bar(x + w / 2, pa, w, label="PA", color=PALETTE[1], edgecolor="none")
    ax.set_xticks(x)
    ax.set_xticklabels(seasons)
    ax.set_ylabel("Points")
    for i, s in enumerate(card.seasons):
        if s.is_champion:
            ax.annotate("★", (i - w / 2, pf[i]), xytext=(0, 4),
                        textcoords="offset points", ha="center", fontsize=12)
        if s.is_last_place:
            ax.annotate("·", (i + w / 2, pa[i]), xytext=(0, 4),
                        textcoords="offset points", ha="center", fontsize=14, color="#888")
    ax.legend(loc="upper left", fontsize=8, frameon=False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(GRID)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=NEUTRAL)
    fig.text(
        0.99, 0.005, _attribution(snapshot_date),
        ha="right", fontsize=6, color="#888",
    )
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return output_path


def _faab_only_chart(
    trade: TradeGrade, output_path: Path, *, snapshot_date: date
) -> Path:
    fig, ax = plt.subplots(figsize=(8, 2.0), dpi=150)
    ax.axis("off")
    text = "FAAB-only swap — not graded.\n\n"
    for m in trade.faab_movements:
        text += f"r{m.get('sender')} → r{m.get('receiver')} · ${m.get('amount')}\n"
    ax.text(0.5, 0.5, text.strip(), ha="center", va="center", fontsize=11, color=NEUTRAL)
    fig.text(
        0.99, 0.04, _attribution(snapshot_date),
        ha="right", fontsize=7, color="#888",
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return output_path
