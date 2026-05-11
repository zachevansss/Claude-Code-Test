"""Deep address probe: is it a contract? Tx count? Latest token transfers?

Uses public RPC + Polygonscan public API (no key needed for low-rate queries).
Read-only diagnostic only.

Usage:
    python scripts/probe_address.py <0xADDR>
"""
import sys

import httpx

RPC = "https://polygon-bor-rpc.publicnode.com"
POLYGONSCAN = "https://api.polygonscan.com/api"


def rpc(client: httpx.Client, method: str, params: list):
    r = client.post(RPC, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=10)
    r.raise_for_status()
    return r.json().get("result")


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: probe_address.py <0xADDR>")
        sys.exit(1)
    addr = sys.argv[1]

    with httpx.Client(verify=False) as client:
        code = rpc(client, "eth_getCode", [addr, "latest"])
        is_contract = code and code != "0x"
        print(f"Address:     {addr}")
        print(f"Type:        {'CONTRACT' if is_contract else 'EOA (regular wallet)'}")

        tx_count_hex = rpc(client, "eth_getTransactionCount", [addr, "latest"])
        tx_count = int(tx_count_hex, 16) if tx_count_hex else 0
        print(f"Tx nonce:    {tx_count} (txs sent FROM this address as signer)")

        # Polygonscan API — list of recent ERC-20 token transfers in or out
        print("\nRecent ERC-20 transfers (Polygonscan, last 10):")
        try:
            r = client.get(
                POLYGONSCAN,
                params={
                    "module": "account",
                    "action": "tokentx",
                    "address": addr,
                    "page": 1,
                    "offset": 10,
                    "sort": "desc",
                },
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            if data.get("status") != "1":
                print(f"  none / {data.get('message', 'unknown')}")
            else:
                for tx in data["result"][:10]:
                    sym = tx.get("tokenSymbol", "?")
                    val = int(tx.get("value", "0")) / (10 ** int(tx.get("tokenDecimal", "0") or 0))
                    direction = "IN " if tx["to"].lower() == addr.lower() else "OUT"
                    other = tx["from"] if direction == "IN " else tx["to"]
                    print(f"  {direction} {val:>14,.4f} {sym:<10} {other}  tx={tx['hash'][:18]}…")
        except Exception as e:  # noqa: BLE001
            print(f"  Polygonscan error: {e}")

        # And native MATIC transfers
        print("\nRecent native MATIC transactions (Polygonscan, last 10):")
        try:
            r = client.get(
                POLYGONSCAN,
                params={
                    "module": "account",
                    "action": "txlist",
                    "address": addr,
                    "page": 1,
                    "offset": 10,
                    "sort": "desc",
                },
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            if data.get("status") != "1":
                print(f"  none / {data.get('message', 'unknown')}")
            else:
                for tx in data["result"][:10]:
                    val = int(tx.get("value", "0")) / 1e18
                    direction = "IN " if tx["to"].lower() == addr.lower() else "OUT"
                    other = tx["from"] if direction == "IN " else tx["to"]
                    print(f"  {direction} {val:>14,.6f} MATIC      {other}  tx={tx['hash'][:18]}…")
        except Exception as e:  # noqa: BLE001
            print(f"  Polygonscan error: {e}")


if __name__ == "__main__":
    main()
