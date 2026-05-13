"""One-time fix: backdate synthetic resolution Trade rows that were stamped
with the sweep time instead of the market's actual UMA close time.

Identifies synthetic resolution rows (source_wallet='resolution',
status='resolved') created after a given cutoff, looks up each row's
conditionId on gamma-api, and rewrites created_at to the market's actual
closedTime (or endDate fallback).

Idempotent: re-running picks up only rows where current created_at still
looks like a sweep stamp (within the cutoff window). Pass a non-default
cutoff to scope the run.

Usage:
    python scripts/_backdate_resolutions.py
    python scripts/_backdate_resolutions.py "2026-05-12 19:00"
"""
import sqlite3
import sys
from datetime import datetime, timezone

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

from src.resolution.checker import _fetch_resolved_from_gamma


def main() -> None:
    cutoff = sys.argv[1] if len(sys.argv) > 1 else "2026-05-12 19:00"
    con = sqlite3.connect("copytrade.db")
    rows = con.execute(
        """
        SELECT id, market_id, created_at
        FROM trades
        WHERE source_wallet='resolution' AND status='resolved'
          AND created_at >= ?
        ORDER BY id
        """,
        (cutoff,),
    ).fetchall()
    print(f"Found {len(rows)} synthetic resolution rows after {cutoff}")

    market_to_rows: dict[str, list[int]] = {}
    for tid, market, ts in rows:
        market_to_rows.setdefault(market, []).append(tid)
    unique_markets = list(market_to_rows.keys())
    print(f"Unique markets: {len(unique_markets)}")

    gamma = _fetch_resolved_from_gamma(unique_markets)
    print(f"Gamma returned actual closedTime for {sum(1 for v in gamma.values() if v[1] is not None)} markets")

    updated = 0
    skipped = 0
    for market, tids in market_to_rows.items():
        entry = gamma.get(market)
        if entry is None:
            skipped += 1
            continue
        _, closed_at = entry
        if closed_at is None:
            skipped += 1
            continue
        # SQLite stores datetimes as ISO strings; format to match python sqlite3 default
        ts_str = closed_at.isoformat(sep=" ")
        for tid in tids:
            con.execute("UPDATE trades SET created_at = ? WHERE id = ?", (ts_str, tid))
            updated += 1
    con.commit()
    print(f"Updated {updated} rows, skipped {skipped} markets with no usable close time")

    # Show new daily distribution
    print("\nNew distribution of these rows by date:")
    dist = con.execute(
        """
        SELECT substr(created_at, 1, 10) AS d, COUNT(*) AS n
        FROM trades
        WHERE source_wallet='resolution' AND status='resolved'
          AND id IN (SELECT id FROM trades WHERE source_wallet='resolution' AND created_at >= ? OR id IN (
            SELECT id FROM trades WHERE source_wallet='resolution' AND status='resolved'
          ))
        GROUP BY d ORDER BY d
        """,
        (cutoff,),
    ).fetchall()
    for d, n in dist:
        print(f"  {d}: {n}")


if __name__ == "__main__":
    main()
