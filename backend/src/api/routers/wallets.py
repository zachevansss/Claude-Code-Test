from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.api.schemas import WalletAddRequest, WalletOut
from src.auth.deps import get_current_user
from src.database.session import get_db
from src.models import User, UserWallet
from src.utils.logging import get_logger

router = APIRouter()
log = get_logger("API")


def _normalize(addr: str) -> str:
    a = addr.strip().lower()
    if not (a.startswith("0x") and len(a) == 42):
        raise HTTPException(
            status_code=400,
            detail="Address must be a 0x-prefixed 42-character hex string",
        )
    return a


@router.get("", response_model=list[WalletOut])
def list_wallets(user: User = Depends(get_current_user)) -> list[WalletOut]:
    return user.wallets


@router.post("/add", response_model=WalletOut)
def add_wallet(
    req: WalletAddRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WalletOut:
    addr = _normalize(req.address)
    if (
        db.query(UserWallet)
        .filter(UserWallet.user_id == user.id, UserWallet.address == addr)
        .first()
    ):
        raise HTTPException(status_code=400, detail="Wallet already tracked")
    w = UserWallet(user_id=user.id, address=addr, label=req.label)
    db.add(w)
    db.commit()
    db.refresh(w)
    log.info("user=%s added wallet=%s", user.id, addr)
    return w


@router.post("/remove")
def remove_wallet(
    req: WalletAddRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    addr = _normalize(req.address)
    w = (
        db.query(UserWallet)
        .filter(UserWallet.user_id == user.id, UserWallet.address == addr)
        .first()
    )
    if not w:
        raise HTTPException(status_code=404, detail="Wallet not found")
    db.delete(w)
    db.commit()
    log.info("user=%s removed wallet=%s", user.id, addr)
    return {"removed": addr}
