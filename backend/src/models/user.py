from datetime import datetime

from sqlalchemy import DateTime, String, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    settings: Mapped["UserSettings"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    wallets: Mapped[list["UserWallet"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    managed_wallet: Mapped["ManagedWallet"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    bot_instance: Mapped["BotInstance"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    trades: Mapped[list["Trade"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    positions: Mapped[list["Position"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
