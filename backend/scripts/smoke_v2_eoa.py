"""V2 EOA smoke test using the official py-clob-client-v2.

Self-funded EOA flow — no proxy, no funder, no Builder layer needed.
Reads the EOA's private key from a JSON backup file (the format the
rotation script writes), so we don't have to touch managed_wallets.

This tests whether the rotated EOA (0x6B66...268C, holds $998 pUSD and
the V2 approvals set up earlier today) can submit orders against
Polymarket V2 directly.

Run:
    .venv/bin/python -m scripts.smoke_v2_eoa \\
        --pk-file /root/copytrade-eoa-backups/rotate_eoa_20260518T222047Z_*.json \\
        --confirm
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, ".")

from py_clob_client_v2 import (
    ApiCreds,
    ClobClient,
    OrderArgs,
    OrderType,
    PartialCreateOrderOptions,
    Side,
    SignatureTypeV2,
)
from eth_account import Account

from src.config.settings import settings


def load_pk_from_file(path_glob: str) -> tuple[str, str]:
    """Resolve a glob, load the first matching JSON, return (address, pk)."""
    matches = glob.glob(path_glob)
    if not matches:
        raise SystemExit(f"no files matching {path_glob}")
    path = matches[0]
    data = json.loads(Path(path).read_text())
    pk = data["private_key"]
    addr = Account.from_key(pk).address
    return addr, pk


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
    parser.add_argument("--pk-file", required=True,
                        help="path (or glob) to a JSON file with a 'private_key' field")
    parser.add_argument("--price", type=float, default=0.01)
    parser.add_argument("--size", type=float, default=5.0)
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()

    eoa, priv = load_pk_from_file(args.pk_file)
    print(f"EOA from PK file: {eoa}")

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

    print(f"\nInitializing trading client (SIG_EOA, no funder) ...")
    client = ClobClient(
        host=settings.polymarket_base_url,
        chain_id=settings.polygon_chain_id,
        key=priv,
        creds=creds,
        signature_type=SignatureTypeV2.EOA,
        # no funder — self-funded EOA mode
    )

    print(f"\nPicking active market ...")
    token_id, market_desc = pick_active_token()
    print(f"  token_id:    {token_id}")
    print(f"  market:      {market_desc}")

    cost = args.price * args.size
    print(f"\nPlan: BUY {args.size} shares @ ${args.price:.3f} (${cost:.4f}) as EOA {eoa}")
    if not args.confirm:
        print("\nDRY RUN — pass --confirm to broadcast."); return 0

    print("\nSigning + posting via py-clob-client-v2 (SIG_EOA) ...")
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
            print("V2 EOA path WORKS on this address.")
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
