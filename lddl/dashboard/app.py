"""Local Streamlit dashboard for the LDDL warehouse.

Run with `uv run lddl dashboard` (preferred) or
`uv run streamlit run lddl/dashboard/app.py`.

Single-file by intent — every tab uses the cached helpers at the top so the
DuckDB connection and analysis aggregations are shared across reruns.
"""

from __future__ import annotations

import hashlib
import html
import os
import random

import altair as alt
import duckdb
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="LDDL · Fantasy Command Center",
    page_icon="🏈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Bridge Streamlit Cloud secrets → env vars so pydantic-settings picks them up.
try:
    secrets = dict(st.secrets) if hasattr(st, "secrets") else {}
except Exception:
    secrets = {}
for key in ("SLEEPER_LEAGUE_ID", "LEAGUE_NAME"):
    if key in secrets and not os.environ.get(key):
        os.environ[key] = str(secrets[key])

from lddl.analysis.drafts import aggregate_by_manager, per_pick_grades  # noqa: E402,F401
from lddl.analysis.franchises import canonical_user_id  # noqa: E402
from lddl.analysis.managers import build_manager_cards  # noqa: E402
from lddl.analysis.recommendations import (  # noqa: E402
    classify_managers,
    current_rosters,
    recommend_trades,
)
from lddl.analysis.snapshots import latest_snapshot  # noqa: E402
from lddl.analysis.trades import grade_trades_for_season  # noqa: E402
from lddl.config import get_settings  # noqa: E402

# ---------- Brand palette ---------------------------------------------------

PALETTE = {
    "bg":          "#0E0F13",
    "surface":     "#171922",
    "surface_hi":  "#20232E",
    "border":      "#2A2E3B",
    "text":        "#ECEDF1",
    "muted":       "#8C92A4",
    "orange":      "#FF6A1A",
    "orange_deep": "#E65100",
    "silver":      "#C7CCD6",
    "purple":      "#8B5CF6",
    "cyan":        "#22D3EE",
    "gold":        "#F5B642",
    "danger":      "#FF5C7C",
    "success":     "#2EE090",
    "magenta":     "#EC4899",
    "lime":        "#A3E635",
    "blue":        "#60A5FA",
}

# Pool of accent colors used to give each manager a distinct identity.
ACCENT_POOL = [
    PALETTE["orange"], PALETTE["cyan"], PALETTE["purple"], PALETTE["gold"],
    PALETTE["success"], PALETTE["magenta"], PALETTE["silver"], PALETTE["lime"],
    PALETTE["blue"], "#F472B6", "#FB923C", "#34D399",
]


def accent_for(seed: str) -> str:
    h = int(hashlib.md5(seed.encode("utf-8")).hexdigest(), 16)
    return ACCENT_POOL[h % len(ACCENT_POOL)]


def initials(name: str) -> str:
    s = (name or "").strip()
    if not s:
        return "??"
    parts = [p for p in s.replace("_", " ").split() if p]
    if len(parts) == 1:
        word = parts[0]
        clean = "".join(c for c in word if c.isalnum())
        return clean[:2].upper() if clean else word[:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _h(s: str) -> str:
    """Collapse multi-line HTML so Streamlit's markdown doesn't treat
    indented lines as a code block."""
    return "".join(line.strip() for line in s.splitlines())


# ---------- CSS injection ---------------------------------------------------

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Bebas+Neue&family=Oswald:wght@500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

:root {
    --c-bg:          #0E0F13;
    --c-surface:     #171922;
    --c-surface-hi:  #20232E;
    --c-border:      #2A2E3B;
    --c-text:        #ECEDF1;
    --c-muted:       #8C92A4;
    --c-orange:      #FF6A1A;
    --c-orange-deep: #E65100;
    --c-silver:      #C7CCD6;
    --c-purple:      #8B5CF6;
    --c-cyan:        #22D3EE;
    --c-gold:        #F5B642;
    --c-danger:      #FF5C7C;
    --c-success:     #2EE090;
}

html, body, [class*="css"], .stApp {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    background: var(--c-bg);
    color: var(--c-text);
}

/* Tighter top padding + cap width */
.main .block-container {
    padding-top: 1.2rem !important;
    padding-bottom: 4rem !important;
    max-width: 1400px;
}

/* Hide Streamlit chrome */
#MainMenu, footer, header[data-testid="stHeader"] { visibility: hidden; height: 0; }

/* Sidebar */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #14161E 0%, #0B0C10 100%);
    border-right: 1px solid var(--c-border);
}
[data-testid="stSidebar"] * { color: var(--c-text) !important; }
[data-testid="stSidebar"] .stCaption,
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p { color: var(--c-muted) !important; }

/* ==================== TABS as nav pills ==================== */
.stTabs [data-baseweb="tab-list"] {
    gap: 6px;
    border-bottom: 1px solid var(--c-border);
    background: transparent;
    padding: 0 4px;
}
.stTabs [data-baseweb="tab"] {
    height: 46px;
    padding: 0 22px;
    background: transparent;
    color: var(--c-muted);
    font-family: 'Oswald', sans-serif;
    font-weight: 600;
    font-size: 14px;
    letter-spacing: 1.2px;
    text-transform: uppercase;
    border-radius: 0;
    border-bottom: 3px solid transparent !important;
    transition: all 160ms ease;
}
.stTabs [data-baseweb="tab"]:hover {
    color: var(--c-text);
    background: rgba(255,106,26,0.06);
}
.stTabs [aria-selected="true"] {
    color: var(--c-orange) !important;
    border-bottom: 3px solid var(--c-orange) !important;
    background: rgba(255,106,26,0.08) !important;
}
.stTabs [data-baseweb="tab-panel"] {
    padding-top: 18px;
}

