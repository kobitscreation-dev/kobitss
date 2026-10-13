"""
Profile Audit Service
=====================
Provides structured, append-only audit-log writes for every security-sensitive
profile operation.  Each write is added to the **same AsyncSession** as the
originating transaction so that audit records are committed atomically with
the mutation — or rolled back if the mutation fails.

Usage
-----
    from backend.services.profile_audit import ProfileAuditService

    audit = ProfileAuditService(db, request)
    await audit.record_profile_update(user_id, changed_fields)
    await db.commit()
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.audit_log import AuditEvent, AuditLog

logger = logging.getLogger(__name__)


def _extract_client_ip(request: Request) -> str:
    """Extract the real client IP, respecting standard reverse-proxy headers."""
    # X-Forwarded-For may contain a comma-separated list; take the first entry
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    cf_ip = request.headers.get("cf-connecting-ip", "")
    if cf_ip:
        return cf_ip.strip()
    if request.client:
        return request.client.host
    return "unknown"


def _extract_user_agent(request: Request) -> str:
    """Extract and truncate User-Agent header to the DB column limit (512 chars)."""
    ua = request.headers.get("user-agent", "")
    return ua[:512]


class ProfileAuditService:
    """
    Stateless service that creates AuditLog entries for profile operations.

    Parameters
    ----------
    db : AsyncSession
        The active SQLAlchemy async session.  Entries are added via ``db.add``
        but NOT committed — the caller is responsible for ``await db.commit()``.
    request : Request
        The originating FastAPI request (used for IP + UA extraction).
    """

    def __init__(self, db: AsyncSession, request: Request) -> None:
        self._db = db
        self._ip = _extract_client_ip(request)
        self._ua = _extract_user_agent(request)

    # ─────────────────────────────────────────────────────────────────────────
    #  Private helper
    # ─────────────────────────────────────────────────────────────────────────

    def _add_entry(
        self,
        user_id: str,
        event: str,
        detail: Optional[Dict[str, Any]] = None,
    ) -> AuditLog:
        """Create and stage an AuditLog row (not yet committed)."""
        entry = AuditLog(
            user_id=user_id,
            event=event,
            ip_address=self._ip,
            user_agent=self._ua,
            detail=json.dumps(detail, default=str) if detail else None,
        )
        self._db.add(entry)
        logger.debug(
            "audit_staged",
            extra={"event": event, "user_id": user_id, "ip": self._ip},
        )
        return entry

    # ─────────────────────────────────────────────────────────────────────────
    #  Public API
    # ─────────────────────────────────────────────────────────────────────────

    def record_profile_update(
        self,
        user_id: str,
        changed_fields: List[str],
    ) -> AuditLog:
        """
        Stage a PROFILE_UPDATE audit entry.

        Parameters
        ----------
        user_id : str
            The UUID of the user whose profile was updated.
        changed_fields : list[str]
            Names of the fields that were mutated (never include raw values —
            only field names to avoid PII in audit log).
        """
        return self._add_entry(
            user_id=user_id,
            event=AuditEvent.PROFILE_UPDATE,
            detail={"changed_fields": changed_fields},
        )

    def record_password_change(self, user_id: str) -> AuditLog:
        """Stage a PASSWORD_CHANGE audit entry."""
        return self._add_entry(
            user_id=user_id,
            event=AuditEvent.PASSWORD_CHANGE,
            detail={"method": "inline_update"},
        )

    def record_dedicated_password_change(self, user_id: str) -> AuditLog:
        """Stage a PASSWORD_CHANGE audit entry for the /change-password endpoint."""
        return self._add_entry(
            user_id=user_id,
            event=AuditEvent.PASSWORD_CHANGE,
            detail={"method": "change_password_resource"},
        )

    def record_account_deactivate(self, user_id: str) -> AuditLog:
        """Stage an ACCOUNT_DEACTIVATE audit entry."""
        return self._add_entry(
            user_id=user_id,
            event=AuditEvent.ACCOUNT_DEACTIVATE,
            detail={"reason": "user_requested_delete"},
        )