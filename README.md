# LDDL Fantasy Analysis

Local CLI tool for analyzing the LDDL dynasty fantasy football league. Pulls full league history from Sleeper, layers FantasyCalc dynasty values on top, and produces PDF reports + chart PNGs for the group chat.

Personal research tool — runs locally on macOS. No auth, no hosting, no MCP server.

## Setup (one-time)

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv).

```bash
uv sync
cp .env.example .env
# edit .env and set SLEEPER_LEAGUE_ID
```

Bootstrap the local warehouse:

```bash
uv run lddl ingest          # pull all historical seasons from Sleeper
uv run lddl snapshot        # take the first FantasyCalc snapshot
uv run lddl validate ingest # 21-check data-quality pass
```

## Daily

```bash
# 7am cron — keeps the FC value history accumulating going forward.
crontab -e
0 7 * * * cd /Users/mattnewman/Desktop/Organized/Claude_Code_Projects/lddl-fantasy-analysis && /Users/mattnewman/.local/bin/uv run lddl snapshot >> data/snapshot.log 2>&1
```

`lddl snapshot` is idempotent for a given date — running it twice on the same day is a no-op (use `--force` to refetch).

**Why daily matters:** FantasyCalc only publishes *current* values. Every day skipped is a day of trade-grading history that can't be reconstructed later.

## Weekly during the season

```bash
uv run lddl ingest           # pull new transactions, matchups, draft picks, brackets
uv run lddl validate ingest  # confirm the pull is clean
```

`lddl ingest` caches completed seasons permanently and only re-fetches the in-progress season; weekly re-ingest is fast (a few hundred small API calls).

## When new data drops

- **Rookie draft completes** → `lddl ingest` to capture the new `draft_picks` rows.
- **Sleeper opens a new season** → `lddl ingest` walks `previous_league_id` automatically and the new season is ingested without any config change.
- **Trade you want to grade now** → `lddl ingest` then `lddl report trade-recap --season YYYY`.

## Producing reports

```bash
uv run lddl report trade-recap --season 2024   # all 2024 trades, graded
uv run lddl report manager-history             # all managers, all seasons
```

Outputs land in `lddl/output/` as both a multi-page PDF and per-trade / per-manager PNGs. The PNGs are sized for iMessage so you can drop a single image in the group chat without sharing the whole PDF.

```
lddl/output/
├── trade_recap_2024.pdf
├── trade_recap_2024_trade_01.png
├── trade_recap_2024_trade_02.png
├── ...
├── manager_history.pdf
├── manager_history_wins_vs_pf.png         # the cover scatter, standalone
└── manager_history_<user_id>.png          # per-manager bar chart, standalone
```

## Architecture

```
lddl/
├── cli.py             — Typer entry point (lddl ingest / snapshot / report ... / validate)
├── config.py          — pydantic-settings reading .env
├── clients/
│   ├── sleeper.py     — public Sleeper API + on-disk cache + tenacity retry
│   └── fantasycalc.py — FantasyCalc public values endpoint
├── ingest/            — Sleeper → DuckDB normalizers
│   ├── league_history.py  — walks previous_league_id chain
│   ├── matchups.py
│   ├── transactions.py    — splits trades into transaction_players + transaction_picks
│   ├── traded_picks       (in transactions.py)
│   ├── drafts.py
│   ├── brackets.py        — winners + losers brackets
│   └── players.py         — weekly /players/nfl refresh
├── snapshot/          — FantasyCalc daily snapshot
├── store/
│   ├── schema.sql     — DuckDB tables
│   └── db.py          — connection helpers
├── analysis/
│   ├── snapshots.py   — at-or-before / at-or-after value lookup helpers
│   ├── picks.py       — pick → FC name mapping
│   ├── trades.py      — trade enumeration + grading
│   ├── standings.py   — regular-season W-L-T from matchups, playoff record
│   ├── managers.py    — per-manager card aggregation
│   └── drafts.py      — pick grade vs slot median
├── reports/
│   ├── charts.py      — matplotlib factories (muted palette)
│   ├── pdf.py         — trade-recap PDF builder (reportlab)
│   └── manager_history.py — manager-history PDF builder
└── validate/          — 21-check data-quality pass
```

