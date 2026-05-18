"""Redeem winning CTF tokens for resolved Polymarket positions.

Background: when a market resolves, the EOA's ERC-1155 outcome tokens become
redeemable for pUSD via ConditionalTokens.redeemPositions. Polymarket's UI
no longer applies to us (funds are out of the proxy), so we call redeem
ourselves. The resolution/checker already closes the position in our DB and
records realized PnL synthetically — this script handles only the on-chain
side, converting outcome tokens → pUSD in the EOA's wallet.

Scope (phase 1):
  * Standard CTF markets only. Neg-risk markets are detected via gamma-api
    `negRisk=true` and skipped with a log line — the NegRiskAdapter has a
    different redeem path that we have not approved yet (per CLAUDE.md).
  * Idempotent: after redemption, the EOA's CTF balance for those tokenIds
    goes to 0, so re-running is a no-op.

Run on the VPS:
    python -m scripts.redeem_resolved_positions --user-id 1            # dry-run
    python -m scripts.redeem_resolved_positions --user-id 1 --confirm  # broadcast
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import defaultdict
from typing import Any

import httpx
from eth_account import Account
from web3 import Web3

sys.path.insert(0, ".")

from src.config.settings import settings
from src.wallet.approvals import CTF_ADDRESS, COLLATERAL
from src.wallet.crypto import decrypt


def _read_only_query(sql: str, params: tuple = ()) -> list[tuple]:
    """Open the SQLite DB read-only so the bot's running write transactions
    don't block us. Works only with sqlite:// URLs."""
    if not settings.database_url.startswith("sqlite"):
        raise RuntimeError("read-only fallback only supports SQLite")
    # sqlite:///./copytrade.db  ->  ./copytrade.db
    path = settings.database_url.replace("sqlite:///", "", 1)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


GAMMA_API = "https://gamma-api.polymarket.com"
PARENT_COLLECTION_ID = b"\x00" * 32  # no parent collection — top-level outcomes

CTF_ABI = [
    {"inputs": [{"name": "owner", "type": "address"},
                {"name": "id", "type": "uint256"}],
     "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "collateralToken", "type": "address"},
                {"name": "parentCollectionId", "type": "bytes32"},
                {"name": "conditionId", "type": "bytes32"},
                {"name": "indexSets", "type": "uint256[]"}],
     "name": "redeemPositions", "outputs": [],
     "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "conditionId", "type": "bytes32"}],
     "name": "payoutDenominator",
     "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
]

PUSD_BALANCE_ABI = [
    {"constant": True, "inputs": [{"name": "o", "type": "address"}],
     "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],
     "type": "function"},
]


