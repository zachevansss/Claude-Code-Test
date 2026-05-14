"""Live stats dashboard for the paper/live bot.

Usage:
    .venv/Scripts/python stats.py            # one-shot snapshot
    .venv/Scripts/python stats.py --watch    # auto-refresh every 5s

Reads directly from the SQLite DB so you don't need a server token. Computes the
same balance/exposure math the bot uses, plus a few extra columns useful for
eyeballing (recent fills, top markets, rejections aren't tracked, fill rate)."""
from __future__ import annotations

# Use the OS-native trust store on Windows + OneDrive + Microsoft Store Python,
# where certifi-based chain validation is broken (see main.py for context).
# Without this, /midpoints calls fail and the dashboard shows "?" for live prices.
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

import argparse
import calendar
import os
import re
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import httpx

# Central time zone (handles CST/CDT automatically based on date)
_CENTRAL_TZ = ZoneInfo("America/Chicago")

CLOB_BASE = "https://clob.polymarket.com"

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "copytrade.db")

# ANSI color helpers. Disabled by setting COLORS to a no-op dict via --no-color.
# On Windows 10+ Terminal/PowerShell renders these natively; older shells may
# need _enable_vt_mode() below.
COLORS = {
    "reset":  "\033[0m",
    "dim":    "\033[2m",
    "bold":   "\033[1m",
    "green":  "\033[32m",
    "red":    "\033[31m",
    "cyan":   "\033[36m",
    "yellow": "\033[33m",
    "gray":   "\033[90m",
    "bgreen": "\033[1;32m",
    "bred":   "\033[1;31m",
}


