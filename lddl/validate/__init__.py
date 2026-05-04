"""Data-quality validation for the ingested Sleeper warehouse."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


class Severity(str, Enum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"


@dataclass
class CheckResult:
    id: int
    category: str
    name: str
    severity: Severity
    summary: str
    details_md: str = ""


def run_validation(
    db_path: Path,
    output_path: Path | None = None,
) -> list[CheckResult]:
    """Run all checks, print to terminal, optionally write a markdown report."""
    from lddl.store.db import connect
    from lddl.validate.checks import ALL_CHECKS
    from lddl.validate.report import print_terminal_report, write_markdown_report

    results: list[CheckResult] = []
    with connect(db_path) as conn:
        for fn in ALL_CHECKS:
            meta = getattr(fn, "_check_meta", None)
            try:
                results.append(fn(conn))
            except Exception as e:
                results.append(
                    CheckResult(
                        id=meta["id"] if meta else -1,
                        category=meta["category"] if meta else "?",
                        name=meta["name"] if meta else fn.__name__,
                        severity=Severity.RED,
                        summary=f"check raised {type(e).__name__}: {e}",
                    )
                )
    print_terminal_report(results)
    if output_path:
        write_markdown_report(
            output_path,
            results,
            db_path=db_path,
            generated_at=datetime.now(timezone.utc),
        )
    return results


def check(id: int, category: str, name: str):
    """Decorator that tags a check function with its display metadata."""

    def decorator(fn):
        fn._check_meta = {"id": id, "category": category, "name": name}
        return fn

    return decorator
