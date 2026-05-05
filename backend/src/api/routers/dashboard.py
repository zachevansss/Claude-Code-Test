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


def _wrap_html(body_text: str, refresh_seconds: int) -> str:
    safe = html.escape(body_text)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{refresh_seconds}">
<title>Bot Dashboard</title>
<style>
  body {{
    background: #0f1115;
    color: #d8dee9;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    margin: 0;
    padding: 12px;
    font-size: 12px;
    line-height: 1.4;
  }}
  pre {{
    margin: 0;
    white-space: pre;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }}
  .meta {{
    color: #6c7280;
    font-size: 10px;
    margin-bottom: 8px;
  }}
  @media (max-width: 600px) {{
    body {{ font-size: 11px; padding: 8px; }}
  }}
</style>
</head>
<body>
<div class="meta">auto-refreshing every {refresh_seconds}s · read-only · view from any device</div>
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
    with sqlite3.connect(_DB_PATH) as con:
        body = stats.render(con, mode=mode, skip_prices=no_prices)
    return HTMLResponse(_wrap_html(body, refresh))
