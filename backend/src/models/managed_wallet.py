from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.base import Base


class ManagedWallet(Base):
    """Per-user EOA controlled by the platform. Private key encrypted at rest
    with the master Fernet key from settings. One per user."""
    __tablename__ = "managed_wallets"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)

    address: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    encrypted_private_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    user: Mapped["User"] = relationship(back_populates="managed_wallet")
