"""Replay every paper trade as if we'd started with $1k and the live-mode
risk rules. Tracks balance / open exposure / daily loss cap throughout, and
reports trades taken vs skipped by reason.

Method:
- Live rules differ from paper only in: mirror_scale 0.075 (vs paper 0.1),
  min_trade_usd $1 (vs $2), max_total_leverage off, max_exposure_per_market_usd off,
  starting bank $1k (vs paper $2.5k).
- Same source signals; same prices; same resolution outcomes.
- Mirror formula is sub-linear (scale × sqrt(source)). With both runs at
  power=0.5, every non-cap-bound live notional = paper_notional × 0.75.
- A paper trade that fired had paper_notional >= $2, so live_notional >= $1.50
  -> always passes the $1 floor. Floor only loses trades that paper *also*
  skipped (so my sim does not "find new trades" -- it would need fresh
  source-API data for that, which is outside scope here).
- Per-trade cap (2% of bank): paper cap $50, live cap $20. After 0.75x scale,
  paper trades capped at $50 in paper map to ~$37.50 live -> capped to $20.
- Daily loss cap (10% of account value at start of day): applied per UTC date.

Output: balance trace, P&L vs paper, trade counts, capital utilization, days
where the daily loss cap would have fired.
"""
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime

PAPER_SCALE = 0.1
LIVE_SCALE = 0.075
SCALE_RATIO = LIVE_SCALE / PAPER_SCALE  # 0.75
PAPER_FLOOR = 2.0
LIVE_FLOOR = 1.0
LIVE_MAX_PCT_PER_TRADE = 0.02
LIVE_DAILY_LOSS_CAP_PCT = 0.10
START_BANK = 1000.0


def date_of(ts_str: str) -> str:
    # SQLite stores 'YYYY-MM-DD HH:MM:SS.ffffff'
    return ts_str[:10]


