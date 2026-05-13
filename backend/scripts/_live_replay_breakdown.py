"""Detailed breakdown of how the $1k live config (mirror 0.075, min $1.50,
2% per-trade cap, 10% daily-loss cap, no leverage cap) would have performed
from day 1 of the paper bot.

Uses the paper-mode trade ledger as the source of source-trader signals
(which captures every source bet >= $400 notional, matching the live $1.50
floor's source-side threshold of $400)."""
from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from statistics import mean

USER = 1
MODE = "paper"
DB = "copytrade.db"

START_BANK = 1000.0
SCALE_RATIO = 0.075 / 0.1  # 0.75
LIVE_FLOOR = 1.5
LIVE_MAX_PCT_PER_TRADE = 0.02
LIVE_DAILY_LOSS_CAP_PCT = 0.10


def date_of(ts: str) -> str:
    return ts[:10]


def simulate(trades: list[tuple]) -> dict:
    balance = START_BANK
    opens: dict[tuple, list[tuple[float, float, float]]] = {}
    daily_pnl: dict[str, float] = defaultdict(float)
    day_start_account: dict[str, float] = {}
    trades_taken = 0
    skips = Counter()
    insufficient = 0
    peak_open_cost = 0.0
    peak_open_count = 0
    peak_account = START_BANK
    min_account = START_BANK
    pnl_by_day: dict[str, float] = defaultdict(float)
    opens_by_day: dict[str, int] = defaultdict(int)
    closes_by_day: dict[str, int] = defaultdict(int)
    realized_total = 0.0
    wins = 0
    losses = 0
    partial = 0
    open_sizes = []  # list of live_notional per opened lot
    win_pnls = []
    loss_pnls = []
    biggest_win = (0.0, "", "")  # (pnl, outcome, date)
    biggest_loss = (0.0, "", "")
    trade_pnls_by_position: dict[tuple, float] = defaultdict(float)

    def total_cost() -> float:
        return sum(c for q in opens.values() for _, _, c in q)

    for tid, src, market, outcome, side, price, size, notional, ts in trades:
        date = date_of(ts)
        if date not in day_start_account:
            day_start_account[date] = balance + total_cost()

        if src == "resolution" or side == "sell":
            key = (market, outcome)
            if key not in opens or not opens[key]:
                continue
            settle = price
            position_pnl = 0.0
            position_cost = 0.0
            for lot_size, entry_px, cost in opens[key]:
                settle_value = lot_size * settle
                realized = settle_value - cost
                balance += settle_value
                realized_total += realized
                daily_pnl[date] += realized
                pnl_by_day[date] += realized
                position_pnl += realized
                position_cost += cost
            del opens[key]
            closes_by_day[date] += 1
            if settle >= 0.99:
                wins += 1
                win_pnls.append(position_pnl)
            elif settle <= 0.01:
                losses += 1
                loss_pnls.append(position_pnl)
            else:
                partial += 1
            if position_pnl > biggest_win[0]:
                biggest_win = (position_pnl, outcome, date)
            if position_pnl < biggest_loss[0]:
                biggest_loss = (position_pnl, outcome, date)
            peak_account = max(peak_account, balance + total_cost())
            min_account = min(min_account, balance + total_cost())
            continue

        # OPEN
        day_account = day_start_account[date]
        daily_cap = day_account * LIVE_DAILY_LOSS_CAP_PCT
        today_loss = -daily_pnl[date] if daily_pnl[date] < 0 else 0.0
        if today_loss >= daily_cap:
            skips["daily_loss_cap"] += 1
            continue
        live_notional = notional * SCALE_RATIO
        if live_notional < LIVE_FLOOR:
            skips["below_min_floor"] += 1
            continue
        max_per_trade = balance * LIVE_MAX_PCT_PER_TRADE
        live_notional = min(live_notional, max_per_trade)
        if live_notional <= 0:
            skips["zero_after_cap"] += 1
            continue
        if live_notional > balance:
            insufficient += 1
            skips["insufficient_balance"] += 1
            continue
        live_size = live_notional / price
        balance -= live_notional
        opens.setdefault((market, outcome), []).append((live_size, price, live_notional))
        trades_taken += 1
        opens_by_day[date] += 1
        open_sizes.append(live_notional)
        tc = total_cost()
        peak_open_cost = max(peak_open_cost, tc)
        peak_open_count = max(peak_open_count, sum(len(q) for q in opens.values()))
        peak_account = max(peak_account, balance + tc)
        min_account = min(min_account, balance + tc)

    remaining_cost = total_cost()
    open_lots = sum(len(q) for q in opens.values())
    return locals()


