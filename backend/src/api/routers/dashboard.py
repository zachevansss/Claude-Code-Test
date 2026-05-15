"""Read-only dashboard.

Routes:
  GET /dashboard       — modern Robinhood-style HTML dashboard (new default)
  GET /dashboard/text  — legacy monospace text-card view (fallback for CLI parity)
  GET /dashboard.json  — structured data feed used by the HTML dashboard

The HTML dashboard fetches /dashboard.json on load and on a polling interval,
so the page never does a hard refresh — numbers tick in place. The text version
is kept around in case the JSON view ever breaks; it talks straight to stats.py.

Intentionally unauthenticated. Reachable only via the Tailscale subnet
(100.64.0.0/10) per ufw rules on the VPS — never exposed to the open internet.
"""
from __future__ import annotations

import html
import os
import re
import sqlite3
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, JSONResponse

# stats.py lives at backend/stats.py — same dir uvicorn runs from.
import stats  # type: ignore

from src.api.routers.dashboard_data import compute_dashboard_data

router = APIRouter()

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(stats.__file__)), "copytrade.db")


# ============================================================================
# JSON endpoint
# ============================================================================

@router.get("/dashboard.json")
def dashboard_json(
    mode: str = Query("paper", pattern="^(paper|live)$"),
    no_prices: bool = Query(False),
) -> JSONResponse:
    if not os.path.exists(_DB_PATH):
        return JSONResponse({"error": "db not found"}, status_code=500)
    with sqlite3.connect(_DB_PATH) as con:
        data = compute_dashboard_data(con, mode=mode, skip_prices=no_prices)
    return JSONResponse(data)


# ============================================================================
# Modern HTML dashboard
# ============================================================================

@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_html(
    mode: str = Query("paper", pattern="^(paper|live)$"),
    refresh: int = Query(5, ge=2, le=60),
) -> HTMLResponse:
    if not os.path.exists(_DB_PATH):
        return HTMLResponse("<h1>db not found</h1>", status_code=500)
    return HTMLResponse(_DASHBOARD_HTML.replace("{{MODE}}", mode).replace("{{REFRESH_MS}}", str(refresh * 1000)))


_DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<meta name="theme-color" content="#0d1117">
<title>Bot Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;600&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #0a0e15;
    --bg-elev: #0d1117;
    --card: #161b22;
    --card-2: #1c2128;
    --border: #30363d;
    --border-soft: #21262d;
    --text: #e6edf3;
    --text-2: #8b949e;
    --text-3: #6e7681;
    --pos: #3fb950;
    --pos-soft: rgba(63, 185, 80, 0.12);
    --neg: #f85149;
    --neg-soft: rgba(248, 81, 73, 0.12);
    --accent: #58a6ff;
    --warn: #d29922;
    --shadow: 0 1px 0 rgba(255,255,255,0.04), 0 4px 12px rgba(0,0,0,0.32);
  }

  * { box-sizing: border-box; }
  html, body {
    margin: 0;
    padding: 0;
    background: var(--bg);
    color: var(--text);
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-feature-settings: 'cv11', 'ss03', 'ss04';
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
    line-height: 1.5;
  }
  body { min-height: 100vh; }
  .mono { font-family: 'JetBrains Mono', ui-monospace, "SF Mono", Consolas, monospace; }
  .num  { font-variant-numeric: tabular-nums; }

  .container {
    max-width: 1280px;
    margin: 0 auto;
    padding: 24px;
  }

  /* ───── Top bar ───── */
  .topbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 24px;
    gap: 16px;
    flex-wrap: wrap;
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .brand-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--accent);
    box-shadow: 0 0 12px var(--accent);
  }
  .brand-name {
    font-weight: 700;
    font-size: 16px;
    letter-spacing: -0.01em;
  }
  .brand-sub {
    color: var(--text-3);
    font-size: 13px;
    font-weight: 500;
  }
  .pill {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 6px 12px;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 999px;
    font-size: 12px;
    font-weight: 600;
    color: var(--text-2);
  }
  .pill .status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--text-3);
  }
  .pill.ok    .status-dot { background: var(--pos); box-shadow: 0 0 8px var(--pos); }
  .pill.warn  .status-dot { background: var(--warn); box-shadow: 0 0 8px var(--warn); }
  .pill.error .status-dot { background: var(--neg); box-shadow: 0 0 8px var(--neg); }
  .pill .mode { text-transform: uppercase; letter-spacing: 0.08em; }

  /* ───── Hero card ───── */
  .hero {
    background: linear-gradient(180deg, var(--card-2) 0%, var(--card) 100%);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 32px;
    margin-bottom: 16px;
    box-shadow: var(--shadow);
    position: relative;
    overflow: hidden;
  }
  .hero-label {
    font-size: 13px;
    color: var(--text-2);
    font-weight: 500;
    margin-bottom: 8px;
    letter-spacing: 0.01em;
  }
  .hero-value {
    font-size: 56px;
    font-weight: 700;
    letter-spacing: -0.03em;
    line-height: 1.05;
    color: var(--text);
  }
  .hero-pnl {
    display: flex;
    align-items: baseline;
    gap: 12px;
    margin-top: 12px;
    font-size: 18px;
    font-weight: 600;
  }
  .hero-pnl .arrow { font-size: 16px; }
  .hero-pnl.pos { color: var(--pos); }
  .hero-pnl.neg { color: var(--neg); }
  .hero-pnl.flat { color: var(--text-2); }
  .hero-spark {
    margin-top: 24px;
    height: 80px;
    position: relative;
  }

  /* ───── Stat row ───── */
  .stats {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
    margin-bottom: 24px;
  }
  .stat {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 18px 20px;
    transition: border-color 0.2s, transform 0.2s;
  }
  .stat:hover { border-color: var(--text-3); transform: translateY(-1px); }
  .stat-label {
    font-size: 12px;
    color: var(--text-2);
    font-weight: 500;
    margin-bottom: 6px;
  }
  .stat-value {
    font-size: 22px;
    font-weight: 700;
    letter-spacing: -0.015em;
  }
  .stat-sub {
    font-size: 12px;
    color: var(--text-3);
    margin-top: 4px;
    font-weight: 500;
  }
  .stat-sub.pos { color: var(--pos); }
  .stat-sub.neg { color: var(--neg); }

  /* ───── Section grid ───── */
  .grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    margin-bottom: 16px;
  }
  .grid.full { grid-template-columns: 1fr; }
  .grid.thirds { grid-template-columns: 2fr 1fr; }

  .card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 22px;
    box-shadow: var(--shadow);
  }
  .card-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 18px;
  }
  .card-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--text-2);
    letter-spacing: 0.02em;
    text-transform: uppercase;
  }
  .card-meta {
    font-size: 12px;
    color: var(--text-3);
    font-weight: 500;
  }

  /* ───── PnL chart ───── */
  #pnl-chart-wrap { height: 280px; position: relative; }

  /* ───── Calendar ───── */
  .cal-grid {
    display: grid;
    grid-template-columns: repeat(7, 1fr);
    gap: 6px;
  }
  .cal-head {
    color: var(--text-3);
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    text-align: center;
    padding: 4px 0;
    letter-spacing: 0.04em;
  }
  .cal-cell {
    aspect-ratio: 1;
    border-radius: 8px;
    padding: 6px 6px 4px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    border: 1px solid transparent;
    background: var(--bg-elev);
    transition: transform 0.15s;
  }
  .cal-cell.empty { background: transparent; }
  .cal-cell.today { border-color: var(--accent); }
  .cal-cell.has-activity:hover { transform: scale(1.04); cursor: default; }
  .cal-day {
    font-size: 11px;
    color: var(--text-2);
    font-weight: 600;
  }
  .cal-cell.today .cal-day { color: var(--accent); }
  .cal-nums {
    text-align: right;
    line-height: 1.15;
  }
  .cal-pnl {
    font-size: 12px;
    font-weight: 700;
  }
  .cal-pct {
    font-size: 10px;
    font-weight: 500;
    color: var(--text-3);
    margin-top: 1px;
  }
  .cal-cell.pos { background: var(--pos-soft); }
  .cal-cell.neg { background: var(--neg-soft); }
  .cal-cell.pos .cal-pnl,
  .cal-cell.pos .cal-pct { color: var(--pos); }
  .cal-cell.neg .cal-pnl,
  .cal-cell.neg .cal-pct { color: var(--neg); }

  /* ───── Performance card ───── */
  .perf-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 16px 24px;
  }
  .perf-item {
    display: flex;
    flex-direction: column;
  }
  .perf-label {
    font-size: 11px;
    color: var(--text-3);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 4px;
    font-weight: 600;
  }
  .perf-value {
    font-size: 20px;
    font-weight: 700;
    letter-spacing: -0.01em;
  }
  .perf-value.pos { color: var(--pos); }
  .perf-value.neg { color: var(--neg); }
  .winrate-bar {
    margin-top: 12px;
    height: 8px;
    border-radius: 4px;
    background: var(--neg-soft);
    overflow: hidden;
    position: relative;
  }
  .winrate-bar-fill {
    height: 100%;
    background: var(--pos);
    border-radius: 4px 0 0 4px;
    transition: width 0.4s ease;
  }
  .winrate-legend {
    display: flex;
    justify-content: space-between;
    font-size: 11px;
    color: var(--text-3);
    margin-top: 6px;
    font-weight: 500;
  }

  /* ───── Risk caps ───── */
  .risk-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 0;
    border-bottom: 1px solid var(--border-soft);
  }
  .risk-row:last-child { border-bottom: none; }
  .risk-name { font-size: 13px; color: var(--text-2); font-weight: 500; }
  .risk-vals { display: flex; gap: 12px; align-items: baseline; }
  .risk-pct  { font-size: 12px; color: var(--text-3); font-weight: 600; }
  .risk-dollar { font-size: 14px; font-weight: 600; }
  .leverage-bar {
    margin-top: 4px;
    height: 4px;
    border-radius: 2px;
    background: var(--border);
    overflow: hidden;
  }
  .leverage-bar-fill {
    height: 100%;
    background: linear-gradient(90deg, var(--pos), var(--warn), var(--neg));
    transition: width 0.4s;
  }

  /* ───── Tables ───── */
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  thead th {
    text-align: left;
    font-size: 11px;
    font-weight: 600;
    color: var(--text-3);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    padding: 0 8px 12px 8px;
    border-bottom: 1px solid var(--border-soft);
  }
  thead th.right { text-align: right; }
  tbody td {
    padding: 12px 8px;
    border-bottom: 1px solid var(--border-soft);
    vertical-align: top;
  }
  tbody tr:last-child td { border-bottom: none; }
  tbody td.right { text-align: right; }
  .title-cell {
    color: var(--text);
    font-weight: 500;
    line-height: 1.35;
    max-width: 360px;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .outcome-tag {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    background: var(--border-soft);
    color: var(--text-2);
    font-size: 11px;
    font-weight: 600;
    margin-top: 4px;
  }
  td.pos { color: var(--pos); font-weight: 600; }
  td.neg { color: var(--neg); font-weight: 600; }

  /* ───── Scrollable lists (positions table, fills, resolutions, winners/losers) ───── */
  .scroll-list {
    max-height: 560px;
    overflow-y: auto;
    /* visible scrollbar feel — dim track so it doesn't dominate */
    scrollbar-width: thin;
    scrollbar-color: var(--border) transparent;
  }
  .scroll-list::-webkit-scrollbar { width: 8px; height: 8px; }
  .scroll-list::-webkit-scrollbar-track { background: transparent; }
  .scroll-list::-webkit-scrollbar-thumb {
    background: var(--border);
    border-radius: 4px;
  }
  .scroll-list::-webkit-scrollbar-thumb:hover { background: var(--text-3); }
  /* keep table header pinned while body scrolls */
  .scroll-list table thead th {
    position: sticky;
    top: 0;
    background: var(--card);
    z-index: 1;
  }

  /* ───── Activity feed ───── */
  .feed { display: flex; flex-direction: column; gap: 12px; }
  .feed-row {
    display: grid;
    grid-template-columns: 80px 1fr auto;
    gap: 12px;
    align-items: center;
    padding: 10px 0;
    border-bottom: 1px solid var(--border-soft);
  }
  .feed-row:last-child { border-bottom: none; }
  .feed-tag {
    display: inline-block;
    padding: 3px 8px;
    border-radius: 5px;
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    text-align: center;
    width: 70px;
  }
  .feed-tag.buy     { background: rgba(88, 166, 255, 0.15); color: var(--accent); }
  .feed-tag.sell    { background: rgba(248, 81, 73, 0.15);  color: var(--neg); }
  .feed-tag.resolve { background: rgba(63, 185, 80, 0.15);  color: var(--pos); }
  .feed-tag.won     { background: var(--pos-soft);          color: var(--pos); }
  .feed-tag.lost    { background: var(--neg-soft);          color: var(--neg); }
  .feed-title {
    font-size: 13px;
    color: var(--text);
    font-weight: 500;
    line-height: 1.3;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .feed-meta {
    font-size: 11px;
    color: var(--text-3);
    margin-top: 2px;
    font-weight: 500;
  }
  .feed-right {
    font-size: 13px;
    font-weight: 700;
    text-align: right;
    line-height: 1.2;
    white-space: nowrap;
  }
  .feed-right.pos { color: var(--pos); }
  .feed-right.neg { color: var(--neg); }
  .feed-right .sub {
    display: block;
    font-size: 11px;
    color: var(--text-3);
    font-weight: 500;
    margin-top: 2px;
  }

  /* ───── Health footer ───── */
  .health {
    margin-top: 24px;
    padding: 16px 20px;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    font-size: 12px;
    color: var(--text-3);
    font-weight: 500;
  }
  .health-item { display: inline-flex; align-items: center; gap: 6px; }
  .health-item .lbl { color: var(--text-3); }
  .health-item .val { color: var(--text-2); }
  .health-item .val.stale { color: var(--warn); }
  .health-item .val.critical { color: var(--neg); }
  .health-error {
    flex-basis: 100%;
    color: var(--neg);
    font-weight: 600;
    padding-top: 8px;
    border-top: 1px solid var(--border-soft);
  }

  /* ───── Misc ───── */
  .empty-msg {
    text-align: center;
    color: var(--text-3);
    padding: 20px;
    font-size: 13px;
  }
  .loading {
    text-align: center;
    color: var(--text-3);
    padding: 60px 20px;
    font-size: 14px;
  }

  /* ───── Responsive ───── */
  @media (max-width: 900px) {
    .container { padding: 16px; }
    .topbar { gap: 8px; }
    .hero { padding: 24px; }
    .hero-value { font-size: 44px; }
    .stats { grid-template-columns: repeat(2, 1fr); }
    .grid, .grid.thirds { grid-template-columns: 1fr; }
    .perf-grid { grid-template-columns: 1fr 1fr; }
    .feed-row { grid-template-columns: 70px 1fr auto; }
    .title-cell { max-width: 200px; }
  }
  @media (max-width: 520px) {
    .hero-value { font-size: 36px; }
    .stats { grid-template-columns: 1fr 1fr; gap: 8px; }
    .stat { padding: 14px 16px; }
    .stat-value { font-size: 18px; }
    .cal-cell { padding: 3px; }
    .cal-day { font-size: 10px; }
    .cal-pnl { font-size: 9px; }
    .card { padding: 16px; }
    table { font-size: 12px; }
    thead th, tbody td { padding: 8px 4px; }
    .title-cell { max-width: 140px; }
  }
</style>
</head>
<body>
<div class="container">
  <div class="topbar">
    <div class="brand">
      <div class="brand-dot"></div>
      <div>
        <div class="brand-name">Polymarket Copy-Trade</div>
        <div class="brand-sub" id="brand-sub">loading…</div>
      </div>
    </div>
    <div id="status-pill" class="pill"><span class="status-dot"></span><span class="mode">—</span></div>
  </div>

  <div id="app" class="loading">Loading dashboard…</div>
</div>

<script>
const MODE = "{{MODE}}";
const REFRESH_MS = {{REFRESH_MS}};
let pnlChart = null;
let sparkChart = null;

const fmtMoney = (v) => {
  if (v === null || v === undefined) return "—";
  const sign = v < 0 ? "-" : "";
  const abs = Math.abs(v);
  return sign + "$" + abs.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};
const fmtMoneySigned = (v) => {
  if (v === null || v === undefined) return "—";
  const sign = v > 0 ? "+" : (v < 0 ? "-" : "");
  const abs = Math.abs(v);
  return sign + "$" + abs.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};
const fmtPct = (v, signed = true) => {
  if (v === null || v === undefined) return "—";
  const sign = signed ? (v > 0 ? "+" : (v < 0 ? "" : "")) : "";
  return sign + v.toFixed(2) + "%";
};
const fmtAge = (s) => {
  if (s === null || s === undefined) return "—";
  if (s < 60) return s + "s";
  if (s < 3600) return Math.floor(s / 60) + "m";
  if (s < 86400) return Math.floor(s / 3600) + "h";
  return Math.floor(s / 86400) + "d";
};
const escapeHtml = (s) => {
  if (s === null || s === undefined) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
};
const fmtTime = (isoUtc) => {
  if (!isoUtc) return "—";
  const d = new Date(isoUtc);
  return d.toLocaleString("en-US", {
    month: "numeric", day: "numeric",
    hour: "numeric", minute: "2-digit",
    hour12: true,
  });
};
const fmtDate = (isoUtc) => {
  if (!isoUtc) return "";
  const d = new Date(isoUtc);
  return d.toLocaleDateString("en-US", { month: "numeric", day: "numeric" });
};

function renderTopbar(data) {
  const bot = data.bot;
  let pillClass = "pill";
  let dot = "—";
  if (bot.status === "running") {
    const tickStale = (bot.tick_age_seconds ?? 9999) > 30;
    const sigCritical = (bot.signal_age_seconds ?? 0) > 14400;
    if (tickStale || sigCritical || bot.last_error) pillClass = "pill error";
    else pillClass = "pill ok";
    dot = bot.status.toUpperCase();
  } else {
    pillClass = "pill warn";
    dot = (bot.status || "—").toUpperCase();
  }
  document.getElementById("status-pill").className = pillClass;
  document.getElementById("status-pill").innerHTML =
    `<span class="status-dot"></span><span class="mode">${escapeHtml(data.mode)}</span> · <span>${escapeHtml(dot)}</span>`;
  document.getElementById("brand-sub").textContent =
    `mode: ${data.mode} · ${data.account.open_positions} open · ${data.strategy.total_fills} total fills`;
}

function renderHero(data) {
  const a = data.account;
  const pnlClass = a.total_pnl > 0 ? "pos" : a.total_pnl < 0 ? "neg" : "flat";
  const arrow = a.total_pnl > 0 ? "↑" : a.total_pnl < 0 ? "↓" : "→";
  return `
    <div class="hero">
      <div class="hero-label">Portfolio value · ${escapeHtml(data.mode)} mode</div>
      <div class="hero-value mono num">${fmtMoney(a.account_value)}</div>
      <div class="hero-pnl ${pnlClass} mono num">
        <span class="arrow">${arrow}</span>
        <span>${fmtMoneySigned(a.total_pnl)}</span>
        <span>(${fmtPct(a.total_pnl_pct)})</span>
      </div>
      <div class="hero-spark">
        <canvas id="spark-chart"></canvas>
      </div>
    </div>
  `;
}

function renderStats(data) {
  const a = data.account;
  const p = data.performance;
  const wrClass = p.win_rate >= 60 ? "pos" : p.win_rate >= 50 ? "" : "neg";
  return `
    <div class="stats">
      <div class="stat">
        <div class="stat-label">Available cash</div>
        <div class="stat-value mono num">${fmtMoney(a.balance)}</div>
        <div class="stat-sub">paper bankroll</div>
      </div>
      <div class="stat">
        <div class="stat-label">Committed</div>
        <div class="stat-value mono num">${fmtMoney(a.committed)}</div>
        <div class="stat-sub">${a.open_positions} open ${a.open_positions === 1 ? 'position' : 'positions'}</div>
      </div>
      <div class="stat">
        <div class="stat-label">Realized P&amp;L</div>
        <div class="stat-value mono num ${a.realized > 0 ? 'pos' : a.realized < 0 ? 'neg' : ''}">${fmtMoneySigned(a.realized)}</div>
        <div class="stat-sub">across ${p.total_closed} closes</div>
      </div>
      <div class="stat">
        <div class="stat-label">Win rate</div>
        <div class="stat-value mono num ${wrClass}">${p.win_rate.toFixed(1)}%</div>
        <div class="stat-sub">${p.wins}W · ${p.losses}L</div>
      </div>
    </div>
  `;
}

function renderPnlChart(data) {
  return `
    <div class="card">
      <div class="card-head">
        <div class="card-title">Cumulative P&amp;L</div>
        <div class="card-meta">${data.pnl_timeline.length} days · all-time</div>
      </div>
      <div id="pnl-chart-wrap"><canvas id="pnl-chart"></canvas></div>
    </div>
  `;
}

function renderCalendar(data) {
  const cal = data.daily_pnl_calendar;
  const headers = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    .map(h => `<div class="cal-head">${h}</div>`).join("");
  const cells = cal.weeks.flatMap(week => week.map(d => {
    if (!d) return `<div class="cal-cell empty"></div>`;
    let cls = "cal-cell";
    if (d.is_today) cls += " today";
    if (d.has_activity && d.realized > 0) cls += " pos has-activity";
    else if (d.has_activity && d.realized < 0) cls += " neg has-activity";
    let pnlText = "";
    let pctText = "";
    if (d.has_activity) {
      const sign = d.realized > 0 ? "+" : (d.realized < 0 ? "-" : "");
      pnlText = sign + "$" + Math.abs(d.realized).toFixed(2);
      const pctSign = d.pct > 0 ? "+" : "";
      pctText = pctSign + d.pct.toFixed(2) + "%";
    }
    return `
      <div class="${cls}" title="${escapeHtml(d.date)}: ${fmtMoneySigned(d.realized)} (${fmtPct(d.pct)})">
        <div class="cal-day">${d.day}</div>
        <div class="cal-nums">
          <div class="cal-pnl mono num">${pnlText}</div>
          <div class="cal-pct mono num">${pctText}</div>
        </div>
      </div>`;
  })).join("");
  return `
    <div class="card">
      <div class="card-head">
        <div class="card-title">Daily P&amp;L · ${escapeHtml(cal.month_label)}</div>
      </div>
      <div class="cal-grid">${headers}${cells}</div>
    </div>
  `;
}

function renderPerformance(data) {
  const p = data.performance;
  const wrFill = Math.max(0, Math.min(100, p.win_rate));
  return `
    <div class="card">
      <div class="card-head">
        <div class="card-title">Performance</div>
      </div>
      <div class="perf-grid">
        <div class="perf-item">
          <div class="perf-label">Avg win</div>
          <div class="perf-value pos mono num">${fmtMoneySigned(p.avg_win)}</div>
        </div>
        <div class="perf-item">
          <div class="perf-label">Avg loss</div>
          <div class="perf-value neg mono num">${fmtMoneySigned(p.avg_loss)}</div>
        </div>
        <div class="perf-item">
          <div class="perf-label">Closes</div>
          <div class="perf-value mono num">${p.total_closed}</div>
        </div>
        <div class="perf-item">
          <div class="perf-label">Win rate</div>
          <div class="perf-value mono num">${p.win_rate.toFixed(1)}%</div>
        </div>
      </div>
      <div class="winrate-bar"><div class="winrate-bar-fill" style="width:${wrFill}%"></div></div>
      <div class="winrate-legend"><span>${p.wins} wins</span><span>${p.losses} losses</span></div>
    </div>
  `;
}

function renderRisk(data) {
  const r = data.risk;
  const s = data.strategy;
  const levFill = Math.max(0, Math.min(100, r.current_leverage_pct));
  const curveNote = s.mirror_power !== 1.0 ? `^${s.mirror_power}` : "";
  return `
    <div class="card">
      <div class="card-head">
        <div class="card-title">Risk &amp; sizing</div>
        <div class="card-meta">${escapeHtml(s.sizing_strategy)} ×${s.mirror_scale}${curveNote} · min ${fmtMoney(s.min_trade_usd)}</div>
      </div>
      <div class="risk-row">
        <span class="risk-name">Per trade</span>
        <span class="risk-vals">
          <span class="risk-pct mono">${r.per_trade_pct.toFixed(1)}%</span>
          <span class="risk-dollar mono num">${fmtMoney(r.per_trade_dollars)}</span>
        </span>
      </div>
      <div class="risk-row">
        <span class="risk-name">Per market</span>
        <span class="risk-vals">
          <span class="risk-pct mono">${r.per_market_pct >= 100 ? "off" : r.per_market_pct.toFixed(1) + "%"}</span>
          <span class="risk-dollar mono num">${r.per_market_pct >= 100 ? "—" : fmtMoney(r.per_market_dollars)}</span>
        </span>
      </div>
      <div class="risk-row">
        <span class="risk-name">Daily loss cap</span>
        <span class="risk-vals">
          <span class="risk-pct mono">${r.daily_loss_cap_pct.toFixed(1)}%</span>
          <span class="risk-dollar mono num">${fmtMoney(r.daily_loss_cap_dollars)}</span>
        </span>
      </div>
      <div style="padding: 12px 0 0 0;">
        <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px;">
          <span class="risk-name">Leverage</span>
          <span class="mono num" style="font-size:13px;font-weight:600;">
            ${r.current_leverage_pct.toFixed(1)}% <span style="color:var(--text-3);font-weight:500;">of ${r.max_leverage_pct.toFixed(0)}%</span>
          </span>
        </div>
        <div class="leverage-bar"><div class="leverage-bar-fill" style="width:${levFill * (100 / Math.max(r.max_leverage_pct, 1))}%"></div></div>
      </div>
    </div>
  `;
}

function renderOpenPositions(data) {
  const positions = data.open_positions;
  if (!positions.length) {
    return `
      <div class="card">
        <div class="card-head"><div class="card-title">Open positions</div></div>
        <div class="empty-msg">No open positions.</div>
      </div>
    `;
  }
  // Show all — scroll for the rest after ~10 fit in view.
  const rows = positions.map(p => {
    const ret = p.return_pct;
    const cls = ret !== null ? (ret > 0 ? "pos" : "neg") : "";
    const retStr = ret !== null ? fmtPct(ret) : "—";
    const upnl = p.unrealized_pnl !== null ? fmtMoneySigned(p.unrealized_pnl) : "—";
    const upnlCls = p.unrealized_pnl !== null
      ? (p.unrealized_pnl > 0 ? "pos" : p.unrealized_pnl < 0 ? "neg" : "")
      : "";
    return `
      <tr>
        <td class="title-cell">${escapeHtml(p.title || "(unknown market)")}<div class="outcome-tag">${escapeHtml(p.outcome)}</div></td>
        <td class="right mono num">${fmtMoney(p.cost_basis)}</td>
        <td class="right mono num">${fmtMoney(p.market_value)}</td>
        <td class="right mono num ${upnlCls}">${upnl}</td>
        <td class="right mono num ${cls}">${retStr}</td>
      </tr>
    `;
  }).join("");
  return `
    <div class="card">
      <div class="card-head">
        <div class="card-title">Open positions</div>
        <div class="card-meta">${positions.length} total · scroll for more</div>
      </div>
      <div class="scroll-list">
        <table>
          <thead>
            <tr>
              <th>Market</th>
              <th class="right">Cost</th>
              <th class="right">Value</th>
              <th class="right">P&amp;L</th>
              <th class="right">Return</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </div>
  `;
}

function renderActivity(data, kind) {
  // kind: "fills" or "resolutions"
  if (kind === "fills") {
    const fills = data.recent_fills;
    if (!fills.length) {
      return `<div class="card"><div class="card-head"><div class="card-title">Recent fills</div></div><div class="empty-msg">No fills yet.</div></div>`;
    }
    const rows = fills.map(f => {
      let tag = "buy";
      let tagText = "BUY";
      if (f.status === "resolved") { tag = "resolve"; tagText = "RESOLVE"; }
      else if (f.side === "sell")   { tag = "sell";    tagText = "SELL"; }
      return `
        <div class="feed-row">
          <span class="feed-tag ${tag}">${tagText}</span>
          <div style="overflow:hidden;">
            <div class="feed-title">${escapeHtml(f.title || "(unknown market)")}</div>
            <div class="feed-meta">${escapeHtml(f.outcome)} @ $${(f.price || 0).toFixed(3)} · ${fmtTime(f.timestamp_utc)}</div>
          </div>
          <div class="feed-right mono num">${fmtMoney(f.notional)}</div>
        </div>
      `;
    }).join("");
    return `
      <div class="card">
        <div class="card-head"><div class="card-title">Recent fills</div><div class="card-meta">${fills.length} loaded · scroll for more</div></div>
        <div class="scroll-list"><div class="feed">${rows}</div></div>
      </div>
    `;
  } else {
    const rs = data.recent_resolutions;
    if (!rs.length) {
      return `<div class="card"><div class="card-head"><div class="card-title">Recent resolutions</div></div><div class="empty-msg">No resolutions yet.</div></div>`;
    }
    const rows = rs.map(r => {
      const won = r.won;
      const tag = won ? "won" : "lost";
      const tagText = won ? "WON" : "LOST";
      const pnlCls = r.pnl !== null && r.pnl !== undefined
        ? (r.pnl > 0 ? "pos" : r.pnl < 0 ? "neg" : "")
        : "";
      const pnlStr = r.pnl !== null && r.pnl !== undefined ? fmtMoneySigned(r.pnl) : "—";
      return `
        <div class="feed-row">
          <span class="feed-tag ${tag}">${tagText}</span>
          <div style="overflow:hidden;">
            <div class="feed-title">${escapeHtml(r.title || "(unknown market)")}</div>
            <div class="feed-meta">${escapeHtml(r.outcome)} · ${fmtTime(r.timestamp_utc)}</div>
          </div>
          <div class="feed-right mono num ${pnlCls}">${pnlStr}</div>
        </div>
      `;
    }).join("");
    return `
      <div class="card">
        <div class="card-head"><div class="card-title">Recent resolutions</div><div class="card-meta">${rs.length} loaded · scroll for more</div></div>
        <div class="scroll-list"><div class="feed">${rows}</div></div>
      </div>
    `;
  }
}

function renderWinnersLosers(data) {
  const buildList = (rows, title, fmt) => {
    if (!rows.length) {
      return `<div class="card"><div class="card-head"><div class="card-title">${title}</div></div><div class="empty-msg">Nothing here yet.</div></div>`;
    }
    const list = rows.map(r => {
      const valStr = fmt(r);
      const cls = (r.pnl || 0) > 0 ? "pos" : "neg";
      return `
        <div class="feed-row" style="grid-template-columns: 1fr auto;">
          <div style="overflow:hidden;">
            <div class="feed-title">${escapeHtml(r.title || "(unknown market)")}</div>
            <div class="feed-meta">${escapeHtml(r.outcome)} · ${fmtDate(r.updated_at)}${r.avg_buy_price !== null && r.exit_price !== null ? ` · ${r.avg_buy_price.toFixed(2)}→${r.exit_price.toFixed(2)}` : ""}</div>
          </div>
          <div class="feed-right mono num ${cls}">${valStr}</div>
        </div>
      `;
    }).join("");
    // ~5 rows visible by default; scroll for the rest.
    return `
      <div class="card">
        <div class="card-head"><div class="card-title">${title}</div><div class="card-meta">${rows.length} loaded · scroll for more</div></div>
        <div class="scroll-list" style="max-height: 360px;"><div class="feed">${list}</div></div>
      </div>
    `;
  };
  return `
    <div class="grid">
      ${buildList(data.winners_dollar, "Biggest winners ($)", r => fmtMoneySigned(r.pnl))}
      ${buildList(data.losers_dollar, "Biggest losers ($)", r => fmtMoneySigned(r.pnl))}
    </div>
    ${data.winners_pct.length ? `
      <div class="grid full">
        ${buildList(data.winners_pct, "Biggest winners (%)", r => (r.return_pct > 0 ? "+" : "") + r.return_pct.toFixed(1) + "%")}
      </div>` : ""}
  `;
}

function renderHealth(data) {
  const b = data.bot;
  const tickStale = (b.tick_age_seconds ?? 9999) > 30;
  const sigStale = (b.signal_age_seconds ?? 0) > 3600;
  const sigCritical = (b.signal_age_seconds ?? 0) > 14400;
  const tickCls = tickStale ? "val critical" : "val";
  const sigCls = sigCritical ? "val critical" : sigStale ? "val stale" : "val";
  return `
    <div class="health">
      <div class="health-item">
        <span class="lbl">Bot:</span>
        <span class="${b.status === 'running' ? '' : 'val stale'}">${escapeHtml((b.status || '').toUpperCase())}</span>
      </div>
      <div class="health-item">
        <span class="lbl">Last tick:</span>
        <span class="${tickCls}">${fmtAge(b.tick_age_seconds)} ago</span>
      </div>
      <div class="health-item">
        <span class="lbl">Last signal:</span>
        <span class="${sigCls}">${fmtAge(b.signal_age_seconds)} ago</span>
      </div>
      <div class="health-item">
        <span class="lbl">Poll status:</span>
        <span class="val">${escapeHtml(b.last_poll_status || "—")}</span>
      </div>
      <div class="health-item">
        <span class="lbl">Updated:</span>
        <span class="val" id="last-updated">just now</span>
      </div>
      ${b.last_error ? `<div class="health-error">⚠ ${escapeHtml(b.last_error)}</div>` : ""}
    </div>
  `;
}

function renderAll(data) {
  if (data.error) {
    document.getElementById("app").innerHTML = `<div class="empty-msg">⚠ ${escapeHtml(data.error)}</div>`;
    return;
  }
  renderTopbar(data);
  document.getElementById("app").innerHTML =
    renderHero(data) +
    renderStats(data) +
    renderPnlChart(data) +
    `<div class="grid"><div>${renderCalendar(data)}</div><div>${renderPerformance(data)}${renderRisk(data)}</div></div>` +
    renderOpenPositions(data) +
    `<div class="grid">${renderActivity(data, "fills")}${renderActivity(data, "resolutions")}</div>` +
    renderWinnersLosers(data) +
    renderHealth(data);

  drawPnlChart(data);
  drawSparkChart(data);
}

function drawPnlChart(data) {
  const ctx = document.getElementById("pnl-chart");
  if (!ctx) return;
  if (pnlChart) { pnlChart.destroy(); pnlChart = null; }
  const points = data.pnl_timeline.map(p => ({ x: p.date, y: p.cumulative_pnl }));
  const last = points[points.length - 1]?.y ?? 0;
  const color = last >= 0 ? "#3fb950" : "#f85149";
  const colorSoft = last >= 0 ? "rgba(63,185,80,0.18)" : "rgba(248,81,73,0.18)";
  pnlChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: points.map(p => p.x),
      datasets: [{
        label: "Cumulative P&L",
        data: points.map(p => p.y),
        borderColor: color,
        backgroundColor: colorSoft,
        fill: true,
        tension: 0.3,
        borderWidth: 2,
        pointRadius: 0,
        pointHoverRadius: 5,
        pointHoverBackgroundColor: color,
        pointHoverBorderColor: "#0a0e15",
        pointHoverBorderWidth: 2,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          enabled: true,
          backgroundColor: "#161b22",
          borderColor: "#30363d",
          borderWidth: 1,
          titleColor: "#e6edf3",
          bodyColor: "#e6edf3",
          padding: 12,
          displayColors: false,
          callbacks: {
            title: (items) => items[0].label,
            label: (ctx) => "$" + ctx.parsed.y.toFixed(2),
          },
        },
      },
      scales: {
        x: {
          ticks: { color: "#6e7681", maxRotation: 0, autoSkipPadding: 32 },
          grid: { display: false },
          border: { display: false },
        },
        y: {
          ticks: {
            color: "#6e7681",
            callback: (v) => "$" + v.toFixed(0),
          },
          grid: { color: "#21262d", drawBorder: false },
          border: { display: false },
        },
      },
    },
  });
}

