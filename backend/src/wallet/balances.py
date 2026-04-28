"""On-chain balance lookups for managed wallets. Read-only — never signs.

The functions return floats (USDC and MATIC, denominated in their natural
units), or None when the RPC call fails. Callers decide how to react: the
/wallet endpoint surfaces the failure to the UI; the bot manager skips the
tick rather than trade with stale balance info."""
from web3 import Web3

from src.config.settings import settings
from src.utils.logging import get_logger

log = get_logger("WALLET")

# Polygon PoS USDC, used by Polymarket as collateral. 6 decimals.
USDC_ADDRESS = Web3.to_checksum_address("0x2791bca1f2de4661ed88a30c99a7a9449aa84174")
USDC_DECIMALS = 6

ERC20_BALANCE_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    }
]


def _w3() -> Web3:
    return Web3(
        Web3.HTTPProvider(settings.polygon_rpc_url, request_kwargs={"timeout": 8})
    )


def get_balances(address: str) -> tuple[float | None, float | None, str | None]:
    """Return (usdc_balance, matic_balance, error). Best-effort — RPC failures
    return Nones with the error string populated."""
    try:
        w3 = _w3()
        addr = Web3.to_checksum_address(address)
        matic_wei = w3.eth.get_balance(addr)
        usdc = w3.eth.contract(address=USDC_ADDRESS, abi=ERC20_BALANCE_ABI)
        usdc_raw = usdc.functions.balanceOf(addr).call()
        return (
            usdc_raw / 10**USDC_DECIMALS,
            matic_wei / 10**18,
            None,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("balance lookup failed for %s: %s", address, e)
        return None, None, f"RPC lookup failed: {e}"


def get_usdc_balance(address: str) -> float | None:
    """Convenience for callers that only need USDC and don't care about errors."""
    usdc, _, _ = get_balances(address)
    return usdc
