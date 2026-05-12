"""Pull the source trader's full activity from Polymarket's data API and
replay it under live-mode rules with $1,000 starting bank.

Unlike the previous replay (which used the paper bot's recorded trades and so
inherited paper's $2 min-trade floor), this version sees every source-trader
trade and re-applies the live floor of $1 — capturing the band of small bets
that paper rejected but live would take. That's the case where insufficient
capital is most likely to bite.

Caches the raw API response to scripts/.cache_source_activity.json so the API
isn't re-hit on every iteration. Delete that file to force a refresh.

Usage:
    python scripts/_replay_live_from_source.py 0x2005d16a84ceefa912d4e380cd32e7ff827875ea
"""
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

import httpx

DATA_API = "https://data-api.polymarket.com"
CACHE_PATH = "scripts/.cache_source_activity.json"

# Live-mode rules (must match memory/project_live_risk_overrides.md)
START_BANK = 1000.0
LIVE_SCALE = 0.075
LIVE_POWER = 0.5
LIVE_FLOOR = float(os.environ.get("LIVE_FLOOR", "1.0"))  # override via env for what-ifs
LIVE_MAX_PCT_PER_TRADE = 0.02
LIVE_DAILY_LOSS_CAP_PCT = 0.10
SINCE_DATE = "2026-05-02"  # match paper-bot's start so the comparison is apples to apples