/* ==================== METRICS (stat tiles) ==================== */
[data-testid="stMetric"] {
    background: linear-gradient(180deg, var(--c-surface) 0%, #14161E 100%);
    border: 1px solid var(--c-border);
    border-radius: 14px;
    padding: 16px 18px;
    transition: border-color 200ms ease, transform 200ms ease;
}
[data-testid="stMetric"]:hover {
    border-color: var(--c-orange);
    transform: translateY(-1px);
}
[data-testid="stMetricLabel"] p {
    color: var(--c-muted) !important;
    font-size: 11px !important;
    text-transform: uppercase;
    letter-spacing: 1.3px;
    font-weight: 600;
}
[data-testid="stMetricValue"] {
    font-family: 'Bebas Neue', sans-serif !important;
    font-size: 38px !important;
    color: var(--c-text) !important;
    line-height: 1 !important;
    letter-spacing: 0.5px !important;
}
[data-testid="stMetricDelta"] { font-family: 'JetBrains Mono', monospace; }

/* ==================== Dataframes / tables ==================== */
[data-testid="stDataFrame"], [data-testid="stTable"] {
    border-radius: 12px;
    overflow: hidden;
    border: 1px solid var(--c-border);
    background: var(--c-surface);
}

/* ==================== Inputs ==================== */
.stSelectbox div[data-baseweb="select"] > div,
.stMultiSelect div[data-baseweb="select"] > div,
.stTextInput input,
.stNumberInput input {
    background: var(--c-surface) !important;
    border-color: var(--c-border) !important;
    border-radius: 10px !important;
    color: var(--c-text) !important;
}
.stSlider [data-baseweb="slider"] > div > div { background: var(--c-orange) !important; }

/* ==================== Scrollbar ==================== */
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-track { background: var(--c-bg); }
::-webkit-scrollbar-thumb { background: var(--c-border); border-radius: 6px; }
::-webkit-scrollbar-thumb:hover { background: var(--c-orange-deep); }

/* ==================== HERO header ==================== */
.lddl-hero {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 18px;
    padding: 22px 26px;
    background:
        radial-gradient(circle at 0% 0%, rgba(255,106,26,0.20), transparent 55%),
        radial-gradient(circle at 100% 100%, rgba(139,92,246,0.14), transparent 55%),
        linear-gradient(135deg, #15171F 0%, #1A1D28 100%);
    border: 1px solid var(--c-border);
    border-radius: 20px;
    margin-bottom: 14px;
    position: relative;
    overflow: hidden;
}
.lddl-hero::after {
    content: '';
    position: absolute;
    inset: 0;
    background: linear-gradient(180deg, transparent 0%, rgba(0,0,0,0.20) 100%);
    pointer-events: none;
    border-radius: 20px;
}
.lddl-hero .brand { display: flex; align-items: center; gap: 16px; z-index: 1; }
.lddl-hero .crest {
    width: 64px; height: 64px;
    border-radius: 16px;
    background: linear-gradient(135deg, #FF6A1A 0%, #E65100 100%);
    display: flex; align-items: center; justify-content: center;
    font-family: 'Bebas Neue', sans-serif;
    font-size: 30px;
    color: #0E0F13;
    box-shadow:
        0 0 30px rgba(255,106,26,0.40),
        inset 0 -3px 0 rgba(0,0,0,0.20);
    position: relative;
    border: 2px solid rgba(199, 204, 214, 0.30);
}
.lddl-hero .logo {
    font-family: 'Bebas Neue', sans-serif;
    font-size: 56px;
    line-height: 1;
    letter-spacing: 5px;
    background: linear-gradient(135deg, #FF6A1A 0%, #FF8A3D 50%, #C7CCD6 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    text-shadow: 0 0 30px rgba(255,106,26,0.30);
    margin-bottom: 2px;
}
.lddl-hero .tagline {
    color: var(--c-silver);
    font-family: 'Oswald', sans-serif;
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 2.5px;
    opacity: 0.85;
}
.lddl-hero .meta { display: flex; gap: 10px; flex-wrap: wrap; z-index: 1; }
.lddl-hero .chip {
    background: rgba(255,255,255,0.04);
    border: 1px solid var(--c-border);
    padding: 10px 14px;
    border-radius: 12px;
    color: var(--c-text);
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
    line-height: 1.4;
    backdrop-filter: blur(6px);
}
.lddl-hero .chip .lbl {
    display: block;
    color: var(--c-muted);
    text-transform: uppercase;
    letter-spacing: 1.2px;
    margin-bottom: 4px;
    font-size: 9px;
    font-weight: 600;
    font-family: 'Oswald', sans-serif;
}
.lddl-hero .chip .val { font-weight: 600; }

/* ==================== Section header ==================== */
.lddl-section {
    display: flex;
    align-items: center;
    gap: 12px;
    margin: 30px 0 14px 0;
}
.lddl-section .bar {
    width: 4px;
    height: 24px;
    background: linear-gradient(180deg, var(--c-orange), var(--c-orange-deep));
    border-radius: 2px;
}
.lddl-section h2 {
    font-family: 'Oswald', sans-serif;
    font-size: 22px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 1.4px;
    color: var(--c-text);
    margin: 0;
}
.lddl-section .sub {
    color: var(--c-muted);
    font-size: 11px;
    margin-left: auto;
    text-transform: uppercase;
    letter-spacing: 1.2px;
    font-family: 'Oswald', sans-serif;
}

/* ==================== Manager card grid ==================== */
.lddl-mgr-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
    gap: 12px;
}
.lddl-mgr {
    background: linear-gradient(180deg, var(--c-surface) 0%, #131520 100%);
    border: 1px solid var(--c-border);
    border-radius: 14px;
    padding: 14px;
    position: relative;
    overflow: hidden;
    transition: transform 180ms ease, border-color 180ms ease, box-shadow 180ms ease;
}
.lddl-mgr::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 3px;
    background: var(--accent, var(--c-orange));
}
.lddl-mgr:hover {
    transform: translateY(-3px);
    border-color: var(--accent, var(--c-orange));
    box-shadow: 0 8px 24px rgba(0,0,0,0.45);
}
.lddl-mgr .row { display: flex; align-items: center; gap: 12px; }
.lddl-mgr .avatar {
    width: 44px; height: 44px;
    border-radius: 12px;
    display: flex; align-items: center; justify-content: center;
    background: var(--accent, var(--c-orange));
    color: #0E0F13;
    font-family: 'Bebas Neue', sans-serif;
    font-size: 22px;
    letter-spacing: 0.5px;
    flex-shrink: 0;
    box-shadow: 0 4px 12px rgba(0,0,0,0.30);
}
.lddl-mgr .name {
    font-family: 'Oswald', sans-serif;
    font-weight: 600;
    color: var(--c-text);
    font-size: 16px;
    text-transform: uppercase;
    letter-spacing: 0.4px;
    line-height: 1.1;
}
.lddl-mgr .team {
    color: var(--c-muted);
    font-size: 11px;
    margin-top: 3px;
    font-style: italic;
    line-height: 1.2;
}
.lddl-mgr .stats {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 6px;
    margin-top: 14px;
    padding-top: 12px;
    border-top: 1px solid var(--c-border);
}
.lddl-mgr .stat { text-align: center; }
.lddl-mgr .stat .n {
    font-family: 'Bebas Neue', sans-serif;
    font-size: 22px;
    color: var(--c-text);
    line-height: 1;
}
.lddl-mgr .stat .l {
    font-size: 9px;
    color: var(--c-muted);
    text-transform: uppercase;
    letter-spacing: 1.1px;
    margin-top: 5px;
    font-weight: 600;
}
.lddl-mgr .badges {
    position: absolute;
    top: 10px; right: 10px;
    display: flex; gap: 4px;
}
.lddl-mgr .badge {
    width: 24px; height: 24px;
    border-radius: 7px;
    display: flex; align-items: center; justify-content: center;
    font-size: 12px;
    font-weight: 700;
}
.lddl-mgr .badge.champ {
    background: linear-gradient(135deg, #F5B642, #E89B16);
    color: #1a1306;
    box-shadow: 0 2px 8px rgba(245,182,66,0.35);
}
.lddl-mgr .badge.last {
    background: linear-gradient(135deg, #FF5C7C, #C03050);
    color: #fff;
    box-shadow: 0 2px 8px rgba(255,92,124,0.30);
}

/* ==================== Champions / Last podium ==================== */
.lddl-podium { display: flex; flex-direction: column; gap: 8px; }
.lddl-row {
    display: flex; align-items: center; gap: 12px;
    padding: 12px 16px;
    background: linear-gradient(90deg, var(--c-surface) 0%, #131520 100%);
    border: 1px solid var(--c-border);
    border-left: 4px solid var(--c-gold);
    border-radius: 10px;
    transition: transform 150ms;
}
.lddl-row:hover { transform: translateX(2px); }
.lddl-row.last { border-left-color: var(--c-danger); }
.lddl-row .yr {
    font-family: 'Bebas Neue', sans-serif;
    font-size: 28px;
    color: var(--c-orange);
    min-width: 64px;
    line-height: 1;
}
.lddl-row.last .yr { color: var(--c-danger); }
.lddl-row .mini-av {
    width: 30px; height: 30px;
    border-radius: 8px;
    display: flex; align-items: center; justify-content: center;
    background: var(--accent, var(--c-orange));
    color: #0E0F13;
    font-family: 'Bebas Neue', sans-serif;
    font-size: 14px;
    flex-shrink: 0;
}
.lddl-row .name {
    font-family: 'Oswald', sans-serif;
    font-weight: 600;
    color: var(--c-text);
    text-transform: uppercase;
    letter-spacing: 0.6px;
    font-size: 14px;
}
.lddl-row .team {
    color: var(--c-muted);
    font-size: 12px;
    margin-left: auto;
    font-style: italic;
}
.lddl-row .icon {
    font-size: 16px;
    margin-left: 6px;
}

/* ==================== Trade card ==================== */
.lddl-trade {
    background: linear-gradient(90deg, var(--c-surface) 0%, #131520 100%);
    border: 1px solid var(--c-border);
    border-radius: 12px;
    padding: 12px 16px;
    margin-bottom: 8px;
    display: flex;
    align-items: center;
    gap: 16px;
}
.lddl-trade .dt {
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
    color: var(--c-muted);
    min-width: 96px;
}
.lddl-trade .pts { color: var(--c-text); font-weight: 600; flex: 1; }
.lddl-trade .seas {
    font-family: 'Oswald', sans-serif;
    color: var(--c-orange);
    font-size: 13px;
    letter-spacing: 0.6px;
}
.lddl-trade .nways {
    font-family: 'JetBrains Mono', monospace;
    color: var(--c-muted);
    font-size: 11px;
    background: rgba(255,255,255,0.04);
    padding: 2px 8px;
    border-radius: 6px;
}

/* ==================== Manager profile (Managers tab) ==================== */
.lddl-profile {
    display: flex; gap: 22px;
    background: linear-gradient(135deg, #15171F 0%, #1B1E2A 100%);
    border: 1px solid var(--c-border);
    border-radius: 20px;
    padding: 24px;
    align-items: center;
    margin-bottom: 16px;
    position: relative;
    overflow: hidden;
}
.lddl-profile::after {
    content: '';
    position: absolute;
    top: 0; right: 0; bottom: 0;
    width: 40%;
    background: radial-gradient(circle at 80% 50%, var(--accent, var(--c-orange)), transparent 70%);
    opacity: 0.18;
    pointer-events: none;
}
.lddl-profile .av {
    width: 88px; height: 88px;
    border-radius: 20px;
    display: flex; align-items: center; justify-content: center;
    background: var(--accent, var(--c-orange));
    color: #0E0F13;
    font-family: 'Bebas Neue', sans-serif;
    font-size: 42px;
    flex-shrink: 0;
    box-shadow: 0 0 36px rgba(255,106,26,0.30), inset 0 -4px 0 rgba(0,0,0,0.20);
    border: 2px solid rgba(199,204,214,0.20);
    z-index: 1;
}
.lddl-profile .info { z-index: 1; flex: 1; }
.lddl-profile .info h1 {
    font-family: 'Bebas Neue', sans-serif;
    font-size: 42px;
    margin: 0;
    letter-spacing: 1.8px;
    color: var(--c-text);
    line-height: 1;
}
.lddl-profile .info .sub {
    color: var(--c-muted);
    font-size: 13px;
    margin-top: 6px;
    font-family: 'JetBrains Mono', monospace;
}
.lddl-profile .info .badges { display: flex; gap: 6px; margin-top: 12px; flex-wrap: wrap; }
.lddl-profile .info .pill {
    background: rgba(255,255,255,0.06);
    border: 1px solid var(--c-border);
    color: var(--c-text);
    padding: 5px 12px;
    border-radius: 999px;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.9px;
    font-weight: 600;
    font-family: 'Oswald', sans-serif;
}
.lddl-profile .info .pill.gold { background: linear-gradient(90deg, #F5B642, #E89B16); color: #1a1306; border-color: transparent; }
.lddl-profile .info .pill.red  { background: linear-gradient(90deg, #FF5C7C, #C03050); color: #fff; border-color: transparent; }
.lddl-profile .info .pill.green { background: linear-gradient(90deg, #2EE090, #1FA86A); color: #062416; border-color: transparent; }

/* ==================== Trade-detail two-column ==================== */
.lddl-side {
    background: linear-gradient(180deg, var(--c-surface) 0%, #131520 100%);
    border: 1px solid var(--c-border);
    border-left: 4px solid var(--accent, var(--c-orange));
    border-radius: 14px;
    padding: 16px;
    height: 100%;
}
.lddl-side .h {
    font-family: 'Oswald', sans-serif;
    font-size: 18px;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    color: var(--c-text);
    margin-bottom: 4px;
    font-weight: 700;
}
.lddl-side .sub {
    color: var(--c-muted);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1.1px;
    margin-bottom: 12px;
    font-family: 'JetBrains Mono', monospace;
}
.lddl-side .net.pos { color: var(--c-success); font-weight: 700; }
.lddl-side .net.neg { color: var(--c-danger); font-weight: 700; }

/* ==================== News ticker ==================== */
.lddl-ticker {
    width: 100%;
    overflow: hidden;
    background: linear-gradient(90deg, #08090C 0%, #14161E 50%, #08090C 100%);
    border: 1px solid var(--c-border);
    padding: 9px 0;
    margin: 0 0 18px 0;
    position: relative;
    border-radius: 12px;
    box-shadow: inset 0 0 24px rgba(0,0,0,0.5);
}
.lddl-ticker::before {
    content: '● LIVE';
    position: absolute;
    left: 0; top: 0; bottom: 0;
    display: flex; align-items: center;
    padding: 0 14px;
    background: linear-gradient(135deg, #FF6A1A, #E65100);
    color: #fff;
    font-family: 'Oswald', sans-serif;
    font-weight: 700;
    letter-spacing: 1.5px;
    font-size: 12px;
    z-index: 2;
    box-shadow: 4px 0 14px rgba(0,0,0,0.7);
}
.lddl-ticker-track {
    display: inline-block;
    white-space: nowrap;
    padding-left: 100px;
    animation: lddl-ticker-scroll 331s linear infinite;
    color: #ECEDF1;
    font-family: 'JetBrains Mono', monospace;
    font-size: 13px;
    font-weight: 500;
}
.lddl-ticker:hover .lddl-ticker-track { animation-play-state: paused; }
@keyframes lddl-ticker-scroll {
    0%   { transform: translate3d(0, 0, 0); }
    100% { transform: translate3d(-50%, 0, 0); }
}
.lddl-ticker-track .dot { color: #FF6A1A; margin: 0 14px; }

/* ==================== Footer ==================== */
.lddl-footer {
    margin-top: 40px;
    padding: 20px;
    text-align: center;
    color: var(--c-muted);
    font-size: 11px;
    letter-spacing: 0.6px;
    border-top: 1px solid var(--c-border);
    font-family: 'JetBrains Mono', monospace;
}
</style>
"""

st.markdown(CSS, unsafe_allow_html=True)


# ---------- Altair theme ----------------------------------------------------

def _altair_theme():
    return {
        "config": {
            "background": "transparent",
            "view": {"stroke": "transparent"},
            "axis": {
                "domain": False,
                "grid": True,
                "gridColor": "#2A2E3B",
                "gridOpacity": 0.45,
                "tickColor": "#8C92A4",
                "labelColor": "#8C92A4",
                "titleColor": "#ECEDF1",
                "labelFont": "Inter",
                "titleFont": "Oswald",
                "labelFontSize": 11,
                "titleFontSize": 12,
                "titleFontWeight": 600,
            },
            "legend": {
                "labelColor": "#ECEDF1",
                "titleColor": "#8C92A4",
                "labelFont": "Inter",
                "titleFont": "Oswald",
                "titleFontSize": 11,
                "labelFontSize": 11,
            },
            "header": {"labelColor": "#ECEDF1", "titleColor": "#ECEDF1"},
            "title": {
                "color": "#ECEDF1",
                "font": "Oswald",
                "fontSize": 16,
                "fontWeight": 700,
            },
            "range": {
                "category": [
                    PALETTE["orange"], PALETTE["cyan"], PALETTE["purple"],
                    PALETTE["gold"], PALETTE["success"], PALETTE["silver"],
                    PALETTE["magenta"], PALETTE["lime"], PALETTE["blue"],
                ],
                "diverging": [PALETTE["danger"], "#5A6070", PALETTE["success"]],
                "ramp": [PALETTE["orange_deep"], PALETTE["orange"], PALETTE["gold"]],
            },
        }
    }


alt.themes.register("lddl", _altair_theme)
alt.themes.enable("lddl")

# Brand-colored chart constants
C_POS = PALETTE["success"]
C_NEG = PALETTE["danger"]
C_PRIMARY = PALETTE["orange"]
C_SECONDARY = PALETTE["cyan"]


# ---------- Cached data accessors -------------------------------------------

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
                    "manager": side.display_name,
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
            "manager": g.picked_by_display_name,
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


@st.cache_data
def cached_archetypes_and_rosters():
    conn = get_conn()
    rosters = current_rosters(conn)
    archetypes = classify_managers(conn, rosters)
    return archetypes, rosters


# ---------- News ticker -----------------------------------------------------

NEWS_HEADLINES: list[str] = [
    "sleepthesenuts named LDDL Manager of the Decade in unanimous panel vote",
    "Hung Dynasty franchise valuation soars past $400M after another flawless waiver week",
    "BREAKING: sleepthesenuts becomes first manager in league history to draft three first-round steals back-to-back-to-back",
    "Local children name sleepthesenuts honorary godfather following yet another generous trade offer",
    "Insiders confirm sleepthesenuts widely considered most handsome man in LDDL, possibly Western Hemisphere",
    "sleepthesenuts releases inspirational memoir 'Hung Like a Champion' — debuts at #1 on NYT bestseller list",
    "League office formally requests sleepthesenuts stop being so good 'for competitive balance reasons'",
    "ESPN insider: sleepthesenuts'd lineup is 'the cleanest start/sit decision tree I have ever audited'",
    "Stephen Torterlo's lineup formally classified by NFL analysts as 'objectively unwatchable'",
    "bigtort benched ALL TIME after forgetting login credentials for fourth straight Sunday",
    "Sources: Stephen Torterlo's draft strategy is just clicking whichever name is highlighted",
    "League office fines Stephen Torterlo undisclosed amount for 'aggressive mediocrity'",
    "bigtort removed from group chats after openly admitting he 'doesn't really watch the games'",
    "Stephen Torterlo's franchise dropped from power rankings after pollsters cite 'embarrassment factor'",
    "Investigators link bigtort's start/sit decisions to mild seasonal depression in opposing managers forced to spectate",
    "BREAKING: Stephen Torterlo trades RB1 for a kicker, claims he 'thought it was a package deal'",
    "Tony 'Two Trigs' Lasagne caught running illicit pasta-laundering operation through the waiver wire",
    "tdeblis files trademark on 'Make America Trade Again' — receives cease and desist within 11 minutes",
    "DeStarz manager spotted pacing CVS parking lot at 2am muttering about 'ceiling vs floor'",
    "Sultans of Schlong owner gideontamir16 unveils new uniform: just a gym sock and a smile",
    "Roster Gymnastics manager patd96 hospitalized after attempting to bench his own QB mid-snap",
    "Smeisman Life: JN55 caught handing out homemade Heisman ballots at a Wendy's drive-thru",
    "harold and kumar GM santoshmorasa adjourns trade negotiation for emergency White Castle run, deal collapses",
    "Robin's dirty lil boy adamisraeli releases statement clarifying nickname 'is mostly metaphorical, allegedly'",
    "The Shining Path's carricki officially classified by league office as 'simply a vibe'",
    "Presti's Love Child manager jchiang17 demands paternity test — Sam Presti declines all comment",
    "Jake&Meis owner jakesilverman1 caught FaceTiming opponent's wife during the Sunday early window",
    "League-wide investigation opened after eight managers simultaneously claim 'my guy was supposed to be active'",
    "Anonymous source: 'Half of these rosters look like they were drafted by raccoons.' League office: 'No comment.'",
]


def render_news_ticker() -> None:
    rng = random.Random()
    headlines = NEWS_HEADLINES.copy()
    rng.shuffle(headlines)
    safe = [html.escape(h) for h in headlines]
    sep = '<span class="dot">◆</span>'
    body = sep.join(safe) + sep
    st.markdown(
        f'<div class="lddl-ticker"><div class="lddl-ticker-track">'
        f'<span>{body}</span><span>{body}</span></div></div>',
        unsafe_allow_html=True,
    )


# ---------- UI helpers ------------------------------------------------------

def section_header(title: str, subtitle: str | None = None) -> None:
    sub_html = (
        f'<div class="sub">{html.escape(subtitle)}</div>' if subtitle else ""
    )
    st.markdown(
        f'<div class="lddl-section"><div class="bar"></div>'
        f'<h2>{html.escape(title)}</h2>{sub_html}</div>',
        unsafe_allow_html=True,
    )


def render_hero(meta: dict, snap: dict, n_managers: int, n_seasons: int) -> None:
    league_name = (meta.get("name") or "LDDL") if meta else "LDDL"
    season = str(meta.get("season", "—")) if meta else "—"
    status = str(meta.get("status", "—")) if meta else "—"
    snap_date = (
        snap["latest_date"].isoformat()
        if snap and snap.get("latest_date") else "—"
    )
    n_dates = snap.get("n_dates", 0) if snap else 0
    n_players = snap.get("n_players_in_latest", 0) if snap else 0

    st.markdown(
        _h(f"""
        <div class="lddl-hero">
          <div class="brand">
            <div class="crest">🏈</div>
            <div>
              <div class="logo">{html.escape(league_name)}</div>
              <div class="tagline">Dynasty · Degenerates · Data</div>
            </div>
          </div>
          <div class="meta">
            <div class="chip"><span class="lbl">Season</span>
              <span class="val">{html.escape(season)}</span>
              <span style="color:#8C92A4;font-size:10px;margin-left:6px;">{html.escape(status)}</span>
            </div>
            <div class="chip"><span class="lbl">Franchises</span>
              <span class="val">{n_managers}</span>
              <span style="color:#8C92A4;font-size:10px;margin-left:6px;">{n_seasons} seasons</span>
            </div>
            <div class="chip"><span class="lbl">Snapshot</span>
              <span class="val">{html.escape(snap_date)}</span>
              <span style="color:#8C92A4;font-size:10px;margin-left:6px;">{n_dates}d · {n_players} players</span>
            </div>
          </div>
        </div>
        """),
        unsafe_allow_html=True,
    )


def render_manager_grid(cards) -> None:
    items = []
    # Sort: champions first, then by total wins
    cards_sorted = sorted(
        cards,
        key=lambda c: (-c.championships, -c.total_wins),
    )
    for c in cards_sorted:
        accent = accent_for(c.display_name)
        primary_team = c.team_names[0] if c.team_names else ""
        badges = ""
        if c.championships > 0:
            badges += (
                f'<div class="badge champ" title="{c.championships}× champion">'
                f'{"🏆" if c.championships == 1 else f"🏆×{c.championships}"}</div>'
            )
        if c.last_places > 0:
            badges += (
                f'<div class="badge last" title="{c.last_places}× last place">💩</div>'
            )
        items.append(_h(f"""
        <div class="lddl-mgr" style="--accent: {accent};">
          <div class="badges">{badges}</div>
          <div class="row">
            <div class="avatar">{html.escape(initials(c.display_name))}</div>
            <div style="min-width:0;">
              <div class="name">{html.escape(c.display_name)}</div>
              <div class="team">{html.escape(primary_team)}</div>
            </div>
          </div>
          <div class="stats">
            <div class="stat"><div class="n">{c.total_wins}-{c.total_losses}</div><div class="l">Reg W-L</div></div>
            <div class="stat"><div class="n">{c.championships}</div><div class="l">Rings</div></div>
            <div class="stat"><div class="n">{c.n_trades}</div><div class="l">Trades</div></div>
          </div>
        </div>
        """))
    st.markdown(
        f'<div class="lddl-mgr-grid">{"".join(items)}</div>',
        unsafe_allow_html=True,
    )


def render_podium(rows: list[tuple], variant: str = "champ") -> None:
    """rows: list of (year, name, team_name)."""
    cls = "" if variant == "champ" else " last"
    icon = "🏆" if variant == "champ" else "💩"
    items = []
    for year, name, team in sorted(rows, reverse=True):
        accent = accent_for(name)
        team_html = (
            f'<div class="team">"{html.escape(team)}"</div>' if team else ""
        )
        items.append(_h(f"""
        <div class="lddl-row{cls}">
          <div class="yr">{html.escape(str(year))}</div>
          <div class="mini-av" style="background:{accent};">{html.escape(initials(name))}</div>
          <div class="name">{html.escape(name)}</div>
          {team_html}
          <div class="icon">{icon}</div>
        </div>
        """))
    st.markdown(
        f'<div class="lddl-podium">{"".join(items)}</div>',
        unsafe_allow_html=True,
    )


def render_manager_profile(c) -> None:
    accent = accent_for(c.display_name)
    aliases = ", ".join(x for x in c.aliases if x != c.display_name)
    teams = ", ".join(c.team_names) or "—"
    badges = []
    if c.championships > 0:
        badges.append(
            f'<span class="pill gold">🏆 {c.championships}× Champion</span>'
        )
    if c.last_places > 0:
        badges.append(
            f'<span class="pill red">💩 {c.last_places}× Last</span>'
        )
    if c.luck > 5:
        badges.append('<span class="pill green">🍀 Lucky</span>')
    elif c.luck < -5:
        badges.append('<span class="pill red">💀 Unlucky</span>')
    badges.append(
        f'<span class="pill">📅 {html.escape(c.first_seen_season)}–{html.escape(c.last_seen_season)}</span>'
    )

    sub_parts = []
    if aliases:
        sub_parts.append(f"AKA {html.escape(aliases)}")
    sub_parts.append(f"Teams: {html.escape(teams)}")

    st.markdown(
        _h(f"""
        <div class="lddl-profile" style="--accent: {accent};">
          <div class="av">{html.escape(initials(c.display_name))}</div>
          <div class="info">
            <h1>{html.escape(c.display_name)}</h1>
            <div class="sub">{' · '.join(sub_parts)}</div>
            <div class="badges">{''.join(badges)}</div>
          </div>
        </div>
        """),
        unsafe_allow_html=True,
    )


def render_footer() -> None:
    st.markdown(
        '<div class="lddl-footer">'
        'LDDL · Built on Sleeper + FantasyCalc data · '
        'Trade and draft grades are <em>directional</em>, not authoritative · '
        'Made with caffeine and bad takes 🏈'
        '</div>',
        unsafe_allow_html=True,
    )


# ---------- Sidebar (slim) --------------------------------------------------

meta = cached_league_meta()
snap = cached_snapshot_summary()
with st.sidebar:
    st.markdown("### 🏈 LDDL")
    if meta:
        st.markdown(
            f"**{meta.get('name', 'LDDL')}**  \n"
            f"Season {meta.get('season')} · {meta.get('status')}"
        )
    if snap:
        st.markdown(
            f"**Snapshot:** {snap['latest_date']}  \n"
            f"{snap['n_dates']}d FC history  \n"
            f"{snap['n_players_in_latest']} players · {snap['n_picks_in_latest']} picks"
        )
    else:
        st.warning("No FC snapshots yet. Run `lddl snapshot`.")
    st.caption(
        "FantasyCalc values are crowdsourced approximations. "
        "Trade and draft grades are directional, not authoritative."
    )

# ---------- Hero + ticker (above tabs) --------------------------------------

cards_all = cached_manager_cards()
seasons_all = cached_seasons()
render_hero(meta, snap, len(cards_all), len(seasons_all))
render_news_ticker()

# ---------- Tabs ------------------------------------------------------------

overview, managers, trades, trade_recs, drafts, snapshots = st.tabs(
    ["Overview", "Managers", "Trades", "Trade Recs", "Drafts", "Snapshots"]
)

# ============================================================================
# OVERVIEW
# ============================================================================

with overview:
    cards = cached_manager_cards()
    seasons = cached_seasons()
    trades_df = cached_all_trades_df()

    n_trades = (
        trades_df[trades_df["is_faab_only"] == False]
        .drop_duplicates("transaction_id").shape[0]
        if not trades_df.empty else 0
    )
    n_champs = sum(c.championships for c in cards)

    section_header("League Pulse", subtitle="At a glance")
    cols = st.columns(4)
    cols[0].metric("Seasons", len(seasons))
    cols[1].metric("Active franchises", len(cards))
    cols[2].metric("Trades graded", n_trades)
    cols[3].metric("Rings handed out", n_champs)

    section_header("The 12 Franchises", subtitle="Hover for accent · 🏆 champ · 💩 last")
    render_manager_grid(cards)

    col_l, col_r = st.columns(2)
    with col_l:
        section_header("Champions", subtitle="🏆")
        champ_rows = []
        for c in cards:
            for s in c.seasons:
                if s.is_champion:
                    champ_rows.append((s.season, c.display_name, s.team_name or ""))
        if champ_rows:
            render_podium(champ_rows, variant="champ")
        else:
            st.info("No champions on file yet.")
    with col_r:
        section_header("Last Place", subtitle="💩")
        last_rows = []
        for c in cards:
            for s in c.seasons:
                if s.is_last_place:
                    last_rows.append((s.season, c.display_name, ""))
        if last_rows:
            render_podium(last_rows, variant="last")
        else:
            st.info("No last-place finishes on file yet.")

    section_header("All-time wins vs points-for", subtitle="Bigger circle = more rings")
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
        pf_min, pf_max = float(df["pf"].min()), float(df["pf"].max())
        w_min, w_max = float(df["wins"].min()), float(df["wins"].max())
        pf_pad = max((pf_max - pf_min) * 0.08, 50.0)
        w_pad = max((w_max - w_min) * 0.10, 2.0)

        circle = alt.Chart(df).mark_circle(opacity=0.85).encode(
            x=alt.X(
                "pf:Q", title="All-time PF",
                scale=alt.Scale(
                    domain=[pf_min - pf_pad, pf_max + pf_pad],
                    nice=False, zero=False,
                ),
            ),
            y=alt.Y(
                "wins:Q", title="Regular-season wins",
                scale=alt.Scale(
                    domain=[w_min - w_pad, w_max + w_pad],
                    nice=False, zero=False,
                ),
            ),
            size=alt.Size(
                "championships:Q", title="Rings",
                scale=alt.Scale(range=[140, 600]),
            ),
            color=alt.Color(
                "color:N",
                scale=alt.Scale(
                    domain=["champion", "last_place", "neutral"],
                    range=[PALETTE["gold"], PALETTE["danger"], PALETTE["silver"]],
                ),
                legend=None,
            ),
            tooltip=[
                "manager", "wins", "losses", "pf", "championships",
                "last_places", "luck", "trades",
            ],
        )
        labels = alt.Chart(df).mark_text(
            dx=12, dy=-10, fontSize=11, color=PALETTE["text"],
            font="Inter", fontWeight=500, align="left",
        ).encode(x="pf:Q", y="wins:Q", text="manager")
        st.altair_chart(
            (circle + labels).properties(height=480),
            use_container_width=True,
        )

    if not trades_df.empty:
        section_header("Recent trades", subtitle="Latest 8")
        recent = (
            trades_df[trades_df["is_faab_only"] == False]
            .drop_duplicates("transaction_id")
            .sort_values("trade_date", ascending=False)
            .head(8)
        )
        items = []
        for _, r in recent.iterrows():
            sides = trades_df[trades_df["transaction_id"] == r["transaction_id"]]
            who = " ↔ ".join(sides["manager"].tolist())
            items.append(
                f'<div class="lddl-trade">'
                f'<div class="dt">{r["trade_date"].strftime("%Y-%m-%d")}</div>'
                f'<div class="seas">{html.escape(str(r["season"]))}</div>'
                f'<div class="pts">{html.escape(who)}</div>'
                f'<div class="nways">{int(r["n_parties"])}-way</div>'
                f'</div>'
            )
        st.markdown("".join(items), unsafe_allow_html=True)

# ============================================================================
# MANAGERS
# ============================================================================

with managers:
    cards = cached_manager_cards()
    name_to_card = {c.display_name: c for c in cards}
    sel = st.selectbox("Select franchise", list(name_to_card.keys()))
    c = name_to_card[sel]

    render_manager_profile(c)

    section_header("Career stats")
    cols = st.columns(6)
    cols[0].metric("Reg W-L-T", f"{c.total_wins}-{c.total_losses}-{c.total_ties}")
    cols[1].metric("Playoff", f"{c.total_playoff_wins}-{c.total_playoff_losses}")
    cols[2].metric("Luck", f"{c.luck:+.0f}")
    cols[3].metric("PF", f"{c.total_fpts:.0f}")
    cols[4].metric("PA", f"{c.total_fpts_against:.0f}")
    cols[5].metric("Trades", c.n_trades)

    section_header("Per-season detail")
    df = pd.DataFrame([
        {
            "Season": s.season,
            "Status": s.league_status,
            "W-L-T": f"{s.wins}-{s.losses}-{s.ties}",
            "PF": round(s.fpts, 1),
            "PA": round(s.fpts_against, 1),
            "Exp W": round(s.expected_wins, 1),
            "Luck": round(s.wins - s.expected_wins, 1),
            "Playoff": f"{s.playoff_wins}-{s.playoff_losses}",
            "Champ": "🏆" if s.is_champion else "",
            "Last": "💩" if s.is_last_place else "",
            "Trades": s.n_trades,
            "Trade Δ": s.trade_net_value,
        }
        for s in c.seasons
    ])
    st.dataframe(df, hide_index=True, use_container_width=True)

    section_header("PF vs PA across seasons")
    if c.seasons:
        bar_df = pd.DataFrame([
            {"season": s.season, "type": "PF", "value": s.fpts}
            for s in c.seasons
        ] + [
            {"season": s.season, "type": "PA", "value": s.fpts_against}
            for s in c.seasons
        ])
        bar = alt.Chart(bar_df).mark_bar(cornerRadius=3).encode(
            x=alt.X("season:O"),
            xOffset="type:N",
            y="value:Q",
            color=alt.Color(
                "type:N",
                scale=alt.Scale(
                    domain=["PF", "PA"],
                    range=[PALETTE["orange"], PALETTE["purple"]],
                ),
            ),
            tooltip=["season", "type", "value"],
        ).properties(height=320)
        st.altair_chart(bar, use_container_width=True)

    if c.trades:
        section_header("Top trades by absolute net value")
        trade_df = pd.DataFrame([
            {
                "date": (t.trade_date.date() if t.trade_date else None),
                "season": t.season,
                "net Δ": t.net_value,
                "won": "✅" if t.won else "❌",
                "n_other_parties": t.n_other_parties,
                "transaction_id": t.transaction_id,
            }
            for t in sorted(c.trades, key=lambda x: -abs(x.net_value))
        ])
        st.dataframe(trade_df, hide_index=True, use_container_width=True)

# ============================================================================
# TRADES
# ============================================================================

with trades:
    seasons = cached_seasons()
    df = cached_all_trades_df()

    if df.empty:
        st.info("No trades graded yet.")
    else:
        section_header("Filter", subtitle="Slice the trade history")
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

        st.markdown(
            f'<div style="color: var(--c-muted); font-family: \'JetBrains Mono\', monospace; '
            f'font-size: 12px; margin-top: 6px;">'
            f'<span style="color: var(--c-orange); font-weight: 700;">{view["transaction_id"].nunique()}</span> trades'
            f' · <span style="color: var(--c-cyan); font-weight: 700;">{len(view)}</span> sides'
            f'</div>',
            unsafe_allow_html=True,
        )

        section_header("All trades")
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

        section_header("Trade-value leaderboard", subtitle="(filtered)")
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

            bar = alt.Chart(leaderboard).mark_bar(cornerRadius=4).encode(
                x=alt.X("Total Δ:Q", title="Total trade Δ at current FC values"),
                y=alt.Y("Franchise:N", sort="-x"),
                color=alt.condition(
                    "datum['Total Δ'] >= 0",
                    alt.value(PALETTE["success"]),
                    alt.value(PALETTE["danger"]),
                ),
                tooltip=list(leaderboard.columns),
            ).properties(height=24 * len(leaderboard) + 40)
            st.altair_chart(bar, use_container_width=True)
            st.dataframe(leaderboard, hide_index=True, use_container_width=True)

        section_header("Trade volume by season")
        per_season = (
            view[view["is_faab_only"] == False]
            .drop_duplicates("transaction_id")
            .groupby("season").size().reset_index(name="n")
        )
        if not per_season.empty:
            chart = alt.Chart(per_season).mark_bar(
                color=PALETTE["orange"], cornerRadius=4,
            ).encode(
                x="season:O", y=alt.Y("n:Q", title="# trades"),
                tooltip=["season", "n"],
            ).properties(height=240)
            st.altair_chart(chart, use_container_width=True)

        section_header("Trade detail", subtitle="Expand a single transaction")
        tx_options = (
            view.sort_values("trade_date", ascending=False)["transaction_id"]
            .drop_duplicates().tolist()
        )
        if tx_options:
            tx_sel = st.selectbox(
                "Pick a transaction", tx_options,
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
                        accent = accent_for(side.display_name)
                        net = side.net_now()
                        net_cls = "pos" if net > 0 else ("neg" if net < 0 else "")
                        with col:
                            st.markdown(
                                _h(f"""
                                <div class="lddl-side" style="--accent: {accent};">
                                  <div class="h">{html.escape(side.display_name)}</div>
                                  <div class="sub">Roster {side.roster_id} · in {side.value_in_now():,} · out {side.value_out_now():,}</div>
                                  <div class="net {net_cls}" style="font-family: 'Bebas Neue', sans-serif; font-size: 30px; line-height: 1; margin-bottom: 8px;">{net:+,}</div>
                                </div>
                                """),
                                unsafe_allow_html=True,
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
                            st.dataframe(
                                pd.DataFrame(rows), hide_index=True,
                                use_container_width=True,
                            )

# ============================================================================
# TRADE RECOMMENDATIONS
# ============================================================================

with trade_recs:
    archetypes, rosters_by_uid = cached_archetypes_and_rosters()

    if not archetypes:
        st.info(
            "Run `lddl ingest` and `lddl snapshot` first — recommendations need both "
            "current rosters and a FantasyCalc snapshot to score players."
        )
    else:
        st.subheader("Team archetypes")
        st.caption(
            "Each manager is bucketed into Contender / Middler / Rebuilder using "
            "value-weighted average roster age and last completed regular-season "
            "record. Contenders are the league's older-and-winning teams; "
            "rebuilders are the younger-and-losing ones."
        )

        archetype_order = {"Contender": 0, "Middler": 1, "Rebuilder": 2}
        arch_df = pd.DataFrame([
            {
                "Manager": a.display_name,
                "Archetype": a.archetype,
                "Avg Age (wgtd)": round(a.avg_age_weighted, 1),
                "Last Reg Record": f"{a.recent_wins}-{a.recent_losses}",
                "Roster Value": int(a.total_roster_value),
                "# Players (FC-ranked)": a.n_rostered,
            }
            for a in sorted(
                archetypes.values(),
                key=lambda x: (
                    archetype_order.get(x.archetype, 99),
                    -x.avg_age_weighted,
                ),
            )
        ])
        st.dataframe(
            arch_df,
            hide_index=True,
            use_container_width=True,
            column_config={
                "Roster Value": st.column_config.NumberColumn(format="%d"),
            },
        )

        st.markdown("---")
        st.subheader("Recommendation settings")
        cols = st.columns(4)
        young_max = cols[0].slider(
            "Max age — 'young upside'", 22.0, 28.0, 25.0, step=0.5,
            help="Contenders give players UNDER this age",
        )
        old_min = cols[1].slider(
            "Min age — 'old vet'", 26.0, 32.0, 28.0, step=0.5,
            help="Rebuilders give players AT or ABOVE this age",
        )
        min_val = cols[2].slider(
            "Min asset value (FC)", 0, 5000, 1500, step=100,
            help="Skip swaps where either side is worth less than this",
        )
        tol_pct = cols[3].slider(
            "Balance tolerance (% diff)", 5, 50, 20, step=1,
            help="Largest allowed value gap between the two sides",
        )

        recs = recommend_trades(
            rosters_by_uid,
            archetypes,
            young_age_max=young_max,
            old_age_min=old_min,
            min_value=min_val,
            balance_tolerance=tol_pct / 100.0,
            max_recs=40,
        )

        st.markdown("---")
        st.subheader(f"Recommendations · {len(recs)} balanced 1-for-1 swaps")
        st.caption(
            "Sorted by fit score (70% balance + 30% age gap). Each swap pairs a "
            "rebuilder's older productive vet with a contender's younger upside "
            "player at roughly equal FantasyCalc dynasty value. Picks aren't "
            "modelled here — these are pure player swaps."
        )

        if not recs:
            st.info(
                "No swaps match the current settings. Try widening tolerance or "
                "lowering the min asset value."
            )
        else:
            def _asset_str(a) -> str:
                age_s = f"{a.age:.1f}" if a.age is not None else "?"
                return f"{a.name} ({a.position}, age {age_s}, {a.value:,})"

            rec_rows = []
            for r in recs:
                rec_rows.append({
                    "Fit": round(r.fit_score, 2),
                    "Contender": r.contender_name,
                    "Cont. gives": _asset_str(r.contender_gives),
                    "Rebuilder": r.rebuilder_name,
                    "Reb. gives": _asset_str(r.rebuilder_gives),
                    "Δ value": r.value_diff,
                    "Age gap": round(r.age_gap, 1),
                })
            st.dataframe(
                pd.DataFrame(rec_rows),
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Fit": st.column_config.ProgressColumn(
                        format="%.2f", min_value=0.0, max_value=1.0,
                    ),
                    "Δ value": st.column_config.NumberColumn(format="%+d"),
                    "Age gap": st.column_config.NumberColumn(format="%+.1f"),
                },
            )

        st.markdown("---")
        st.caption(
            "FantasyCalc values are crowdsourced approximations — these are "
            "starting points for conversation, not authoritative offers. The "
            "engine considers only currently-rostered, FC-ranked players; "
            "picks, FAAB, and unranked depth pieces are excluded from v1."
        )


# ============================================================================
# DRAFTS
# ============================================================================

with drafts:
    df = cached_pick_grades_df()
    if df.empty:
        st.info("No 3-round rookie draft data yet.")
    else:
        years = sorted(df["season"].unique())
        sel_year = st.selectbox("Season", years, index=len(years) - 1)
        view = df[df["season"] == sel_year].copy().sort_values("pick_no")

        section_header(f"{sel_year} draft board")
        st.dataframe(
            view[
                ["pick_no", "round", "slot", "manager", "player",
                 "actual", "expected", "delta"]
            ],
            hide_index=True,
            use_container_width=True,
        )

        section_header("Pick value vs expected", subtitle="Δ vs slot median")
        scatter = alt.Chart(view).mark_circle(size=140, opacity=0.85).encode(
            x=alt.X("pick_no:Q", title="Pick number"),
            y=alt.Y("delta:Q", title="Δ vs slot median"),
            color=alt.condition(
                "datum.delta >= 0",
                alt.value(PALETTE["success"]),
                alt.value(PALETTE["danger"]),
            ),
            tooltip=["pick_no", "manager", "player", "actual",
                     "expected", "delta"],
        )
        rule = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(
            color=PALETTE["muted"], strokeDash=[4, 4],
        ).encode(y="y:Q")
        st.altair_chart(
            (scatter + rule).properties(height=340),
            use_container_width=True,
        )

        c_l, c_r = st.columns(2)
        with c_l:
            section_header("Steals", subtitle="Top +Δ")
            st.dataframe(
                view.nlargest(10, "delta")[
                    ["pick_no", "manager", "player", "actual", "expected", "delta"]
                ],
                hide_index=True, use_container_width=True,
            )
        with c_r:
            section_header("Reaches", subtitle="Top -Δ")
            st.dataframe(
                view.nsmallest(10, "delta")[
                    ["pick_no", "manager", "player", "actual", "expected", "delta"]
                ],
                hide_index=True, use_container_width=True,
            )

        section_header("Cumulative draft grade", subtitle="All seasons, by franchise")
        agg = df.groupby("franchise").agg(
            picks=("pick_no", "count"),
            avg_delta=("delta", "mean"),
            total_delta=("delta", "sum"),
        ).round(1).reset_index().sort_values("avg_delta", ascending=False)
        st.dataframe(agg, hide_index=True, use_container_width=True)

# ============================================================================
# SNAPSHOTS
# ============================================================================

with snapshots:
    df = cached_top_assets_df(top_n=100)
    if df.empty:
        st.info("No snapshots yet — run `lddl snapshot`.")
    else:
        section_header(
            f"Top assets",
            subtitle=f"Snapshot {df['snapshot_date'].iloc[0]}",
        )
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

        section_header("Top movers", subtitle="30-day trend")
        movers = view.dropna(subset=["trend_30d"]).copy()
        if not movers.empty:
            movers = pd.concat([
                movers.nlargest(8, "trend_30d"),
                movers.nsmallest(8, "trend_30d"),
            ])
            chart = alt.Chart(movers).mark_bar(cornerRadius=4).encode(
                x=alt.X("trend_30d:Q", title="30-day trend"),
                y=alt.Y("name:N", sort="-x"),
                color=alt.condition(
                    "datum.trend_30d >= 0",
                    alt.value(PALETTE["success"]),
                    alt.value(PALETTE["danger"]),
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

# ---------- Footer ----------------------------------------------------------

render_footer()