function drawSparkChart(data) {
  const ctx = document.getElementById("spark-chart");
  if (!ctx) return;
  if (sparkChart) { sparkChart.destroy(); sparkChart = null; }
  // Last 30 days for the hero sparkline.
  const recent = data.pnl_timeline.slice(-30);
  const color = data.account.total_pnl >= 0 ? "#3fb950" : "#f85149";
  sparkChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: recent.map(p => p.date),
      datasets: [{
        data: recent.map(p => p.cumulative_pnl),
        borderColor: color,
        backgroundColor: color + "20",
        fill: true,
        tension: 0.35,
        borderWidth: 2,
        pointRadius: 0,
        pointHoverRadius: 5,
        pointHoverBackgroundColor: color,
        pointHoverBorderColor: "#0a0e15",
        pointHoverBorderWidth: 2,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          enabled: true,
          backgroundColor: "#161b22",
          borderColor: "#30363d",
          borderWidth: 1,
          titleColor: "#e6edf3",
          bodyColor: "#e6edf3",
          padding: 10,
          displayColors: false,
          callbacks: {
            title: (items) => items[0].label,
            label: (ctx) => "$" + ctx.parsed.y.toFixed(2),
          },
        },
      },
      scales: { x: { display: false }, y: { display: false } },
    },
  });
}