def fetch_activity(addr: str) -> list[dict]:
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH) as f:
            items = json.load(f)
        print(f"using cached {len(items):,} activity rows from {CACHE_PATH}")
        return items

    print(f"fetching activity for {addr} (no cache)...")
    items: list[dict] = []
    limit = 500
    offset = 0
    with httpx.Client(timeout=30) as client:
        while True:
            try:
                r = client.get(f"{DATA_API}/activity", params={"user": addr, "limit": limit, "offset": offset})
                r.raise_for_status()
            except httpx.HTTPStatusError as e:
                # data-api caps offset at some value (~3500). Try smaller limit, then bail.
                if limit > 100:
                    print(f"  HTTP {e.response.status_code} at offset={offset}; retrying with limit=100")
                    limit = 100
                    continue
                print(f"  HTTP {e.response.status_code} at offset={offset} with limit={limit} — stopping pagination")
                break
            page = r.json()
            if isinstance(page, dict):
                page = page.get("data") or page.get("activity") or []
            if not page:
                break
            items.extend(page)
            print(f"  +{len(page):,}  (cum {len(items):,})  offset={offset}")
            if len(page) < limit:
                break
            offset += limit
            if offset > 20000:
                print("  ! stopping at 20k as a safety cutoff")
                break

    os.makedirs(os.path.dirname(CACHE_PATH) or ".", exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        json.dump(items, f)
    print(f"cached {len(items):,} rows to {CACHE_PATH}")
    return items


def normalize(items: list[dict]) -> list[dict]:
    """Return a chronologically-sorted list of {ts, type, side, market, outcome, price, size, usd}."""
    out = []
    for a in items:
        ts = a.get("timestamp") or a.get("time") or 0
        if not ts:
            continue
        ts = int(ts)
        # filter to bot's observation window
        date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        if date_str < SINCE_DATE:
            continue
        t_type = (a.get("type") or "TRADE").upper()
        side = (a.get("side") or "").upper()
        market = a.get("conditionId") or a.get("marketId") or a.get("market")
        outcome = a.get("outcome") or a.get("outcomeName") or "?"
        try:
            price = float(a.get("price") or 0)
            size = float(a.get("size") or 0)
        except (TypeError, ValueError):
            continue
        usd = float(a.get("usdcSize") or (price * size) or 0)
        # Keep REDEEMs (price=0 by API convention); drop only malformed trades
        if not market:
            continue
        if t_type == "TRADE" and (price <= 0 or size <= 0):
            continue
        if t_type == "REDEEM" and size <= 0:
            continue
        out.append({
            "ts": ts,
            "date": date_str,
            "type": t_type,
            "side": side,
            "market": str(market),
            "outcome": str(outcome),
            "price": price,
            "size": size,
            "usd": usd,
        })
    out.sort(key=lambda r: r["ts"])
    return out


def simulate(events: list[dict], *, floor: float = LIVE_FLOOR) -> dict:
    balance = START_BANK
    # FIFO lots per (market, outcome): list of (size, entry_price, cost_basis)
    opens: dict[tuple, list[tuple[float, float, float]]] = {}
    # Trader's last BUY per market — REDEEMs in this dataset have no outcome
    # label, so we use the trader's most recent buy in that market to infer
    # the winning side (the side they redeem must be the winner).
    trader_last_buy_outcome: dict[str, str] = {}
    daily_pnl: dict[str, float] = defaultdict(float)
    day_start_account: dict[str, float] = {}
    trades_taken = 0
    skips = Counter()
    insufficient_balance_events = 0
    peak_open_cost = 0.0
    peak_open_count = 0
    peak_account = START_BANK
    min_cash = START_BANK
    pnl_by_day: dict[str, float] = defaultdict(float)
    opens_by_day: dict[str, int] = defaultdict(int)
    insufficient_by_day: dict[str, int] = defaultdict(int)
    realized_total = 0.0
    redeems_processed = 0
    redeems_skipped_no_match = 0

    def total_cost() -> float:
        return sum(c for q in opens.values() for _, _, c in q)

    for ev in events:
        date = ev["date"]
        if date not in day_start_account:
            day_start_account[date] = balance + total_cost()

        # REDEEM: trader cashed out their winning side. Close our lots in that
        # market at $1 (winner) and $0 (loser, complementary outcome).
        if ev["type"] == "REDEEM":
            market = ev["market"]
            winner = trader_last_buy_outcome.get(market)
            if winner is None:
                redeems_skipped_no_match += 1
                continue
            # Close winning outcome at $1, all other outcomes in same market at $0
            keys_to_close = [k for k in opens if k[0] == market]
            if not keys_to_close:
                continue
            redeems_processed += 1
            for key in keys_to_close:
                _, our_outcome = key
                settle_price = 1.0 if our_outcome == winner else 0.0
                for lot_size, entry_px, cost in opens[key]:
                    settle_value = lot_size * settle_price
                    realized = settle_value - cost
                    balance += settle_value
                    realized_total += realized
                    daily_pnl[date] += realized
                    pnl_by_day[date] += realized
                del opens[key]
            min_cash = min(min_cash, balance)
            peak_account = max(peak_account, balance + total_cost())
            continue

        if ev["side"] == "SELL":
            # Close from FIFO queue at the trader's sell price.
            key = (ev["market"], ev["outcome"])
            if key not in opens or not opens[key]:
                continue
            # Source size scaled to our position size — but we tracked our scaled lots
            # already. We close OUR lots (in FIFO order) at the SELL price.
            # For each of OUR lots, the trader's SELL might be a partial of theirs;
            # we use price as the close price for the lot. We close as many of our
            # lots as the trader closed proportionally — simpler: close all of our
            # remaining when trader closes their whole side at same market+outcome.
            # Approximation: close all our lots at this price (good enough for
            # capital/PNL summary; trader almost always exits cleanly per market).
            lots = opens.pop(key)
            settle = ev["price"]
            for lot_size, entry_px, cost in lots:
                settle_value = lot_size * settle
                realized = settle_value - cost
                balance += settle_value
                realized_total += realized
                daily_pnl[date] += realized
                pnl_by_day[date] += realized
            min_cash = min(min_cash, balance)
            peak_account = max(peak_account, balance + total_cost())
            continue

        if ev["side"] != "BUY":
            # SPLIT / MERGE / REDEEM / REWARD / etc. — ignore for the simulation.
            continue

        # BUY: record trader's chosen outcome for later REDEEM matching, then size
        trader_last_buy_outcome[ev["market"]] = ev["outcome"]
        # 1. Daily loss cap
        day_account = day_start_account[date]
        daily_cap = day_account * LIVE_DAILY_LOSS_CAP_PCT
        today_loss = -daily_pnl[date] if daily_pnl[date] < 0 else 0.0
        if today_loss >= daily_cap:
            skips["daily_loss_cap"] += 1
            continue

        # 2. Mirror sizing: scale * source_notional^power
        source_notional = ev["usd"]
        notional = LIVE_SCALE * (source_notional ** LIVE_POWER)

        # 3. Floor
        if notional < floor:
            skips["below_min_floor"] += 1
            continue

        # 4. Per-trade cap (2% of cash)
        max_per_trade = balance * LIVE_MAX_PCT_PER_TRADE
        notional = min(notional, max_per_trade)
        if notional <= 0:
            skips["zero_after_cap"] += 1
            continue

        # 5. Insufficient cash — THIS is where running out of capital shows up
        if notional > balance:
            skips["insufficient_balance"] += 1
            insufficient_balance_events += 1
            insufficient_by_day[date] += 1
            continue

        lot_size = notional / ev["price"]
        balance -= notional
        opens.setdefault((ev["market"], ev["outcome"]), []).append((lot_size, ev["price"], notional))
        trades_taken += 1
        opens_by_day[date] += 1
        tc = total_cost()
        peak_open_cost = max(peak_open_cost, tc)
        peak_open_count = max(peak_open_count, sum(len(q) for q in opens.values()))
        peak_account = max(peak_account, balance + tc)
        min_cash = min(min_cash, balance)

    # Final state
    remaining_cost = total_cost()
    open_lots = sum(len(q) for q in opens.values())
    result = {
        "floor": floor,
        "events_processed": len(events),
        "trades_taken": trades_taken,
        "trades_skipped": dict(skips),
        "redeems_processed": redeems_processed,
        "redeems_ignored": redeems_skipped_no_match,
        "insufficient_balance_events": insufficient_balance_events,
        "min_cash": min_cash,
        "peak_open_cost": peak_open_cost,
        "peak_open_count": peak_open_count,
        "peak_account": peak_account,
        "realized_pnl": realized_total,
        "ending_cash": balance,
        "open_cost_remaining": remaining_cost,
        "open_lots_remaining": open_lots,
        "pnl_by_day": dict(pnl_by_day),
        "opens_by_day": dict(opens_by_day),
        "insufficient_by_day": dict(insufficient_by_day),
        "day_start_account": dict(day_start_account),
        "daily_pnl": dict(daily_pnl),
        "n_days": len(day_start_account),
        "period_start": events[0]["date"] if events else None,
        "period_end": events[-1]["date"] if events else None,
    }

    print()
    print("=" * 74)
    print("LIVE-RULES REPLAY — source-trader's raw activity, $1,000 bank")
    print("=" * 74)
    if events:
        d0 = events[0]["date"]
        d1 = events[-1]["date"]
        print(f"Period:                    {d0} -> {d1}   ({len(day_start_account)} days)")
    print(f"Source events processed:   {len(events):,}")
    print(f"BUYs the bot took (live):  {trades_taken:,}")
    print(f"BUYs skipped:              {sum(skips.values()):,}")
    for r, n in skips.most_common():
        print(f"    {r:<24} {n:,}")
    print(f"REDEEMs processed:         {redeems_processed}  (closed our open lots in those markets)")
    print(f"REDEEMs ignored:           {redeems_skipped_no_match}  (no matching prior BUY recorded)")
    print()
    print("Capital — DID WE RUN OUT?")
    if insufficient_balance_events == 0:
        print(f"  No. 0 trades skipped due to insufficient balance across the full {len(day_start_account)}-day window.")
    else:
        print(f"  YES — {insufficient_balance_events} trades skipped due to insufficient balance.")
        print("  Days with insufficient-balance skips:")
        for d, n in sorted(insufficient_by_day.items()):
            print(f"    {d}: {n} blocked")
    print(f"  Min cash balance:        ${min_cash:,.2f}")
    print(f"  Peak open notional:      ${peak_open_cost:,.2f}   ({peak_open_cost / START_BANK * 100:.1f}% of starting bank)")
    print(f"  Peak concurrent lots:    {peak_open_count:,}")
    print(f"  Peak account value:      ${peak_account:,.2f}")
    print()
    print("P&L")
    print(f"  Realized P&L:            ${realized_total:+,.2f}")
    print(f"  Realized ROI:            {realized_total / START_BANK * 100:+.2f}%")
    print(f"  Ending cash:             ${balance:,.2f}")
    print(f"  Open positions cost:     ${remaining_cost:,.2f}  ({open_lots} lots)")
    print(f"  Account value at end:    ${balance + remaining_cost:,.2f}")
    print()
    print("Daily loss-cap (10%):")
    days_capped = [d for d, daily in daily_pnl.items() if -daily >= day_start_account[d] * LIVE_DAILY_LOSS_CAP_PCT]
    if not days_capped:
        print("  Never triggered.")
    else:
        for d in sorted(days_capped):
            print(f"  {d}: realized ${daily_pnl[d]:+,.2f}, cap ${day_start_account[d]*LIVE_DAILY_LOSS_CAP_PCT:,.2f}")
    print()
    print("Per-day (sorted by P&L)")
    rows = sorted(pnl_by_day.items(), key=lambda kv: kv[1])
    print(f"  {'date':<12} {'realized P&L':>14}   trades opened   insufficient-bal skips")
    for d, p in rows:
        print(f"  {d:<12} {p:>+13.2f}   {opens_by_day.get(d,0):>13}   {insufficient_by_day.get(d,0):>10}")
    return result


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: _replay_live_from_source.py <0xSOURCE>")
        sys.exit(1)
    addr = sys.argv[1].lower()
    items = fetch_activity(addr)
    events = normalize(items)
    print(f"normalized {len(events):,} events in window (since {SINCE_DATE})")
    simulate(events)


if __name__ == "__main__":
    main()
