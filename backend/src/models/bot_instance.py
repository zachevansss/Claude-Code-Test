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

    user: Mapped["User"] = relationship(back_populates="bot_instance")
