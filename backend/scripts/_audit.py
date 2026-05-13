"""Full integrity audit of the paper-mode trade ledger.

Runs every consistency check I could think of. Each check prints PASS or a
diagnostic and a count of offending rows. The audit is read-only — it never
writes to the DB."""
from __future__ import annotations

import sqlite3
import sys
from collections import defaultdict

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

import httpx
import json as _json

USER = 1
MODE = "paper"
DB = "copytrade.db"


def banner(title: str) -> None:
    print(f"\n── {title} {'─' * max(0, 70 - len(title))}")


def check(label: str, bad: int, *, detail: str = "", warn: bool = False) -> None:
    if bad == 0:
        print(f"  PASS  {label}")
    else:
        prefix = "WARN" if warn else "FAIL"
        print(f"  {prefix}  {label}: {bad}")
        if detail:
            print(f"        {detail}")


def main() -> None:
    con = sqlite3.connect(DB)
    cur = con.cursor()
    rs: dict[str, int] = {"pass": 0, "fail": 0, "warn": 0}

    def report(label: str, n: int, detail: str = "", warn: bool = False) -> None:
        if n == 0:
            rs["pass"] += 1
            print(f"  PASS  {label}")
            return
        rs["warn" if warn else "fail"] += 1
        prefix = "WARN" if warn else "FAIL"
        print(f"  {prefix}  {label}: {n}")
        if detail:
            print(f"        {detail}")

    # ───────────────────────────────────────────────────────────────────────
    banner("A. Trade row integrity")
    # ───────────────────────────────────────────────────────────────────────
    n = cur.execute(
        "SELECT COUNT(*) FROM trades WHERE user_id=? AND mode=?",
        (USER, MODE),
    ).fetchone()[0]
    print(f"  total trades: {n:,}")

    n = cur.execute(
        "SELECT COUNT(*) FROM trades WHERE user_id=? AND mode=? AND (price IS NULL OR price < 0 OR price > 1.0001)",
        (USER, MODE),
    ).fetchone()[0]
    report("trades with price outside [0, 1]", n)

    n = cur.execute(
        "SELECT COUNT(*) FROM trades WHERE user_id=? AND mode=? AND (size IS NULL OR size <= 0)",
        (USER, MODE),
    ).fetchone()[0]
    report("trades with non-positive size", n)

    n = cur.execute(
        "SELECT COUNT(*) FROM trades WHERE user_id=? AND mode=? AND (market_id IS NULL OR market_id = '')",
        (USER, MODE),
    ).fetchone()[0]
    report("trades with missing market_id", n)

    n = cur.execute(
        "SELECT COUNT(*) FROM trades WHERE user_id=? AND mode=? AND (outcome IS NULL OR outcome = '')",
        (USER, MODE),
    ).fetchone()[0]
    report("trades with missing outcome", n)

    # Duplicate external_tx (would mean same source trade copied twice)
    n = cur.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT external_tx FROM trades WHERE user_id=? AND mode=? AND external_tx IS NOT NULL
            GROUP BY external_tx HAVING COUNT(*) > 1
        )
        """,
        (USER, MODE),
    ).fetchone()[0]
    report("duplicate external_tx (source trade copied twice)", n)

    # Resolutions with side != 'sell' (data corruption)
    n = cur.execute(
        "SELECT COUNT(*) FROM trades WHERE user_id=? AND mode=? AND source_wallet='resolution' AND side != 'sell'",
        (USER, MODE),
    ).fetchone()[0]
    report("resolutions with side != sell", n)

    # Resolutions with status != 'resolved'
    n = cur.execute(
        "SELECT COUNT(*) FROM trades WHERE user_id=? AND mode=? AND source_wallet='resolution' AND status != 'resolved'",
        (USER, MODE),
    ).fetchone()[0]
    report("resolutions with status != resolved", n)

    # ───────────────────────────────────────────────────────────────────────
    banner("B. Position row integrity")
    # ───────────────────────────────────────────────────────────────────────
    n_pos = cur.execute(
        "SELECT COUNT(*) FROM positions WHERE user_id=? AND mode=?",
        (USER, MODE),
    ).fetchone()[0]
    n_open = cur.execute(
        "SELECT COUNT(*) FROM positions WHERE user_id=? AND mode=? AND size > 0",
        (USER, MODE),
    ).fetchone()[0]
    print(f"  positions: {n_pos:,} total / {n_open:,} open")

    n = cur.execute(
        "SELECT COUNT(*) FROM positions WHERE user_id=? AND mode=? AND size < 0",
        (USER, MODE),
    ).fetchone()[0]
    report("positions with NEGATIVE size", n)

    n = cur.execute(
        "SELECT COUNT(*) FROM positions WHERE user_id=? AND mode=? AND size > 0 AND avg_price <= 0",
        (USER, MODE),
    ).fetchone()[0]
    report("open positions with avg_price <= 0", n)

    n = cur.execute(
        "SELECT COUNT(*) FROM positions WHERE user_id=? AND mode=? AND size > 0 AND avg_price > 1.0001",
        (USER, MODE),
    ).fetchone()[0]
    report("open positions with avg_price > 1.0", n)

    # Orphan positions: open size but no buy trade exists
    rows = cur.execute(
        """
        SELECT p.market_id, p.outcome
        FROM positions p
        WHERE p.user_id=? AND p.mode=? AND p.size > 0
          AND NOT EXISTS (
            SELECT 1 FROM trades t
            WHERE t.user_id=p.user_id AND t.mode=p.mode
              AND t.market_id=p.market_id AND t.outcome=p.outcome
              AND t.side='buy'
          )
        """,
        (USER, MODE),
    ).fetchall()
    report("open positions with no matching BUY trade", len(rows))

    # ───────────────────────────────────────────────────────────────────────
    banner("C. P&L consistency (per position)")
    # ───────────────────────────────────────────────────────────────────────
    # For each CLOSED position (size == 0), realized_pnl_usd should equal
    # (sum sell notional) - (sum buy notional). Tolerance 1¢ for fp drift.
    mismatches: list[tuple] = []
    for pid, mkt, outcome, size, avg, realized in cur.execute(
        "SELECT id, market_id, outcome, size, avg_price, realized_pnl_usd FROM positions WHERE user_id=? AND mode=? AND size = 0",
        (USER, MODE),
    ):
        buy_not = cur.execute(
            "SELECT COALESCE(SUM(notional_usd), 0) FROM trades WHERE user_id=? AND mode=? AND market_id=? AND outcome=? AND side='buy'",
            (USER, MODE, mkt, outcome),
        ).fetchone()[0]
        sell_not = cur.execute(
            "SELECT COALESCE(SUM(notional_usd), 0) FROM trades WHERE user_id=? AND mode=? AND market_id=? AND outcome=? AND side='sell'",
            (USER, MODE, mkt, outcome),
        ).fetchone()[0]
        expected = sell_not - buy_not
        if abs(expected - realized) > 0.02:  # 2¢ tolerance
            mismatches.append((pid, mkt[:16], outcome, realized, expected, expected - realized))
    detail = ""
    if mismatches:
        detail = f"e.g. pos id={mismatches[0][0]} outcome={mismatches[0][2]!r} realized={mismatches[0][3]:.2f} expected={mismatches[0][4]:.2f}"
    report(f"closed positions where realized_pnl != sell-buy (>2¢)", len(mismatches), detail)

    # Total realized P&L from positions should match sum of (sells - buys) across all positions
    total_realized_pos = cur.execute(
        "SELECT COALESCE(SUM(realized_pnl_usd), 0) FROM positions WHERE user_id=? AND mode=?",
        (USER, MODE),
    ).fetchone()[0]
    total_sells = cur.execute(
        "SELECT COALESCE(SUM(notional_usd), 0) FROM trades WHERE user_id=? AND mode=? AND side='sell'",
        (USER, MODE),
    ).fetchone()[0]
    total_buys = cur.execute(
        "SELECT COALESCE(SUM(notional_usd), 0) FROM trades WHERE user_id=? AND mode=? AND side='buy'",
        (USER, MODE),
    ).fetchone()[0]
    # If positions are still open, realized = sells - (buys for closed). Tricky.
    # Better invariant: realized_pos = sum over positions of (sells - buys_against_size_taken)
    # For now, compare realized_pnl from positions ~ sells - (buys - open_cost_basis)
    open_cost = cur.execute(
        "SELECT COALESCE(SUM(size * avg_price), 0) FROM positions WHERE user_id=? AND mode=? AND size > 0",
        (USER, MODE),
    ).fetchone()[0]
    # Invariant: total_realized_pos ≈ total_sells - (total_buys - open_cost)
    expected_realized = total_sells - (total_buys - open_cost)
    drift = abs(total_realized_pos - expected_realized)
    print(f"  total realized (positions table): {total_realized_pos:+,.2f}")
    print(f"  total sells: {total_sells:,.2f}   total buys: {total_buys:,.2f}   open cost: {open_cost:,.2f}")
    print(f"  expected realized = sells - (buys - open_cost): {expected_realized:+,.2f}   drift: ${drift:.2f}")
    report("global realized vs sells/buys/open_cost invariant (>$5)", 1 if drift > 5 else 0)

    # ───────────────────────────────────────────────────────────────────────
    banner("D. Stuck-resolution check (gamma-api authoritative)")
    # ───────────────────────────────────────────────────────────────────────
    # Are any open positions actually resolved per Polymarket?
    open_positions = cur.execute(
        """
        SELECT p.market_id, p.outcome, p.size, p.avg_price,
               (SELECT t.asset_id FROM trades t WHERE t.user_id=p.user_id AND t.market_id=p.market_id AND t.outcome=p.outcome AND t.asset_id IS NOT NULL LIMIT 1)
        FROM positions p
        WHERE p.user_id=? AND p.mode=? AND p.size > 0
        """,
        (USER, MODE),
    ).fetchall()
    unique_markets = list({p[0] for p in open_positions})
    # Probe gamma in small batches
    stuck = 0
    closed_cids: set[str] = set()
    for i in range(0, len(unique_markets), 15):
        batch = unique_markets[i:i + 15]
        params = [("condition_ids", c) for c in batch] + [("closed", "true"), ("limit", "15")]
        try:
            r = httpx.get("https://gamma-api.polymarket.com/markets", params=params, timeout=10)
            if r.status_code == 200:
                for m in r.json():
                    cid = m.get("conditionId")
                    if cid:
                        closed_cids.add(cid)
        except Exception:
            pass
    for mkt, outcome, size, avg, asset in open_positions:
        if mkt in closed_cids:
            stuck += 1
    report(
        f"open positions that gamma-api says are CLOSED (stuck stale)",
        stuck,
        warn=True,
    )

    # ───────────────────────────────────────────────────────────────────────
    banner("E. Win/loss accounting")
    # ───────────────────────────────────────────────────────────────────────
    # Count resolved trades by settle price
    settle_buckets = cur.execute(
        """
        SELECT
            SUM(CASE WHEN price >= 0.99 THEN 1 ELSE 0 END) AS winners,
            SUM(CASE WHEN price <= 0.01 THEN 1 ELSE 0 END) AS losers,
            SUM(CASE WHEN price > 0.01 AND price < 0.99 THEN 1 ELSE 0 END) AS partials,
            COUNT(*) AS total
        FROM trades
        WHERE user_id=? AND mode=? AND source_wallet='resolution'
        """,
        (USER, MODE),
    ).fetchone()
    wins, losses, partials, total = settle_buckets
    print(f"  resolution trades: {total:,} ({wins:,} winners @≈$1, {losses:,} losers @≈$0, {partials:,} partial)")
    win_rate = wins / (wins + losses) * 100 if (wins + losses) else 0
    print(f"  win rate (W vs W+L): {win_rate:.1f}%")

    # ───────────────────────────────────────────────────────────────────────
    banner("F. Daily P&L sanity")
    # ───────────────────────────────────────────────────────────────────────
    # Sum realized P&L per UTC date from resolution trades. For each resolution,
    # P&L = sell_notional - (size × avg_buy_price) — but we only have per-trade
    # data here, so approximate: P&L per resolution row = (sell_price - position.avg_price) * size
    # which is what _close_position writes to positions.realized_pnl_usd.
    # Better: trust the position-level realized. For per-day, attribute each
    # position's realized to the day of its LATEST sell.
    daily = defaultdict(float)
    daily_count = defaultdict(int)
    # Materialize outer query first so the inner queries can reuse the cursor.
    pos_rows = cur.execute(
        "SELECT id, market_id, outcome, realized_pnl_usd FROM positions WHERE user_id=? AND mode=?",
        (USER, MODE),
    ).fetchall()
    for pos_id, mkt, outcome, realized in pos_rows:
        if realized == 0:
            continue
        latest = cur.execute(
            "SELECT MAX(created_at) FROM trades WHERE user_id=? AND mode=? AND market_id=? AND outcome=? AND side='sell'",
            (USER, MODE, mkt, outcome),
        ).fetchone()[0]
        if latest is None:
            continue
        day = latest[:10]
        daily[day] += realized
        daily_count[day] += 1

    print(f"  Daily realized P&L (from positions.realized_pnl_usd attributed to last sell date):")
    print(f"  {'date':<12} {'P&L':>12}  resolutions")
    for day in sorted(daily):
        print(f"  {day:<12} {daily[day]:>+12.2f}  {daily_count[day]:>3}")

    # Total of daily should match total_realized_pos
    daily_sum = sum(daily.values())
    drift = abs(daily_sum - total_realized_pos)
    print(f"\n  sum daily = {daily_sum:+.2f}   positions realized total = {total_realized_pos:+.2f}   drift = ${drift:.2f}")
    report("sum of daily P&L vs total realized (>1¢)", 1 if drift > 0.01 else 0)

    # ───────────────────────────────────────────────────────────────────────
    banner("G. Bot health / source-trader tracking")
    # ───────────────────────────────────────────────────────────────────────
    # Most recent buy trade timestamp
    last_buy = cur.execute(
        "SELECT MAX(created_at) FROM trades WHERE user_id=? AND mode=? AND side='buy'",
        (USER, MODE),
    ).fetchone()[0]
    print(f"  last buy trade in DB: {last_buy}")

    # Bot instance status
    row = cur.execute(
        "SELECT status, last_started_at, last_error FROM bot_instances WHERE user_id=?",
        (USER,),
    ).fetchone()
    if row:
        print(f"  bot status: {row[0]}  last_started_at: {row[1]}  last_error: {row[2]}")

    # Source-wallet count
    n_wallets = cur.execute(
        "SELECT COUNT(*) FROM user_wallets WHERE user_id=? AND is_active=1",
        (USER,),
    ).fetchone()[0]
    print(f"  active source wallets tracked: {n_wallets}")

    # ───────────────────────────────────────────────────────────────────────
    banner("Summary")
    # ───────────────────────────────────────────────────────────────────────
    print(f"  PASS: {rs['pass']}   WARN: {rs['warn']}   FAIL: {rs['fail']}")
    if rs["fail"] == 0 and rs["warn"] == 0:
        print("  All checks green.")
    sys.exit(rs["fail"])


if __name__ == "__main__":
    main()
