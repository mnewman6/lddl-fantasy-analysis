"""Terminal + markdown rendering of validation results."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from lddl.validate import CheckResult, Severity

_SEVERITY_STYLE = {
    Severity.GREEN: ("green", "PASS"),
    Severity.YELLOW: ("yellow", "WARN"),
    Severity.RED: ("red", "FAIL"),
}

_CATEGORY_ORDER = ["coverage", "identity", "trades", "matchups", "drafts", "hygiene"]


def _counts(results: list[CheckResult]) -> dict[Severity, int]:
    counts = {s: 0 for s in Severity}
    for r in results:
        counts[r.severity] = counts.get(r.severity, 0) + 1
    return counts


def print_terminal_report(results: list[CheckResult], console: Console | None = None) -> None:
    console = console or Console()
    by_cat: dict[str, list[CheckResult]] = {}
    for r in results:
        by_cat.setdefault(r.category, []).append(r)

    console.print("\n[bold]LDDL Ingest Validation[/bold]")
    for cat in _CATEGORY_ORDER:
        if cat not in by_cat:
            continue
        console.print(f"\n[bold cyan]{cat.title()}[/bold cyan]")
        for r in sorted(by_cat[cat], key=lambda x: x.id):
            color, label = _SEVERITY_STYLE[r.severity]
            console.print(
                f"  [{color}]{label}[/{color}]  {r.id:>2}. {r.name}"
                f" — {r.summary}"
            )

    counts = _counts(results)
    total = sum(counts.values())
    console.print(
        f"\n[bold]Summary[/bold]: {total} checks  |  "
        f"[green]{counts[Severity.GREEN]} pass[/green]  "
        f"[yellow]{counts[Severity.YELLOW]} warn[/yellow]  "
        f"[red]{counts[Severity.RED]} fail[/red]"
    )


def write_markdown_report(
    output_path: Path,
    results: list[CheckResult],
    *,
    db_path: Path,
    generated_at: datetime,
) -> None:
    counts = _counts(results)
    total = sum(counts.values())

    lines: list[str] = []
    lines.append("# LDDL Ingest Validation Report")
    lines.append("")
    lines.append(f"- Generated: {generated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append(f"- Database: `{db_path}`")
    lines.append(
        f"- Result: **{total} checks** — {counts[Severity.GREEN]} pass, "
        f"{counts[Severity.YELLOW]} warn, {counts[Severity.RED]} fail"
    )
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| ID | Category | Check | Status | Summary |")
    lines.append("| --- | --- | --- | --- | --- |")
    for r in sorted(results, key=lambda x: x.id):
        lines.append(
            f"| {r.id} | {r.category} | {r.name} | {r.severity.value} | {r.summary} |"
        )
    lines.append("")
    lines.append("---")
    lines.append("")

    by_cat: dict[str, list[CheckResult]] = {}
    for r in results:
        by_cat.setdefault(r.category, []).append(r)
    for cat in _CATEGORY_ORDER:
        if cat not in by_cat:
            continue
        lines.append(f"## {cat.title()}")
        lines.append("")
        for r in sorted(by_cat[cat], key=lambda x: x.id):
            lines.append(f"### {r.id}. {r.name} — {r.severity.value}")
            lines.append("")
            lines.append(r.summary)
            lines.append("")
            if r.details_md:
                lines.append(r.details_md)
                lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines))
