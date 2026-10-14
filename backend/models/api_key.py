from typing import Optional, Tuple
from datetime import datetime, timezone
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Boolean, ForeignKey, DateTime
import hashlib
import secrets

from backend.models.base import Base


class ApiKey(Base):
    """
    High-entropy cryptographic API Key for Developer CLI, VS Code Extension, and CI/CD.
    Raw keys are prefixed with 'kb_live_' and NEVER stored in plaintext.
    Only the SHA-256 hash is persisted in the database.
    """
    __tablename__ = "api_keys"

    name: Mapped[str] = mapped_column(String(128), default="Default Key")
    key_prefix: Mapped[str] = mapped_column(String(16), index=True)  # e.g. "kb_live_a1b2"
    hashed_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # SHA-256 hex digest
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    scopes: Mapped[str] = mapped_column(String(256), default="read,write,cli,extension")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    @classmethod
    def generate_key(cls, prefix: str = "kb_live_") -> Tuple[str, str, str]:
        """
        Generate a cryptographically secure API key with high entropy.
        Returns:
            (raw_key, key_prefix, hashed_key)
        The raw_key must be presented to the user ONCE and never retained in plaintext.
        """
        random_entropy = secrets.token_hex(24)  # 192 bits of entropy
        raw_key = f"{prefix}{random_entropy}"
        key_prefix = raw_key[:12]  # Identifiable prefix for audit logs and UI display
        hashed_key = cls.hash_key(raw_key)
        return raw_key, key_prefix, hashed_key

    @classmethod
    def hash_key(cls, raw_key: str) -> str:
        """Hash a raw API key using SHA-256."""
        return hashlib.sha256(raw_key.strip().encode("utf-8")).hexdigest()
