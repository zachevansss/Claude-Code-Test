from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.analytics.engine import AnalyticsEngine
from src.api.schemas import StatsOut, TradeOut
from src.auth.deps import get_current_user
from src.database.session import get_db
from src.models import Trade, User

router = APIRouter()


@router.get("/trades", response_model=list[TradeOut])
def list_trades(
    limit: int = Query(default=100, ge=1, le=1000),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[TradeOut]:
    return (
        db.query(Trade)
        .filter(Trade.user_id == user.id)
        .order_by(Trade.created_at.desc())
        .limit(limit)
        .all()
    )


@router.get("/stats", response_model=StatsOut)
def stats(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> StatsOut:
    return AnalyticsEngine(db).compute(user.id)
