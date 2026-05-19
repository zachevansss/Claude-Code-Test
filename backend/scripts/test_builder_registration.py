"""Test whether anyone can self-register as a Polymarket Builder via
POST /auth/builder-api-key, using the EOA-bound user API creds for L2 auth.

If this returns a Builder API key, the path forward is clear: we add the
Builder HMAC headers to every order POST and orders will be accepted.
If it returns 403 / "must apply" / similar, the Builder program is gated
and we'd need to apply with Polymarket.

L2 headers are computed via the same HMAC scheme as user-level API auth
(POLY_TIMESTAMP + method + path + body, hashed with the API secret).
"""
from __future__ import annotations

import sys

import httpx

sys.path.insert(0, ".")

from py_clob_client.client import ClobClient

from src.config.settings import settings
from src.database.session import SessionLocal
from src.executor import v2_signing
from src.models import ManagedWallet
from src.wallet.manager import WalletManager


def main() -> int:
    db = SessionLocal()
    try:
        wallet = db.query(ManagedWallet).filter(ManagedWallet.user_id == 1).first()
        if not wallet:
            print("no managed wallet for user_id=1"); return 1
        priv = WalletManager.get_private_key_hex(wallet)
        eoa = wallet.address
        proxy = wallet.proxy_address
    finally:
        db.close()

    print(f"EOA:           {eoa}")
    print(f"proxy_address: {proxy or '(null)'}")

    # Derive EOA-bound L2 creds via the standard flow (we know this works).
    print("\nDeriving EOA-bound API creds ...")
    c = ClobClient(
        host=settings.polymarket_base_url, key=priv,
        chain_id=settings.polygon_chain_id, signature_type=0,
    )
    creds = c.create_or_derive_api_creds()
    c.set_api_creds(creds)
    print(f"  api_key: {creds.api_key}")

    # Build L2 headers for POST /auth/builder-api-key (no body)
    method = "POST"
    path = "/auth/builder-api-key"
    body_str = ""
    headers = v2_signing.build_l2_headers(
        signer_address=eoa,
        api_key=creds.api_key,
        api_secret=creds.api_secret,
        api_passphrase=creds.api_passphrase,
        method=method,
        request_path=path,
        body=body_str,
    )

    url = f"{settings.polymarket_base_url}{path}"
    print(f"\nPOSTing {url} (empty body, L2 auth headers) ...")
    resp = httpx.post(url, headers=headers, timeout=15)
    print(f"HTTP {resp.status_code}")
    print(f"Body: {resp.text[:500]}")

    if resp.status_code == 200:
        print("\nSUCCESS — Builder registration is self-serve!")
        print("Next step: thread builder HMAC headers through the order POST flow.")
    elif resp.status_code in (401, 403):
        print("\nGATED — Builder program requires Polymarket approval.")
        print("We'd need to apply with them to get builder creds.")
    else:
        print("\nOther response — see body above for context.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
