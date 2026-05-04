"""Trade recap PDF builder using reportlab Platypus."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from lddl.analysis import SeasonRecap, Side, TradeGrade
from lddl.reports.charts import trade_chart

PAGE_MARGIN = 0.7 * inch
INK = colors.HexColor("#222222")
MUTED = colors.HexColor("#666666")
ACCENT = colors.HexColor("#4f7cac")
DIVIDER = colors.HexColor("#dddddd")
GIVEN_BG = colors.HexColor("#fbf3f1")
RECEIVED_BG = colors.HexColor("#f0f5fb")


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "h1": ParagraphStyle(
            "H1", parent=base["Heading1"], fontSize=22, leading=26,
            textColor=INK, spaceAfter=4,
        ),
        "h2": ParagraphStyle(
            "H2", parent=base["Heading2"], fontSize=14, leading=18,
            textColor=INK, spaceAfter=4,
        ),
        "h3": ParagraphStyle(
            "H3", parent=base["Heading3"], fontSize=11, leading=14,
            textColor=ACCENT, spaceAfter=2,
        ),
        "body": ParagraphStyle(
            "Body", parent=base["BodyText"], fontSize=10, leading=13,
            textColor=INK,
        ),
        "muted": ParagraphStyle(
            "Muted", parent=base["BodyText"], fontSize=9, leading=12,
            textColor=MUTED,
        ),
        "caveat": ParagraphStyle(
            "Caveat", parent=base["BodyText"], fontSize=9, leading=12,
            textColor=MUTED, italic=True, leftIndent=8, borderPadding=4,
        ),
    }


def _draw_footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    page_w, _ = LETTER
    canvas.drawString(
        PAGE_MARGIN, 0.4 * inch,
        f"Data: Sleeper + FantasyCalc · Generated {datetime.now().strftime('%Y-%m-%d')}",
    )
    canvas.drawRightString(page_w - PAGE_MARGIN, 0.4 * inch, f"Page {doc.page}")
    canvas.restoreState()


def _summary_line(side: Side) -> str:
    return (
        f"<b>{side.display_name}</b>"
        + (f" · &ldquo;{side.team_name}&rdquo;" if side.team_name else "")
        + f" (roster {side.roster_id})"
    )


def _asset_rows(side: Side) -> list[list]:
    rows = [["", "Asset", "Value"]]
    for a in side.given:
        v = f"{a.value_now:,}" if a.value_now is not None else "—"
        rows.append(["GAVE", a.label, v])
    for a in side.received:
        v = f"{a.value_now:,}" if a.value_now is not None else "—"
        rows.append(["GOT", a.label, v])
    rows.append([
        "NET",
        f"in {side.value_in_now():,} · out {side.value_out_now():,}",
        f"{side.net_now():+,}",
    ])
    return rows


def _side_table(side: Side) -> Table:
    rows = _asset_rows(side)
    t = Table(rows, colWidths=[0.55 * inch, 3.0 * inch, 0.85 * inch])
    style = [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
        ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f0f0")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.3, DIVIDER),
        ("FONT", (0, 1), (-1, -2), "Helvetica", 9),
        ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 9),
        ("LINEABOVE", (0, -1), (-1, -1), 0.5, INK),
        ("ALIGN", (2, 0), (2, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for i, r in enumerate(rows[1:-1], start=1):
        if r[0] == "GAVE":
            style.append(("BACKGROUND", (0, i), (-1, i), GIVEN_BG))
        elif r[0] == "GOT":
            style.append(("BACKGROUND", (0, i), (-1, i), RECEIVED_BG))
    t.setStyle(TableStyle(style))
    return t


def _trade_section(
    trade: TradeGrade,
    index: int,
    total: int,
    chart_path: Path,
    styles: dict,
) -> list:
    flow: list = []
    date_str = (
        trade.trade_date.strftime("%Y-%m-%d")
        if trade.trade_date else "(no date)"
    )
    parties = " ↔ ".join(
        f"r{s.roster_id} {s.display_name}" for s in trade.sides
    )
    flow.append(Paragraph(
        f"Trade {index} of {total} · {date_str}", styles["h2"]
    ))
    flow.append(Paragraph(parties, styles["muted"]))
    flow.append(Spacer(1, 6))

    if trade.is_faab_only:
        flow.append(Paragraph(
            "<i>FAAB-only swap — not graded. The schema doesn't model "
            "waiver-budget transfers as trade assets.</i>",
            styles["caveat"],
        ))
        for m in trade.faab_movements:
            flow.append(Paragraph(
                f"r{m.get('sender')} sent r{m.get('receiver')} ${m.get('amount')}",
                styles["body"],
            ))
        flow.append(Spacer(1, 8))
        flow.append(Image(str(chart_path), width=6.5 * inch, height=1.6 * inch))
        return flow

    for side in trade.sides:
        flow.append(Paragraph(_summary_line(side), styles["h3"]))
        flow.append(_side_table(side))
        flow.append(Spacer(1, 4))

    winner = trade.winner
    if winner and trade.margin_now > 0:
        flow.append(Paragraph(
            f"<b>Current verdict:</b> {winner.display_name} wins by "
            f"{trade.margin_now:,} value points.",
            styles["body"],
        ))
    else:
        flow.append(Paragraph(
            "<b>Current verdict:</b> even (or insufficient FC values).",
            styles["body"],
        ))
    if trade.n_assets_unranked:
        flow.append(Paragraph(
            f"<i>{trade.n_assets_unranked} asset(s) had no FC value at this "
            "snapshot (consumed picks or unranked players); counted as 0.</i>",
            styles["caveat"],
        ))

    flow.append(Spacer(1, 8))
    flow.append(Image(str(chart_path), width=6.5 * inch, height=2.6 * inch))
    return flow


def build_trade_recap(
    recap: SeasonRecap,
    output_dir: Path,
    *,
    pdf_basename: str | None = None,
) -> Path:
    """Render charts + assemble the trade-recap PDF.

    Each trade also gets a standalone PNG at
    ``{output_dir}/trade_recap_{season}_trade_{NN}.png`` for iMessage.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_basename = pdf_basename or f"trade_recap_{recap.season}.pdf"
    pdf_path = output_dir / pdf_basename

    chart_paths: list[Path] = []
    for i, trade in enumerate(recap.trades, 1):
        chart_path = output_dir / f"trade_recap_{recap.season}_trade_{i:02d}.png"
        trade_chart(trade, chart_path, snapshot_date=recap.snapshot_date)
        chart_paths.append(chart_path)

    styles = _styles()
    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=LETTER,
        leftMargin=PAGE_MARGIN,
        rightMargin=PAGE_MARGIN,
        topMargin=PAGE_MARGIN,
        bottomMargin=PAGE_MARGIN,
        title=f"{recap.league_name} Trade Recap {recap.season}",
        author="lddl-fantasy-analysis",
    )
    flow: list = []

    # Cover
    flow.append(Paragraph(
        f"{recap.league_name} · Trade Recap · {recap.season}", styles["h1"]
    ))
    flow.append(Paragraph(
        f"Generated {datetime.now().strftime('%Y-%m-%d')} · "
        f"FantasyCalc snapshot {recap.snapshot_date.isoformat()} ({recap.snapshot_format_label})",
        styles["muted"],
    ))
    flow.append(Spacer(1, 14))

    n_traded = len(recap.trades)
    n_faab = sum(1 for t in recap.trades if t.is_faab_only)
    n_graded = n_traded - n_faab
    flow.append(Paragraph(
        f"<b>{n_traded} trade(s)</b> in {recap.season}: "
        f"{n_graded} graded, {n_faab} FAAB-only and excluded.",
        styles["body"],
    ))
    flow.append(Spacer(1, 8))

    flow.append(Paragraph(
        "<b>What this report does</b>", styles["h3"]
    ))
    flow.append(Paragraph(
        "For each completed trade, list every player and pick on each side, "
        "look up its dynasty value at the most-recent FantasyCalc snapshot, "
        "and report the net value delta per side. The side with the higher "
        "net is &ldquo;winning&rdquo; the trade at current valuations.",
        styles["body"],
    ))
    flow.append(Spacer(1, 4))
    flow.append(Paragraph(
        "<b>Caveats — read these.</b>", styles["h3"]
    ))
    flow.append(Paragraph(
        "FantasyCalc values are crowdsourced approximations. This grade is "
        "<i>directional, not authoritative</i>.",
        styles["caveat"],
    ))
    flow.append(Paragraph(
        "All values are at the current snapshot, not at the trade date. As "
        "daily snapshots accumulate, future versions of this report will "
        "include &ldquo;at trade,&rdquo; &ldquo;6 months later,&rdquo; and "
        "&ldquo;1 year later&rdquo; columns.",
        styles["caveat"],
    ))
    flow.append(Paragraph(
        "Picks use FantasyCalc&rsquo;s round-bucket value (e.g. &ldquo;2026 "
        "1st&rdquo;), not slot-specific (&ldquo;1.01&rdquo; vs &ldquo;1.12&rdquo;). "
        "For un-traded picks of past drafts, FC has no current value — those "
        "show as &mdash; and count as 0.",
        styles["caveat"],
    ))
    flow.append(Paragraph(
        "Players outside FantasyCalc&rsquo;s top ~440 dynasty rankings have "
        "no value and count as 0.",
        styles["caveat"],
    ))
    flow.append(PageBreak())

    for i, trade in enumerate(recap.trades, 1):
        flow.extend(_trade_section(trade, i, n_traded, chart_paths[i - 1], styles))
        if i < n_traded:
            flow.append(PageBreak())

    doc.build(flow, onFirstPage=_draw_footer, onLaterPages=_draw_footer)
    return pdf_path
