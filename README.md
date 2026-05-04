# LDDL Fantasy Analysis

Local CLI tool for analyzing the LDDL dynasty fantasy football league. Pulls full league history from Sleeper, layers FantasyCalc dynasty values on top, and produces PDF reports + chart PNGs for the league group chat.

Personal research tool — runs locally on macOS, no hosted services.

## Status

Build in progress. Working today:

- `uv run lddl --help` — CLI scaffold with all command names wired up
- All `ingest` / `snapshot` / `report ...` commands print a "not yet implemented" notice; they get filled in across the build steps below.

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv).

```bash
uv sync
cp .env.example .env
# edit .env and set SLEEPER_LEAGUE_ID
```

PDF rendering uses WeasyPrint, which has system dependencies. On macOS:

```bash
brew install pango gdk-pixbuf libffi
```

(Install once when we reach the PDF step — not needed for the early build steps.)

## Usage

```bash
uv run lddl --help
uv run lddl ingest                            # step 2 — pull league history into DuckDB
uv run lddl snapshot                          # step 3 — snapshot today's FantasyCalc values
uv run lddl report trade-recap --season 2024  # step 4
uv run lddl report manager-history            # step 5
uv run lddl report league-state               # step 5+
```

Run `uv run lddl snapshot` daily (cron or manually) to build a value-history archive — FantasyCalc values can only be collected going forward, not backfilled.

## Build order

1. Repo skeleton + CLI scaffold ✅
2. Sleeper client + league-history walker + `lddl ingest`
3. FantasyCalc client + `lddl snapshot`
4. Trade analysis + `lddl report trade-recap`
5. Manager + draft analysis + their reports
6. PDF/chart polish

## Layout

- `lddl/cli.py` — CLI entry point (Typer)
- `lddl/clients/` — Sleeper + FantasyCalc HTTP clients
- `lddl/ingest/` — pulls Sleeper data into DuckDB
- `lddl/store/` — DuckDB schema + helpers
- `lddl/analysis/` — trade grading, manager performance, draft grading
- `lddl/reports/` — chart + PDF builders
- `data/lddl.duckdb` — local warehouse (gitignored)
- `data/raw/` — cached raw Sleeper API responses, rebuildable (gitignored)
- `lddl/output/` — generated PDFs and PNGs (gitignored)

## Notes

- FantasyCalc values are crowdsourced approximations. Trade grades are directional, not authoritative — every report says so.
- KeepTradeCut is intentionally excluded (their TOS forbids scraping).
- Sleeper rate limit: <1000 req/min. Past-season data is cached permanently once fetched.
