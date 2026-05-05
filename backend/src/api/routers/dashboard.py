"""Read-only HTML dashboard for phone/remote viewing.

Serves the same content stats.py prints, wrapped in mobile-friendly HTML with
auto-refresh. Intentionally unauthenticated — exposing it via a public tunnel
(cloudflared, ngrok) gives anyone with the URL view-only access. There is no
write or control surface here, only render.
"""
from __future__ import annotations

import html
import os
import sqlite3

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

# stats.py lives at backend/stats.py — same dir uvicorn runs from. Import the
# render function so both CLI and HTML see identical content.
import stats  # type: ignore

router = APIRouter()

# Repo layout: backend/copytrade.db sits next to backend/stats.py.
_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(stats.__file__)), "copytrade.db")


import re


def _colorize_html(escaped: str) -> str:
    """Light HTML pass to color positive (+$X) green and negative (-$X) red,
    plus highlight section headers. Operates on already-html-escaped text."""
    # Section headers like "─── ACCOUNT ─────────"
    escaped = re.sub(
        r"(───\s*[A-Z][A-Z &amp;0-9 ]*\s*───*)",
        r'<span class="hdr">\1</span>',
        escaped,
    )
    # The big "POLYMARKET COPY-TRADE BOT" banner
    escaped = re.sub(
        r"(═══\s*POLYMARKET COPY-TRADE BOT\s*═══)",
        r'<span class="banner">\1</span>',
        escaped,
    )
    # +$1,234.56 or +$1.50 (positive PnL)
    escaped = re.sub(r"(\+\$[\d,]+\.\d{2})", r'<span class="pos">\1</span>', escaped)
    # -$1,234.56 (negative PnL)
    escaped = re.sub(r"(-\$[\d,]+\.\d{2})", r'<span class="neg">\1</span>', escaped)
    # Percentages with sign: +3.72% or -3.72%
    escaped = re.sub(r"(\+\d+\.\d{2}%)", r'<span class="pos">\1</span>', escaped)
    escaped = re.sub(r"(?<![\d.])(-\d+\.\d{2}%)", r'<span class="neg">\1</span>', escaped)
    # Status indicators
    escaped = escaped.replace("● RUNNING", '<span class="ok">● RUNNING</span>')
    escaped = escaped.replace("● STOPPED", '<span class="warn">● STOPPED</span>')
    escaped = re.sub(r"\bWON\b", '<span class="pos">WON</span>', escaped)
    escaped = re.sub(r"\bLOST\b", '<span class="neg">LOST</span>', escaped)
    return escaped


def _wrap_html(body_text: str, refresh_seconds: int) -> str:
    safe = _colorize_html(html.escape(body_text))
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{refresh_seconds}">
<title>Bot Dashboard</title>
<style>
  :root {{
    --bg: #0b0d12;
    --fg: #d8dee9;
    --dim: #6c7280;
    --pos: #56d364;
    --neg: #f47174;
    --hdr: #79c0ff;
    --banner: #c3a6ff;
    --warn: #d4a72c;
    --card: #161922;
    --border: #232733;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{
    background: var(--bg);
    color: var(--fg);
    font-family: ui-monospace, "SF Mono", "Cascadia Mono", Menlo, Consolas, monospace;
    margin: 0;
    padding: 0;
    line-height: 1.5;
  }}
  body {{
    padding: 14px;
    font-size: 12px;
  }}
  .meta {{
    color: var(--dim);
    font-size: 10px;
    margin-bottom: 10px;
    text-align: center;
    letter-spacing: 0.05em;
    text-transform: uppercase;
  }}
  pre {{
    margin: 0;
    white-space: pre;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    background: var(--card);
    padding: 14px 12px;
    border-radius: 10px;
    border: 1px solid var(--border);
  }}
  .pos    {{ color: var(--pos); font-weight: 600; }}
  .neg    {{ color: var(--neg); font-weight: 600; }}
  .hdr    {{ color: var(--hdr); font-weight: 700; letter-spacing: 0.04em; }}
  .banner {{ color: var(--banner); font-weight: 700; }}
  .ok     {{ color: var(--pos); }}
  .warn   {{ color: var(--warn); }}
  @media (max-width: 600px) {{
    body {{ font-size: 11px; padding: 8px; }}
    pre  {{ padding: 10px 8px; }}
  }}
</style>
</head>
<body>
<div class="meta">auto-refresh {refresh_seconds}s · read-only · pull to refresh</div>
<pre>{safe}</pre>
</body>
</html>"""


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    mode: str = Query("paper", regex="^(paper|live)$"),
    refresh: int = Query(5, ge=2, le=60),
    no_prices: bool = Query(False),
) -> HTMLResponse:
    if not os.path.exists(_DB_PATH):
        return HTMLResponse("<h1>db not found</h1>", status_code=500)
    # Disable ANSI colors for HTML — they'd render as literal escape codes.
    # The HTML wrapper provides its own styling instead.
    saved = dict(stats.COLORS)
    for k in stats.COLORS:
        stats.COLORS[k] = ""
    try:
        with sqlite3.connect(_DB_PATH) as con:
            body = stats.render(con, mode=mode, skip_prices=no_prices)
    finally:
        stats.COLORS.update(saved)
    return HTMLResponse(_wrap_html(body, refresh))
