"""Smoke test for Phase 2: poll the real Polymarket data API for a known
active wallet, parse responses through WalletTracker, and print signals.

Run from backend/:
    .venv/Scripts/python scripts/smoke_tracker.py

Pass `--wallet 0x...` to test a different address."""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.tracker.poller import WalletTracker  # noqa: E402

DEFAULT_WALLET = "0x2005d16a84ceefa912d4e380cd32e7ff827875ea"


async def main(wallet: str) -> int:
    print(f"polling Polymarket data API for {wallet} ...")
    # initialized=True so this single poll emits every fill it sees
    # (the production tracker uses initialized=False for first poll to avoid
    # historical-flood; for a smoke test we want the opposite).
    tracker = WalletTracker([wallet], seen=set(), initialized=True)
    signals = await tracker.poll()
    print(f"emitted {len(signals)} signal(s)\n")
    for s in signals[:10]:
        tx = (s.external_tx or "")[:14]
        print(
            f"  {s.side:4s} {s.outcome[:32]:32s} "
            f"@{s.price:.4f} size={s.size:>10.2f} tx={tx}..."
        )
    if len(signals) > 10:
        print(f"  ... and {len(signals) - 10} more")
    return 0 if signals else 2  # 2 = "polled OK but no fills found"


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--wallet", default=DEFAULT_WALLET)
    args = p.parse_args()
    sys.exit(asyncio.run(main(args.wallet)))
