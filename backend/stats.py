"""Live stats dashboard for the paper/live bot.

Usage:
    .venv/Scripts/python stats.py            # one-shot snapshot
    .venv/Scripts/python stats.py --watch    # auto-refresh every 5s

Reads directly from the SQLite DB so you don't need a server token. Computes the
same balance/exposure math the bot uses, plus a few extra columns useful for
eyeballing (recent fills, top markets, rejections aren't tracked, fill rate)."""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from collections import defaultdict

import httpx

CLOB_BASE = "https://clob.polymarket.com"

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "copytrade.db")


def fmt_money(x: float) -> str:
    return f"${x:,.2f}"


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
        "SELECT mode, sizing_strategy, mirror_scale, min_trade_usd,"
        " max_percent_per_trade, max_exposure_per_market_pct,"
        " daily_loss_cap_usd, paper_balance_usd FROM user_settings"
    ).fetchone()
    if not settings:
        return "(no user_settings rows — sign up first)"
    (
        run_mode, strategy, mirror_scale, min_trade,
        per_trade_pct, per_market_pct, daily_loss_cap, starting,
    ) = settings

    bot = cur.execute(
        "SELECT status, last_started_at, last_error FROM bot_instances LIMIT 1"
    ).fetchone()
    bot_status = bot[0] if bot else "(no bot record)"

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
    # outcome -> (outcome, notional, size, avg_price, asset_id)
    market_data: dict[str, tuple[str, float, float, float, str | None]] = {}

    # Pull each open position together with the asset_id from any matching
    # trade row, so we can batch midpoint lookups for unrealized PnL.
    rows = cur.execute(
        "SELECT p.outcome, p.size, p.avg_price, p.realized_pnl_usd,"
        " (SELECT t.asset_id FROM trades t"
        "    WHERE t.user_id = p.user_id AND t.market_id = p.market_id"
        "      AND t.outcome = p.outcome AND t.mode = p.mode"
        "      AND t.asset_id IS NOT NULL LIMIT 1) AS asset_id"
        " FROM positions p WHERE p.mode = ?", (mode,)
    ).fetchall()
    for outcome, size, avg_price, pnl, asset_id in rows:
        notional = size * avg_price
        committed += notional
        realized += pnl
        if size > 0:
            open_positions += 1
            market_data[outcome] = (outcome, notional, size, avg_price, asset_id)

    if mode == "paper":
        balance = max(0.0, starting - committed + realized)
    else:
        balance = float("nan")  # live balance is on-chain; not computed here
    per_trade_cap = balance * (per_trade_pct / 100.0)
    per_market_cap = balance * (per_market_pct / 100.0)
    runway = int(balance / max(avg_notional, 0.5)) if avg_notional > 0 else 0

    out.append(f"=== {run_mode.upper()} STATS ===")
    out.append(f"bot status:           {bot_status}")
    out.append(f"strategy:             {strategy}  (mirrorx{mirror_scale}  min ${min_trade:.2f})")
    out.append(f"per-trade cap:        {per_trade_pct:.2f}% = {fmt_money(per_trade_cap)}")
    out.append(f"per-market cap:       {per_market_pct:.2f}% = {fmt_money(per_market_cap)}")
    out.append(f"daily loss cap:       {fmt_money(daily_loss_cap)}")
    out.append("")
    out.append(f"fills:                {n_fills}  ({n_buys} buys, {n_sells} sells)")
    out.append(f"avg notional:         {fmt_money(avg_notional)}  (range {fmt_money(min_notional)}-{fmt_money(max_notional)})")
    out.append(f"capital deployed:     {fmt_money(total_notional)}")
    out.append("")
    # Fetch live midpoints in one batch and compute unrealized PnL.
    asset_ids = [d[4] for d in market_data.values() if d[4]]
    midpoints = fetch_midpoints(asset_ids) if not skip_prices else {}
    unrealized = 0.0
    market_value = 0.0
    priced = 0
    for _outcome, _notional, size, avg_price, asset_id in market_data.values():
        if asset_id and asset_id in midpoints:
            mid = midpoints[asset_id]
            market_value += size * mid
            unrealized += size * (mid - avg_price)
            priced += 1
        else:
            # No price -> treat current value as cost basis
            market_value += size * avg_price

    total_pnl = realized + unrealized
    pnl_pct = (total_pnl / starting * 100.0) if starting else 0.0

    out.append(f"available balance:    {fmt_money(balance)}")
    out.append(f"committed in open:    {fmt_money(committed)}")
    if asset_ids and not skip_prices:
        out.append(f"current market value: {fmt_money(market_value)}  (priced {priced}/{len(asset_ids)})")
        out.append(f"unrealized PnL:       {fmt_money(unrealized)}")
    out.append(f"realized PnL:         {fmt_money(realized)}")
    out.append(f"TOTAL PnL:            {fmt_money(total_pnl)}  ({pnl_pct:+.2f}% of bankroll)")
    out.append(f"open positions:       {open_positions}")
    out.append(f"runway @ avg fill:    ~{runway} more trades")
    out.append("")

    top = sorted(market_data.values(), key=lambda r: -r[1])[:8]
    if top:
        out.append("top open markets (cost / mkt val / unrealized):")
        for outcome, notional, size, avg_price, asset_id in top:
            mid = midpoints.get(asset_id) if asset_id else None
            if mid is not None:
                mv = size * mid
                upnl = size * (mid - avg_price)
                pnl_str = f"{fmt_money(upnl):>9}"
            else:
                mv = notional
                pnl_str = "    (n/a)"
            pct = (notional / per_market_cap * 100.0) if per_market_cap else 0.0
            bar_len = min(20, max(0, int(pct / 5)))
            bar = "#" * bar_len + ("+" if pct > 100 else "")
            out.append(
                f"  {outcome:<24} cost={fmt_money(notional):>9}  mv={fmt_money(mv):>9}  upnl={pnl_str}  {pct:>5.1f}%  {bar}"
            )
        out.append("")

    last = cur.execute(
        "SELECT created_at, side, outcome, ROUND(price,4), ROUND(size,2), ROUND(notional_usd,2)"
        " FROM trades WHERE mode = ? ORDER BY id DESC LIMIT 8", (mode,)
    ).fetchall()
    if last:
        out.append("most recent fills:")
        for ts, side, outcome, price, size, notional in last:
            out.append(f"  {ts[:19]}  {side:<4}  {outcome:<24} @${price:<7}  size={size:<8}  ${notional}")

    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true", help="auto-refresh every 5s")
    ap.add_argument("--mode", default="paper", choices=("paper", "live"))
    ap.add_argument("--no-prices", action="store_true",
                    help="skip live midpoint fetch (faster, no PnL)")
    args = ap.parse_args()

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