def fetch_markets(condition_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Returns {conditionId: market_dict} for whatever gamma-api knows about.

    Hits the same endpoint resolution/checker uses, but doesn't filter to
    closed-only — we want resolution status AND negRisk flag for ALL our
    markets so we can decide what to skip vs redeem."""
    if not condition_ids:
        return {}
    out: dict[str, dict[str, Any]] = {}
    # Gamma's filter is `condition_ids=<cid>` (repeated param). Batch at 20.
    for i in range(0, len(condition_ids), 20):
        batch = condition_ids[i:i + 20]
        params: list[tuple[str, str]] = [("condition_ids", c) for c in batch]
        params.append(("limit", str(len(batch))))
        try:
            r = httpx.get(f"{GAMMA_API}/markets", params=params, timeout=10.0)
            r.raise_for_status()
            payload = r.json()
            if isinstance(payload, list):
                for m in payload:
                    cid = m.get("conditionId")
                    if cid:
                        out[cid] = m
        except Exception as e:  # noqa: BLE001
            print(f"  gamma-api batch failed: {e}")
    return out


def to_bytes32(hex_str: str) -> bytes:
    """conditionId comes from gamma-api as 0x-prefixed 32-byte hex. Convert
    to raw bytes for the eth_abi encoder."""
    s = hex_str[2:] if hex_str.startswith("0x") else hex_str
    return bytes.fromhex(s)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()

    # Read managed_wallets row (need encrypted_private_key to derive signer).
    wallet_rows = _read_only_query(
        "SELECT address, encrypted_private_key FROM managed_wallets WHERE user_id = ?",
        (args.user_id,),
    )
    if not wallet_rows:
        print(f"no managed wallet for user_id={args.user_id}"); return 1
    _addr, encrypted_pk = wallet_rows[0]
    signer = Account.from_key(decrypt(encrypted_pk))
    eoa = signer.address

    # Pull distinct (market_id, asset_id) pairs from live filled trades.
    # market_id IS the conditionId (verified via tracker/poller.py).
    rows = _read_only_query(
        """
        SELECT DISTINCT market_id, asset_id
        FROM trades
        WHERE user_id = ?
          AND mode = 'live'
          AND status IN ('filled', 'partial')
          AND asset_id IS NOT NULL
        """,
        (args.user_id,),
    )

    print(f"EOA: {eoa}")
    print(f"Distinct (market_id, asset_id) pairs from live filled trades: {len(rows)}")
    if not rows:
        print("Nothing to redeem.")
        return 0

    # Group asset_ids by market_id (same condition can have multiple outcomes).
    by_market: dict[str, list[str]] = defaultdict(list)
    for market_id, asset_id in rows:
        by_market[market_id].append(asset_id)

    print(f"\nUnique markets traded: {len(by_market)}")

    # Fetch market metadata so we can: (1) skip neg-risk, (2) skip unresolved,
    # (3) map asset_id -> outcome index for indexSet calc.
    markets = fetch_markets(list(by_market.keys()))
    print(f"Gamma-api returned metadata for: {len(markets)} markets")

    w3 = Web3(Web3.HTTPProvider(settings.polygon_rpc_url, request_kwargs={"timeout": 15}))
    ctf = w3.eth.contract(address=CTF_ADDRESS, abi=CTF_ABI)
    pusd = w3.eth.contract(address=COLLATERAL, abi=PUSD_BALANCE_ABI)

    pusd_before = pusd.functions.balanceOf(eoa).call()

    # Build a redemption plan.
    plan: list[dict[str, Any]] = []  # {market_id, indexSets, balances, market_title}
    for cid, asset_ids in by_market.items():
        m = markets.get(cid)
        if not m:
            print(f"  [{cid[:10]}...] no gamma-api data — skip")
            continue
        title = (m.get("question") or m.get("title") or "")[:80]
        if m.get("negRisk"):
            print(f"  [{cid[:10]}...] neg-risk market — SKIP (not yet supported): {title}")
            continue

        # Resolution check: payoutDenominator > 0 means CTF has the resolution
        # report and redemption will succeed.
        try:
            denom = ctf.functions.payoutDenominator(to_bytes32(cid)).call()
        except Exception as e:  # noqa: BLE001
            print(f"  [{cid[:10]}...] payoutDenominator() failed: {e} — skip")
            continue
        if denom == 0:
            print(f"  [{cid[:10]}...] not yet resolved on-chain — skip: {title}")
            continue

        # Determine indexSets: for each asset_id we hold, find its position in
        # the market's clobTokenIds list; indexSet = 1 << position.
        clob_tokens_raw = m.get("clobTokenIds")
        if isinstance(clob_tokens_raw, str):
            try:
                clob_tokens = json.loads(clob_tokens_raw)
            except json.JSONDecodeError:
                clob_tokens = []
        else:
            clob_tokens = clob_tokens_raw or []
        if not clob_tokens:
            print(f"  [{cid[:10]}...] no clobTokenIds in gamma response — skip: {title}")
            continue

        # For each unique asset_id we have, check on-chain balance and find index.
        index_sets: list[int] = []
        balances: list[tuple[int, int]] = []  # (index, balance)
        for aid in set(asset_ids):
            try:
                bal = ctf.functions.balanceOf(eoa, int(aid)).call()
            except Exception as e:  # noqa: BLE001
                print(f"  [{cid[:10]}...] balanceOf failed for asset {aid}: {e}")
                continue
            if bal == 0:
                continue
            # Find index of this asset_id in clobTokens (compare as strings to
            # avoid uint mismatch from JSON int parsing).
            try:
                idx = [str(t) for t in clob_tokens].index(str(aid))
            except ValueError:
                print(f"  [{cid[:10]}...] asset {aid} not in clobTokenIds — skip this side")
                continue
            index_sets.append(1 << idx)
            balances.append((idx, bal))

        if not index_sets:
            print(f"  [{cid[:10]}...] no on-chain balance for any outcome — skip: {title}")
            continue

        plan.append({
            "market_id": cid,
            "indexSets": index_sets,
            "balances": balances,
            "title": title,
        })
        bal_str = ", ".join(f"slot{i}: {b/1e6:.2f}" for i, b in balances)
        print(f"  [{cid[:10]}...] READY: indexSets={index_sets}  balances=[{bal_str}]  {title}")

    print(f"\nRedeemable markets: {len(plan)}")
    if not plan:
        print("Nothing redeemable. Exiting.")
        return 0

    if not args.confirm:
        print("\nDRY RUN — pass --confirm to broadcast.")
        return 0

    # Broadcast
    nonce = w3.eth.get_transaction_count(eoa)
    gp = w3.eth.gas_price
    print(f"\nGas price: {gp/1e9:.2f} gwei  |  starting nonce: {nonce}")
    chain_id = settings.polygon_chain_id

    receipts: list[dict[str, Any]] = []
    for entry in plan:
        cid = entry["market_id"]
        idx_sets = entry["indexSets"]
        print(f"\nRedeeming {cid[:10]}...  indexSets={idx_sets}  ({entry['title']})")
        tx = ctf.functions.redeemPositions(
            COLLATERAL, PARENT_COLLECTION_ID, to_bytes32(cid), idx_sets
        ).build_transaction({
            "from": eoa, "nonce": nonce, "gas": 300_000,
            "gasPrice": gp, "chainId": chain_id,
        })
        signed = signer.sign_transaction(tx)
        raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
        h = w3.eth.send_raw_transaction(raw)
        print(f"  tx: {h.hex()}")
        rcpt = w3.eth.wait_for_transaction_receipt(h, timeout=180)
        print(f"  status: {rcpt.status}  block={rcpt.blockNumber}  gas={rcpt.gasUsed}")
        receipts.append({"market_id": cid, "tx": h.hex(), "status": rcpt.status})
        if rcpt.status != 1:
            print(f"  redemption FAILED — continuing with next market")
        nonce += 1

    time.sleep(2)
    pusd_after = pusd.functions.balanceOf(eoa).call()
    delta = (pusd_after - pusd_before) / 1e6
    print(f"\npUSD before: {pusd_before/1e6:,.6f}")
    print(f"pUSD after:  {pusd_after/1e6:,.6f}")
    print(f"Redeemed:    +{delta:,.6f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
