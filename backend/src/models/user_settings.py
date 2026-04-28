from sqlalchemy import Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.base import Base


class UserSettings(Base):
    """Per-user runtime settings: mode, sizing strategy, risk caps, paper balance."""
    __tablename__ = "user_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)

    mode: Mapped[str] = mapped_column(String(16), default="paper", nullable=False)

    # Sizing strategy
    sizing_strategy: Mapped[str] = mapped_column(String(16), default="percent", nullable=False)
    sizing_percent: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    sizing_fixed_usd: Mapped[float] = mapped_column(Float, default=10.0, nullable=False)

    # Risk caps
    max_percent_per_trade: Mapped[float] = mapped_column(Float, default=5.0, nullable=False)
    max_exposure_per_market_usd: Mapped[float] = mapped_column(Float, default=100.0, nullable=False)
    daily_loss_cap_usd: Mapped[float] = mapped_column(Float, default=50.0, nullable=False)

    # Execution
    slippage_tolerance_pct: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    # Paper-mode starting balance
    paper_balance_usd: Mapped[float] = mapped_column(Float, default=1000.0, nullable=False)

    user: Mapped["User"] = relationship(back_populates="settings")
