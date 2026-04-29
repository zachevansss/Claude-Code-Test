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

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "copytrade.db")


def fmt_money(x: float) -> str:
    return f"${x:,.2f}"


def render(con: sqlite3.Connection, mode: str = "paper") -> str:
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
    market_notional: dict[str, tuple[str, float]] = {}
    for outcome, size, avg_price, pnl in cur.execute(
        "SELECT outcome, size, avg_price, realized_pnl_usd"
        " FROM positions WHERE mode = ?", (mode,)
    ):
        notional = size * avg_price
        committed += notional
        realized += pnl
        if size > 0:
            open_positions += 1
            market_notional[outcome] = (outcome, notional)

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
    out.append(f"available balance:    {fmt_money(balance)}")
    out.append(f"committed in open:    {fmt_money(committed)}")
    out.append(f"realized PnL:         {fmt_money(realized)}")
    out.append(f"open positions:       {open_positions}")
    out.append(f"runway @ avg fill:    ~{runway} more trades")
    out.append("")

    top = sorted(market_notional.values(), key=lambda r: -r[1])[:8]
    if top:
        out.append("top open markets by exposure:")
        for outcome, notional in top:
            pct = (notional / per_market_cap * 100.0) if per_market_cap else 0.0
            bar = "#" * int(pct / 5)
            out.append(f"  {outcome:<28} {fmt_money(notional):>9}  {pct:>5.1f}% of cap  {bar}")
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
    args = ap.parse_args()

    if not os.path.exists(DB_PATH):
        print(f"db not found at {DB_PATH}")
        sys.exit(1)

    while True:
        with sqlite3.connect(DB_PATH) as con:
            output = render(con, mode=args.mode)
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
