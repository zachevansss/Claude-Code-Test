"""Smoke test: post a single tiny limit order, well out of fill range, and
cancel it. Confirms whether user_id=1's managed EOA is accepted by Polymarket's
V2 CLOB after the EOA rotation.

Default: BUY 5 shares at $0.01 on the first active market. At $0.01, no one
will sell — so even if the order is accepted, it won't fill. We cancel
immediately to clean up either way.

Run from backend/ on the VPS:
    .venv/bin/python -m scripts.smoke_v2_order               # dry-run
    .venv/bin/python -m scripts.smoke_v2_order --confirm     # actually post

Expected outcomes:
  - HTTP 200 with order_id   -> rotation WORKED. Order accepted; we cancel.
  - 4xx "maker not allowed"  -> rotation DIDN'T fix it; deeper issue.
  - 4xx other (min size etc) -> structural issue, re-run with bigger --size.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import httpx
from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import to_checksum_address

sys.path.insert(0, ".")

from py_clob_client.client import ClobClient

from src.config.settings import settings
from src.database.session import SessionLocal
from src.executor import v2_signing
from src.models import ManagedWallet
from src.wallet.manager import WalletManager


_CLOB_AUTH_MESSAGE = "This message attests that I control the given wallet"


def derive_proxy_bound_api_creds(eoa_priv: str, proxy_address: str) -> dict[str, str]:
    """Authenticate as `proxy_address` (signed by the EOA's PK) and obtain
    API credentials bound to the proxy. Mirrors what polymarket.com's UI does
    but is not exposed by py-clob-client.

    The L1 EIP-712 message's `address` field is the proxy — that's the bit
    `createL1Headers(signer, chainId, nonce, ts, address?)` in clob-client-v2
    exposes but py-clob-client doesn't. The signature is by the EOA's PK so
    Polymarket's server recovers the EOA, checks it's authorized for the
    claimed proxy, and (if so) issues an API key keyed to the proxy.
    """
    ts = int(time.time())
    nonce = 0
    proxy_cs = to_checksum_address(proxy_address)

    typed_data = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
            ],
            "ClobAuth": [
                {"name": "address", "type": "address"},
                {"name": "timestamp", "type": "string"},
                {"name": "nonce", "type": "uint256"},
                {"name": "message", "type": "string"},
            ],
        },
        "primaryType": "ClobAuth",
        "domain": {
            "name": "ClobAuthDomain",
            "version": "1",
            "chainId": settings.polygon_chain_id,
        },
        "message": {
            "address": proxy_cs,
            "timestamp": str(ts),
            "nonce": nonce,
            "message": _CLOB_AUTH_MESSAGE,
        },
    }
    encoded = encode_typed_data(full_message=typed_data)
    sig = Account.sign_message(encoded, private_key=eoa_priv).signature
    sig_hex = "0x" + sig.hex().removeprefix("0x")

    headers = {
        "POLY_ADDRESS": proxy_cs,
        "POLY_SIGNATURE": sig_hex,
        "POLY_TIMESTAMP": str(ts),
        "POLY_NONCE": str(nonce),
    }

    url = f"{settings.polymarket_base_url}/auth/api-key"
    resp = httpx.post(url, headers=headers, timeout=15)
    # If a key already exists for this proxy, POST returns 400; fall back to
    # GET /auth/derive-api-key (same headers, same signature) to retrieve it.
    if resp.status_code == 400:
        resp = httpx.get(
            f"{settings.polymarket_base_url}/auth/derive-api-key",
            headers=headers, timeout=15,
        )
    resp.raise_for_status()
    body = resp.json()
    creds = {
        "api_key": body.get("apiKey") or body.get("api_key"),
        "api_secret": body.get("secret") or body.get("api_secret"),
        "api_passphrase": body.get("passphrase") or body.get("api_passphrase"),
    }
    if not creds["api_key"]:
        raise RuntimeError(f"proxy-bound L1 auth returned no api_key: {body}")
    return creds


def get_client(wallet: ManagedWallet) -> ClobClient:
    """Build a ClobClient with API creds. If the wallet has a proxy_address,
    use the proxy-bound L1 auth flow (manually constructed L1 EIP-712 with
    address=proxy) instead of py-clob-client's default EOA-bound flow."""
    priv = WalletManager.get_private_key_hex(wallet)
    if wallet.proxy_address:
        c = ClobClient(
            host=settings.polymarket_base_url, key=priv,
            chain_id=settings.polygon_chain_id,
            signature_type=1, funder=wallet.proxy_address,
        )
        from py_clob_client.clob_types import ApiCreds
        creds = derive_proxy_bound_api_creds(priv, wallet.proxy_address)
        c.set_api_creds(ApiCreds(
            api_key=creds["api_key"],
            api_secret=creds["api_secret"],
            api_passphrase=creds["api_passphrase"],
        ))
    else:
        c = ClobClient(
            host=settings.polymarket_base_url, key=priv,
            chain_id=settings.polygon_chain_id, signature_type=0,
        )
        c.set_api_creds(c.create_or_derive_api_creds())
    return c


def pick_active_token(client: ClobClient) -> tuple[str, str]:
    """Find an active market with a live orderbook (has bids or asks).

    The CLOB's `simplified-markets` first page is heavily polluted with archived
    markets that return 404 from /book. The Gamma API at gamma-api.polymarket.com
    exposes a clean `active=true & closed=false` filter and sorts by 24h volume,
    so a single page is almost guaranteed to contain at least one live book.
    Falls back to CLOB simplified-markets only if Gamma is unreachable."""
    probed = 0
    try:
        resp = httpx.get(
            "https://gamma-api.polymarket.com/markets",
            params={"active": "true", "closed": "false", "limit": 25,
                    "order": "volume24hr", "ascending": "false"},
            timeout=10,
        )
        resp.raise_for_status()
        markets = resp.json()
        for m in markets:
            tids_raw = m.get("clobTokenIds")
            if not tids_raw:
                continue
            tids = json.loads(tids_raw) if isinstance(tids_raw, str) else tids_raw
            for tid in tids:
                if not tid or str(tid) == "0":
                    continue
                probed += 1
                try:
                    book = client.get_order_book(str(tid))
                except Exception:
                    continue
                bids = getattr(book, "bids", None) or []
                asks = getattr(book, "asks", None) or []
                if bids or asks:
                    q = (m.get("question") or m.get("slug") or "?")[:60]
                    return str(tid), q
    except Exception as e:
        print(f"  (gamma lookup failed, falling back to simplified-markets: {e})")

    # Fallback: legacy simplified-markets probe
    resp = client.get_simplified_markets("")
    markets = resp.get("data") if isinstance(resp, dict) else None
    if not markets:
        raise RuntimeError(f"no markets found (probed {probed} via gamma)")
    for m in markets:
        if not m.get("accepting_orders"):
            continue
        for tok in m.get("tokens", []):
            tid = tok.get("token_id")
            if not tid or tid == "0":
                continue
            probed += 1
            try:
                book = client.get_order_book(tid)
            except Exception:
                continue
            bids = getattr(book, "bids", None) or []
            asks = getattr(book, "asks", None) or []
            if bids or asks:
                return tid, f"{m.get('question', '?')[:60]} ({tok.get('outcome')})"
        if probed > 50:
            break
    raise RuntimeError(f"no active markets with live orderbooks found (probed {probed})")


def _l2_headers(client: ClobClient, addr: str, method: str, path: str, body: str) -> dict:
    return v2_signing.build_l2_headers(
        signer_address=addr,
        api_key=client.creds.api_key,
        api_secret=client.creds.api_secret,
        api_passphrase=client.creds.api_passphrase,
        method=method,
        request_path=path,
        body=body,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--token-id", default=None,
                        help="specific token to bid on (default: auto-pick first active)")
    parser.add_argument("--price", type=float, default=0.01,
                        help="limit price 0.001-0.999 (default 0.01, below any reasonable ask)")
    parser.add_argument("--size", type=float, default=5.0,
                        help="size in shares (default 5)")
    parser.add_argument("--confirm", action="store_true",
                        help="actually POST the order (default dry-run)")
    parser.add_argument("--sig-type", choices=["auto", "eoa", "proxy", "1271"], default="auto",
                        help="override signing mode. auto: 1271 if proxy set else eoa. "
                             "proxy: maker=proxy, signer=EOA, sig_type=1 (POLY_PROXY). "
                             "1271: maker=signer=proxy, sig_type=3 (POLY_1271).")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        wallet = (
            db.query(ManagedWallet)
            .filter(ManagedWallet.user_id == args.user_id)
            .first()
        )
        if not wallet:
            print(f"no managed wallet for user_id={args.user_id}"); return 1
        addr = wallet.address
        proxy = wallet.proxy_address
    finally:
        db.close()

    print(f"EOA:           {addr}")
    print(f"proxy_address: {proxy or '(null — pure EOA mode)'}")

    print(f"\nInitializing CLOB client + deriving API creds ...")
    client = get_client(wallet)
    print(f"api_key:       {client.creds.api_key}")

    if args.token_id:
        token_id = args.token_id
        desc = "(user-specified)"
    else:
        print(f"\nAuto-picking an active market ...")
        token_id, desc = pick_active_token(client)
    print(f"token_id:      {token_id}")
    print(f"market:        {desc}")

    tick_size = client.get_tick_size(token_id)
    neg_risk = client.get_neg_risk(token_id)
    exchange_label = "NegRisk V2" if neg_risk else "CTF V2"
    print(f"tick_size:     {tick_size}")
    print(f"neg_risk:      {neg_risk}  ({exchange_label})")

    cost_usd = args.price * args.size
    print(f"\nPlan: BUY {args.size} shares @ ${args.price:.3f} (${cost_usd:.4f}) on {exchange_label}")
    print(f"      This order is deeply out-of-bid; it should be accepted but never fill.")
    if not args.confirm:
        print("\nDRY RUN — pass --confirm to broadcast.")
        return 0

    # Build + sign
    exchange = v2_signing.NEG_RISK_EXCHANGE_V2 if neg_risk else v2_signing.CTF_EXCHANGE_V2
    maker_amt, taker_amt = v2_signing.compute_amounts("BUY", args.size, args.price, tick_size)

    # Pick signing mode. POLY_PROXY (sig_type=1) is the only mode where
    # maker=proxy AND signer=EOA — meaning it can satisfy both Polymarket
    # checks simultaneously (maker allowed because it's a proxy; signer
    # matches the API-key EOA).
    mode = args.sig_type
    if mode == "auto":
        mode = "1271" if proxy else "eoa"
    if mode == "eoa":
        maker = signer = addr
        sig_type = v2_signing.SIG_EOA
    elif mode == "proxy":
        if not proxy:
            raise SystemExit("--sig-type proxy needs proxy_address set on the wallet")
        maker = proxy
        signer = addr
        sig_type = v2_signing.SIG_POLY_PROXY
    elif mode == "1271":
        if not proxy:
            raise SystemExit("--sig-type 1271 needs proxy_address set on the wallet")
        maker = signer = proxy
        sig_type = v2_signing.SIG_POLY_1271
    else:
        raise SystemExit(f"unknown sig-type: {mode}")
    print(f"sig mode:      {mode}  (maker={maker[:10]}..., signer={signer[:10]}..., sig_type={sig_type})")
    order = v2_signing.build_order(
        maker=maker, signer=signer, token_id=token_id,
        maker_amount=maker_amt, taker_amount=taker_amt,
        side="BUY", signature_type=sig_type,
    )
    priv = WalletManager.get_private_key_hex(wallet)
    signed = v2_signing.sign_order(order, exchange, priv)
    body = v2_signing.order_to_wire(signed, owner=client.creds.api_key, order_type="GTC")
    body_str = json.dumps(body, separators=(",", ":"))

    print(f"\nPOST {settings.polymarket_base_url}/order ...")
    resp = httpx.post(
        f"{settings.polymarket_base_url}/order",
        content=body_str,
        headers={**_l2_headers(client, proxy or addr, "POST", "/order", body_str),
                 "Content-Type": "application/json"},
        timeout=15,
    )
    print(f"HTTP {resp.status_code}")
    print(f"Body: {resp.text[:500]}")

    if resp.status_code >= 400:
        print("\n--- RESULT: REJECTED ---")
        if "maker" in resp.text.lower() and "not allowed" in resp.text.lower():
            print("STILL hitting 'maker not allowed' — rotation did NOT unblock orders.")
        elif "size" in resp.text.lower() or "minimum" in resp.text.lower():
            print("Structural rejection (size/min). Re-run with --size 100 --price 0.01.")
        else:
            print("Other rejection — see body above.")
        return 2

    payload = resp.json()
    order_id = (
        payload.get("orderID")
        or payload.get("order_id")
        or payload.get("orderHash")
    )
    if payload.get("success") is False or (not order_id and "errorMsg" in payload):
        print("\n--- RESULT: REJECTED (200 but success=false) ---")
        print(f"errorMsg: {payload.get('errorMsg')}")
        return 3

    print(f"\n--- RESULT: ACCEPTED ---")
    print(f"order_id: {order_id}")
    print(f"Rotation WORKED. Polymarket accepts orders from the new EOA.")

    if not order_id:
        print("WARN: no order_id parsed from response — manual cancel may be needed.")
        return 0

    # Cancel
    print(f"\nCancelling order {order_id} ...")
    cancel_body = json.dumps({"orderID": order_id}, separators=(",", ":"))
    cresp = httpx.request(
        "DELETE",
        f"{settings.polymarket_base_url}/order",
        content=cancel_body,
        headers={**_l2_headers(client, proxy or addr, "DELETE", "/order", cancel_body),
                 "Content-Type": "application/json"},
        timeout=15,
    )
    print(f"Cancel HTTP {cresp.status_code}")
    print(f"Cancel body: {cresp.text[:300]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
