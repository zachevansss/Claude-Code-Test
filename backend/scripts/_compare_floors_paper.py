"""Side-by-side comparison: replay the full 11-day paper-bot trade ledger
through live rules at two different min_trade_usd floors.

Why this is a valid comparison:
  - Paper used mirror_scale=0.1, min_trade=$2. So paper recorded every source
    trade with source_notional >= $400 (since 0.1*sqrt(400)=$2).
  - Live uses mirror_scale=0.075. With a $1.50 floor it accepts source >= $400
    (0.075*sqrt(400)=$1.50). With a $2.00 floor it accepts source >= $711.
  - $1.50 floor: every paper trade survives, scaled to 0.75x.
  - $2.00 floor: only paper trades with paper_notional >= $2.67 (=> source>=$711)
    survive, also scaled to 0.75x.
  - Per-trade cap shrinks proportionally with the bank ($50 paper -> $20 live).

What we cannot model from paper data alone:
  - Trades with $1.00-floor would catch (live_notional in [1.00, 1.50)) — paper
    skipped these. Out of scope for this comparison.

Usage: python scripts/_compare_floors_paper.py
"""
from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict

USER = 1
MODE = "paper"
DB = "copytrade.db"

# Live-mode parameters (held constant across both scenarios)
START_BANK = 1000.0
SCALE_RATIO = 0.075 / 0.1  # 0.75 — converts paper notionals to live equivalent
LIVE_MAX_PCT_PER_TRADE = 0.02
LIVE_DAILY_LOSS_CAP_PCT = 0.10


def date_of(ts: str) -> str:
    return ts[:10]


def simulate(trades: list[tuple], *, floor: float) -> dict:
    """Replay paper-bot trades through live rules at the given floor.

    `trades` is sorted-by-created_at list of:
        (id, source_wallet, market_id, outcome, side, price, size, notional_usd,
         created_at)
    """
    balance = START_BANK
    # FIFO lots per (market, outcome)
    opens: dict[tuple, list[tuple[float, float, float]]] = {}
    daily_pnl: dict[str, float] = defaultdict(float)
    day_start_account: dict[str, float] = {}
    trades_taken = 0
    skips = Counter()
    insufficient_balance_events = 0
    peak_open_cost = 0.0
    peak_open_count = 0
    peak_account = START_BANK
    pnl_by_day: dict[str, float] = defaultdict(float)
    opens_by_day: dict[str, int] = defaultdict(int)
    closes_by_day: dict[str, int] = defaultdict(int)
    realized_total = 0.0
    wins = 0
    losses = 0
    partial = 0  # closes at mid-range price (rare)

    def total_cost() -> float:
        return sum(c for q in opens.values() for _, _, c in q)

    for tid, src, market, outcome, side, price, size, notional, ts in trades:
        date = date_of(ts)
        if date not in day_start_account:
            day_start_account[date] = balance + total_cost()

        # ────────── CLOSE (resolution or sell)
        if src == "resolution" or side == "sell":
            key = (market, outcome)
            if key not in opens or not opens[key]:
                # Position never opened in our sim (probably below floor) — skip
                continue
            settle = price
            for lot_size, entry_px, cost in opens[key]:
                settle_value = lot_size * settle
                realized = settle_value - cost
                balance += settle_value
                realized_total += realized
                daily_pnl[date] += realized
                pnl_by_day[date] += realized
            del opens[key]
            closes_by_day[date] += 1
            if settle >= 0.99:
                wins += 1
            elif settle <= 0.01:
                losses += 1
            else:
                partial += 1
            peak_account = max(peak_account, balance + total_cost())
            continue

        # ────────── OPEN (BUY)
        # 1. Daily loss cap (% of account value at day start)
        day_account = day_start_account[date]
        daily_cap = day_account * LIVE_DAILY_LOSS_CAP_PCT
        today_loss = -daily_pnl[date] if daily_pnl[date] < 0 else 0.0
        if today_loss >= daily_cap:
            skips["daily_loss_cap"] += 1
            continue

        # 2. Scale paper notional down to live equivalent
        live_notional = notional * SCALE_RATIO

        # 3. Floor check
        if live_notional < floor:
            skips["below_min_floor"] += 1
            continue

        # 4. Per-trade cap (2% of cash)
        max_per_trade = balance * LIVE_MAX_PCT_PER_TRADE
        live_notional = min(live_notional, max_per_trade)
        if live_notional <= 0:
            skips["zero_after_cap"] += 1
            continue

        # 5. Insufficient cash
        if live_notional > balance:
            insufficient_balance_events += 1
            skips["insufficient_balance"] += 1
            continue

        live_size = live_notional / price
        balance -= live_notional
        opens.setdefault((market, outcome), []).append((live_size, price, live_notional))
        trades_taken += 1
        opens_by_day[date] += 1
        tc = total_cost()
        peak_open_cost = max(peak_open_cost, tc)
        peak_open_count = max(peak_open_count, sum(len(q) for q in opens.values()))
        peak_account = max(peak_account, balance + tc)

    remaining_cost = total_cost()
    open_lots = sum(len(q) for q in opens.values())
    return {
        "floor": floor,
        "trades_taken": trades_taken,
        "skips": dict(skips),
        "insufficient_balance": insufficient_balance_events,
        "wins": wins,
        "losses": losses,
        "partial": partial,
        "win_rate": wins / (wins + losses) * 100 if (wins + losses) else 0.0,
        "realized_pnl": realized_total,
        "roi_pct": realized_total / START_BANK * 100,
        "ending_cash": balance,
        "open_cost_remaining": remaining_cost,
        "open_lots_remaining": open_lots,
        "peak_open_cost": peak_open_cost,
        "peak_open_count": peak_open_count,
        "peak_account": peak_account,
        "pnl_by_day": dict(pnl_by_day),
        "opens_by_day": dict(opens_by_day),
        "closes_by_day": dict(closes_by_day),
    }


