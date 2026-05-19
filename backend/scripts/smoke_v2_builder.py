"""Smoke test combining everything we now know:

  * V2 order signing (our parity-verified v2_signing module — Polymarket V2
    rejects V1 orders with order_version_mismatch since 2026-04-28)
  * SIG_POLY_PROXY (sig_type=1):  maker = proxy, signer = EOA
  * User L2 HMAC headers (POLY_*) keyed to the EOA-bound API key
  * Builder L2 HMAC headers (POLY_BUILDER_*) — the layer that was missing in
    every prior attempt; Polymarket V2 rejects proxy orders without them

If this returns ACCEPTED, the production executor can be rewritten to do
the same in the bot loop.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import sys
import time
from pathlib import Path

import httpx
from eth_utils import to_checksum_address

sys.path.insert(0, ".")

from py_clob_client.client import ClobClient

from src.config.settings import settings
from src.database.session import SessionLocal
from src.executor import v2_signing
from src.models import ManagedWallet
from src.wallet.manager import WalletManager


_FALLBACK_BUILDER = {
    "key": "019e3dbf-f958-7bd6-8fcb-af985b0e056b",
    "secret": "PCQTaW5PUOL5cRs81d0ZbqQl5lqKOvgbMooBtiM7PQ8=",
    "passphrase": "060d2b4ede54ee9752163c03bd8cca8e75c09ff45df15ad2866907aa45ace9ae",
}


def load_builder_creds() -> dict:
    path = Path("/root/copytrade-builder-creds.json")
    if path.exists():
        return json.loads(path.read_text())
    print(f"  (no {path} — using fallback creds from memory)")
    return _FALLBACK_BUILDER


def _build_hmac_signature(secret_b64url: str, ts: int, method: str, path: str, body: str) -> str:
    """Match the Polymarket / py-builder-signing-sdk HMAC scheme exactly:
    base64-url decode the secret, sign `ts + method + path + body`, base64-url
    encode. Both user-L2 and builder-L2 use this identical scheme."""
    key = base64.urlsafe_b64decode(secret_b64url + "=" * ((4 - len(secret_b64url) % 4) % 4))
    msg = f"{ts}{method}{path}"
    if body:
        msg += body
    digest = hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii")


def build_user_l2_headers(eoa: str, api_creds, method: str, path: str, body: str) -> dict[str, str]:
    ts = int(time.time())
    sig = _build_hmac_signature(api_creds.api_secret, ts, method, path, body)
    return {
        "POLY_ADDRESS": to_checksum_address(eoa),
        "POLY_SIGNATURE": sig,
        "POLY_TIMESTAMP": str(ts),
        "POLY_API_KEY": api_creds.api_key,
        "POLY_PASSPHRASE": api_creds.api_passphrase,
    }


def build_builder_l2_headers(builder: dict, method: str, path: str, body: str) -> dict[str, str]:
    ts = int(time.time())
    sig = _build_hmac_signature(builder["secret"], ts, method, path, body)
    return {
        "POLY_BUILDER_API_KEY": builder["key"],
        "POLY_BUILDER_SIGNATURE": sig,
        "POLY_BUILDER_TIMESTAMP": str(ts),
        "POLY_BUILDER_PASSPHRASE": builder["passphrase"],
    }


def pick_active_token(client: ClobClient) -> tuple[str, str]:
    resp = httpx.get(
        "https://gamma-api.polymarket.com/markets",
        params={"active": "true", "closed": "false", "limit": 25,
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
            try:
                book = client.get_order_book(str(tid))
            except Exception:
                continue
            if (getattr(book, "bids", None) or []) or (getattr(book, "asks", None) or []):
                return str(tid), (m.get("question") or "?")[:60]
    raise RuntimeError("no markets with live orderbooks in gamma top-25")


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

    print(f"EOA:           {eoa}")
    print(f"proxy_address: {proxy}")
    if not proxy:
        print("ERROR: this flow needs proxy_address set on the wallet."); return 2

    print("\nLoading builder credentials ...")
    builder = load_builder_creds()
    print(f"  builder key: {builder['key']}")

    print("\nDeriving EOA-bound user API credentials ...")
    pyclob = ClobClient(
        host=settings.polymarket_base_url, key=priv,
        chain_id=settings.polygon_chain_id, signature_type=0,
    )
    user_creds = pyclob.create_or_derive_api_creds()
    pyclob.set_api_creds(user_creds)
    print(f"  user api_key: {user_creds.api_key}")

    print("\nPicking active market ...")
    token_id, market = pick_active_token(pyclob)
    tick_size = pyclob.get_tick_size(token_id)
    neg_risk = pyclob.get_neg_risk(token_id)
    exchange = v2_signing.NEG_RISK_EXCHANGE_V2 if neg_risk else v2_signing.CTF_EXCHANGE_V2
    print(f"  token_id:    {token_id}")
    print(f"  market:      {market}")
    print(f"  tick_size:   {tick_size}  neg_risk: {neg_risk}")

    print(f"\nPlan: BUY {args.size} @ ${args.price:.3f}  ({'NegRisk' if neg_risk else 'CTF'} V2)")
    print(f"      maker=proxy, signer=EOA, sig_type=SIG_POLY_PROXY (1)")
    print(f"      USER L2 headers (api_key={user_creds.api_key[:8]}...) + BUILDER L2 headers")
    if not args.confirm:
        print("\nDRY RUN — pass --confirm to broadcast."); return 0

    # Build + sign the V2 order
    maker_amt, taker_amt = v2_signing.compute_amounts("BUY", args.size, args.price, tick_size)
    order = v2_signing.build_order(
        maker=proxy,
        signer=eoa,
        token_id=token_id,
        maker_amount=maker_amt,
        taker_amount=taker_amt,
        side="BUY",
        signature_type=v2_signing.SIG_POLY_PROXY,
    )
    signed = v2_signing.sign_order(order, exchange, priv)
    body_dict = v2_signing.order_to_wire(signed, owner=user_creds.api_key, order_type="GTC")
    body_str = json.dumps(body_dict, separators=(",", ":"))

    path = "/order"
    user_h = build_user_l2_headers(eoa, user_creds, "POST", path, body_str)
    builder_h = build_builder_l2_headers(builder, "POST", path, body_str)
    headers = {**user_h, **builder_h, "Content-Type": "application/json"}

    print(f"\nPOSTing /order with USER + BUILDER headers ...")
    resp = httpx.post(
        f"{settings.polymarket_base_url}{path}",
        content=body_str, headers=headers, timeout=15,
    )
    print(f"  HTTP {resp.status_code}")
    print(f"  body: {resp.text[:500]}")

    if resp.status_code >= 400:
        print(f"\n--- RESULT: REJECTED ---")
        return 3

    payload = resp.json()
    order_id = payload.get("orderID") or payload.get("order_id") or payload.get("orderHash")
    if payload.get("success") is False or (not order_id and "errorMsg" in payload):
        print(f"\n--- RESULT: REJECTED (200 + errorMsg) ---  errorMsg={payload.get('errorMsg')}")
        return 4

    print(f"\n--- RESULT: ACCEPTED ---")
    print(f"order_id: {order_id}")
    print(f"V2 + Builder path works end-to-end. Cancelling now ...")

    cancel_body = json.dumps({"orderID": order_id}, separators=(",", ":"))
    cancel_user_h = build_user_l2_headers(eoa, user_creds, "DELETE", "/order", cancel_body)
    cancel_builder_h = build_builder_l2_headers(builder, "DELETE", "/order", cancel_body)
    cresp = httpx.request(
        "DELETE", f"{settings.polymarket_base_url}/order",
        content=cancel_body,
        headers={**cancel_user_h, **cancel_builder_h, "Content-Type": "application/json"},
        timeout=15,
    )
    print(f"  cancel HTTP {cresp.status_code}: {cresp.text[:300]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
