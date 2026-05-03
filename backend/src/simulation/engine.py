"""Paper-trading engine. Mirrors ExecutionEngine API but never hits the network.
Updates trades + positions tables with mode='paper'."""
from datetime import datetime

from sqlalchemy.orm import Session

from src.models import Position, Trade
from src.risk.manager import SizedOrder
from src.utils.logging import get_logger

log = get_logger("SIMULATION")


class SimulationEngine:
    def __init__(self, db: Session, user_id: int) -> None:
        self.db = db
        self.user_id = user_id

    def execute(self, order: SizedOrder, source_wallet: str) -> Trade:
        trade = Trade(
            user_id=self.user_id,
            source_wallet=source_wallet,
            market_id=order.market_id,
            asset_id=order.asset_id,
            outcome=order.outcome,
            title=order.title,
            side=order.side,
            price=order.price,
            size=order.size,
            notional_usd=order.notional_usd,
            mode="paper",
            status="filled",
            external_tx=order.external_tx,
            created_at=datetime.utcnow(),
        )
        self.db.add(trade)

        pos = (
            self.db.query(Position)
            .filter(
                Position.user_id == self.user_id,
                Position.market_id == order.market_id,
                Position.outcome == order.outcome,
                Position.mode == "paper",
            )
            .first()
        )
        if pos is None:
            # Explicit zero init — SQLAlchemy's Column default=0.0 only fires
            # on flush, but the math below runs pre-flush.
            pos = Position(
                user_id=self.user_id,
                market_id=order.market_id,
                outcome=order.outcome,
                mode="paper",
                size=0.0,
                avg_price=0.0,
                realized_pnl_usd=0.0,
            )
            self.db.add(pos)

        if order.side == "buy":
            new_size = pos.size + order.size
            if new_size > 0:
                pos.avg_price = (
                    pos.avg_price * pos.size + order.price * order.size
                ) / new_size
            pos.size = new_size
        else:  # "sell"
            close_size = min(pos.size, order.size)
            pos.realized_pnl_usd += (order.price - pos.avg_price) * close_size
            pos.size = max(0.0, pos.size - order.size)

        self.db.commit()
        self.db.refresh(trade)
        log.info(
            "paper-fill user=%s %s %s @%.4f size=%.4f notional=%.2f",
            self.user_id, order.side, order.outcome,
            order.price, order.size, order.notional_usd,
        )
        return trade
