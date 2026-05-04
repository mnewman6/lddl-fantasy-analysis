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
