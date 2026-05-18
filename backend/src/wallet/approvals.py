"""One-time on-chain approvals so a managed wallet can trade on Polymarket.

What we approve, for both the V2 CTF Exchange AND the V2 NegRisk Exchange
(many Polymarket markets — political, multi-outcome — route through neg-risk):
  1. pUSD.approve(<exchange>, max)          — exchange can pull pUSD for buys
  2. CTF.setApprovalForAll(<exchange>, True) — exchange can move outcome tokens

Both contracts share the same pUSD collateral token and the same CTF
(conditional tokens) contract; only the spender/operator differs. Per-spender
allowances and approvals are independent, so each exchange needs its own pair.

Txs are sent as legacy gas (not EIP-1559) for broad RPC compatibility on
Polygon. We check current allowance/approval first so a re-run costs no gas
if everything is already set.

V2 vs V1: Polymarket migrated the order book to new exchange contracts in
~Feb 2026; collateral moved from USDC.e to pUSD (Polymarket-issued, 1:1
backed). The addresses below are the V2 ones; V1 exchanges still exist but
are no longer used for new orders.

NOT HANDLED: the NegRisk Adapter contract (used for split/merge/redeem of
neg-risk positions) is separate. The bot only places limit orders, which the
exchange approvals above cover. Redemption after market resolution is done
manually via the Polymarket UI."""
from typing import Any

from eth_account.signers.local import LocalAccount
from web3 import Web3
from web3.contract import Contract

from src.config.settings import settings
from src.utils.logging import get_logger
from src.wallet.balances import PUSD_ADDRESS

log = get_logger("WALLET")

# Polymarket V2 on Polygon. Sourced from the official @polymarket/clob-client-v2
# (src/config.ts MATIC_CONTRACTS) and cross-checked on-chain.
COLLATERAL = PUSD_ADDRESS  # pUSD — the V2 collateral token
CTF_ADDRESS = Web3.to_checksum_address("0x4D97DCd97eC945f40cF65F87097ACe5EA0476045")
CTF_EXCHANGE_V2 = Web3.to_checksum_address("0xE111180000d2663C0091e4f400237545B87B996B")
NEG_RISK_EXCHANGE_V2 = Web3.to_checksum_address("0xe2222d279d744050d28e00520010520000310F59")
EXCHANGES: list[tuple[str, str]] = [
    ("CTF Exchange V2", CTF_EXCHANGE_V2),
    ("NegRisk Exchange V2", NEG_RISK_EXCHANGE_V2),
]

MAX_UINT256 = 2**256 - 1
APPROVE_THRESHOLD = 2**200  # if allowance >= this, treat as "already approved max"
MIN_MATIC_FOR_APPROVALS = 0.05  # ~10x worst-case gas at 100gwei

ERC20_APPROVE_ABI: list[dict[str, Any]] = [
    {
        "constant": False,
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "value", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
]

CTF_ABI: list[dict[str, Any]] = [
    {
        "constant": False,
        "inputs": [
            {"name": "operator", "type": "address"},
            {"name": "approved", "type": "bool"},
        ],
        "name": "setApprovalForAll",
        "outputs": [],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "operator", "type": "address"},
        ],
        "name": "isApprovedForAll",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
]


class SetupError(RuntimeError):
    """Raised when on-chain setup cannot proceed (insufficient gas, RPC down, etc.)."""


def _w3() -> Web3:
    return Web3(
        Web3.HTTPProvider(settings.polygon_rpc_url, request_kwargs={"timeout": 12})
    )


def _send(
    w3: Web3,
    signer: LocalAccount,
    contract: Contract,
    fn_name: str,
    args: tuple,
    gas_limit: int,
    nonce: int,
) -> str:
    """Build, sign, and broadcast a tx. Returns the tx hash. Does not wait for confirmation."""
    tx = contract.functions[fn_name](*args).build_transaction({
        "from": signer.address,
        "nonce": nonce,
        "gas": gas_limit,
        "gasPrice": w3.eth.gas_price,
        "chainId": settings.polygon_chain_id,
    })
    signed = signer.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
    return w3.eth.send_raw_transaction(raw).hex()


def setup_wallet(signer: LocalAccount) -> tuple[float, list[dict[str, Any]]]:
    """Run the approval flow. Returns (matic_balance, actions[]) where each
    action is {contract, spender, status: 'approved'|'already', tx: '0x...'|None}."""
    try:
        w3 = _w3()
        addr = signer.address
        matic_wei = w3.eth.get_balance(addr)
    except Exception as e:  # noqa: BLE001
        raise SetupError(f"RPC unavailable: {e}") from e

    matic = matic_wei / 10**18
    if matic < MIN_MATIC_FOR_APPROVALS:
        raise SetupError(
            f"insufficient MATIC for gas: {matic:.4f} < {MIN_MATIC_FOR_APPROVALS} required. "
            f"Send a small amount of MATIC to {addr} and retry."
        )

    collateral = w3.eth.contract(address=COLLATERAL, abi=ERC20_APPROVE_ABI)
    ctf = w3.eth.contract(address=CTF_ADDRESS, abi=CTF_ABI)

    actions: list[dict[str, Any]] = []
    nonce = w3.eth.get_transaction_count(addr)

    for label, spender in EXCHANGES:
        # pUSD allowance check + approve
        current_allowance = collateral.functions.allowance(addr, spender).call()
        if current_allowance >= APPROVE_THRESHOLD:
            actions.append({"contract": f"pUSD->{label}", "spender": spender, "status": "already", "tx": None})
        else:
            tx = _send(w3, signer, collateral, "approve", (spender, MAX_UINT256), gas_limit=120_000, nonce=nonce)
            nonce += 1
            actions.append({"contract": f"pUSD->{label}", "spender": spender, "status": "approved", "tx": tx})
            log.info("pUSD approve %s sent: %s", label, tx)

        # CTF setApprovalForAll check + approve
        if ctf.functions.isApprovedForAll(addr, spender).call():
            actions.append({"contract": f"CTF->{label}", "spender": spender, "status": "already", "tx": None})
        else:
            tx = _send(w3, signer, ctf, "setApprovalForAll", (spender, True), gas_limit=120_000, nonce=nonce)
            nonce += 1
            actions.append({"contract": f"CTF->{label}", "spender": spender, "status": "approved", "tx": tx})
            log.info("CTF setApprovalForAll %s sent: %s", label, tx)

    return matic, actions
