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

# Sections that should span the full grid width (they have wide content)
_WIDE_SECTIONS = {
    "DAILY P&L CALENDAR",
    "TOP OPEN POSITIONS",
    "RECENT RESOLUTIONS",
    "RECENT FILLS",
}


def _colorize_inline(escaped: str) -> str:
    """Color spans for already-escaped HTML text inside a card body."""
    escaped = re.sub(r"(\+\$[\d,]+\.\d{2})", r'<span class="pos">\1</span>', escaped)
    escaped = re.sub(r"(-\$[\d,]+\.\d{2})", r'<span class="neg">\1</span>', escaped)
    escaped = re.sub(r"(\+\d+\.\d{2}%)", r'<span class="pos">\1</span>', escaped)
    escaped = re.sub(r"(?<![\d.])(-\d+\.\d{2}%)", r'<span class="neg">\1</span>', escaped)
    escaped = escaped.replace("● RUNNING", '<span class="ok">● RUNNING</span>')
    escaped = escaped.replace("● STOPPED", '<span class="warn">● STOPPED</span>')
    escaped = re.sub(r"\bWON\b", '<span class="pos">WON</span>', escaped)
    escaped = re.sub(r"\bLOST\b", '<span class="neg">LOST</span>', escaped)
    return escaped


def _split_into_cards(rendered: str) -> list[tuple[str, str]]:
    """Split rendered dashboard text into (heading, body) cards.

    Lines before the first heading are returned as the special "BANNER" card.
    Heading lines look like ``───  ACCOUNT  ───────────────────…``."""
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
        # Trim leading/trailing blank lines per card
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines and title == "BANNER":
            continue
        out.append((title, "\n".join(lines)))
    return out


def _wrap_html(body_text: str, refresh_seconds: int) -> str:
    cards = _split_into_cards(body_text)
    card_html_parts: list[str] = []
    for title, body in cards:
        body_safe = _colorize_inline(html.escape(body))
        css_class = "card"
        if title == "BANNER":
            css_class += " full"
        elif any(title.startswith(name) for name in _WIDE_SECTIONS):
            css_class += " wide"
        if title == "BANNER":
            # Banner has no title bar — content speaks for itself.
            card_html_parts.append(
                f'<div class="{css_class}"><pre>{body_safe}</pre></div>'
            )
        else:
            card_html_parts.append(
                f'<div class="{css_class}">'
                f'<div class="card-title">{html.escape(title)}</div>'
                f'<pre>{body_safe}</pre></div>'
            )
    cards_block = "\n".join(card_html_parts)

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
    --card2: #1a1e29;
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
    margin-bottom: 12px;
    text-align: center;
    letter-spacing: 0.05em;
    text-transform: uppercase;
  }}
  .grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
    gap: 12px;
    align-items: start;
  }}
  .card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 12px 14px;
    overflow: hidden;
  }}
  .card.wide  {{ grid-column: span 2; }}
  .card.full  {{ grid-column: 1 / -1; background: var(--card2); }}
  .card-title {{
    color: var(--hdr);
    font-weight: 700;
    letter-spacing: 0.06em;
    font-size: 10px;
    text-transform: uppercase;
    border-bottom: 1px solid var(--border);
    padding-bottom: 6px;
    margin-bottom: 8px;
  }}
  pre {{
    margin: 0;
    white-space: pre;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }}
  .pos    {{ color: var(--pos); font-weight: 600; }}
  .neg    {{ color: var(--neg); font-weight: 600; }}
  .ok     {{ color: var(--pos); }}
  .warn   {{ color: var(--warn); }}
  /* Mobile: stack everything in one column */
  @media (max-width: 760px) {{
    body {{ font-size: 11px; padding: 8px; }}
    .grid {{
      grid-template-columns: 1fr;
      gap: 8px;
    }}
    .card.wide, .card.full {{ grid-column: 1; }}
    .card {{ padding: 10px 12px; }}
  }}
</style>
</head>
<body>
<div class="meta">auto-refresh {refresh_seconds}s · read-only · pull to refresh</div>
<div class="grid">
{cards_block}
</div>
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
