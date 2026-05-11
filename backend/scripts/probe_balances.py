"""One-shot read-only balance probe via raw JSON-RPC.

Bypasses local CA store quirks on Windows by talking to Polygon RPC over a
plain httpx client with TLS verification disabled. Use only for read-only
public data — never for signed transactions.

Usage:
    python scripts/probe_balances.py <0xADDR> [<0xADDR> ...]
"""
import sys

import httpx

# A few free Polygon RPCs to try in order — first one that answers wins.
RPCS = [
    "https://polygon-bor-rpc.publicnode.com",
    "https://polygon.blockpi.network/v1/rpc/public",
    "https://1rpc.io/matic",
    "https://polygon.drpc.org",
    "https://polygon-rpc.com",
    "https://polygon.llamarpc.com",
    "https://rpc.ankr.com/polygon",
]

TOKENS = {
    "USDC.e (bridged)": ("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", 6),
    "USDC (native)":    ("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359", 6),
    "USDT":             ("0xc2132D05D31c914a87C6611C10748AEb04B58e8F", 6),
    "DAI":              ("0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063", 18),
}


def rpc_call(client: httpx.Client, rpc: str, method: str, params: list) -> str:
    r = client.post(rpc, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=10)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(data["error"])
    return data["result"]


def balance_of(client: httpx.Client, rpc: str, token: str, holder: str) -> int:
    holder_padded = holder.lower().replace("0x", "").rjust(64, "0")
    data = "0x70a08231" + holder_padded  # keccak("balanceOf(address)")[:4] = 0x70a08231
    hex_result = rpc_call(client, rpc, "eth_call", [{"to": token, "data": data}, "latest"])
    return int(hex_result, 16) if hex_result and hex_result != "0x" else 0


def matic_balance(client: httpx.Client, rpc: str, holder: str) -> int:
    hex_result = rpc_call(client, rpc, "eth_getBalance", [holder, "latest"])
    return int(hex_result, 16) if hex_result and hex_result != "0x" else 0


def pick_rpc(client: httpx.Client) -> str:
    for rpc in RPCS:
        try:
            rpc_call(client, rpc, "eth_chainId", [])
            return rpc
        except Exception as e:  # noqa: BLE001
            print(f"  skip {rpc}: {e}")
    raise RuntimeError("no working RPC")


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: probe_balances.py <0xADDR> [<0xADDR> ...]")
        sys.exit(1)

    with httpx.Client(verify=False) as client:
        rpc = pick_rpc(client)
        print(f"Using RPC: {rpc}\n")
        for addr in sys.argv[1:]:
            print(f"== {addr} ==")
            for name, (contract, decimals) in TOKENS.items():
                try:
                    raw = balance_of(client, rpc, contract, addr)
                    bal = raw / (10 ** decimals)
                    flag = "  <-- HAS BALANCE" if bal > 0 else ""
                    print(f"  {name:<22} {bal:>14,.4f}{flag}")
                except Exception as e:  # noqa: BLE001
                    print(f"  {name:<22} ERROR: {e}")
            try:
                m = matic_balance(client, rpc, addr) / 1e18
                print(f"  MATIC                  {m:>14,.6f}")
            except Exception as e:  # noqa: BLE001
                print(f"  MATIC                  ERROR: {e}")
            print()


if __name__ == "__main__":
    main()
