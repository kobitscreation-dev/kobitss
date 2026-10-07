from typing import Optional
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Integer, ForeignKey, Enum, DateTime, Boolean
from datetime import datetime, timezone
import enum
import uuid

from backend.models.base import Base

class LedgerTransactionType(str, enum.Enum):
    TOPUP = "TOPUP"
    MISSION_RESERVATION = "MISSION_RESERVATION"
    MISSION_SETTLEMENT = "MISSION_SETTLEMENT"
    MISSION_RELEASE = "MISSION_RELEASE"
    ADJUSTMENT = "ADJUSTMENT"

class LedgerTransaction(Base):
    __tablename__ = "ledger_transactions"

    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    type: Mapped[LedgerTransactionType] = mapped_column(Enum(LedgerTransactionType))
    amount: Mapped[int] = mapped_column(Integer) # Integer credits
    currency: Mapped[str] = mapped_column(String, default="credits")
    reference_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True) # e.g. stripe session ID or mission ID
    status: Mapped[str] = mapped_column(String, default="completed")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    metadata_json: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String, nullable=True, unique=True, index=True)

    organization = relationship("Organization")

class CreditPackage(Base):
    __tablename__ = "credit_packages"

    name: Mapped[str] = mapped_column(String, nullable=False)
    credits: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String, default="usd")
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    stripe_price_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
