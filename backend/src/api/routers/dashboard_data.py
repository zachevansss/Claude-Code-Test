"""Structured dashboard data — used by /dashboard.json and the HTML view.

Pulls the same numbers stats.py prints to the CLI, but returns them as a
nested dict so the new web dashboard can render charts, tables, and counters
without parsing pre-formatted text.

Deliberately duplicates a small amount of SQL with stats.py rather than
refactoring the CLI tool — keeping the two independent means breaking one
doesn't ripple to the other. The shared helpers (compute_daily_pnl,
fetch_midpoints) live in stats.py and are imported.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from typing import Any

# stats.py is at backend/stats.py and exports compute_daily_pnl + fetch_midpoints.
import stats  # type: ignore


def _utc_iso(dt_str: str | None) -> str | None:
    """Normalize a naive-UTC isoformat string from SQLite back to ISO-with-Z.
    Trade.created_at and bot_instances columns are written via datetime.utcnow()
    so they're stored without a timezone. The frontend assumes UTC."""
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError):
        return dt_str


def _age_seconds(dt_str: str | None) -> int | None:
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int((datetime.now(timezone.utc) - dt).total_seconds())
    except (TypeError, ValueError):
        return None


def compute_dashboard_data(
    con: sqlite3.Connection,
    mode: str = "paper",
    skip_prices: bool = False,
) -> dict[str, Any]:
    """Single-shot dashboard data computation. Returns a JSON-serializable dict
    with all the sections the HTML dashboard renders."""
    cur = con.cursor()

    # --- user settings (caps + sizing config) ---
    settings_row = cur.execute(
        "SELECT mode, sizing_strategy, mirror_scale, mirror_power, min_trade_usd,"
        " max_percent_per_trade, max_exposure_per_market_pct,"
        " max_total_leverage_pct, daily_loss_cap_pct, paper_balance_usd"
        " FROM user_settings"
    ).fetchone()
    if not settings_row:
        return {"error": "no user_settings rows — sign up first"}
    (
        run_mode, strategy, mirror_scale, mirror_power, min_trade,
        per_trade_pct, per_market_pct, max_leverage_pct,
        daily_loss_pct, starting_bankroll,
    ) = settings_row

    # --- bot health row ---
    bot_row = cur.execute(
        "SELECT status, last_started_at, last_error, last_tick_at,"
        " last_signal_emitted_at, last_poll_status"
        " FROM bot_instances LIMIT 1"
    ).fetchone()
    if bot_row:
        bot_status, last_started, last_err, last_tick, last_signal, last_poll = bot_row
    else:
        bot_status = "no bot"
        last_started = last_err = last_tick = last_signal = last_poll = None

    # --- fills aggregates ---
    fills = cur.execute(
        "SELECT COUNT(*), COALESCE(SUM(notional_usd),0), COALESCE(AVG(notional_usd),0)"
        " FROM trades WHERE mode = ?", (mode,)
    ).fetchone()
    n_fills, total_notional, avg_notional = fills
    n_buys = cur.execute(
        "SELECT COUNT(*) FROM trades WHERE mode = ? AND side = 'buy'", (mode,)
    ).fetchone()[0]
    n_sells = cur.execute(
        "SELECT COUNT(*) FROM trades WHERE mode = ? AND side = 'sell'", (mode,)
    ).fetchone()[0]

    # --- positions (open + closed) ---
    committed = 0.0
    realized = 0.0
    open_count = 0
    # (market_id, outcome) -> tuple
    open_data: list[dict] = []

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
    open_raw: list[dict] = []
    for market_id, outcome, size, avg_price, pnl, asset_id, title in rows:
        notional = size * avg_price
        committed += notional
        realized += pnl
        if size > 0:
            open_count += 1
            open_raw.append({
                "market_id": market_id,
                "outcome": outcome,
                "size": size,
                "avg_price": avg_price,
                "cost_basis": notional,
                "asset_id": asset_id,
                "title": title,
            })

    # --- live midpoints for unrealized PnL ---
    asset_ids = [p["asset_id"] for p in open_raw if p["asset_id"]]
    midpoints = stats.fetch_midpoints(asset_ids) if (asset_ids and not skip_prices) else {}
    unrealized = 0.0
    market_value = 0.0
    for p in open_raw:
        mid = midpoints.get(p["asset_id"]) if p["asset_id"] else None
        if mid is not None:
            mv = p["size"] * mid
            upnl = p["size"] * (mid - p["avg_price"])
            ret_pct = (mid - p["avg_price"]) / p["avg_price"] * 100.0 if p["avg_price"] else 0.0
            p["current_price"] = mid
            p["market_value"] = mv
            p["unrealized_pnl"] = upnl
            p["return_pct"] = ret_pct
            unrealized += upnl
            market_value += mv
        else:
            p["current_price"] = None
            p["market_value"] = p["cost_basis"]
            p["unrealized_pnl"] = None
            p["return_pct"] = None
            market_value += p["cost_basis"]

    # Sort open positions by cost basis descending.
    open_raw.sort(key=lambda r: -r["cost_basis"])

    # --- balance + leverage ---
    if mode == "paper":
        balance = max(0.0, starting_bankroll - committed + realized)
    else:
        balance = None  # live balance lives on-chain; client can hit /wallet
    total_pnl = realized + unrealized
    pnl_pct = (total_pnl / starting_bankroll * 100.0) if starting_bankroll else 0.0
    account_value = (balance or 0.0) + committed

    per_trade_dollars = (balance or 0.0) * (per_trade_pct / 100.0)
    per_market_dollars = (balance or 0.0) * (per_market_pct / 100.0)
    max_leverage_dollars = account_value * (max_leverage_pct / 100.0)
    current_leverage_pct = (committed / account_value * 100.0) if account_value else 0.0
    daily_loss_dollars = account_value * (daily_loss_pct / 100.0)

    # --- performance (closed positions) ---
    closed_pnls = cur.execute(
        "SELECT realized_pnl_usd FROM positions"
        " WHERE mode = ? AND size = 0 AND realized_pnl_usd != 0",
        (mode,),
    ).fetchall()
    wins = sum(1 for r in closed_pnls if r[0] > 0)
    losses = sum(1 for r in closed_pnls if r[0] < 0)
    total_closed = wins + losses
    win_rate = (wins / total_closed * 100.0) if total_closed else 0.0
    avg_win = (sum(r[0] for r in closed_pnls if r[0] > 0) / wins) if wins else 0.0
    avg_loss = (sum(r[0] for r in closed_pnls if r[0] < 0) / losses) if losses else 0.0

    # --- daily PnL (calendar + cumulative line) ---
    daily = stats.compute_daily_pnl(con, mode)
    today = date.today()
    # Calendar: every day of the current local month with realized/pct/etc.
    import calendar as _cal
    cal = _cal.Calendar(firstweekday=6)  # Sunday=6 (so week starts on Sunday)
    weeks = cal.monthdayscalendar(today.year, today.month)
    cal_days: list[list[dict | None]] = []
    for week in weeks:
        week_row: list[dict | None] = []
        for day in week:
            if day == 0:
                week_row.append(None)
                continue
            d = date(today.year, today.month, day)
            entry = daily.get(d)
            week_row.append({
                "day": day,
                "date": d.isoformat(),
                "is_today": d == today,
                "realized": entry["realized"] if entry else 0.0,
                "pct": (entry["realized"] / starting_bankroll * 100.0)
                       if entry and starting_bankroll else 0.0,
                "trades": (entry["buys"] + entry["sells"]) if entry else 0,
                "has_activity": entry is not None,
            })
        cal_days.append(week_row)

    # Cumulative PnL timeline (running sum over all days, daily-pnl-replay style).
    timeline_sorted = sorted(daily.items())
    timeline: list[dict] = []
    running = 0.0
    for d, info in timeline_sorted:
        running += info["realized"]
        timeline.append({
            "date": d.isoformat(),
            "cumulative_pnl": round(running, 2),
            "daily_realized": round(info["realized"], 2),
        })

    # --- biggest winners ($) and losers ($) ---
    winners_dollar_rows = cur.execute(
        "SELECT p.outcome, p.realized_pnl_usd, p.updated_at, p.avg_price,"
        " (SELECT t.price FROM trades t WHERE t.user_id=p.user_id"
        "    AND t.market_id=p.market_id AND t.outcome=p.outcome"
        "    AND t.mode=p.mode AND t.side='sell' ORDER BY t.id DESC LIMIT 1) AS exit_price,"
        " (SELECT t.title FROM trades t WHERE t.user_id=p.user_id"
        "    AND t.market_id=p.market_id AND t.outcome=p.outcome"
        "    AND t.mode=p.mode AND t.title IS NOT NULL LIMIT 1) AS title"
        " FROM positions p WHERE p.mode = ? AND p.realized_pnl_usd > 0"
        " ORDER BY p.realized_pnl_usd DESC LIMIT 5",
        (mode,),
    ).fetchall()
    losers_dollar_rows = cur.execute(
        "SELECT p.outcome, p.realized_pnl_usd, p.updated_at, p.avg_price,"
        " (SELECT t.price FROM trades t WHERE t.user_id=p.user_id"
        "    AND t.market_id=p.market_id AND t.outcome=p.outcome"
        "    AND t.mode=p.mode AND t.side='sell' ORDER BY t.id DESC LIMIT 1) AS exit_price,"
        " (SELECT t.title FROM trades t WHERE t.user_id=p.user_id"
        "    AND t.market_id=p.market_id AND t.outcome=p.outcome"
        "    AND t.mode=p.mode AND t.title IS NOT NULL LIMIT 1) AS title"
        " FROM positions p WHERE p.mode = ? AND p.realized_pnl_usd < 0"
        " ORDER BY p.realized_pnl_usd ASC LIMIT 5",
        (mode,),
    ).fetchall()

    def _fmt_closed(row):
        outcome, pnl, updated_at, avg_buy, exit_price, title = row
        ret_pct = None
        if avg_buy and exit_price is not None and avg_buy > 0:
            ret_pct = (exit_price - avg_buy) / avg_buy * 100.0
        return {
            "outcome": outcome,
            "title": title,
            "pnl": pnl,
            "updated_at": _utc_iso(updated_at),
            "avg_buy_price": avg_buy,
            "exit_price": exit_price,
            "return_pct": ret_pct,
        }

    winners_dollar = [_fmt_closed(r) for r in winners_dollar_rows]
    losers_dollar = [_fmt_closed(r) for r in losers_dollar_rows]

    # --- biggest winners (%) ---
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
    closed_with_ret = [_fmt_closed(r) for r in all_closed]
    winners_pct = sorted(
        [c for c in closed_with_ret if (c["return_pct"] or 0) > 0],
        key=lambda c: -(c["return_pct"] or 0),
    )[:5]

    # --- recent resolutions (last 8) ---
    resolutions_rows = cur.execute(
        "SELECT t.created_at, t.outcome, t.title, t.price, t.size,"
        " (SELECT p.realized_pnl_usd FROM positions p"
        "    WHERE p.user_id=t.user_id AND p.market_id=t.market_id"
        "      AND p.outcome=t.outcome AND p.mode=t.mode) AS pnl"
        " FROM trades t WHERE t.mode = ? AND t.status = 'resolved'"
        " ORDER BY t.id DESC LIMIT 8",
        (mode,),
    ).fetchall()
    resolutions = [
        {
            "timestamp_utc": _utc_iso(ts),
            "outcome": outcome,
            "title": title,
            "won": (price is not None and price > 0.5),
            "exit_price": price,
            "size": size,
            "pnl": pnl,
        }
        for ts, outcome, title, price, size, pnl in resolutions_rows
    ]

    # --- recent fills (last 12) ---
    fills_rows = cur.execute(
        "SELECT created_at, side, outcome, title, price, size, notional_usd, status"
        " FROM trades WHERE mode = ? ORDER BY id DESC LIMIT 12",
        (mode,),
    ).fetchall()
    recent_fills = [
        {
            "timestamp_utc": _utc_iso(ts),
            "side": side,
            "outcome": outcome,
            "title": title,
            "price": price,
            "size": size,
            "notional": notional,
            "status": status,
        }
        for ts, side, outcome, title, price, size, notional, status in fills_rows
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "mode": mode,
        "bot": {
            "status": bot_status,
            "last_started_at": _utc_iso(last_started),
            "last_tick_at": _utc_iso(last_tick),
            "last_signal_emitted_at": _utc_iso(last_signal),
            "last_poll_status": last_poll,
            "last_error": last_err,
            "tick_age_seconds": _age_seconds(last_tick),
            "signal_age_seconds": _age_seconds(last_signal),
        },
        "account": {
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(pnl_pct, 2),
            "realized": round(realized, 2),
            "unrealized": round(unrealized, 2),
            "account_value": round(account_value, 2),
            "balance": round(balance, 2) if balance is not None else None,
            "committed": round(committed, 2),
            "open_positions": open_count,
            "market_value": round(market_value, 2),
            "starting_bankroll": round(starting_bankroll, 2),
        },
        "risk": {
            "per_trade_pct": per_trade_pct,
            "per_trade_dollars": round(per_trade_dollars, 2),
            "per_market_pct": per_market_pct,
            "per_market_dollars": round(per_market_dollars, 2),
            "max_leverage_pct": max_leverage_pct,
            "max_leverage_dollars": round(max_leverage_dollars, 2),
            "current_leverage_pct": round(current_leverage_pct, 2),
            "daily_loss_cap_pct": daily_loss_pct,
            "daily_loss_cap_dollars": round(daily_loss_dollars, 2),
        },
        "strategy": {
            "sizing_strategy": strategy,
            "mirror_scale": mirror_scale,
            "mirror_power": mirror_power,
            "min_trade_usd": min_trade,
            "total_fills": n_fills,
            "buys": n_buys,
            "sells": n_sells,
            "avg_notional": round(avg_notional, 2),
            "total_notional": round(total_notional, 2),
        },
        "performance": {
            "total_closed": total_closed,
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
        },
        "daily_pnl_calendar": {
            "month_label": today.strftime("%B %Y"),
            "year": today.year,
            "month": today.month,
            "first_day_of_week": 6,  # Sunday
            "weeks": cal_days,
        },
        "pnl_timeline": timeline,
        "winners_dollar": winners_dollar,
        "winners_pct": winners_pct,
        "losers_dollar": losers_dollar,
        "open_positions": open_raw,
        "recent_resolutions": resolutions,
        "recent_fills": recent_fills,
    }