def _enable_vt_mode() -> None:
    """Best-effort enable of ANSI VT processing on legacy Windows consoles."""
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel = ctypes.windll.kernel32
        h = kernel.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_ulong()
        kernel.GetConsoleMode(h, ctypes.byref(mode))
        kernel.SetConsoleMode(h, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:  # noqa: BLE001
        pass


def c(name: str) -> str:
    return COLORS.get(name, "")


def fmt_money(x: float) -> str:
    return f"${x:,.2f}"


def fmt_pnl(x: float, width: int = 10) -> str:
    """Color-coded PnL: green for positive (with +), red for negative, dim for zero."""
    if x > 0:
        s = f"+{fmt_money(x)}"
        return f"{c('green')}{s:>{width}}{c('reset')}"
    if x < 0:
        s = f"-{fmt_money(abs(x))}"
        return f"{c('red')}{s:>{width}}{c('reset')}"
    return f"{c('dim')}{fmt_money(0):>{width}}{c('reset')}"


def heading(label: str, width: int = 68) -> str:
    """Section header with horizontal rule."""
    inner = f" {label.upper()} "
    pad = width - len(inner) - 4
    return f"{c('cyan')}─── {inner}{'─' * max(pad, 0)}{c('reset')}"


_ANSI_RE = re.compile(r"\033\[[\d;]+m")


def _visible_len(s: str) -> int:
    """Length excluding ANSI escape sequences."""
    return len(_ANSI_RE.sub("", s))


def _pad_visible(s: str, width: int) -> str:
    """Left-pad string to `width` visible chars (ANSI codes not counted)."""
    deficit = width - _visible_len(s)
    return s + (" " * max(deficit, 0))


def merge_columns(left: list[str], right: list[str], left_w: int = 44, gap: str = "  ") -> list[str]:
    """Merge two text blocks side-by-side. Each row pairs left[i] (padded to
    left_w visible chars) with right[i]. Shorter block padded with empty rows."""
    rows = max(len(left), len(right))
    out = []
    for i in range(rows):
        L = left[i] if i < len(left) else ""
        R = right[i] if i < len(right) else ""
        out.append(_pad_visible(L, left_w) + gap + R)
    return out


def render_pnl_calendar(daily: dict, starting: float) -> list[str]:
    """Monthly calendar grid showing realized $ and % per day for the
    current local month. Today's date is bolded. Returns a list of lines."""
    today = date.today()
    year, month = today.year, today.month
    month_name = calendar.month_name[month]
    cal = calendar.Calendar(firstweekday=0)  # Monday-first
    weeks = cal.monthdayscalendar(year, month)

    cell_w = 10
    cols = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    out = [heading(f"daily p&l calendar  ({month_name} {year})")]
    out.append(
        "  " + "".join(
            f"{c('dim')}{col:<{cell_w}}{c('reset')}" for col in cols
        ).rstrip()
    )

    for week in weeks:
        day_row = ["  "]
        amt_row = ["  "]
        pct_row = ["  "]
        for day in week:
            if day == 0:
                day_row.append(" " * cell_w)
                amt_row.append(" " * cell_w)
                pct_row.append(" " * cell_w)
                continue
            d = date(year, month, day)
            day_label = str(day)
            if d == today:
                day_label = f"{c('bold')}{c('cyan')}{day_label}{c('reset')}"
            day_row.append(_pad_visible(day_label, cell_w))

            entry = daily.get(d)
            if entry is None:
                amt_row.append(" " * cell_w)
                pct_row.append(" " * cell_w)
                continue
            pnl = entry["realized"]
            pct = (pnl / starting * 100.0) if starting else 0.0
            if pnl > 0:
                amt = f"{c('green')}+${pnl:.2f}{c('reset')}"
                pct_s = f"{c('green')}+{pct:.2f}%{c('reset')}"
            elif pnl < 0:
                amt = f"{c('red')}-${abs(pnl):.2f}{c('reset')}"
                pct_s = f"{c('red')}{pct:.2f}%{c('reset')}"
            else:
                amt = f"{c('dim')}$0.00{c('reset')}"
                pct_s = f"{c('dim')}0.00%{c('reset')}"
            amt_row.append(_pad_visible(amt, cell_w))
            pct_row.append(_pad_visible(pct_s, cell_w))
        out.append("".join(day_row).rstrip())
        out.append("".join(amt_row).rstrip())
        out.append("".join(pct_row).rstrip())
        out.append("")
    return out


def _utc_iso_to_local_date(ts: str) -> date | None:
    """Parse a UTC ISO timestamp from the trades table and return the calendar
    date in the system's local timezone. Trade.created_at is written by
    datetime.utcnow() (naive but representing UTC), so we tag it UTC and let
    .astimezone() shift it to local."""
    try:
        dt = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
        return dt.astimezone().date()
    except (TypeError, ValueError):
        return None


def _utc_iso_to_local_str(ts: str) -> str:
    """Same as above but returns a local-time string for display."""
    try:
        dt = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return ts[:19]


def _utc_iso_to_central_short(ts: str) -> str:
    """MM-DD HH:MM AM/PM in America/Chicago for fills/resolutions rows."""
    try:
        dt = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
        return dt.astimezone(_CENTRAL_TZ).strftime("%m-%d %I:%M %p")
    except (TypeError, ValueError):
        return ts[5:16]


def _age_seconds(ts: str | None) -> float | None:
    """Seconds elapsed since the naive-UTC timestamp `ts`. None if unparseable
    or missing. Used by the health banner to flag stale tick/signal columns."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except (TypeError, ValueError):
        return None


def _fmt_age(seconds: float | None) -> str:
    """Compact age string: '4s', '12m', '3h', '2d', or '—' for never."""
    if seconds is None:
        return "—"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def compute_daily_pnl(con: sqlite3.Connection, mode: str) -> dict[date, dict]:
    """Replay trades chronologically and bucket realized PnL by calendar day.

    Avg-price isn't stored on Trade rows, so we have to reconstruct it by
    walking buys/sells in order. Linear in number of trades — fine at our
    scale, won't be once trade volume gets big.

    Returns: {date: {realized, buys, sells, volume_buys, volume_sells}}"""
    positions: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"size": 0.0, "avg_price": 0.0}
    )
    daily: dict[date, dict] = defaultdict(
        lambda: {
            "realized": 0.0,
            "buys": 0,
            "sells": 0,
            "volume_buys": 0.0,
            "volume_sells": 0.0,
        }
    )

    rows = con.execute(
        "SELECT created_at, market_id, outcome, side, price, size, notional_usd"
        " FROM trades WHERE mode = ? ORDER BY id ASC",
        (mode,),
    ).fetchall()

    for ts, market_id, outcome, side, price, size, notional in rows:
        day = _utc_iso_to_local_date(ts)
        if day is None:
            continue
        pos = positions[(market_id, outcome)]
        bucket = daily[day]
        if side == "buy":
            new_size = pos["size"] + size
            if new_size > 0:
                pos["avg_price"] = (
                    pos["avg_price"] * pos["size"] + price * size
                ) / new_size
            pos["size"] = new_size
            bucket["buys"] += 1
            bucket["volume_buys"] += notional
        else:  # sell
            close_size = min(pos["size"], size)
            pnl = (price - pos["avg_price"]) * close_size
            bucket["realized"] += pnl
            bucket["sells"] += 1
            bucket["volume_sells"] += notional
            pos["size"] = max(0.0, pos["size"] - size)

    return daily


def fetch_midpoints(asset_ids: list[str]) -> dict[str, float]:
    """Batch-fetch current prices from Polymarket CLOB. Returns asset_id -> price.

    Tries /midpoints first (returns mid of best bid/ask for active markets).
    For any asset_id missing from that response — typically a resolved market
    where the orderbook has been removed — falls back to /last-trades-prices,
    which still works after resolution and reflects the final settled price.
    Empty dict on any error so the dashboard stays usable when CLOB is down."""
    if not asset_ids:
        return {}
    out: dict[str, float] = {}
    body = [{"token_id": a} for a in asset_ids]

    try:
        r = httpx.post(f"{CLOB_BASE}/midpoints", json=body, timeout=10.0)
        r.raise_for_status()
        for k, v in r.json().items():
            if v is not None:
                try:
                    out[k] = float(v)
                except (TypeError, ValueError):
                    pass
    except Exception:  # noqa: BLE001
        pass

    missing = [a for a in asset_ids if a not in out]
    if missing:
        try:
            r = httpx.post(
                f"{CLOB_BASE}/last-trades-prices",
                json=[{"token_id": a} for a in missing],
                timeout=10.0,
            )
            r.raise_for_status()
            for row in r.json():
                tok = row.get("token_id")
                price = row.get("price")
                if tok and price is not None:
                    try:
                        out[tok] = float(price)
                    except (TypeError, ValueError):
                        pass
        except Exception:  # noqa: BLE001
            pass

    return out


def render(con: sqlite3.Connection, mode: str = "paper", skip_prices: bool = False) -> str:
    out = []
    cur = con.cursor()

    settings = cur.execute(
        "SELECT mode, sizing_strategy, mirror_scale, mirror_power, min_trade_usd,"
        " max_percent_per_trade, max_exposure_per_market_pct,"
        " max_total_leverage_pct, daily_loss_cap_pct, paper_balance_usd"
        " FROM user_settings"
    ).fetchone()
    if not settings:
        return "(no user_settings rows — sign up first)"
    (
        run_mode, strategy, mirror_scale, mirror_power, min_trade,
        per_trade_pct, per_market_pct, max_leverage_pct,
        daily_loss_pct, starting,
    ) = settings

    bot = cur.execute(
        "SELECT status, last_started_at, last_error, last_tick_at,"
        " last_signal_emitted_at, last_poll_status"
        " FROM bot_instances LIMIT 1"
    ).fetchone()
    if bot:
        bot_status = bot[0]
        last_tick_at = bot[3]
        last_signal_at = bot[4]
        last_poll_status = bot[5]
        last_err = bot[2]
    else:
        bot_status = "(no bot record)"
        last_tick_at = last_signal_at = last_poll_status = last_err = None

    fills = cur.execute(
        "SELECT COUNT(*), COALESCE(SUM(notional_usd),0), COALESCE(AVG(notional_usd),0),"
        " COALESCE(MIN(notional_usd),0), COALESCE(MAX(notional_usd),0)"
        " FROM trades WHERE mode = ?", (mode,)
    ).fetchone()
    n_fills, total_notional, avg_notional, min_notional, max_notional = fills
    n_buys = cur.execute(
        "SELECT COUNT(*) FROM trades WHERE mode = ? AND side = 'buy'", (mode,)
    ).fetchone()[0]
    n_sells = cur.execute(
        "SELECT COUNT(*) FROM trades WHERE mode = ? AND side = 'sell'", (mode,)
    ).fetchone()[0]

    committed = 0.0
    realized = 0.0
    open_positions = 0
    # (market_id, outcome) -> (outcome, notional, size, avg_price, asset_id)
    # Keyed by (market_id, outcome) because outcome alone collides — many
    # markets share generic names like "Yes" / "No" / "Over" / "Under".
    market_data: dict[tuple[str, str], tuple[str, float, float, float, str | None]] = {}

    rows = cur.execute(
        "SELECT p.market_id, p.outcome, p.size, p.avg_price, p.realized_pnl_usd,"
        " (SELECT t.asset_id FROM trades t"
        "    WHERE t.user_id = p.user_id AND t.market_id = p.market_id"
        "      AND t.outcome = p.outcome AND t.mode = p.mode"
        "      AND t.asset_id IS NOT NULL LIMIT 1) AS asset_id,"
        " (SELECT t.title FROM trades t"
        "    WHERE t.user_id = p.user_id AND t.market_id = p.market_id"
        "      AND t.outcome = p.outcome AND t.mode = p.mode"
        "      AND t.title IS NOT NULL LIMIT 1) AS title"
        " FROM positions p WHERE p.mode = ?", (mode,)
    ).fetchall()
    for market_id, outcome, size, avg_price, pnl, asset_id, title in rows:
        notional = size * avg_price
        committed += notional
        realized += pnl
        if size > 0:
            open_positions += 1
            market_data[(market_id, outcome)] = (
                outcome, notional, size, avg_price, asset_id, market_id, title,
            )

    if mode == "paper":
        balance = max(0.0, starting - committed + realized)
    else:
        balance = float("nan")  # live balance is on-chain; not computed here
    per_trade_cap = balance * (per_trade_pct / 100.0)
    per_market_cap = balance * (per_market_pct / 100.0)
    runway = int(balance / max(avg_notional, 0.5)) if avg_notional > 0 else 0

    # Fetch live midpoints in one batch and compute unrealized PnL.
    asset_ids = [d[4] for d in market_data.values() if d[4]]
    midpoints = fetch_midpoints(asset_ids) if not skip_prices else {}
    unrealized = 0.0
    market_value = 0.0
    priced = 0
    for _outcome, _notional, size, avg_price, asset_id, _mid_id, _title in market_data.values():
        if asset_id and asset_id in midpoints:
            mid = midpoints[asset_id]
            market_value += size * mid
            unrealized += size * (mid - avg_price)
            priced += 1
        else:
            market_value += size * avg_price

    total_pnl = realized + unrealized
    pnl_pct = (total_pnl / starting * 100.0) if starting else 0.0
    account_value = balance + committed
    max_leverage_dollars = account_value * (max_leverage_pct / 100.0)
    current_leverage_pct = (committed / account_value * 100.0) if account_value else 0.0
    daily_loss_cap_dollars = account_value * (daily_loss_pct / 100.0)

    # Build each section as its own list of lines first, then compose layout.
    LEFT_W = 44

    # ── Banner + Health ──
    # Status colour: green when DB row says running AND tick is fresh, yellow
    # for stopped/unknown, red when the bot row says running but no tick has
    # landed recently (tick coroutine dead) or there's a stale last_error.
    tick_age = _age_seconds(last_tick_at)
    sig_age = _age_seconds(last_signal_at)
    tick_stale = bot_status == "running" and (tick_age is None or tick_age > 30)
    # Signal staleness only meaningful while running. >1h = warn (tracker may
    # be blind or source quiet), >4h while running = red (very likely tracker
    # stall — even a quiet source should produce something every few hours).
    sig_warn = bot_status == "running" and sig_age is not None and sig_age > 3600
    sig_critical = bot_status == "running" and sig_age is not None and sig_age > 14400

    if bot_status != "running":
        status_color = "yellow"
    elif tick_stale or last_err:
        status_color = "red"
    elif sig_critical:
        status_color = "red"
    elif sig_warn:
        status_color = "yellow"
    else:
        status_color = "green"

    banner = [
        f"{c('bold')}{c('cyan')}═══ POLYMARKET COPY-TRADE BOT ═══{c('reset')}  "
        f"{c('dim')}{run_mode.upper()}{c('reset')}  "
        f"{c(status_color)}● {bot_status.upper()}{c('reset')}"
    ]

    # Health line. Always shown when the bot row exists, so a healthy bot is
    # also confirmed visually (not just "no error means fine"). Red ⚠ flags
    # call attention to stale tick (loop dead) or stale signal (likely stall).
    if bot is not None:
        tick_part_color = "red" if tick_stale else "dim"
        if sig_critical:
            sig_part_color = "red"
        elif sig_warn:
            sig_part_color = "yellow"
        else:
            sig_part_color = "dim"
        warn_flag = ""
        if tick_stale or sig_critical:
            warn_flag = f"{c('red')}{c('bold')}⚠ {c('reset')}"
        elif sig_warn:
            warn_flag = f"{c('yellow')}{c('bold')}⚠ {c('reset')}"
        poll_str = last_poll_status or "—"
        # If poll status starts with "poll error" highlight it red.
        if last_poll_status and last_poll_status.startswith("poll error"):
            poll_str = f"{c('red')}{last_poll_status}{c('reset')}"
        else:
            poll_str = f"{c('dim')}{last_poll_status or '—'}{c('reset')}"
        health_line = (
            f"  {warn_flag}health: "
            f"{c('dim')}tick{c('reset')} {c(tick_part_color)}{_fmt_age(tick_age)}{c('reset')} · "
            f"{c('dim')}signal{c('reset')} {c(sig_part_color)}{_fmt_age(sig_age)}{c('reset')} · "
            f"{poll_str}"
        )
        banner.append(health_line)
        if last_err:
            banner.append(
                f"  {c('red')}{c('bold')}error:{c('reset')} {c('red')}{last_err[:200]}{c('reset')}"
            )

    # ── Account ──
    account = [heading("account", width=LEFT_W)]
    account.append(f"  {c('bold')}Total P&L{c('reset')}        "
                   f"{fmt_pnl(total_pnl)}  "
                   f"{c('green' if pnl_pct >= 0 else 'red')}{pnl_pct:+.2f}%{c('reset')}")
    account.append(f"  {c('dim')}  realized{c('reset')}         {fmt_pnl(realized)}")
    if asset_ids and not skip_prices:
        account.append(f"  {c('dim')}  unrealized{c('reset')}       {fmt_pnl(unrealized)}")
    account.append("")
    account.append(f"  Account Value     {c('bold')}{fmt_money(account_value):>11}{c('reset')}")
    account.append(f"  Available Cash    {fmt_money(balance):>11}")
    account.append(f"  Committed         {fmt_money(committed):>11}  {c('dim')}({open_positions} open){c('reset')}")
    if asset_ids and not skip_prices:
        account.append(f"  Mkt Value (open)  {fmt_money(market_value):>11}")

    # ── Risk Caps ──
    risk = [heading("risk caps", width=LEFT_W)]
    lev_color = "yellow" if current_leverage_pct > max_leverage_pct * 0.7 else "green"
    risk.append(f"  Per-Trade       {per_trade_pct:>5.1f}%  {fmt_money(per_trade_cap):>10}")
    pmkt_str = "off" if per_market_pct >= 100 else fmt_money(per_market_cap)
    risk.append(f"  Per-Market      {per_market_pct:>5.1f}%  {pmkt_str:>10}")
    risk.append(f"  Total Leverage  {max_leverage_pct:>5.1f}%  {fmt_money(max_leverage_dollars):>10}  "
                f"{c(lev_color)}{current_leverage_pct:>4.1f}% used{c('reset')}")
    risk.append(f"  Daily Loss      {daily_loss_pct:>5.1f}%  {fmt_money(daily_loss_cap_dollars):>10}")

    # ── Strategy ──
    strat = [heading("strategy", width=LEFT_W)]
    curve_note = "" if mirror_power == 1.0 else f" ^ {mirror_power}"
    strat.append(f"  {strategy}  x{mirror_scale}{curve_note}   "
                 f"{c('dim')}min{c('reset')} {fmt_money(min_trade)}")
    strat.append(f"  {c('dim')}{n_fills} fills · avg {fmt_money(avg_notional)} · "
                 f"{n_buys}B / {n_sells}S{c('reset')}")

    # ── Performance ──
    closed = cur.execute(
        "SELECT realized_pnl_usd FROM positions"
        " WHERE mode = ? AND size = 0 AND realized_pnl_usd != 0",
        (mode,),
    ).fetchall()
    wins = sum(1 for r in closed if r[0] > 0)
    losses = sum(1 for r in closed if r[0] < 0)
    total_closed = wins + losses
    win_rate = (wins / total_closed * 100.0) if total_closed else 0.0
    avg_win = (sum(r[0] for r in closed if r[0] > 0) / wins) if wins else 0.0
    avg_loss = (sum(r[0] for r in closed if r[0] < 0) / losses) if losses else 0.0
    perf: list[str] = []
    if total_closed:
        perf = [heading("performance", width=LEFT_W)]
        wr_color = "green" if win_rate >= 60 else "yellow" if win_rate >= 50 else "red"
        perf.append(f"  Closed Positions  {total_closed}")
        perf.append(f"  Win Rate          {c(wr_color)}{c('bold')}{win_rate:>5.1f}%{c('reset')}  "
                    f"{c('dim')}({c('green')}{wins}W{c('reset')}{c('dim')}/"
                    f"{c('red')}{losses}L{c('reset')}{c('dim')}){c('reset')}")
        perf.append(f"  Avg Win           {fmt_pnl(avg_win)}")
        perf.append(f"  Avg Loss          {fmt_pnl(avg_loss)}")

    # ── Daily P&L Calendar (right column) ──
    daily = compute_daily_pnl(con, mode)
    cal_lines = render_pnl_calendar(daily, starting) if daily else []

    # ── Biggest Winners / Losers ──
    # Pull the most recent sell price (resolution or actual sell) per closed
    # position so we can show buy → sell prices and % return.
    winners_sql = (
        "SELECT p.outcome, p.realized_pnl_usd, p.updated_at, p.avg_price,"
        " (SELECT t.price FROM trades t WHERE t.user_id=p.user_id"
        "    AND t.market_id=p.market_id AND t.outcome=p.outcome"
        "    AND t.mode=p.mode AND t.side='sell' ORDER BY t.id DESC LIMIT 1) AS exit_price,"
        " (SELECT t.title FROM trades t WHERE t.user_id=p.user_id"
        "    AND t.market_id=p.market_id AND t.outcome=p.outcome"
        "    AND t.mode=p.mode AND t.title IS NOT NULL LIMIT 1) AS title"
        " FROM positions p WHERE p.mode = ? AND p.realized_pnl_usd {} 0"
        " ORDER BY p.realized_pnl_usd {} LIMIT 5"
    )
    winners = cur.execute(winners_sql.format(">", "DESC"), (mode,)).fetchall()
    losers = cur.execute(winners_sql.format("<", "ASC"), (mode,)).fetchall()

    def _format_closed_row(row):
        outcome, pnl, updated_at, avg_buy, exit_price, title = row
        label = (title or "(unknown)")[:42]
        day = _utc_iso_to_local_str(updated_at)[:10] if updated_at else "       "
        if avg_buy and exit_price is not None:
            ret_pct = (exit_price - avg_buy) / avg_buy * 100.0
            color = "green" if ret_pct >= 0 else "red"
            sign = "+" if ret_pct >= 0 else ""
            prices = f"{avg_buy:.3f}→{exit_price:.3f}"
            ret_part = f"{c(color)}{sign}{ret_pct:>6.1f}%{c('reset')}"
        else:
            prices = "  ?  →  ?  "
            ret_part = f"{c('dim')}     ?{c('reset')}"
        return (
            f"  {c('dim')}{day}{c('reset')}  {fmt_pnl(pnl, width=9)}  "
            f"{ret_part}  {c('dim')}{prices}{c('reset')}  "
            f"{outcome:<16}  {c('dim')}{label}{c('reset')}"
        )

    win_block: list[str] = []
    if winners:
        win_block.append(heading("biggest winners ($)"))
        for row in winners:
            win_block.append(_format_closed_row(row))
    lose_block: list[str] = []
    if losers:
        lose_block.append(heading("biggest losers ($)"))
        for row in losers:
            lose_block.append(_format_closed_row(row))

    # Pull all closed positions with their exit price so we can rank by %
    # return as well as dollar PnL. Filter avg_price > 0 to avoid div-by-zero.
    all_closed = cur.execute(
        "SELECT p.outcome, p.realized_pnl_usd, p.updated_at, p.avg_price,"
        " (SELECT t.price FROM trades t WHERE t.user_id=p.user_id"
        "    AND t.market_id=p.market_id AND t.outcome=p.outcome"
        "    AND t.mode=p.mode AND t.side='sell' ORDER BY t.id DESC LIMIT 1) AS exit_price,"
        " (SELECT t.title FROM trades t WHERE t.user_id=p.user_id"
        "    AND t.market_id=p.market_id AND t.outcome=p.outcome"
        "    AND t.mode=p.mode AND t.title IS NOT NULL LIMIT 1) AS title"
        " FROM positions p WHERE p.mode = ?"
        "   AND p.size = 0 AND p.realized_pnl_usd != 0 AND p.avg_price > 0",
        (mode,),
    ).fetchall()

    def _pct_ret(row):
        _, _, _, avg, exit_p, _ = row
        if avg and exit_p is not None:
            return (exit_p - avg) / avg * 100.0
        return None

    pct_winners = sorted(
        [r for r in all_closed if (_pct_ret(r) or 0) > 0],
        key=lambda r: -(_pct_ret(r) or 0),
    )[:5]

    win_pct_block: list[str] = []
    if pct_winners:
        win_pct_block.append(heading("biggest winners (%)"))
        for row in pct_winners:
            win_pct_block.append(_format_closed_row(row))

    # ── Top Open Positions (full width) ──
    top = sorted(market_data.values(), key=lambda r: -r[1])[:8]
    open_block: list[str] = []
    if top:
        open_block.append(heading("top open positions"))
        for outcome, notional, size, avg_price, asset_id, mid_id, title in top:
            mid = midpoints.get(asset_id) if asset_id else None
            if mid is not None:
                mv = size * mid
                upnl = size * (mid - avg_price)
                ret_pct = (mid - avg_price) / avg_price * 100.0 if avg_price else 0.0
                pnl_part = fmt_pnl(upnl, width=10)
                color = "green" if ret_pct >= 0 else "red"
                sign = "+" if ret_pct >= 0 else ""
                pct_part = f"{c(color)}{sign}{ret_pct:>6.1f}%{c('reset')}"
                price_part = f"{c('dim')}{avg_price:.3f}→{mid:.3f}{c('reset')}"
            else:
                mv = notional
                pnl_part = f"{c('dim')}     n/a{c('reset')}"
                pct_part = f"{c('dim')}      ?{c('reset')}"
                price_part = f"{c('dim')}{avg_price:.3f}→  ?  {c('reset')}"
            header = (title[:66] + "…") if title and len(title) > 66 else (title or f"({mid_id[-8:]})")
            open_block.append(f"  {c('bold')}{header}{c('reset')}")
            open_block.append(
                f"      {outcome:<22} {c('dim')}cost{c('reset')} {fmt_money(notional):>7}  "
                f"{c('dim')}mv{c('reset')} {fmt_money(mv):>7}  "
                f"{c('dim')}upnl{c('reset')} {pnl_part}  {pct_part}  {price_part}"
            )

    # ── Recent Resolutions (full width) ──
    resolutions = cur.execute(
        "SELECT t.created_at, t.outcome, t.title, t.price, t.size,"
        " (SELECT p.realized_pnl_usd FROM positions p"
        "    WHERE p.user_id=t.user_id AND p.market_id=t.market_id"
        "      AND p.outcome=t.outcome AND p.mode=t.mode) AS pnl"
        " FROM trades t WHERE t.mode = ? AND t.status = 'resolved'"
        " ORDER BY t.id DESC LIMIT 8",
        (mode,),
    ).fetchall()
    res_block: list[str] = []
    if resolutions:
        res_block.append(heading("recent resolutions"))
        for ts, outcome, title, price, size, pnl in resolutions:
            won = price is not None and price > 0.5
            tag = f"{c('green')}WON {c('reset')}" if won else f"{c('red')}LOST{c('reset')}"
            label = (title or "(unknown)")[:50]
            pnl_part = fmt_pnl(pnl, width=9) if pnl is not None else f"{c('dim')}      n/a{c('reset')}"
            short_ts = _utc_iso_to_central_short(ts)
            res_block.append(f"  {c('dim')}{short_ts}{c('reset')}  {tag}  {pnl_part}   "
                             f"{outcome:<18} {c('dim')}{label}{c('reset')}")

    # ── Recent Fills (full width) ──
    last = cur.execute(
        "SELECT created_at, side, outcome, title, ROUND(price,4),"
        " ROUND(size,2), ROUND(notional_usd,2), status"
        " FROM trades WHERE mode = ? ORDER BY id DESC LIMIT 8",
        (mode,),
    ).fetchall()
    fill_block: list[str] = []
    if last:
        fill_block.append(heading("recent fills"))
        for ts, side, outcome, title, price, size, notional, status in last:
            if status == "resolved":
                tag = f"{c('cyan')}RESOLVE{c('reset')}"
            elif side == "buy":
                tag = f"{c('green')}BUY    {c('reset')}"
            else:
                tag = f"{c('red')}SELL   {c('reset')}"
            label = (title or "")[:48]
            short_ts = _utc_iso_to_central_short(ts)
            fill_block.append(
                f"  {c('dim')}{short_ts}{c('reset')}  {tag}  "
                f"{c('bold')}{fmt_money(notional):>7}{c('reset')}  "
                f"{outcome:<18} @ ${price:<6}  {c('dim')}{label}{c('reset')}"
            )

    # ───────── Compose layout ─────────
    out.extend(banner)
    out.append("")
    for block in (account, risk, strat, perf, cal_lines,
                  win_block, win_pct_block, lose_block,
                  open_block, res_block, fill_block):
        if block:
            out.extend(block)
            out.append("")

    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true", help="auto-refresh every 5s")
    ap.add_argument("--mode", default="paper", choices=("paper", "live"))
    ap.add_argument("--no-prices", action="store_true",
                    help="skip live midpoint fetch (faster, no PnL)")
    ap.add_argument("--resolve", action="store_true",
                    help="run resolution sweep before snapshot")
    ap.add_argument("--no-color", action="store_true",
                    help="disable ANSI colors (use on legacy terminals)")
    args = ap.parse_args()

    if args.no_color:
        for k in COLORS:
            COLORS[k] = ""
    else:
        _enable_vt_mode()

    if args.resolve:
        # One-shot sweep using the SQLAlchemy session.
        from src.database.session import SessionLocal
        from src.resolution.checker import check_resolutions
        with SessionLocal() as db:
            n = check_resolutions(db, user_id=1, mode=args.mode)
        print(f"resolved {n} position(s)\n")

    if not os.path.exists(DB_PATH):
        print(f"db not found at {DB_PATH}")
        sys.exit(1)

    while True:
        with sqlite3.connect(DB_PATH) as con:
            output = render(con, mode=args.mode, skip_prices=args.no_prices)
        if args.watch:
            os.system("cls" if os.name == "nt" else "clear")
            print(output)
            print("\n(refreshing every 5s — ctrl-c to exit)")
            try:
                time.sleep(5)
            except KeyboardInterrupt:
                break
        else:
            print(output)
            break


if __name__ == "__main__":
    main()
