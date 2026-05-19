"""Direct V2 order signing — bypass py-clob-client's order_builder entirely.

py-clob-client 0.34.6 hardcodes V1 exchange addresses inside get_contract_config,
and monkey-patching only the order_builder reference didn't take effect (likely
because the function is resolved through multiple import paths). This script
uses python-order-utils directly with the V2 exchange address, so there's no
ambiguity about which contract the signature is bound to.

Flow:
  1. Read managed wallet (Magic Link EOA + proxy) from DB
  2. Use py-clob-client only for: deriving EOA-bound API creds, picking a
     market via Gamma API, getting tick_size + neg_risk
  3. Build the 12-field V4-shape order with python-order-utils directly,
     signed against the V2 exchange (verifyingContract = 0xE111... or 0xe222...)
  4. POST /order manually with user L2 HMAC headers + Builder L2 HMAC headers

Run from backend/ on the VPS:
    .venv/bin/python -m scripts.smoke_direct_v2 --confirm
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
from py_clob_client.order_builder.helpers import (
    decimal_places, round_down, round_normal, round_up, to_token_decimals,
)
from py_order_utils.builders import OrderBuilder as UtilsOrderBuilder
from py_order_utils.model import OrderData
from py_order_utils.model.sides import BUY as PO_BUY
from py_order_utils.model.signatures import POLY_PROXY
from py_order_utils.signer import Signer as UtilsSigner

from src.config.settings import settings
from src.database.session import SessionLocal
from src.models import ManagedWallet
from src.wallet.manager import WalletManager


# V2 exchange addresses (the values py-clob-client should be using but isn't).
V2_CTF = "0xE111180000d2663C0091e4f400237545B87B996B"
V2_NEG = "0xe2222d279d744050d28e00520010520000310F59"

ROUND_CONFIG = {
    "0.1":    (1, 2, 3),  # (price_decimals, size_decimals, amount_decimals)
    "0.01":   (2, 2, 4),
    "0.001":  (3, 2, 5),
    "0.0001": (4, 2, 6),
}

_FALLBACK_BUILDER = {
    "key": "019e3dbf-f958-7bd6-8fcb-af985b0e056b",
    "secret": "PCQTaW5PUOL5cRs81d0ZbqQl5lqKOvgbMooBtiM7PQ8=",
    "passphrase": "060d2b4ede54ee9752163c03bd8cca8e75c09ff45df15ad2866907aa45ace9ae",
}


def load_builder_creds() -> dict:
    p = Path("/root/copytrade-builder-creds.json")
    if p.exists():
        return json.loads(p.read_text())
    print(f"  (no {p} — using fallback creds from memory)")
    return _FALLBACK_BUILDER


def compute_buy_amounts(size: float, price: float, tick: str) -> tuple[int, int]:
    """Match py-clob-client's get_order_amounts() for BUY orders. Returns
    (maker_amount_int, taker_amount_int) in token decimals (10^6)."""
    p_dec, s_dec, a_dec = ROUND_CONFIG[tick]
    raw_price = round_normal(price, p_dec)
    raw_taker = round_down(size, s_dec)
    raw_maker = raw_taker * raw_price
    if decimal_places(raw_maker) > a_dec:
        raw_maker = round_up(raw_maker, a_dec + 4)
        if decimal_places(raw_maker) > a_dec:
            raw_maker = round_down(raw_maker, a_dec)
    return to_token_decimals(raw_maker), to_token_decimals(raw_taker)


def _hmac(secret_b64url: str, ts: int, method: str, path: str, body: str) -> str:
    key = base64.urlsafe_b64decode(secret_b64url + "=" * ((4 - len(secret_b64url) % 4) % 4))
    msg = f"{ts}{method}{path}"
    if body:
        msg += body
    digest = hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii")


def user_l2_headers(eoa: str, creds, method: str, path: str, body: str) -> dict[str, str]:
    ts = int(time.time())
    return {
        "POLY_ADDRESS": to_checksum_address(eoa),
        "POLY_SIGNATURE": _hmac(creds.api_secret, ts, method, path, body),
        "POLY_TIMESTAMP": str(ts),
        "POLY_API_KEY": creds.api_key,
        "POLY_PASSPHRASE": creds.api_passphrase,
    }


def builder_l2_headers(builder: dict, method: str, path: str, body: str) -> dict[str, str]:
    ts = int(time.time())
    return {
        "POLY_BUILDER_API_KEY": builder["key"],
        "POLY_BUILDER_SIGNATURE": _hmac(builder["secret"], ts, method, path, body),
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

    if not proxy:
        print("ERROR: proxy_address required."); return 2
    print(f"EOA:           {eoa}")
    print(f"proxy_address: {proxy}")

    builder = load_builder_creds()
    print(f"\nbuilder key: {builder['key']}")

    print("\nDeriving EOA-bound user API creds via py-clob-client ...")
    pyclob = ClobClient(
        host=settings.polymarket_base_url, key=priv,
        chain_id=settings.polygon_chain_id, signature_type=0,
    )
    user_creds = pyclob.create_or_derive_api_creds()
    pyclob.set_api_creds(user_creds)
    print(f"  user api_key: {user_creds.api_key}")

    print("\nPicking active market ...")
    token_id, market_desc = pick_active_token(pyclob)
    tick_size = pyclob.get_tick_size(token_id)
    neg_risk = pyclob.get_neg_risk(token_id)
    exchange = V2_NEG if neg_risk else V2_CTF
    print(f"  token_id:    {token_id}")
    print(f"  market:      {market_desc}")
    print(f"  tick_size:   {tick_size}  neg_risk: {neg_risk}")
    print(f"  exchange:    {exchange}  ({'NegRisk' if neg_risk else 'CTF'} V2)")

    maker_amt, taker_amt = compute_buy_amounts(args.size, args.price, tick_size)
    print(f"\nPlan: BUY size={args.size} price=${args.price} -> maker={maker_amt} taker={taker_amt}")
    print(f"      maker=proxy, signer=EOA, sig_type=POLY_PROXY (1)")
    if not args.confirm:
        print("\nDRY RUN — pass --confirm to broadcast."); return 0

    # Build + sign V2 order DIRECTLY against the V2 exchange contract
    utils_signer = UtilsSigner(key=priv)
    builder_ob = UtilsOrderBuilder(exchange, settings.polygon_chain_id, utils_signer)
    data = OrderData(
        maker=proxy,
        signer=eoa,
        taker="0x0000000000000000000000000000000000000000",
        tokenId=str(token_id),
        makerAmount=str(maker_amt),
        takerAmount=str(taker_amt),
        side=PO_BUY,
        feeRateBps="0",
        nonce="0",
        expiration="0",
        signatureType=POLY_PROXY,
    )
    signed = builder_ob.build_signed_order(data)
    order_dict = signed.dict()
    body = {"order": order_dict, "owner": user_creds.api_key,
            "orderType": "GTC", "postOnly": False}
    body_str = json.dumps(body, separators=(",", ":"))
    print(f"\nWire body: {body_str[:300]}...")

    path = "/order"
    headers = {
        **user_l2_headers(eoa, user_creds, "POST", path, body_str),
        **builder_l2_headers(builder, "POST", path, body_str),
        "Content-Type": "application/json",
    }
    print(f"\nPOSTing /order with USER + BUILDER L2 headers ...")
    resp = httpx.post(f"{settings.polymarket_base_url}{path}",
                       content=body_str, headers=headers, timeout=15)
    print(f"  HTTP {resp.status_code}")
    print(f"  body: {resp.text[:500]}")

    if resp.status_code >= 400:
        print(f"\n--- RESULT: REJECTED ---")
        return 3
    payload = resp.json()
    order_id = payload.get("orderID") or payload.get("order_id") or payload.get("orderHash")
    err = payload.get("errorMsg")
    if err or not order_id:
        print(f"\n--- RESULT: REJECTED (200) ---  errorMsg={err}")
        return 4
    print(f"\n--- RESULT: ACCEPTED ---")
    print(f"order_id: {order_id}")
    print(f"V2 + Builder direct-signing path works end-to-end.")

    # Cancel
    cb = json.dumps({"orderID": order_id}, separators=(",", ":"))
    ch = {
        **user_l2_headers(eoa, user_creds, "DELETE", "/order", cb),
        **builder_l2_headers(builder, "DELETE", "/order", cb),
        "Content-Type": "application/json",
    }
    cr = httpx.request("DELETE", f"{settings.polymarket_base_url}/order",
                        content=cb, headers=ch, timeout=15)
    print(f"  cancel HTTP {cr.status_code}: {cr.text[:300]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
