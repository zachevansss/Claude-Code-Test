"""On-chain balance lookups for managed wallets. Read-only — never signs.

The functions return floats (USDC and MATIC, denominated in their natural
units), or None when the RPC call fails. Callers decide how to react: the
/wallet endpoint surfaces the failure to the UI; the bot manager skips the
tick rather than trade with stale balance info."""
from web3 import Web3

from src.config.settings import settings
from src.utils.logging import get_logger

log = get_logger("WALLET")

# Polymarket-issued pUSD (1:1 backed by USDC) became the exchange's collateral
# token in the 2026-04-28 exchange upgrade. Reading USDC.e here returns 0 for
# every post-upgrade Polymarket account, even though deposits succeed — the
# Coinbase onramp wraps USDC into pUSD at deposit time. 6 decimals, same as USDC.
PUSD_ADDRESS = Web3.to_checksum_address("0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB")
PUSD_DECIMALS = 6

# Kept for backward compatibility with approvals.py until that module is migrated
# to approve pUSD against the (new) exchange. Do not use for balance reads.
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
    """Return (collateral_balance, matic_balance, error) for a Polymarket
    address. Collateral is pUSD post-2026-04-28; the first element is exposed
    via the API as `usdc_balance` for now — it is the user's tradable USD
    balance regardless of which token Polymarket happens to denominate it in.
    Best-effort: RPC failures return Nones with the error string populated."""
    try:
        w3 = _w3()
        addr = Web3.to_checksum_address(address)
        matic_wei = w3.eth.get_balance(addr)
        pusd = w3.eth.contract(address=PUSD_ADDRESS, abi=ERC20_BALANCE_ABI)
        pusd_raw = pusd.functions.balanceOf(addr).call()
        return (
            pusd_raw / 10**PUSD_DECIMALS,
            matic_wei / 10**18,
            None,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("balance lookup failed for %s: %s", address, e)
        return None, None, f"RPC lookup failed: {e}"


def get_usdc_balance(address: str) -> float | None:
    """Convenience for callers that only need the tradable collateral balance
    and don't care about RPC errors. Despite the name, returns pUSD."""
    bal, _, _ = get_balances(address)
    return bal
