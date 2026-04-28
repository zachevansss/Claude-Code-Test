"""Aggregate statistics over a user's trades and positions."""
from collections import defaultdict

from sqlalchemy.orm import Session

from src.api.schemas import StatsOut, WalletStat
from src.models import Position, Trade, UserSettings


class AnalyticsEngine:
    def __init__(self, db: Session) -> None:
        self.db = db

    def compute(self, user_id: int) -> StatsOut:
        trades = self.db.query(Trade).filter(Trade.user_id == user_id).all()
        positions = self.db.query(Position).filter(Position.user_id == user_id).all()
        settings = (
            self.db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
        )

        realized = sum(p.realized_pnl_usd for p in positions)
        starting = settings.paper_balance_usd if settings else 0.0
        roi_pct = (realized / starting * 100.0) if starting else 0.0

        wins = sum(1 for p in positions if p.realized_pnl_usd > 0)
        losses = sum(1 for p in positions if p.realized_pnl_usd < 0)
        win_rate = wins / (wins + losses) if (wins + losses) else 0.0

        by_wallet: dict[str, dict] = defaultdict(
            lambda: {"trades": 0, "notional_usd": 0.0}
        )
        for t in trades:
            d = by_wallet[t.source_wallet]
            d["trades"] += 1
            d["notional_usd"] += t.notional_usd

        return StatsOut(
            total_pnl_usd=realized,
            total_trades=len(trades),
            win_rate=win_rate,
            roi_pct=roi_pct,
            by_wallet=[
                WalletStat(wallet=k, trades=v["trades"], notional_usd=v["notional_usd"])
                for k, v in by_wallet.items()
            ],
        )
