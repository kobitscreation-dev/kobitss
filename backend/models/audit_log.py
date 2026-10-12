"""
AuditLog model – records security-sensitive operations for compliance and debugging.

Every write to this table is append-only (no updates, no deletes).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import String, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class AuditEvent(str):
    """String constants for audit event types."""
    # Authentication
    USER_LOGIN = "user.login"
    USER_LOGIN_FAILED = "user.login_failed"
    USER_LOGOUT = "user.logout"
    USER_REGISTER = "user.register"
    # Profile / Account
    PROFILE_UPDATE = "profile.update"
    PASSWORD_CHANGE = "profile.password_change"
    PASSWORD_RESET_REQUEST = "profile.password_reset_request"
    PASSWORD_RESET_COMPLETE = "profile.password_reset_complete"
    # Account lifecycle
    ACCOUNT_DEACTIVATE = "account.deactivate"
    ACCOUNT_HARD_DELETE = "account.hard_delete"
    # OAuth
    OAUTH_LINK = "oauth.link"
    OAUTH_UNLINK = "oauth.unlink"
    OAUTH_LOGIN = "oauth.login"


class AuditLog(Base):
    """
    Immutable audit trail entry.

    Columns
    -------
    user_id     – subject of the action (may be None for pre-auth events such as failed logins)
    event       – dotted event name constant (see AuditEvent above)
    ip_address  – client IP extracted from X-Forwarded-For or request.client.host
    user_agent  – raw User-Agent header (first 512 chars)
    detail      – optional free-form JSON or human-readable description
    occurred_at – explicit UTC timestamp (separate from created_at for clarity)
    """

    __tablename__ = "audit_logs"

    user_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    event: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )