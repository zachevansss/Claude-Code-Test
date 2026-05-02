from sqlalchemy import Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.base import Base


class UserSettings(Base):
    """Per-user runtime settings: mode, sizing strategy, risk caps, paper balance."""
    __tablename__ = "user_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)

    mode: Mapped[str] = mapped_column(String(16), default="paper", nullable=False)

    # Sizing strategy: "percent" | "fixed" | "mirror"
    #   percent — sizing_percent of (balance - in_flight_notional) per trade
    #   fixed   — sizing_fixed_usd per trade
    #   mirror  — source_notional_usd * mirror_scale per trade; skipped if < min_trade_usd
    sizing_strategy: Mapped[str] = mapped_column(String(16), default="percent", nullable=False)
    sizing_percent: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    sizing_fixed_usd: Mapped[float] = mapped_column(Float, default=10.0, nullable=False)
    # Mirror-strategy params. mirror_scale is the multiplier on source's USD
    # notional (1.0 = match dollar-for-dollar). min_trade_usd is the floor
    # below which signals are skipped rather than boosted.
    mirror_scale: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    min_trade_usd: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    # Risk caps
    max_percent_per_trade: Mapped[float] = mapped_column(Float, default=5.0, nullable=False)
    # Per-market exposure cap as a % of current balance, so the cap rises and
    # falls with bankroll. e.g. 10.0 = no single market may hold more than 10%
    # of available capital at any time.
    max_exposure_per_market_pct: Mapped[float] = mapped_column(Float, default=10.0, nullable=False)
    # Daily loss cap as a % of current balance. e.g. 10.0 = halt the bot for
    # the day once realized losses exceed 10% of available capital. Scales
    # with the bankroll, so the dollar threshold rises as you win and drops
    # as you lose (extra-conservative behavior on the way down).
    daily_loss_cap_pct: Mapped[float] = mapped_column(Float, default=10.0, nullable=False)
    # Legacy fixed-USD daily cap. Kept for backwards-compat in DB rows; risk
    # manager uses daily_loss_cap_pct instead. Will be removed once all
    # deployments are migrated.
    daily_loss_cap_usd: Mapped[float] = mapped_column(Float, default=50.0, nullable=False)

    # Execution
    slippage_tolerance_pct: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    # Paper-mode starting balance
    paper_balance_usd: Mapped[float] = mapped_column(Float, default=1000.0, nullable=False)

    user: Mapped["User"] = relationship(back_populates="settings")