DuckDB warehouse at `data/lddl.duckdb`; raw cached API responses at `data/raw/`. Both gitignored.

## Caveats and known limitations

These are **product decisions**, not bugs. They show up in every report's caveat section.

- **FantasyCalc values are crowdsourced approximations.** Trade and draft grades are *directional, not authoritative*. KeepTradeCut is intentionally excluded (their TOS forbids scraping).
- **Picks use FC's round-bucket value** (`"2026 1st"`), not slot-specific (`"2026 Pick 1.05"`). Slot resolution from `draft_picks` + `transaction_picks` is genuinely ambiguous when one picker received multiple picks of the same round in the same season — which actually happens in LDDL.
- **Consumed picks have no FC value.** Once a draft happens, FantasyCalc drops those picks from current values. Trades that primarily moved old picks (e.g. 2024 trades pre-rookie-draft) under-grade because most assets show as `—` and count as 0.
- **All values are at the latest snapshot.** &ldquo;At trade date,&rdquo; &ldquo;6 months later,&rdquo; and &ldquo;1 year later&rdquo; columns are deferred until daily snapshots accumulate. The architecture is in place; only data is missing. Run the daily cron faithfully and the columns fill in over time.
- **FAAB-only &ldquo;trades&rdquo; are listed but not graded.** Sleeper supports waiver-budget swaps as a transaction type; we don't model FAAB as an asset.
- **Players outside FantasyCalc's top ~440 dynasty rankings count as 0.** Fine for casual depth pieces.
- **`r.wins` from Sleeper appears to include consolation games.** We compute regular-season W-L-T from `matchups` directly so the luck metric (actual − expected) balances league-wide to zero.

## Validation

```bash
uv run lddl validate ingest   # 21 data-quality checks; exits 1 on any RED.
```

Writes a markdown report to `lddl/output/validation_report.md` with full per-check tables. Designed to surface problems for review, not auto-fix them — the user decides what's a real bug vs. a quirk.

## What's deferred

- **`lddl report league-state`** — current-season snapshot (power rankings, recent notable trades, waiver activity, standings). Still stubbed; not yet implemented.
- **Slot-specific pick valuations** — would require resolving each pick's slot from prior-season standings or draft history. Ambiguous in LDDL when one picker received multiple picks per round.
- **At-trade-date / +6mo / +1y FC values** — fills in automatically as daily snapshots accumulate. The trade-grader code path uses `latest_snapshot()` for now; switch to date-aware lookups once we have history.
- **`lddl validate snapshots`** — parallel command for FC snapshot health (any missing dates, value drift sanity).

## Common operations

| Goal | Command |
| --- | --- |
| First-time setup | `uv sync && cp .env.example .env && uv run lddl ingest && uv run lddl snapshot` |
| Daily snapshot | `uv run lddl snapshot` (idempotent per-date) |
| Weekly re-pull | `uv run lddl ingest && uv run lddl validate ingest` |
| Force a refetch | `uv run lddl ingest --force` or `uv run lddl snapshot --force` |
| Skip player metadata refresh | `uv run lddl ingest --skip-players` |
| Trade recap for a season | `uv run lddl report trade-recap --season 2024` |
| Manager history | `uv run lddl report manager-history` |
| List CLI commands | `uv run lddl --help` |

## Troubleshooting

- **`SLEEPER_LEAGUE_ID is not set`** — copy `.env.example` to `.env` and fill it in.
- **`No DuckDB file at ...`** — run `lddl ingest` first.
- **`No FantasyCalc snapshots in DB`** — run `lddl snapshot` first.
- **Reports look wrong after manual SQL edits** — re-run `lddl ingest --force` to rebuild from cached raw API responses; nothing in `data/raw/` ever needs to be re-fetched once a season completes.
