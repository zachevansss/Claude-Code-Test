"""Delete + recreate Polymarket CLOB API key for user_id=1's EOA in pure EOA mode.

Hypothesis: the existing API key was created back when ManagedWallet.proxy_address
was set (POLY_PROXY mode). Polymarket may tag the key server-side as belonging to
a deposit-wallet user, causing orders from a clean-EOA-mode client (sig_type=0,
maker=EOA) to be rejected with "maker address not allowed, please use the deposit
wallet flow". This script:

  1. Initializes a ClobClient in EOA mode (sig_type=0, no funder).
  2. Calls create_or_derive — pulls the existing creds.
  3. Lists API keys (sanity).
  4. Calls delete_api_key — removes the old key.
  5. Re-initializes a ClobClient in EOA mode + create_api_creds (NOT derive) —
     forces a fresh key registered in pure-EOA context.
  6. Sanity-checks the new key exists.

If after this the bot still gets "maker not allowed", the issue is server-side
EOA-blocklisting (would need a fresh EOA) — not an API-key tagging issue.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from py_clob_client.client import ClobClient

from src.config.settings import settings
from src.database.session import SessionLocal
from src.models import ManagedWallet
from src.wallet.manager import WalletManager


def _new_eoa_client(priv: str) -> ClobClient:
    return ClobClient(
        host=settings.polymarket_base_url,
        key=priv,
        chain_id=settings.polygon_chain_id,
        signature_type=0,
    )


def main() -> int:
    db = SessionLocal()
    try:
        w = db.query(ManagedWallet).filter(ManagedWallet.user_id == 1).first()
        if not w:
            print("no managed wallet"); return 1
        priv = WalletManager.get_private_key_hex(w)
        print(f"EOA: {w.address}")
        print(f"proxy_address: {w.proxy_address}")
    finally:
        db.close()

    print("\n[1] Initializing EOA-mode CLOB client (no funder) ...")
    c = _new_eoa_client(priv)
    print("[2] Deriving existing creds ...")
    creds = c.create_or_derive_api_creds()
    c.set_api_creds(creds)
    print(f"    existing api_key: {creds.api_key}")

    print("[3] Listing API keys on this address ...")
    try:
        keys_resp = c.get_api_keys()
        print(f"    response: {keys_resp}")
    except Exception as e:  # noqa: BLE001
        print(f"    list failed (not fatal): {e}")

    print("[4] Deleting the existing API key ...")
    try:
        del_resp = c.delete_api_key()
        print(f"    delete response: {del_resp}")
    except Exception as e:  # noqa: BLE001
        print(f"    delete failed: {e}")
        return 2

    print("\n[5] Creating a FRESH API key in pure EOA mode ...")
    c2 = _new_eoa_client(priv)
    new_creds = c2.create_api_key()
    print(f"    new api_key: {new_creds.api_key}")
    c2.set_api_creds(new_creds)

    print("\n[6] Sanity: list keys again to confirm new one is registered ...")
    try:
        keys_resp = c2.get_api_keys()
        print(f"    response: {keys_resp}")
    except Exception as e:  # noqa: BLE001
        print(f"    list failed: {e}")

    print("\nDONE. Next bot start will derive these fresh creds (same EOA signs).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
