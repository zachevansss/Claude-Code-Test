from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.base import Base


class BotInstance(Base):
    """Persisted bot state per user. Used to restart loops on server boot."""
    __tablename__ = "bot_instances"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)

    # "stopped" | "running" | "error"
    status: Mapped[str] = mapped_column(String(16), default="stopped", nullable=False)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Liveness telemetry — written every tick by BotManager so the dashboard
    # (and a future /health endpoint) can detect a silently-stalled tracker.
    # last_tick_at: timestamp of the most recent _tick() entry. If this is
    #   stale by more than ~30s, the tick coroutine has died or is stuck.
    # last_signal_emitted_at: timestamp of the most recent tick that produced
    #   at least one fresh TradeSignal. Stale-for-hours while source wallet
    #   is active = tracker is alive but blind (the failure mode we hit
    #   2026-05-13).
    # last_poll_status: one-line summary of the most recent poll outcome
    #   ("ok: 2 signals", "ok: no new", "wallet 0xabc: ReadTimeout", etc.)
    last_tick_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_signal_emitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_poll_status: Mapped[str | None] = mapped_column(String(256), nullable=True)

    user: Mapped["User"] = relationship(back_populates="bot_instance")