async function fetchAndRender() {
  try {
    const resp = await fetch(`/dashboard.json?mode=${encodeURIComponent(MODE)}`, { cache: "no-store" });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const data = await resp.json();
    renderAll(data);
  } catch (e) {
    document.getElementById("app").innerHTML =
      `<div class="empty-msg">⚠ Could not load dashboard data: ${escapeHtml(String(e))}</div>`;
  }
}

fetchAndRender();
setInterval(fetchAndRender, REFRESH_MS);
</script>
</body>
</html>"""


# ============================================================================
# Legacy text dashboard — kept at /dashboard/text for parity with the CLI view
# ============================================================================

_WIDE_SECTIONS = {
    "DAILY P&L CALENDAR",
    "TOP OPEN POSITIONS",
    "RECENT RESOLUTIONS",
    "RECENT FILLS",
}


def _colorize_inline(escaped: str) -> str:
    escaped = re.sub(r"(\+\$[\d,]+\.\d{2})", r'<span class="pos">\1</span>', escaped)
    escaped = re.sub(r"(-\$[\d,]+\.\d{2})", r'<span class="neg">\1</span>', escaped)
    escaped = re.sub(r"(\+\d+\.\d{2}%)", r'<span class="pos">\1</span>', escaped)
    escaped = re.sub(r"(?<![\d.])(-\d+\.\d{2}%)", r'<span class="neg">\1</span>', escaped)
    escaped = escaped.replace("● RUNNING", '<span class="ok">● RUNNING</span>')
    escaped = escaped.replace("● STOPPED", '<span class="warn">● STOPPED</span>')
    escaped = re.sub(r"\bWON\b", '<span class="pos">WON</span>', escaped)
    escaped = re.sub(r"\bLOST\b", '<span class="neg">LOST</span>', escaped)
    escaped = escaped.replace("⚠", '<span class="neg">⚠</span>')
    escaped = re.sub(r"\b(error:)", r'<span class="neg">\1</span>', escaped)
    escaped = re.sub(r"\b(poll error)", r'<span class="neg">\1</span>', escaped)
    return escaped


def _split_into_cards(rendered: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, list[str]]] = [("BANNER", [])]
    hdr_re = re.compile(r"^─── \s*(.+?)\s* ───+$")
    for raw in rendered.splitlines():
        m = hdr_re.match(raw.strip())
        if m:
            sections.append((m.group(1).strip().upper(), []))
        else:
            sections[-1][1].append(raw)
    out: list[tuple[str, str]] = []
    for title, lines in sections:
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines and title == "BANNER":
            continue
        out.append((title, "\n".join(lines)))
    return out


def _wrap_legacy_html(body_text: str, refresh_seconds: int) -> str:
    cards = _split_into_cards(body_text)
    parts: list[str] = []
    for title, body in cards:
        body_safe = _colorize_inline(html.escape(body))
        css_class = "card"
        if title == "BANNER":
            css_class += " full"
            parts.append(f'<div class="{css_class}"><pre>{body_safe}</pre></div>')
        else:
            if any(title.startswith(name) for name in _WIDE_SECTIONS):
                css_class += " wide"
            parts.append(
                f'<div class="{css_class}">'
                f'<div class="card-title">{html.escape(title)}</div>'
                f'<pre>{body_safe}</pre></div>'
            )
    cards_block = "\n".join(parts)
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{refresh_seconds}">
<title>Bot Dashboard (text)</title><style>
:root{{--bg:#0b0d12;--fg:#d8dee9;--dim:#6c7280;--pos:#56d364;--neg:#f47174;--hdr:#79c0ff;--warn:#d4a72c;--card:#161922;--card2:#1a1e29;--border:#232733;}}
*{{box-sizing:border-box}}html,body{{background:var(--bg);color:var(--fg);font-family:ui-monospace,Consolas,monospace;margin:0;padding:0;line-height:1.5}}
body{{padding:14px;font-size:12px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:12px;align-items:start}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:12px 14px;overflow:hidden}}
.card.wide{{grid-column:span 2}}.card.full{{grid-column:1/-1;background:var(--card2)}}
.card-title{{color:var(--hdr);font-weight:700;letter-spacing:.06em;font-size:10px;text-transform:uppercase;border-bottom:1px solid var(--border);padding-bottom:6px;margin-bottom:8px}}
pre{{margin:0;white-space:pre;overflow-x:auto}}
.pos{{color:var(--pos);font-weight:600}}.neg{{color:var(--neg);font-weight:600}}.ok{{color:var(--pos)}}.warn{{color:var(--warn)}}
@media(max-width:760px){{.grid{{grid-template-columns:1fr;gap:8px}}.card.wide,.card.full{{grid-column:1}}}}
</style></head><body><div class="grid">{cards_block}</div></body></html>"""


@router.get("/dashboard/text", response_class=HTMLResponse)
def dashboard_text(
    mode: str = Query("paper", pattern="^(paper|live)$"),
    refresh: int = Query(5, ge=2, le=60),
    no_prices: bool = Query(False),
) -> HTMLResponse:
    if not os.path.exists(_DB_PATH):
        return HTMLResponse("<h1>db not found</h1>", status_code=500)
    saved = dict(stats.COLORS)
    for k in stats.COLORS:
        stats.COLORS[k] = ""
    try:
        with sqlite3.connect(_DB_PATH) as con:
            body = stats.render(con, mode=mode, skip_prices=no_prices)
    finally:
        stats.COLORS.update(saved)
    return HTMLResponse(_wrap_legacy_html(body, refresh))
