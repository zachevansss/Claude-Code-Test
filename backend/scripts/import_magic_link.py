"""Replace user_id=1's managed_wallets row with a Polymarket Magic Link
EOA + proxy address. Used to test whether the proxy / POLY_1271 signing
path is currently accepted by Polymarket V2.

The credentials are read from a 2-line file (default `/tmp/ml.txt`):
  line 1: EOA private key hex (0x-prefixed, 66 chars)
  line 2: proxy contract address (0x-prefixed, 42 chars)

Reading from a file keeps the PK out of the shell history (where it would
land if passed as an argv flag).

Usage:
    .venv/bin/python -m scripts.import_magic_link               # dry-run
    .venv/bin/python -m scripts.import_magic_link --confirm     # replace

To REVERT to the post-rotation EOA (0x6b66ebac...268c):
    Run again with a file whose line 1 is that EOA's PK (in the backup at
    /root/copytrade-eoa-backups/rotate_eoa_*.json under the "private_key"
    field) and line 2 is left empty (no proxy).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, ".")

from src.database.session import SessionLocal
from src.models import ManagedWallet
from src.wallet.manager import WalletManager


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--file", default="/tmp/ml.txt",
                        help="2-line file: line 1 = private key, line 2 = proxy address (empty for none)")
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"file not found: {path}"); return 1
    lines = [ln.strip() for ln in path.read_text().splitlines()]
    if len(lines) < 1 or not lines[0]:
        print(f"file {path} has no private key on line 1"); return 1
    priv = lines[0]
    proxy: str | None = lines[1] if len(lines) >= 2 and lines[1] else None

    # Validate lengths so we catch swapped-fields mistakes loudly
    if not priv.startswith("0x") or len(priv) != 66:
        print(f"line 1 must be 0x-prefixed 66-char private key; got {len(priv)} chars starting {priv[:6]!r}")
        return 2
    if proxy is not None and (not proxy.startswith("0x") or len(proxy) != 42):
        print(f"line 2 must be 0x-prefixed 42-char proxy address (or empty); got {len(proxy)} chars starting {proxy[:6]!r}")
        return 2

    db = SessionLocal()
    try:
        existing = (
            db.query(ManagedWallet)
            .filter(ManagedWallet.user_id == args.user_id)
            .first()
        )
        print(f"Current managed_wallet for user_id={args.user_id}:")
        if existing:
            print(f"  address:        {existing.address}")
            print(f"  proxy_address:  {existing.proxy_address or '(null)'}")
        else:
            print("  (no existing row — would create new)")

        print(f"\nWill replace with:")
        print(f"  EOA:            (derived from PK in {path})")
        print(f"  proxy_address:  {proxy or '(null — pure EOA mode)'}")

        if not args.confirm:
            print("\nDRY RUN — pass --confirm to actually replace.")
            return 0

        new_wallet = WalletManager.import_for_user(
            user_id=args.user_id,
            private_key_hex=priv,
            db=db,
            replace_existing=True,
            proxy_address=proxy,
        )
        db.commit()
        print(f"\nReplaced.")
        print(f"  EOA address:    {new_wallet.address}")
        print(f"  proxy_address:  {new_wallet.proxy_address}")
        print(f"\nNext: .venv/bin/python -m scripts.smoke_v2_order --confirm")
        print(f"      (will use POLY_1271 mode because proxy_address is set)")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
