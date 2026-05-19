"""Smoke test using py-clob-client-v2 — Polymarket's official V2 Python client.

Mirrors the official `gtc_limit_buy_deposit_wallet.py` example from
github.com/Polymarket/py-clob-client-v2 — the documented V2 path for Magic
Link / deposit-wallet users.

Config:
  signature_type = SignatureTypeV2.POLY_1271 (= 3)
  funder         = proxy address (the user's Magic Link deposit wallet)
  key            = the EOA's private key (Magic Link controller)

Requirements:
  - py-clob-client-v2 installed:
      .venv/bin/pip install py-clob-client-v2
  - The proxy must have pUSD balance + V2 approvals on-chain
    (we'll see "insufficient balance" or similar if not, which still proves
    the auth path works)

Run from backend/:
    .venv/bin/python -m scripts.smoke_v2_official --confirm
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, ".")

try:
    from py_clob_client_v2 import (
        ApiCreds,
        ClobClient,
        OrderArgs,
        OrderType,
        PartialCreateOrderOptions,
        Side,
        SignatureTypeV2,
    )
except ImportError as e:
    print(f"py-clob-client-v2 not installed. Install it with:")
    print(f"  .venv/bin/pip install py-clob-client-v2")
    print(f"(import error: {e})")
    sys.exit(99)

from src.config.settings import settings
from src.database.session import SessionLocal
from src.models import ManagedWallet
from src.wallet.manager import WalletManager


def pick_active_token() -> tuple[str, str]:
    resp = httpx.get(
        "https://gamma-api.polymarket.com/markets",
        params={"active": "true", "closed": "false", "limit": 10,
                "order": "volume24hr", "ascending": "false"},
        timeout=10,
    )
    resp.raise_for_status()
    for m in resp.json():
        tids_raw = m.get("clobTokenIds")
        if not tids_raw:
            continue
        tids = json.loads(tids_raw) if isinstance(tids_raw, str) else tids_raw
        for tid in tids:
            if not tid or str(tid) == "0":
                continue
            return str(tid), (m.get("question") or "?")[:60]
    raise RuntimeError("no active markets in gamma top-10")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--price", type=float, default=0.01)
    parser.add_argument("--size", type=float, default=5.0)
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        wallet = db.query(ManagedWallet).filter(ManagedWallet.user_id == args.user_id).first()
        if not wallet:
            print(f"no managed wallet for user_id={args.user_id}"); return 1
        eoa = wallet.address
        proxy = wallet.proxy_address
        priv = WalletManager.get_private_key_hex(wallet)
    finally:
        db.close()

    if not proxy:
        print("ERROR: proxy_address required for POLY_1271 / deposit wallet flow."); return 2
    print(f"EOA:           {eoa}")
    print(f"proxy/funder:  {proxy}")

    # Step 1: derive EOA-bound API creds via the V2 client's standard flow.
    print(f"\nDeriving EOA-bound API credentials ...")
    auth_only_client = ClobClient(
        host=settings.polymarket_base_url,
        chain_id=settings.polygon_chain_id,
        key=priv,
    )
    creds_obj = auth_only_client.create_or_derive_api_key()
    creds = ApiCreds(
        api_key=creds_obj.api_key,
        api_secret=creds_obj.api_secret,
        api_passphrase=creds_obj.api_passphrase,
    )
    print(f"  api_key: {creds.api_key}")

    # Step 2: build full client with POLY_1271 + funder=proxy
    print(f"\nInitializing trading client (POLY_1271 + funder=proxy) ...")
    client = ClobClient(
        host=settings.polymarket_base_url,
        chain_id=settings.polygon_chain_id,
        key=priv,
        creds=creds,
        signature_type=SignatureTypeV2.POLY_1271,
        funder=proxy,
    )

    # Step 3: pick a market
    print(f"\nPicking active market ...")
    token_id, market_desc = pick_active_token()
    print(f"  token_id:    {token_id}")
    print(f"  market:      {market_desc}")

    cost = args.price * args.size
    print(f"\nPlan: BUY {args.size} shares @ ${args.price:.3f} (${cost:.4f})")
    if not args.confirm:
        print("\nDRY RUN — pass --confirm to broadcast."); return 0

    print("\nSigning + posting via py-clob-client-v2 (POLY_1271 mode) ...")
    try:
        resp = client.create_and_post_order(
            order_args=OrderArgs(
                token_id=token_id,
                price=args.price,
                side=Side.BUY,
                size=args.size,
            ),
            options=PartialCreateOrderOptions(tick_size="0.01"),
            order_type=OrderType.GTC,
        )
    except Exception as e:  # noqa: BLE001
        print(f"\nFAILED: {type(e).__name__}: {e}")
        return 3
    print(f"\nResponse: {resp}")

    if isinstance(resp, dict):
        order_id = resp.get("orderID") or resp.get("order_id")
        if order_id:
            print(f"\n--- RESULT: ACCEPTED ---  order_id: {order_id}")
            print("py-clob-client-v2 + POLY_1271 deposit-wallet flow WORKS.")
            try:
                cancel_resp = client.cancel(order_id)
                print(f"cancel response: {cancel_resp}")
            except Exception as e:  # noqa: BLE001
                print(f"cancel failed (order may auto-expire): {e}")
            return 0
    print(f"\n--- RESULT: see response above ---")
    return 4


if __name__ == "__main__":
    sys.exit(main())