def fmt(x: float, signed: bool = False) -> str:
    if signed:
        return f"{'+' if x >= 0 else ''}${x:,.2f}"
    return f"${x:,.2f}"


def main() -> None:
    con = sqlite3.connect(DB)
    rows = list(con.execute(
        """
        SELECT id, source_wallet, market_id, outcome, side, price, size, notional_usd, created_at
        FROM trades
        WHERE user_id=? AND mode=?
        ORDER BY created_at ASC, id ASC
        """,
        (USER, MODE),
    ))
    print(f"Loaded {len(rows):,} paper-mode trade rows")

    a = simulate(rows, floor=1.5)
    b = simulate(rows, floor=2.0)

    # Side-by-side header
    bar = "─" * 78
    print()
    print(bar)
    print(f"PAPER-DATA REPLAY UNDER LIVE RULES — $1k bank, scale 0.075, max_per_trade 2%, daily_loss_cap 10%")
    print(bar)
    print(f"  {'metric':<34}  {'$1.50 floor (live plan)':>22}    {'$2.00 floor (paper)':>22}")
    print(f"  {'-'*34}  {'-'*22}    {'-'*22}")

    def row(label: str, av: str, bv: str) -> None:
        print(f"  {label:<34}  {av:>22}    {bv:>22}")

    row("BUYs taken",                  f"{a['trades_taken']:,}",                       f"{b['trades_taken']:,}")
    row("BUYs skipped (below floor)",  f"{a['skips'].get('below_min_floor',0):,}",     f"{b['skips'].get('below_min_floor',0):,}")
    row("BUYs skipped (daily cap)",    f"{a['skips'].get('daily_loss_cap',0):,}",      f"{b['skips'].get('daily_loss_cap',0):,}")
    row("BUYs skipped (insufficient cash)", f"{a['insufficient_balance']:,}",          f"{b['insufficient_balance']:,}")
    row("", "", "")
    row("--- P&L ---", "", "")
    row("realized P&L",                fmt(a['realized_pnl'], signed=True),             fmt(b['realized_pnl'], signed=True))
    row("realized ROI",                f"{a['roi_pct']:+.2f}%",                         f"{b['roi_pct']:+.2f}%")
    row("ending cash",                 fmt(a['ending_cash']),                           fmt(b['ending_cash']))
    row("open cost remaining",         fmt(a['open_cost_remaining']),                   fmt(b['open_cost_remaining']))
    row("open lots remaining",         f"{a['open_lots_remaining']:,}",                 f"{b['open_lots_remaining']:,}")
    row("account value at end",        fmt(a['ending_cash']+a['open_cost_remaining']),  fmt(b['ending_cash']+b['open_cost_remaining']))
    row("", "", "")
    row("--- win/loss ---", "", "")
    row("winners (settle≈$1)",         f"{a['wins']:,}",                                f"{b['wins']:,}")
    row("losers (settle≈$0)",          f"{a['losses']:,}",                              f"{b['losses']:,}")
    row("partials (mid)",              f"{a['partial']:,}",                             f"{b['partial']:,}")
    row("win rate (W / W+L)",          f"{a['win_rate']:.2f}%",                         f"{b['win_rate']:.2f}%")
    row("", "", "")
    row("--- capital ---", "", "")
    row("peak open notional",          fmt(a['peak_open_cost']),                        fmt(b['peak_open_cost']))
    row("peak open % of bank",         f"{a['peak_open_cost']/START_BANK*100:.1f}%",    f"{b['peak_open_cost']/START_BANK*100:.1f}%")
    row("peak concurrent open lots",   f"{a['peak_open_count']:,}",                     f"{b['peak_open_count']:,}")
    row("peak account value",          fmt(a['peak_account']),                          fmt(b['peak_account']))

    # Daily P&L side by side
    print()
    print("Daily realized P&L")
    print(f"  {'date':<12}  {'$1.50 floor':>14}  trades  |  {'$2.00 floor':>14}  trades")
    all_days = sorted(set(list(a['pnl_by_day'].keys()) + list(b['pnl_by_day'].keys())))
    for d in all_days:
        pa = a['pnl_by_day'].get(d, 0.0)
        pb = b['pnl_by_day'].get(d, 0.0)
        ta = a['opens_by_day'].get(d, 0)
        tb = b['opens_by_day'].get(d, 0)
        print(f"  {d:<12}  {pa:>+14.2f}  {ta:>6}  |  {pb:>+14.2f}  {tb:>6}")

    # Diff
    print()
    diff = a['realized_pnl'] - b['realized_pnl']
    diff_acct = (a['ending_cash']+a['open_cost_remaining']) - (b['ending_cash']+b['open_cost_remaining'])
    print(f"Difference ($1.50 - $2.00):  realized P&L {diff:+.2f}   account value {diff_acct:+.2f}")
    extra = a['trades_taken'] - b['trades_taken']
    print(f"$1.50 floor took {extra:+,} more trades ({extra/b['trades_taken']*100:+.1f}% more) than $2 floor")


if __name__ == "__main__":
    main()
