"""Managed-wallet read-only endpoint. Returns the user's deposit address and
on-chain balances (USDC + native MATIC) on Polygon."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from web3 import Web3

from src.api.schemas import ManagedWalletOut
from src.auth.deps import get_current_user
from src.config.settings import settings
from src.database.session import get_db
from src.models import ManagedWallet, User
from src.utils.logging import get_logger
from src.wallet.manager import WalletManager

router = APIRouter()
log = get_logger("WALLET")

# USDC on Polygon (PoS) — used by Polymarket for collateral.
_POLYGON_USDC = Web3.to_checksum_address("0x2791bca1f2de4661ed88a30c99a7a9449aa84174")
_USDC_DECIMALS = 6
# Minimal ABI: balanceOf only.
_ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    }
]


def _lookup_balances(address: str) -> tuple[float | None, float | None, str | None]:
    """Return (usdc, matic, error). RPC failures don't raise — they're surfaced
    in the response so the UI can still show the deposit address."""
    try:
        w3 = Web3(Web3.HTTPProvider(settings.polygon_rpc_url, request_kwargs={"timeout": 8}))
        addr = Web3.to_checksum_address(address)
        matic_wei = w3.eth.get_balance(addr)
        usdc = w3.eth.contract(address=_POLYGON_USDC, abi=_ERC20_ABI)
        usdc_raw = usdc.functions.balanceOf(addr).call()
        return (
            usdc_raw / 10**_USDC_DECIMALS,
            matic_wei / 10**18,
            None,
        )
    except Exception as e:  # noqa: BLE001 — best-effort balance display
        log.warning("balance lookup failed for %s: %s", address, e)
        return None, None, f"RPC lookup failed: {e}"


@router.get("", response_model=ManagedWalletOut)
def get_wallet(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> ManagedWalletOut:
    wallet = (
        db.query(ManagedWallet).filter(ManagedWallet.user_id == user.id).first()
    )
    if not wallet:
        # Should only happen for users created before this feature shipped.
        wallet = WalletManager.get_or_create(user.id, db)

    usdc, matic, err = _lookup_balances(wallet.address)
    return ManagedWalletOut(
        address=wallet.address,
        usdc_balance=usdc,
        matic_balance=matic,
        balance_error=err,
    )
