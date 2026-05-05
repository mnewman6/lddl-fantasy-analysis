"""Manager-history PDF builder.

Cover page (championships, extremes, league-wide scatter) plus one
section per manager: lifetime stats, per-season detail, trade summary,
draft summary, and a PF/PA chart.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from lddl.analysis.drafts import (
    ManagerDraftGrade,
    PickGrade,
    aggregate_by_manager,
    per_pick_grades,
)
from lddl.analysis.managers import ManagerCard
from lddl.analysis.snapshots import SnapshotRef
from lddl.reports.charts import manager_seasons_chart, wins_vs_pf_scatter

PAGE_MARGIN = 0.7 * inch
INK = colors.HexColor("#222222")
MUTED = colors.HexColor("#666666")
ACCENT = colors.HexColor("#4f7cac")
DIVIDER = colors.HexColor("#dddddd")
HIGHLIGHT_BG = colors.HexColor("#f6f9fc")


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "h1": ParagraphStyle("H1", parent=base["Heading1"], fontSize=22, leading=26,
                             textColor=INK, spaceAfter=4),
        "h2": ParagraphStyle("H2", parent=base["Heading2"], fontSize=15, leading=18,
                             textColor=INK, spaceAfter=2),
        "h3": ParagraphStyle("H3", parent=base["Heading3"], fontSize=11, leading=13,
                             textColor=ACCENT, spaceAfter=2),
        "body": ParagraphStyle("Body", parent=base["BodyText"], fontSize=9,
                               leading=12, textColor=INK),
        "muted": ParagraphStyle("Muted", parent=base["BodyText"], fontSize=9,
                                leading=12, textColor=MUTED),
        "caveat": ParagraphStyle("Caveat", parent=base["BodyText"], fontSize=8,
                                 leading=11, textColor=MUTED, italic=True,
                                 leftIndent=8),
        "stat_label": ParagraphStyle("StatLabel", parent=base["BodyText"], fontSize=7,
                                     leading=9, textColor=MUTED, alignment=1),
        "stat_value": ParagraphStyle("StatValue", parent=base["BodyText"], fontSize=14,
                                     leading=16, textColor=INK, alignment=1),
    }


def _draw_footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    page_w, _ = LETTER
    canvas.drawString(
        PAGE_MARGIN, 0.4 * inch,
        f"LDDL Manager History · Generated {datetime.now().strftime('%Y-%m-%d')}",
    )
    canvas.drawRightString(page_w - PAGE_MARGIN, 0.4 * inch, f"Page {doc.page}")
    canvas.restoreState()


def _stat_block(label: str, value: str, styles) -> Table:
    t = Table([[Paragraph(value, styles["stat_value"])],
               [Paragraph(label, styles["stat_label"])]],
              colWidths=[1.45 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), HIGHLIGHT_BG),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _seasons_table(card: ManagerCard, styles) -> Table:
    rows = [["Season", "Status", "Reg W-L-T", "PF", "PA", "Luck",
             "Playoff", "Champ", "Last", "Trades", "Trade Δ"]]
    for s in card.seasons:
        rec = f"{s.wins}-{s.losses}-{s.ties}"
        playoff = (
            f"{s.playoff_wins}-{s.playoff_losses}"
            if s.playoff_wins + s.playoff_losses else "—"
        )
        luck = s.wins - s.expected_wins
        rows.append([
            s.season,
            s.league_status,
            rec,
            f"{s.fpts:.1f}",
            f"{s.fpts_against:.1f}",
            f"{luck:+.1f}",
            playoff,
            "★" if s.is_champion else "",
            "·" if s.is_last_place else "",
            str(s.n_trades),
            f"{s.trade_net_value:+,d}" if s.n_trades else "—",
        ])
    t = Table(rows, colWidths=[
        0.55 * inch, 0.65 * inch, 0.7 * inch, 0.6 * inch, 0.6 * inch,
        0.5 * inch, 0.55 * inch, 0.4 * inch, 0.4 * inch, 0.5 * inch, 0.7 * inch,
    ])
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
        ("LINEBELOW", (0, 0), (-1, 0), 0.4, INK),
        ("ALIGN", (2, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def _trade_summary(card: ManagerCard, styles) -> list:
    flow: list = []
    if not card.trades:
        flow.append(Paragraph("<b>Trades:</b> none.", styles["body"]))
        return flow
    n = len(card.trades)
    pct = card.trade_win_rate * 100
    net = card.total_trade_value
    flow.append(Paragraph(
        f"<b>Trades:</b> {n} · win rate {pct:.0f}% · net Δ "
        f"{net:+,d} (current values)",
        styles["body"],
    ))
    top = sorted(card.trades, key=lambda t: -abs(t.net_value))[:3]
    for t in top:
        date_s = t.trade_date.strftime("%Y-%m-%d") if t.trade_date else t.season
        verdict = "won" if t.won else ("lost" if t.net_value < 0 else "even")
        flow.append(Paragraph(
            f"&nbsp;&nbsp;{date_s} · {t.season} · "
            f"Δ {t.net_value:+,d} · {verdict}",
            styles["muted"],
        ))
    return flow


def _draft_summary(grade: ManagerDraftGrade | None, styles) -> list:
    flow: list = []
    if not grade or grade.n_picks == 0:
        flow.append(Paragraph(
            "<b>Draft:</b> no picks in 3-round rookie drafts.", styles["body"]
        ))
        return flow
    flow.append(Paragraph(
        f"<b>Draft:</b> {grade.n_picks} picks · avg Δ vs slot median "
        f"{grade.avg_delta:+.0f}",
        styles["body"],
    ))
    if grade.best_pick:
        bp = grade.best_pick
        flow.append(Paragraph(
            f"&nbsp;&nbsp;Best: {bp.season} R{bp.round}.{bp.draft_slot:02d} "
            f"{bp.player_name or '?'} · Δ {bp.delta:+.0f}",
            styles["muted"],
        ))
    if grade.worst_pick and grade.worst_pick is not grade.best_pick:
        wp = grade.worst_pick
        flow.append(Paragraph(
            f"&nbsp;&nbsp;Worst: {wp.season} R{wp.round}.{wp.draft_slot:02d} "
            f"{wp.player_name or '?'} · Δ {wp.delta:+.0f}",
            styles["muted"],
        ))
    return flow


def _manager_section(
    card: ManagerCard,
    draft_grade: ManagerDraftGrade | None,
    chart_path: Path,
    styles,
) -> list:
    flow: list = []
    aliases = ", ".join(a for a in card.aliases if a != card.display_name)
    teams = ", ".join(card.team_names) if card.team_names else "—"
    flow.append(Paragraph(card.display_name, styles["h2"]))
    flow.append(Paragraph(
        f"Aliases: {aliases or '—'} · Team names: {teams} · "
        f"Active {card.first_seen_season}–{card.last_seen_season}",
        styles["muted"],
    ))
    flow.append(Spacer(1, 6))

    # Stat tiles row
    tiles = [
        _stat_block("REG W-L-T",
                    f"{card.total_wins}-{card.total_losses}-{card.total_ties}",
                    styles),
        _stat_block("PLAYOFF W-L",
                    f"{card.total_playoff_wins}-{card.total_playoff_losses}",
                    styles),
        _stat_block("CHAMPS", str(card.championships), styles),
        _stat_block("LAST PLACE", str(card.last_places), styles),
        _stat_block("LUCK", f"{card.luck:+.0f}", styles),
    ]
    tiles_row = Table([tiles], colWidths=[1.45 * inch] * 5)
    tiles_row.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    flow.append(tiles_row)
    flow.append(Spacer(1, 3))

    pf_tiles = [
        _stat_block("PF", f"{card.total_fpts:.0f}", styles),
        _stat_block("PA", f"{card.total_fpts_against:.0f}", styles),
        _stat_block("EXP W", f"{card.total_expected_wins:.1f}", styles),
        _stat_block("# TRADES", str(card.n_trades), styles),
        _stat_block("TRADE Δ", f"{card.total_trade_value:+,d}", styles),
    ]
    pf_row = Table([pf_tiles], colWidths=[1.45 * inch] * 5)
    pf_row.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    flow.append(pf_row)
    flow.append(Spacer(1, 8))

    flow.append(_seasons_table(card, styles))
    flow.append(Spacer(1, 8))

    flow.extend(_trade_summary(card, styles))
    flow.extend(_draft_summary(draft_grade, styles))
    flow.append(Spacer(1, 6))

    flow.append(Image(str(chart_path), width=6.5 * inch, height=2.4 * inch))
    return flow


def _cover_page(
    cards: list[ManagerCard],
    snapshot_date: date,
    snapshot_format_label: str,
    scatter_path: Path,
    styles,
) -> list:
    flow: list = []
    flow.append(Paragraph("LDDL · Manager History", styles["h1"]))
    seasons = sorted({s.season for c in cards for s in c.seasons})
    flow.append(Paragraph(
        f"Generated {datetime.now().strftime('%Y-%m-%d')} · "
        f"{len(cards)} managers across seasons {seasons[0]}–{seasons[-1]} · "
        f"FantasyCalc snapshot {snapshot_date.isoformat()} ({snapshot_format_label})",
        styles["muted"],
    ))
    flow.append(Spacer(1, 12))

    # Champions roll
    champs = []
    for c in cards:
        for s in c.seasons:
            if s.is_champion:
                champs.append((s.season, c.display_name, s.team_name or ""))
    champs.sort()
    flow.append(Paragraph("<b>Champions</b>", styles["h3"]))
    for season, name, team in champs:
        suffix = f" · &ldquo;{team}&rdquo;" if team else ""
        flow.append(Paragraph(f"{season} — {name}{suffix}", styles["body"]))
    flow.append(Spacer(1, 6))

    # Last places
    lasts = []
    for c in cards:
        for s in c.seasons:
            if s.is_last_place:
                lasts.append((s.season, c.display_name))
    lasts.sort()
    flow.append(Paragraph("<b>Last place</b>", styles["h3"]))
    for season, name in lasts:
        flow.append(Paragraph(f"{season} — {name}", styles["body"]))
    flow.append(Spacer(1, 6))

    # Extremes
    extremes: list[tuple[str, str]] = []
    if cards:
        most_w = max(cards, key=lambda c: c.total_wins)
        fewest_w = min(cards, key=lambda c: c.total_wins)
        most_pf = max(cards, key=lambda c: c.total_fpts)
        most_lucky = max(cards, key=lambda c: c.luck)
        most_unlucky = min(cards, key=lambda c: c.luck)
        most_trades = max(cards, key=lambda c: c.n_trades)
        extremes.append(("Most regular-season wins",
                         f"{most_w.display_name} ({most_w.total_wins}W)"))
        extremes.append(("Fewest wins",
                         f"{fewest_w.display_name} ({fewest_w.total_wins}W)"))
        extremes.append(("Most points for",
                         f"{most_pf.display_name} ({most_pf.total_fpts:.0f})"))
        extremes.append(("Most lucky (W − expected)",
                         f"{most_lucky.display_name} ({most_lucky.luck:+.1f})"))
        extremes.append(("Most unlucky",
                         f"{most_unlucky.display_name} ({most_unlucky.luck:+.1f})"))
        extremes.append(("Most trades",
                         f"{most_trades.display_name} ({most_trades.n_trades})"))
    flow.append(Paragraph("<b>Notable extremes</b>", styles["h3"]))
    for label, value in extremes:
        flow.append(Paragraph(f"{label}: {value}", styles["body"]))
    flow.append(Spacer(1, 10))

    flow.append(Image(str(scatter_path), width=6.5 * inch, height=4.4 * inch))
    flow.append(Spacer(1, 8))

    flow.append(Paragraph(
        "Wins are regular-season head-to-head only, computed from matchups. "
        "Luck = actual wins − weeks they would have beaten the league median. "
        "Trade values use the current FantasyCalc snapshot only — historical "
        "&ldquo;at trade&rdquo; values become available as snapshots accumulate. "
        "FantasyCalc values are crowdsourced approximations; grades are directional.",
        styles["caveat"],
    ))
    return flow


def build_manager_history(
    cards: list[ManagerCard],
    snapshot: SnapshotRef,
    snapshot_format_label: str,
    output_dir: Path,
    *,
    pdf_basename: str = "manager_history.pdf",
    draft_grades: dict[str, ManagerDraftGrade] | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = output_dir / pdf_basename

    scatter_path = output_dir / "manager_history_wins_vs_pf.png"
    wins_vs_pf_scatter(cards, scatter_path, snapshot_date=snapshot.snapshot_date)

    chart_paths: list[Path] = []
    for c in cards:
        cp = output_dir / f"manager_history_{c.user_id}.png"
        manager_seasons_chart(c, cp, snapshot_date=snapshot.snapshot_date)
        chart_paths.append(cp)

    styles = _styles()
    doc = SimpleDocTemplate(
        str(pdf_path), pagesize=LETTER,
        leftMargin=PAGE_MARGIN, rightMargin=PAGE_MARGIN,
        topMargin=PAGE_MARGIN, bottomMargin=PAGE_MARGIN,
        title="LDDL Manager History",
        author="lddl-fantasy-analysis",
    )
    flow: list = _cover_page(
        cards, snapshot.snapshot_date, snapshot_format_label, scatter_path, styles,
    )
    flow.append(PageBreak())

    for i, (c, cp) in enumerate(zip(cards, chart_paths)):
        grade = (draft_grades or {}).get(c.user_id)
        flow.extend(_manager_section(c, grade, cp, styles))
        if i < len(cards) - 1:
            flow.append(PageBreak())

    doc.build(flow, onFirstPage=_draw_footer, onLaterPages=_draw_footer)
    return pdf_path


def build_manager_history_from_db(
    conn,
    output_dir: Path,
    *,
    pdf_basename: str = "manager_history.pdf",
) -> Path:
    """End-to-end builder taking only a DB connection + output dir."""
    from lddl.analysis.managers import build_manager_cards
    from lddl.analysis.snapshots import latest_snapshot

    snap = latest_snapshot(conn)
    if snap is None:
        raise RuntimeError("No FantasyCalc snapshots in DB. Run `lddl snapshot` first.")
    cards = build_manager_cards(conn)
    grades = aggregate_by_manager(per_pick_grades(conn, snap))
    fmt_label = (
        f"{'Superflex' if snap.format_num_qbs == 2 else f'{snap.format_num_qbs}QB'}, "
        f"{snap.format_ppr} PPR, {snap.format_num_teams}-team "
        f"{'dynasty' if snap.format_is_dynasty else 'redraft'}"
    )
    return build_manager_history(
        cards, snap, fmt_label, output_dir, pdf_basename=pdf_basename,
        draft_grades=grades,
    )
