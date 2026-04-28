"""Managed-wallet endpoints. GET returns address + on-chain balances."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.api.schemas import ManagedWalletOut
from src.auth.deps import get_current_user
from src.database.session import get_db
from src.models import ManagedWallet, User
from src.utils.logging import get_logger
from src.wallet.balances import get_balances
from src.wallet.manager import WalletManager

router = APIRouter()
log = get_logger("WALLET")


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

    usdc, matic, err = get_balances(wallet.address)
    return ManagedWalletOut(
        address=wallet.address,
        usdc_balance=usdc,
        matic_balance=matic,
        balance_error=err,
    )
