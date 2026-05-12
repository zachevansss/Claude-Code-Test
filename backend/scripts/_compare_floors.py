"""Side-by-side comparison of two min-trade-floor scenarios on the same raw
source-trader activity window. All other live-mode rules held constant.

Usage:
    python scripts/_compare_floors.py <0xSOURCE>
"""
import io
import sys
from contextlib import redirect_stdout

# Reuse the simulator module
import scripts._replay_live_from_source as sim_mod


def run_quiet(events, *, floor: float) -> dict:
    """Run simulate() and capture/discard its print output, returning the dict."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = sim_mod.simulate(events, floor=floor)
    return result


def fmt_money(x: float) -> str:
    sign = "+" if x >= 0 else ""
    return f"{sign}${x:,.2f}"


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: _compare_floors.py <0xSOURCE>")
        sys.exit(1)
    addr = sys.argv[1].lower()

    items = sim_mod.fetch_activity(addr)
    events = sim_mod.normalize(items)
    print(f"Normalized {len(events):,} events across {(events[-1]['ts'] - events[0]['ts']) / 86400:.1f} days")
    print(f"Window: {events[0]['date']} -> {events[-1]['date']}")
    print()

    a = run_quiet(events, floor=1.0)
    b = run_quiet(events, floor=2.0)

    # Layout: label | Scenario A ($1 floor) | Scenario B ($2 floor)
    rows = [
        ("min_trade_usd",          f"$1.00",                       f"$2.00"),
        ("source events seen",     f"{a['events_processed']:,}",   f"{b['events_processed']:,}"),
        ("BUYs taken",             f"{a['trades_taken']:,}",       f"{b['trades_taken']:,}"),
        ("BUYs skipped (below floor)", f"{a['trades_skipped'].get('below_min_floor',0):,}",
                                       f"{b['trades_skipped'].get('below_min_floor',0):,}"),
        ("BUYs skipped (insufficient cash)", f"{a['insufficient_balance_events']:,}",
                                              f"{b['insufficient_balance_events']:,}"),
        ("REDEEMs processed",      f"{a['redeems_processed']:,}",  f"{b['redeems_processed']:,}"),
        ("",                       "",                              ""),
        ("--- capital ---",        "",                              ""),
        ("min cash balance",       f"${a['min_cash']:,.2f}",       f"${b['min_cash']:,.2f}"),
        ("peak open notional",     f"${a['peak_open_cost']:,.2f}", f"${b['peak_open_cost']:,.2f}"),
        ("peak open % of bank",    f"{a['peak_open_cost']/sim_mod.START_BANK*100:.1f}%",
                                   f"{b['peak_open_cost']/sim_mod.START_BANK*100:.1f}%"),
        ("peak concurrent lots",   f"{a['peak_open_count']:,}",    f"{b['peak_open_count']:,}"),
        ("peak account value",     f"${a['peak_account']:,.2f}",   f"${b['peak_account']:,.2f}"),
        ("",                       "",                              ""),
        ("--- P&L (window) ---",   "",                              ""),
        ("realized P&L",           fmt_money(a['realized_pnl']),   fmt_money(b['realized_pnl'])),
        ("realized ROI",           f"{a['realized_pnl']/sim_mod.START_BANK*100:+.2f}%",
                                   f"{b['realized_pnl']/sim_mod.START_BANK*100:+.2f}%"),
        ("ending cash",            f"${a['ending_cash']:,.2f}",    f"${b['ending_cash']:,.2f}"),
        ("open cost remaining",    f"${a['open_cost_remaining']:,.2f}",
                                   f"${b['open_cost_remaining']:,.2f}"),
        ("open lots remaining",    f"{a['open_lots_remaining']:,}",
                                   f"{b['open_lots_remaining']:,}"),
        ("account value at end",   f"${a['ending_cash']+a['open_cost_remaining']:,.2f}",
                                   f"${b['ending_cash']+b['open_cost_remaining']:,.2f}"),
    ]

    print("=" * 78)
    print(f"$1k bank, all other live rules constant — comparing min_trade_usd")
    print(f"  scale=0.075  power=0.5  max_per_trade=2%  daily_loss_cap=10%  leverage_cap=off")
    print("=" * 78)
    print(f"  {'metric':<32}  {'$1 floor':>18}    {'$2 floor':>18}")
    print(f"  {'-'*32}  {'-'*18}    {'-'*18}")
    for label, av, bv in rows:
        print(f"  {label:<32}  {av:>18}    {bv:>18}")

    # Daily P&L side by side
    print()
    print("Daily realized P&L")
    all_days = sorted(set(list(a['pnl_by_day'].keys()) + list(b['pnl_by_day'].keys())))
    print(f"  {'date':<12}  {'$1 floor':>14}  trades  |  {'$2 floor':>14}  trades")
    for d in all_days:
        pa = a['pnl_by_day'].get(d, 0.0)
        pb = b['pnl_by_day'].get(d, 0.0)
        ta = a['opens_by_day'].get(d, 0)
        tb = b['opens_by_day'].get(d, 0)
        print(f"  {d:<12}  {pa:>+14.2f}  {ta:>6}  |  {pb:>+14.2f}  {tb:>6}")


if __name__ == "__main__":
    main()
