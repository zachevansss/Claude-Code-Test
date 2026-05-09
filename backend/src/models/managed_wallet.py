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

    # Polymarket "Magic Link / proxy wallet" support. For email-signup users,
    # the EOA at `address` signs orders but funds live at this separate
    # smart-contract wallet. When set, the executor passes signature_type=1
    # (POLY_PROXY) plus funder=<this address> to py-clob-client, and balance
    # lookups read this address instead of the EOA. NULL = self-funded EOA.
    proxy_address: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    user: Mapped["User"] = relationship(back_populates="managed_wallet")
