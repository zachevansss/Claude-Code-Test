from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.api.schemas import BotStatusOut
from src.auth.deps import get_current_user
from src.bot_manager.manager import bot_manager
from src.database.session import get_db
from src.models import BotInstance, User
from src.utils.logging import get_logger

router = APIRouter()
log = get_logger("API")


def _get_or_create_instance(db: Session, user_id: int) -> BotInstance:
    inst = db.query(BotInstance).filter(BotInstance.user_id == user_id).first()
    if not inst:
        inst = BotInstance(user_id=user_id)
        db.add(inst)
        db.commit()
        db.refresh(inst)
    return inst


@router.post("/start", response_model=BotStatusOut)
async def start(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> BotStatusOut:
    inst = _get_or_create_instance(db, user.id)
    await bot_manager.start(user.id)
    inst.status = "running"
    inst.last_started_at = datetime.utcnow()
    inst.last_error = None
    db.commit()
    db.refresh(inst)
    return inst


@router.post("/stop", response_model=BotStatusOut)
async def stop(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> BotStatusOut:
    inst = _get_or_create_instance(db, user.id)
    await bot_manager.stop(user.id)
    inst.status = "stopped"
    db.commit()
    db.refresh(inst)
    return inst


@router.get("/status", response_model=BotStatusOut)
def status(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> BotStatusOut:
    return _get_or_create_instance(db, user.id)
