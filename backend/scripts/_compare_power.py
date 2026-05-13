"""Side-by-side: current live config (mirror_power=0.5, sqrt curve) vs
the same config without the curve (mirror_power=1.0, linear sizing).

Linear sizing on a small bank with a 2% per-trade cap mostly produces
trades that hit the cap (every trade ≈ $20), so this is mainly an
illustration of how aggressive linear sizing gets on the source trader's
typical $400–$10,000 bets.

To recover source_notional from a paper trade row:
  paper used scale=0.1, power=0.5
  paper_notional = 0.1 × source^0.5
  source = (paper_notional / 0.1)^2 = 100 × paper_notional^2

Then for each scenario:
  live_notional = scale × source^power"""
from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from statistics import mean

USER = 1
MODE = "paper"
DB = "copytrade.db"

START_BANK = 1000.0
LIVE_FLOOR = 2.0
LIVE_MAX_PCT_PER_TRADE = 0.02
LIVE_DAILY_LOSS_CAP_PCT = 0.10
PAPER_SCALE = 0.1
PAPER_POWER = 0.5
PAPER_CAP_USD = 50.0  # paper's per-trade cap: 2% of $2.5k

LIVE_SCALE = 0.075


def date_of(ts: str) -> str:
    return ts[:10]


def recover_source_notional(paper_notional: float) -> float:
    """Reverse the paper sizing formula. Paper used scale=0.1 power=0.5,
    so source = (paper / 0.1)^(1/0.5) = (paper * 10)^2 = 100 * paper^2.

    Returns a lower-bound estimate when paper hit its per-trade cap."""
    return 100.0 * paper_notional * paper_notional


