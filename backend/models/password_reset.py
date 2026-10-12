"""
PasswordResetToken model – short-lived (15-minute) single-use tokens
issued during the forgot-password flow.

Design decisions:
- Tokens are stored as SHA-256 hashes to prevent exposure if the DB is compromised.
- `used_at` is set on first use; subsequent uses are rejected.
- `expires_at` is checked on every use.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import String, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base

# Token validity window (minutes)
RESET_TOKEN_EXPIRE_MINUTES: int = 15


class PasswordResetToken(Base):
    """Single-use, short-lived password reset token."""

    __tablename__ = "password_reset_tokens"

    # Foreign key to users.id; we store it as a plain string so a deleted user
    # doesn't cascade-delete audit history.
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) > self.expires_at

    @property
    def is_used(self) -> bool:
        return self.used_at is not None

    @property
    def is_valid(self) -> bool:
        return not self.is_expired and not self.is_used