def main() -> None:
    con = sqlite3.connect("copytrade.db")
    rows = list(con.execute(
        "SELECT id, created_at, source_wallet, market_id, outcome, side, "
        "price, size, notional_usd "
        "FROM trades WHERE user_id=1 AND mode='paper' ORDER BY created_at ASC, id ASC"
    ))

    balance = START_BANK
    opens: dict[tuple, list[tuple[float, float, float]]] = {}  # (mkt, outcome) -> [(size, entry, cost), ...]
    daily_pnl: dict[str, float] = defaultdict(float)
    day_start_account: dict[str, float] = {}
    trades_taken = 0
    skips = Counter()
    peak_open_notional = 0.0
    peak_open_count = 0
    peak_balance = START_BANK
    min_balance = START_BANK
    insufficient_balance_events = 0
    daily_cap_blocks = Counter()  # date -> count
    pnl_by_day: dict[str, float] = defaultdict(float)
    trades_by_day: dict[str, int] = defaultdict(int)
    realized_pnl_total = 0.0

    def total_open_cost() -> float:
        return sum(c for q in opens.values() for _, _, c in q)

    for row in rows:
        _id, ts, src, mkt, outcome, side, price, size, paper_notional = row
        date = date_of(ts)
        if date not in day_start_account:
            day_start_account[date] = balance + total_open_cost()

        if src == "resolution":
            # Close all lots in this (market, outcome) at the settle price
            key = (mkt, outcome)
            if key not in opens or not opens[key]:
                # Paper closed a position we didn't open in this replay -> ignore
                continue
            settle_price = price
            realized = 0.0
            freed_cash = 0.0
            for lot_size, entry_px, cost in opens[key]:
                settle_value = lot_size * settle_price
                realized += settle_value - cost
                freed_cash += settle_value
            del opens[key]
            balance += freed_cash
            realized_pnl_total += realized
            daily_pnl[date] += realized
            pnl_by_day[date] += realized
            peak_balance = max(peak_balance, balance + total_open_cost())
            min_balance = min(min_balance, balance + total_open_cost())
            continue

        # Open trade: apply live rules
        # 1. Daily loss cap
        day_account = day_start_account[date]
        daily_loss_cap = day_account * LIVE_DAILY_LOSS_CAP_PCT
        today_loss = -daily_pnl[date] if daily_pnl[date] < 0 else 0.0
        if today_loss >= daily_loss_cap:
            skips["daily_loss_cap"] += 1
            daily_cap_blocks[date] += 1
            continue

        # 2. Mirror-scale conversion: 0.75x paper notional
        live_notional = paper_notional * SCALE_RATIO

        # 3. Floor check
        if live_notional < LIVE_FLOOR:
            skips["below_min_floor"] += 1
            continue

        # 4. Per-trade cap (2% of current bank, where bank = cash; matches risk.py)
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
        opens.setdefault((mkt, outcome), []).append((live_size, price, live_notional))
        trades_taken += 1
        trades_by_day[date] += 1
        tot_open = total_open_cost()
        peak_open_notional = max(peak_open_notional, tot_open)
        peak_open_count = max(peak_open_count, sum(len(q) for q in opens.values()))
        peak_balance = max(peak_balance, balance + tot_open)
        min_balance = min(min_balance, balance + tot_open)

    # Final state
    ending_bank = balance
    open_cost_remaining = total_open_cost()
    account_value_now = ending_bank + open_cost_remaining
    total_pnl = realized_pnl_total
    open_lots = sum(len(q) for q in opens.values())

    print("=" * 70)
    print(f"LIVE-RULES REPLAY (starting $1,000, applied to every paper trade)")
    print("=" * 70)
    print(f"Period:                {rows[0][1][:10]} -> {rows[-1][1][:10]}  ({len(day_start_account)} days)")
    print(f"Total paper signals:   {len(rows):,}  (opens + resolutions)")
    print(f"Trades taken:          {trades_taken:,}")
    print(f"Trades skipped:        {sum(skips.values()):,}")
    for reason, n in skips.most_common():
        print(f"    {reason:<22} {n:,}")
    print()
    print("Capital trace")
    print(f"  Starting bank:       ${START_BANK:,.2f}")
    print(f"  Ending CASH balance: ${ending_bank:,.2f}")
    print(f"  Open positions cost: ${open_cost_remaining:,.2f}   ({open_lots} open lots)")
    print(f"  Account value now:   ${account_value_now:,.2f}")
    print(f"  Realized P&L:        ${total_pnl:+,.2f}")
    print(f"  Realized ROI:        {(total_pnl / START_BANK) * 100:+.2f}%")
    print(f"  Peak account value:  ${peak_balance:,.2f}")
    print(f"  Min account value:   ${min_balance:,.2f}")
    print()
    print("Capital utilization")
    print(f"  Peak open notional:  ${peak_open_notional:,.2f}   ({peak_open_notional / START_BANK * 100:.1f}% of starting bank)")
    print(f"  Peak concurrent open lots: {peak_open_count:,}")
    print(f"  Insufficient-balance skips: {insufficient_balance_events}")
    print()
    print(f"Daily-loss-cap hits ({LIVE_DAILY_LOSS_CAP_PCT*100:.0f}% rule):")
    if not daily_cap_blocks:
        print("  None — the daily loss cap never fired.")
    else:
        for date, n in sorted(daily_cap_blocks.items()):
            print(f"  {date}: blocked {n} trade(s) (daily P&L was ${daily_pnl[date]:+,.2f}, cap ${day_start_account[date]*LIVE_DAILY_LOSS_CAP_PCT:,.2f})")
    print()
    print("Per-day breakdown (top 10 worst days, top 5 best days)")
    by_day_sorted = sorted(pnl_by_day.items(), key=lambda kv: kv[1])
    print("  worst:")
    for d, p in by_day_sorted[:10]:
        print(f"    {d}: ${p:+8.2f}   ({trades_by_day.get(d,0)} trades opened)")
    print("  best:")
    for d, p in by_day_sorted[-5:][::-1]:
        print(f"    {d}: ${p:+8.2f}   ({trades_by_day.get(d,0)} trades opened)")
    print()
    print("Reference: paper run was +$1,030.61 on $2,500 bank (+41.2%)")
    print(f"           live replay  {'+' if total_pnl >= 0 else ''}${total_pnl:.2f} on $1,000 bank ({total_pnl/START_BANK*100:+.2f}%)")


if __name__ == "__main__":
    main()