def simulate(trades: list[tuple], *, power: float, label: str) -> dict:
    """Replay paper trades with sizing live_notional = LIVE_SCALE × source^power."""
    balance = START_BANK
    opens: dict[tuple, list[tuple[float, float, float]]] = {}
    daily_pnl: dict[str, float] = defaultdict(float)
    day_start_account: dict[str, float] = {}
    trades_taken = 0
    skips = Counter()
    cap_hits = 0
    insufficient = 0
    peak_open_cost = 0.0
    peak_open_count = 0
    peak_account = START_BANK
    min_account = START_BANK
    pnl_by_day: dict[str, float] = defaultdict(float)
    opens_by_day: dict[str, int] = defaultdict(int)
    realized_total = 0.0
    wins = 0
    losses = 0
    partial = 0
    open_sizes = []

    def total_cost() -> float:
        return sum(c for q in opens.values() for _, _, c in q)

    for tid, src, market, outcome, side, price, size, paper_notional, ts in trades:
        date = date_of(ts)
        if date not in day_start_account:
            day_start_account[date] = balance + total_cost()

        if src == "resolution" or side == "sell":
            key = (market, outcome)
            if key not in opens or not opens[key]:
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
            if settle >= 0.99:
                wins += 1
            elif settle <= 0.01:
                losses += 1
            else:
                partial += 1
            peak_account = max(peak_account, balance + total_cost())
            min_account = min(min_account, balance + total_cost())
            continue

        # Daily loss cap
        day_account = day_start_account[date]
        daily_cap = day_account * LIVE_DAILY_LOSS_CAP_PCT
        today_loss = -daily_pnl[date] if daily_pnl[date] < 0 else 0.0
        if today_loss >= daily_cap:
            skips["daily_loss_cap"] += 1
            continue

        source_notional = recover_source_notional(paper_notional)
        live_notional = LIVE_SCALE * (source_notional ** power)

        if live_notional < LIVE_FLOOR:
            skips["below_min_floor"] += 1
            continue

        max_per_trade = balance * LIVE_MAX_PCT_PER_TRADE
        if live_notional > max_per_trade:
            cap_hits += 1
            live_notional = max_per_trade
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

    return {
        "label": label,
        "power": power,
        "trades_taken": trades_taken,
        "skips": dict(skips),
        "cap_hits": cap_hits,
        "insufficient": insufficient,
        "wins": wins,
        "losses": losses,
        "partial": partial,
        "realized_pnl": realized_total,
        "ending_cash": balance,
        "open_cost": total_cost(),
        "open_lots": sum(len(q) for q in opens.values()),
        "peak_open_cost": peak_open_cost,
        "peak_open_count": peak_open_count,
        "peak_account": peak_account,
        "min_account": min_account,
        "open_sizes": open_sizes,
        "pnl_by_day": dict(pnl_by_day),
        "opens_by_day": dict(opens_by_day),
    }


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
    print(f"Loaded {len(trades):,} paper trade rows\n")

    a = simulate(trades, power=0.5, label="curve (sqrt)")
    b = simulate(trades, power=1.0, label="linear (flat)")

    bar = "─" * 80
    print(bar)
    print(f"CURRENT LIVE CONFIG  vs  SAME WITHOUT mirror_power CURVE")
    print(f"  bank=$1000  scale=0.075  min=$2.00  max_per_trade=2%  daily_loss=10%")
    print(bar)

    def row(label, av, bv):
        print(f"  {label:<32}  {av:>22}    {bv:>22}")

    print(f"  {'metric':<32}  {'power=0.5 (sqrt)':>22}    {'power=1.0 (linear)':>22}")
    print(f"  {'-'*32}  {'-'*22}    {'-'*22}")
    row("sizing formula", "0.075×√(source)", "0.075×source")
    row("", "", "")
    row("BUYs taken", f"{a['trades_taken']:,}", f"{b['trades_taken']:,}")
    row("BUYs below floor", f"{a['skips'].get('below_min_floor',0):,}", f"{b['skips'].get('below_min_floor',0):,}")
    row("BUYs hit per-trade $20 cap", f"{a['cap_hits']:,}", f"{b['cap_hits']:,}")
    row("BUYs skipped (insufficient $)", f"{a['insufficient']:,}", f"{b['insufficient']:,}")
    row("", "", "")
    row("avg trade size",
        f"${mean(a['open_sizes']):.2f}" if a['open_sizes'] else "—",
        f"${mean(b['open_sizes']):.2f}" if b['open_sizes'] else "—")
    row("largest trade",
        f"${max(a['open_sizes']):.2f}" if a['open_sizes'] else "—",
        f"${max(b['open_sizes']):.2f}" if b['open_sizes'] else "—")
    row("total volume traded",
        f"${sum(a['open_sizes']):,.2f}",
        f"${sum(b['open_sizes']):,.2f}")
    row("", "", "")
    row("realized P&L",
        fmt(a['realized_pnl'], signed=True),
        fmt(b['realized_pnl'], signed=True))
    row("realized ROI",
        f"{a['realized_pnl']/START_BANK*100:+.2f}%",
        f"{b['realized_pnl']/START_BANK*100:+.2f}%")
    row("ending account value",
        fmt(a['ending_cash']+a['open_cost']),
        fmt(b['ending_cash']+b['open_cost']))
    row("", "", "")
    row("winners", f"{a['wins']:,}", f"{b['wins']:,}")
    row("losers", f"{a['losses']:,}", f"{b['losses']:,}")
    wlA = a['wins']+a['losses']
    wlB = b['wins']+b['losses']
    row("win rate",
        f"{a['wins']/wlA*100:.2f}%" if wlA else "—",
        f"{b['wins']/wlB*100:.2f}%" if wlB else "—")
    row("", "", "")
    row("peak open notional",
        fmt(a['peak_open_cost']),
        fmt(b['peak_open_cost']))
    row("peak open % of bank",
        f"{a['peak_open_cost']/START_BANK*100:.1f}%",
        f"{b['peak_open_cost']/START_BANK*100:.1f}%")
    row("peak account value",
        fmt(a['peak_account']),
        fmt(b['peak_account']))
    row("min account value",
        fmt(a['min_account']),
        fmt(b['min_account']))

    # Daily breakdown
    print()
    print("Daily realized P&L")
    print(f"  {'date':<12}  {'power=0.5':>14}  {'power=1.0':>14}")
    all_days = sorted(set(list(a['pnl_by_day'].keys()) + list(b['pnl_by_day'].keys())))
    for d in all_days:
        pa = a['pnl_by_day'].get(d, 0.0)
        pb = b['pnl_by_day'].get(d, 0.0)
        print(f"  {d:<12}  {pa:>+14.2f}  {pb:>+14.2f}")


if __name__ == "__main__":
    main()