def fmt(x: float, signed: bool = False) -> str:
    return f"{'+' if signed and x >= 0 else ''}${x:,.2f}"


def main() -> None:
    con = sqlite3.connect(DB)
    trades = list(con.execute(
        """SELECT id, source_wallet, market_id, outcome, side, price, size, notional_usd, created_at
           FROM trades WHERE user_id=? AND mode=?
           ORDER BY created_at ASC, id ASC""",
        (USER, MODE),
    ))
    print(f"Source data: {len(trades):,} paper-mode trade rows")

    s = simulate(trades)
    pnl_by_day = s["pnl_by_day"]
    opens_by_day = s["opens_by_day"]
    closes_by_day = s["closes_by_day"]
    daily_pnl = s["daily_pnl"]
    day_start_account = s["day_start_account"]

    bar = "─" * 76
    print()
    print(bar)
    print(f"$1k LIVE REPLAY — full 11-day paper window")
    print(f"  bank=$1000  scale=0.075  power=0.5  min_trade=$1.50  max_per_trade=2%")
    print(f"  daily_loss_cap=10%  max_leverage=off  slippage=1.5%")
    print(bar)

    # Headline
    pnl = s["realized_total"]
    end_acct = s["balance"] + s["remaining_cost"]
    print()
    print(f"  Period:             {trades[0][8][:10]} to {trades[-1][8][:10]}  ({len(day_start_account)} days)")
    print(f"  Starting bank:      ${START_BANK:,.2f}")
    print(f"  Realized P&L:       {fmt(pnl, signed=True)}")
    print(f"  Realized ROI:       {pnl/START_BANK*100:+.2f}%")
    print(f"  Ending cash:        ${s['balance']:,.2f}")
    print(f"  Open positions:     ${s['remaining_cost']:,.2f}  ({s['open_lots']} lots)")
    print(f"  Account value end:  ${end_acct:,.2f}")
    print(f"  Total return:       {(end_acct-START_BANK)/START_BANK*100:+.2f}%")

    # Trade activity
    print()
    print(f"  ── Trade activity ──")
    print(f"  BUYs opened:        {s['trades_taken']:,}")
    print(f"  Avg trade size:     ${mean(s['open_sizes']):.3f}" if s['open_sizes'] else "")
    print(f"  Smallest trade:     ${min(s['open_sizes']):.3f}" if s['open_sizes'] else "")
    print(f"  Largest trade:      ${max(s['open_sizes']):.3f}" if s['open_sizes'] else "")
    print(f"  Total volume:       ${sum(s['open_sizes']):,.2f}")
    print(f"  BUYs skipped:       {sum(s['skips'].values()):,}")
    for r, n in s['skips'].most_common() if isinstance(s['skips'], Counter) else sorted(s['skips'].items(), key=lambda x: -x[1]):
        print(f"      {r:<22} {n:,}")

    # Win/loss
    print()
    print(f"  ── Win / loss ──")
    print(f"  Closed positions:   {s['wins'] + s['losses'] + s['partial']:,}")
    print(f"  Winners (~$1):      {s['wins']:,}")
    print(f"  Losers (~$0):       {s['losses']:,}")
    print(f"  Partials (mid):     {s['partial']:,}")
    wl = s['wins'] + s['losses']
    if wl:
        print(f"  Win rate (W/W+L):   {s['wins']/wl*100:.2f}%")
    if s['win_pnls']:
        print(f"  Avg winning trade:  {fmt(mean(s['win_pnls']), signed=True)}")
    if s['loss_pnls']:
        print(f"  Avg losing trade:   {fmt(mean(s['loss_pnls']), signed=True)}")
    print(f"  Biggest win:        {fmt(s['biggest_win'][0], signed=True)}  on {s['biggest_win'][2]}  ({s['biggest_win'][1][:40]})")
    print(f"  Biggest loss:       {fmt(s['biggest_loss'][0], signed=True)}  on {s['biggest_loss'][2]}  ({s['biggest_loss'][1][:40]})")

    # Capital
    print()
    print(f"  ── Capital ──")
    print(f"  Peak account value: ${s['peak_account']:,.2f}  ({s['peak_account']/START_BANK*100:.1f}% of start)")
    print(f"  Min account value:  ${s['min_account']:,.2f}  ({s['min_account']/START_BANK*100:.1f}% of start)")
    print(f"  Peak open notional: ${s['peak_open_cost']:,.2f}  (recycled capital from realized wins)")
    print(f"  Peak open lots:     {s['peak_open_count']:,}  concurrent")
    print(f"  Insufficient-cash:  {s['insufficient']}  (zero = never starved)")

    # Daily breakdown
    print()
    print(f"  ── Daily breakdown ──")
    print(f"  {'date':<12} {'P&L':>10} {'ROI %':>8} {'opens':>6} {'closes':>7} {'day-start-bank':>15}")
    cum = START_BANK
    for d in sorted(set(list(opens_by_day.keys()) + list(pnl_by_day.keys()))):
        dp = pnl_by_day.get(d, 0.0)
        op = opens_by_day.get(d, 0)
        cl = closes_by_day.get(d, 0)
        ds = day_start_account.get(d, cum)
        roi = dp / ds * 100 if ds else 0
        cum = ds + dp
        marker = "*" if dp > 0 else ("" if dp == 0 else "")
        print(f"  {d:<12} {dp:>+10.2f} {roi:>+7.2f}% {op:>6} {cl:>7} {ds:>15,.2f}")

    # Wins vs losses count by day
    winning_days = sum(1 for d in pnl_by_day.values() if d > 0)
    losing_days = sum(1 for d in pnl_by_day.values() if d < 0)
    print()
    print(f"  Days positive: {winning_days}   Days negative: {losing_days}   Days flat: {len(pnl_by_day)-winning_days-losing_days}")
    if pnl_by_day:
        best_d = max(pnl_by_day, key=lambda d: pnl_by_day[d])
        worst_d = min(pnl_by_day, key=lambda d: pnl_by_day[d])
        print(f"  Best day:  {best_d}  {fmt(pnl_by_day[best_d], signed=True)}")
        print(f"  Worst day: {worst_d}  {fmt(pnl_by_day[worst_d], signed=True)}")

    # Annualized projection
    if pnl > 0 and len(day_start_account) >= 7:
        daily_avg = pnl / len(day_start_account)
        proj_30 = daily_avg * 30
        proj_365 = (1 + pnl/START_BANK) ** (365/len(day_start_account)) * START_BANK - START_BANK
        print()
        print(f"  ── Naive forward projection (purely illustrative) ──")
        print(f"  Avg daily P&L:        {fmt(daily_avg, signed=True)}")
        print(f"  Naive 30-day project: {fmt(proj_30, signed=True)} ({proj_30/START_BANK*100:+.1f}%)")
        print(f"  Compounded 1-year:    {fmt(proj_365, signed=True)} ({proj_365/START_BANK*100:+.1f}%)")
        print(f"  (DON'T trust these — 11 days is way too small a sample for forward-looking math)")


if __name__ == "__main__":
    main()
