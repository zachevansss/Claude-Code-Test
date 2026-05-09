"""Managed-wallet endpoints.

GET / — read-only: return address + on-chain balances.
POST /setup — one-time on-chain approvals so the wallet can trade on Polymarket.
              Idempotent: re-running after success is a no-op (no gas spent)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.api.schemas import (
    ApprovalAction,
    ManagedWalletOut,
    WalletImportRequest,
    WalletSetupOut,
)
from src.auth.deps import get_current_user
from src.database.session import get_db
from src.models import ManagedWallet, Trade, User
from src.utils.logging import get_logger
from src.wallet.approvals import SetupError, setup_wallet
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
        wallet = WalletManager.get_or_create(user.id, db)

    # For Magic Link users, the proxy holds the funds — read balances there.
    # Self-funded EOA users have proxy_address NULL, fall through to EOA.
    balance_addr = wallet.proxy_address or wallet.address
    usdc, matic, err = get_balances(balance_addr)
    return ManagedWalletOut(
        address=wallet.address,
        proxy_address=wallet.proxy_address,
        usdc_balance=usdc,
        matic_balance=matic,
        balance_error=err,
    )


@router.post("/import", response_model=ManagedWalletOut)
def import_wallet(
    req: WalletImportRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ManagedWalletOut:
    """Replace the auto-generated managed wallet with one from a user-supplied
    private key. Use case: reuse an existing funded Polymarket EOA instead of
    moving funds to a fresh address.

    Refuses if any live trades already exist for this user — overwriting the
    wallet would orphan their on-chain positions from the platform's view of
    them. Burn down to zero live exposure before importing if you need to."""
    if req.replace_existing:
        live_trade_count = (
            db.query(Trade)
            .filter(Trade.user_id == user.id, Trade.mode == "live")
            .count()
        )
        if live_trade_count > 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"user has {live_trade_count} live trade(s) — refusing to "
                    "overwrite managed wallet. Close live positions first or "
                    "delete the trade history if this is dev-only."
                ),
            )

    try:
        wallet = WalletManager.import_for_user(
            user.id,
            req.private_key,
            db,
            replace_existing=req.replace_existing,
            proxy_address=req.proxy_address,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    db.refresh(wallet)

    balance_addr = wallet.proxy_address or wallet.address
    usdc, matic, err = get_balances(balance_addr)
    return ManagedWalletOut(
        address=wallet.address,
        proxy_address=wallet.proxy_address,
        usdc_balance=usdc,
        matic_balance=matic,
        balance_error=err,
    )


@router.post("/setup", response_model=WalletSetupOut)
def setup(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> WalletSetupOut:
    wallet = (
        db.query(ManagedWallet).filter(ManagedWallet.user_id == user.id).first()
    )
    if not wallet:
        raise HTTPException(status_code=404, detail="No managed wallet for user")

    # Magic Link / proxy-wallet users have approvals already set up by
    # Polymarket itself when they created the account. The EOA we hold doesn't
    # own anything to approve, and would just spend MATIC for no-op txs.
    if wallet.proxy_address:
        return WalletSetupOut(
            address=wallet.proxy_address,
            matic_balance=0.0,
            actions=[],
        )

    signer = WalletManager.get_signer(wallet)
    try:
        matic, actions = setup_wallet(signer)
    except SetupError as e:
        # Surface as 400 — actionable user error (insufficient gas, etc).
        raise HTTPException(status_code=400, detail=str(e))

    log.info(
        "setup complete user=%s actions=%s",
        user.id,
        ",".join(f"{a['contract']}:{a['status']}" for a in actions),
    )
    return WalletSetupOut(
        address=wallet.address,
        matic_balance=matic,
        actions=[ApprovalAction(**a) for a in actions],
    )
