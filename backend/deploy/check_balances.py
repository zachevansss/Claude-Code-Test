"""Diagnose stablecoin balances on Polygon for a Polymarket proxy address.

Run from the VPS while in backend/ folder:
    ./.venv/bin/python deploy/check_balances.py 0xPROXY_ADDRESS
"""
import os
import sys

from dotenv import load_dotenv
from web3 import Web3

CANDIDATES = {
    "USDC.e (bridged)": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
    "USDC (native)":    "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
    "USDT (Tether)":    "0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
    "DAI":              "0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063",
}

ABI = [{
    "constant": True,
    "inputs": [{"name": "owner", "type": "address"}],
    "name": "balanceOf",
    "outputs": [{"name": "", "type": "uint256"}],
    "type": "function",
}]


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: check_balances.py <0xADDRESS>")
        sys.exit(1)
    addr = sys.argv[1]

    load_dotenv()
    rpc = os.environ.get("POLYGON_RPC_URL")
    if not rpc:
        print("POLYGON_RPC_URL not set in env")
        sys.exit(1)

    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 12}))
    target = Web3.to_checksum_address(addr)

    print(f"Address: {addr}\n")
    for name, contract_addr in CANDIDATES.items():
        c = w3.eth.contract(address=Web3.to_checksum_address(contract_addr), abi=ABI)
        raw = c.functions.balanceOf(target).call()
        decimals = 18 if "DAI" in name else 6
        bal = raw / (10 ** decimals)
        print(f"  {name:<22} {contract_addr}  balance={bal:,.4f}")

    matic = w3.eth.get_balance(target) / 1e18
    print(f"\n  MATIC: {matic:,.6f}")


if __name__ == "__main__":
    main()
