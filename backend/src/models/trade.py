from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.base import Base


class Trade(Base):
    """Every fill (paper or live). One row per filled order."""
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    source_wallet: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    market_id: Mapped[str] = mapped_column(String(128), nullable=False)
    asset_id: Mapped[str | None] = mapped_column(String(128), nullable=True)  # ERC-1155 token id
    outcome: Mapped[str] = mapped_column(String(64), nullable=False)
    # Human-readable market question / title from source activity row.
    # e.g. "Will Fukushima United FC win on 2026-05-03?"
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    side: Mapped[str] = mapped_column(String(8), nullable=False)  # "buy" | "sell"
    price: Mapped[float] = mapped_column(Float, nullable=False)
    size: Mapped[float] = mapped_column(Float, nullable=False)
    notional_usd: Mapped[float] = mapped_column(Float, nullable=False)

    mode: Mapped[str] = mapped_column(String(16), nullable=False)  # "paper" | "live"
    # status values: "submitted" | "filled" | "partial" | "cancelled" | "expired"
    # Paper trades skip "submitted" and go straight to "filled".
    status: Mapped[str] = mapped_column(String(32), default="filled", nullable=False)
    external_tx: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Live-only: CLOB order id returned by Polymarket on submission. Used to
    # poll order status and reconcile fill_price/filled_size against the limit.
    clob_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    filled_size: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False, index=True
    )

    user: Mapped["User"] = relationship(back_populates="trades")
