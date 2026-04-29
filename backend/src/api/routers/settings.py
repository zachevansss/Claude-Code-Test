from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.api.schemas import ModeRequest, RiskSettingsRequest, SettingsOut
from src.auth.deps import get_current_user
from src.database.session import get_db
from src.models import User, UserSettings
from src.utils.logging import get_logger

router = APIRouter()
log = get_logger("API")


def _get_settings(db: Session, user_id: int) -> UserSettings:
    s = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
    if not s:
        s = UserSettings(user_id=user_id)
        db.add(s)
        db.commit()
        db.refresh(s)
    return s


@router.get("", response_model=SettingsOut)
def get_settings(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> SettingsOut:
    return _get_settings(db, user.id)


@router.post("/risk", response_model=SettingsOut)
def set_risk(
    req: RiskSettingsRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SettingsOut:
    s = _get_settings(db, user.id)
    if req.sizing_strategy is not None and req.sizing_strategy not in {"percent", "fixed", "mirror"}:
        raise HTTPException(
            status_code=400,
            detail="sizing_strategy must be 'percent', 'fixed', or 'mirror'",
        )
    for field, value in req.model_dump(exclude_unset=True).items():
        setattr(s, field, value)
    db.commit()
    db.refresh(s)
    log.info("user=%s updated risk settings", user.id)
    return s


@router.post("/mode", response_model=SettingsOut)
def set_mode(
    req: ModeRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SettingsOut:
    if req.mode not in {"paper", "live"}:
        raise HTTPException(status_code=400, detail="mode must be 'paper' or 'live'")
    s = _get_settings(db, user.id)
    s.mode = req.mode
    db.commit()
    db.refresh(s)
    log.info("user=%s switched to mode=%s", user.id, req.mode)
    return s
