"""Check pUSD allowance + CTF setApprovalForAll status on a Polymarket proxy
against both the legacy and V2 exchange contracts. Read-only.

Usage:
    python scripts/probe_approvals.py <0xPROXY>
"""
import sys

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

import httpx

from src.config.settings import settings


PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

EXCHANGES = {
    "CTF Exchange V1 (legacy)":  "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
    "NegRisk Exchange V1 (legacy)": "0xC5d563A36AE78145C45a50134d48A1215220f80a",
    "CTF Exchange V2 (post-Apr28)":  "0xE111180000d2663C0091e4f400237545B87B996B",
    "NegRisk Exchange V2 (post-Apr28)": "0xe2222d279d744050d28e00520010520000310F59",
    "NegRisk Adapter":             "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296",
}

APPROVE_THRESHOLD = 2 ** 200


def rpc_call(client: httpx.Client, method: str, params: list):
    r = client.post(settings.polygon_rpc_url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=10)
    r.raise_for_status()
    return r.json().get("result")


def _pad(addr: str) -> str:
    return addr.lower().replace("0x", "").rjust(64, "0")


def allowance(client, owner: str, spender: str) -> int:
    data = "0xdd62ed3e" + _pad(owner) + _pad(spender)  # allowance(address,address)
    res = rpc_call(client, "eth_call", [{"to": PUSD, "data": data}, "latest"])
    return int(res, 16) if res and res != "0x" else 0


def is_approved_for_all(client, owner: str, operator: str) -> bool:
    data = "0xe985e9c5" + _pad(owner) + _pad(operator)  # isApprovedForAll(address,address)
    res = rpc_call(client, "eth_call", [{"to": CTF, "data": data}, "latest"])
    return bool(int(res, 16)) if res and res != "0x" else False


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: probe_approvals.py <0xPROXY>")
        sys.exit(1)
    proxy = sys.argv[1]

    with httpx.Client() as client:
        print(f"Proxy: {proxy}\n")
        for label, addr in EXCHANGES.items():
            a = allowance(client, proxy, addr)
            ctf_ok = is_approved_for_all(client, proxy, addr)
            pusd_status = "approved (max)" if a >= APPROVE_THRESHOLD else f"{a / 1e6:.4f} pUSD"
            ctf_status = "approved" if ctf_ok else "NOT approved"
            print(f"  {label:<35} ({addr})")
            print(f"      pUSD allowance: {pusd_status}")
            print(f"      CTF setApprovalForAll: {ctf_status}\n")


if __name__ == "__main__":
    main()
