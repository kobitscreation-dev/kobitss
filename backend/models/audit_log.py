"""
AuditLog model – SOC2 Type II Tamper-Evident Cryptographic Hash-Chained Audit Trail.

Every write to this table is append-only (no updates, no deletes) and cryptographically
sealed with:
1. `prev_hash`       – SHA-256 `entry_hash` of the preceding log entry in the chain
                       (or `GENESIS_HASH` = 64 zeros for sequence_number == 1).
2. `entry_hash`      – SHA-256 digest over `(prev_hash + canonical_json(payload))`.
3. `hmac_signature`  – HMAC-SHA256 signature over `(entry_hash + canonical_json(payload))`
                       using `settings.AUDIT_HMAC_SECRET` (or `settings.SECRET_KEY`).
4. `code_change_hash` / `content_hash` – SHA-256 digest of file reads and code changes.
"""
from __future__ import annotations

import enum
import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from sqlalchemy import DateTime, Integer, String, Text, event, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.config import settings
from backend.models.base import Base

GENESIS_HASH: str = "0" * 64


class AuditCategory(str, enum.Enum):
    """High-level SOC2 Type II Trust Services Criteria categories."""
    AUTH = "AUTH"
    RBAC = "RBAC"
    FILE_ACCESS = "FILE_ACCESS"
    COMMAND_EXECUTION = "COMMAND_EXECUTION"
    NETWORK_ACCESS = "NETWORK_ACCESS"
    CODE_CHANGE = "CODE_CHANGE"
    COMPLIANCE = "COMPLIANCE"
    SYSTEM = "SYSTEM"


class AuditSeverity(str, enum.Enum):
    """Severity levels for audit events."""
    INFO = "INFO"
    WARNING = "WARNING"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AuditEvent(str):
    """String constants for security and SOC2 Type II operational audit events."""
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
    # SOC2 Type II Operational Telemetry
    FILE_READ = "file.read"
    COMMAND_EXECUTE = "command.execute"
    COMMAND_BLOCKED = "command.blocked"
    IP_ACCESS = "ip.access"
    CODE_CHANGE = "code.change"
    CODE_WRITE = "code.write"
    CODE_EDIT = "code.edit"
    CODE_DELETE = "code.delete"
    # Enterprise RBAC
    RBAC_ROLE_ASSIGNED = "rbac.role_assigned"
    RBAC_ROLE_UPDATED = "rbac.role_updated"
    RBAC_MEMBER_ADDED = "rbac.member_added"
    RBAC_MEMBER_REMOVED = "rbac.member_removed"
    RBAC_ACCESS_DENIED = "rbac.access_denied"
    RBAC_POLICY_EVALUATED = "rbac.policy_evaluated"
    # Compliance & Audit Verification
    AUDIT_CHAIN_VERIFIED = "audit.chain_verified"
    AUDIT_TAMPER_DETECTED = "audit.tamper_detected"
    AUDIT_EXPORTED = "audit.exported"


def _get_hmac_secret(secret_key: Optional[str] = None) -> bytes:
    """Resolve the HMAC signing key bytes from explicit override, environment, or settings."""
    raw = (
        secret_key
        or os.environ.get("KOBITS_AUDIT_HMAC_SECRET")
        or os.environ.get("AUDIT_HMAC_SECRET")
        or getattr(settings, "KOBITS_AUDIT_HMAC_SECRET", None)
        or settings.AUDIT_HMAC_SECRET
        or settings.SECRET_KEY
    )
    return raw.encode("utf-8")


def _get_verification_hmac_secrets(
    secret_key: Optional[str] = None,
    previous_secret_keys: Optional[Sequence[str]] = None,
) -> List[bytes]:
    """
    Resolve candidate HMAC keys for signature verification (active key first,
    followed by any rotated keys when `secret_key` is not explicitly pinned).
    """
    candidates: List[bytes] = [_get_hmac_secret(secret_key)]
    if previous_secret_keys:
        for k in previous_secret_keys:
            if k and k.strip():
                b = k.strip().encode("utf-8")
                if b not in candidates:
                    candidates.append(b)
    if secret_key is None:
        raw_prev = (
            os.environ.get("KOBITS_AUDIT_HMAC_PREVIOUS_SECRETS")
            or os.environ.get("AUDIT_HMAC_PREVIOUS_SECRETS")
            or getattr(settings, "AUDIT_HMAC_PREVIOUS_SECRETS", None)
            or ""
        )
        for part in raw_prev.split(","):
            cleaned = part.strip()
            if cleaned:
                b = cleaned.encode("utf-8")
                if b not in candidates:
                    candidates.append(b)
    return candidates


def compute_sha256_hex(data: Union[str, bytes, None]) -> str:
    """Compute a deterministic lowercase hex SHA-256 digest."""
    if data is None:
        data = b""
    elif isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def compute_code_change_hash(
    file_path: str,
    before_content: Optional[str] = None,
    after_content: Optional[str] = None,
    operation: str = "modify",
) -> str:
    """
    Compute a cryptographic SHA-256 digest signing a code modification.
    Binds file_path, operation, SHA-256(before_content), and SHA-256(after_content).
    """
    envelope = {
        "file_path": (file_path or "").replace("\\", "/").lstrip("/"),
        "operation": operation,
        "before_sha256": compute_sha256_hex(before_content or ""),
        "after_sha256": compute_sha256_hex(after_content or ""),
    }
    canonical = json.dumps(envelope, sort_keys=True, separators=(",", ":"))
    return compute_sha256_hex(canonical)


def normalize_audit_timestamp(dt: Optional[Union[datetime, str]]) -> str:
    """
    Normalize a datetime to an ISO-8601 UTC string with microsecond precision.
    Ensures deterministic hashing across SQLite (which strips tzinfo) and PostgreSQL.
    """
    if dt is None:
        return ""
    if isinstance(dt, str):
        cleaned = dt.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(cleaned)
        except ValueError:
            return dt
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def infer_audit_category(event_name: str) -> str:
    """Infer default SOC2 category from dotted event name."""
    ev = (event_name or "").lower()
    if ev.startswith("file."):
        return AuditCategory.FILE_ACCESS.value
    if ev.startswith("command."):
        return AuditCategory.COMMAND_EXECUTION.value
    if ev.startswith("ip.") or ev.startswith("network."):
        return AuditCategory.NETWORK_ACCESS.value
    if ev.startswith("code."):
        return AuditCategory.CODE_CHANGE.value
    if ev.startswith("rbac."):
        return AuditCategory.RBAC.value
    if ev.startswith("audit."):
        return AuditCategory.COMPLIANCE.value
    return AuditCategory.AUTH.value


class AuditLog(Base):
    """
    Immutable, tamper-evident SOC2 Type II audit trail entry.

    Every entry records:
    - Who performed the action (`user_id`, `organization_id`, `actor_role`)
    - What happened (`event`, `category`, `severity`, `status`, `detail`)
    - Operational telemetry (`file_path`, `command_executed`, `ip_address`, `code_change_hash`, `content_hash`)
    - Cryptographic hash chain (`sequence_number`, `prev_hash`, `entry_hash`, `hmac_signature`)
    """

    __tablename__ = "audit_logs"

    user_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    organization_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    actor_role: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    session_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    mission_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    project_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)

    event: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(64), default=AuditCategory.AUTH.value, nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(32), default=AuditSeverity.INFO.value, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="SUCCESS", nullable=False)

    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    resource_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    file_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True, index=True)
    command_executed: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    code_change_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    sequence_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False, index=True)
    prev_hash: Mapped[str] = mapped_column(String(64), default=GENESIS_HASH, nullable=False)
    entry_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    hmac_signature: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __init__(self, **kwargs: Any) -> None:
        explicit_seq = "sequence_number" in kwargs and kwargs["sequence_number"] is not None
        explicit_prev = "prev_hash" in kwargs and kwargs["prev_hash"] is not None
        explicit_entry_hash = "entry_hash" in kwargs and bool(kwargs["entry_hash"])
        explicit_hmac = "hmac_signature" in kwargs and bool(kwargs["hmac_signature"])

        if not kwargs.get("id"):
            kwargs["id"] = str(uuid.uuid4())
        if not kwargs.get("occurred_at"):
            kwargs["occurred_at"] = datetime.now(timezone.utc)

        event_val = str(kwargs.get("event") or "")
        if not kwargs.get("category"):
            kwargs["category"] = infer_audit_category(event_val)
        if not kwargs.get("severity"):
            if "denied" in event_val or "blocked" in event_val or "tamper" in event_val:
                kwargs["severity"] = AuditSeverity.HIGH.value
            elif "failed" in event_val:
                kwargs["severity"] = AuditSeverity.WARNING.value
            else:
                kwargs["severity"] = AuditSeverity.INFO.value
        if not kwargs.get("status"):
            if "denied" in event_val or "blocked" in event_val:
                kwargs["status"] = "DENIED"
            elif "failed" in event_val:
                kwargs["status"] = "FAILURE"
            else:
                kwargs["status"] = "SUCCESS"

        if kwargs.get("sequence_number") is None:
            kwargs["sequence_number"] = 1
        if not kwargs.get("prev_hash"):
            kwargs["prev_hash"] = GENESIS_HASH

        super().__init__(**kwargs)

        self._explicit_chain: bool = explicit_seq and explicit_prev
        self._explicit_entry_hash: bool = explicit_entry_hash
        self._explicit_hmac: bool = explicit_hmac

        if not explicit_entry_hash:
            self.entry_hash = self.compute_entry_hash()
        if not explicit_hmac:
            self.hmac_signature = self.compute_hmac_signature()

    def canonical_payload(self) -> Dict[str, Any]:
        """
        Return the deterministic dictionary representation of the audit entry
        used for SHA-256 hash chaining and HMAC-SHA256 signing.
        """
        return {
            "id": str(self.id or ""),
            "sequence_number": int(self.sequence_number or 1),
            "prev_hash": str(self.prev_hash or GENESIS_HASH),
            "organization_id": self.organization_id,
            "user_id": self.user_id,
            "actor_role": self.actor_role,
            "session_id": self.session_id,
            "mission_id": self.mission_id,
            "project_id": self.project_id,
            "event": str(self.event or ""),
            "category": str(self.category or AuditCategory.AUTH.value),
            "severity": str(self.severity or AuditSeverity.INFO.value),
            "status": str(self.status or "SUCCESS"),
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "file_path": self.file_path,
            "command_executed": self.command_executed,
            "code_change_hash": self.code_change_hash,
            "content_hash": self.content_hash,
            "detail": self.detail,
            "occurred_at": normalize_audit_timestamp(self.occurred_at),
        }

    def canonical_json(self) -> str:
        """Serialize canonical_payload() into deterministic sorted-key compact JSON."""
        return json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"))

    def compute_entry_hash(self) -> str:
        """Compute the SHA-256 chain hash binding `prev_hash` and `canonical_json()`."""
        prev = str(self.prev_hash or GENESIS_HASH)
        material = f"{prev}:{self.canonical_json()}"
        return compute_sha256_hex(material)

    def compute_hmac_signature(self, secret_key: Optional[str] = None) -> str:
        """Compute the HMAC-SHA256 signature over `entry_hash` and `canonical_json()`."""
        key_bytes = _get_hmac_secret(secret_key)
        current_entry_hash = self.entry_hash or self.compute_entry_hash()
        msg = f"{current_entry_hash}:{self.canonical_json()}".encode("utf-8")
        return hmac.new(key_bytes, msg, hashlib.sha256).hexdigest()

    def seal(
        self,
        prev_hash: Optional[str] = None,
        sequence_number: Optional[int] = None,
        secret_key: Optional[str] = None,
    ) -> "AuditLog":
        """Seal the entry with sequence_number, prev_hash, entry_hash, and hmac_signature."""
        if sequence_number is not None:
            self.sequence_number = int(sequence_number)
        if prev_hash is not None:
            self.prev_hash = str(prev_hash)
        if not self.prev_hash:
            self.prev_hash = GENESIS_HASH
        if not self.sequence_number:
            self.sequence_number = 1
        self.entry_hash = self.compute_entry_hash()
        self.hmac_signature = self.compute_hmac_signature(secret_key=secret_key)
        self._explicit_chain = True
        self._explicit_entry_hash = True
        self._explicit_hmac = True
        return self

    def verify_signature(
        self,
        secret_key: Optional[str] = None,
        previous_secret_keys: Optional[Sequence[str]] = None,
    ) -> bool:
        """
        Verify this entry's HMAC-SHA256 signature against the active signing key
        (or any configured rotated historical keys in `previous_secret_keys` / `AUDIT_HMAC_PREVIOUS_SECRETS`).
        """
        stored_sig = str(self.hmac_signature or "")
        if not stored_sig:
            return False
        current_entry_hash = self.entry_hash or self.compute_entry_hash()
        msg = f"{current_entry_hash}:{self.canonical_json()}".encode("utf-8")
        for candidate_key in _get_verification_hmac_secrets(secret_key, previous_secret_keys):
            expected_sig = hmac.new(candidate_key, msg, hashlib.sha256).hexdigest()
            if hmac.compare_digest(stored_sig, expected_sig):
                return True
        return False

    def verify_integrity(
        self,
        expected_prev_hash: Optional[str] = None,
        expected_sequence_number: Optional[int] = None,
        secret_key: Optional[str] = None,
        previous_secret_keys: Optional[Sequence[str]] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Verify this entry's cryptographic integrity:
        1. Sequence continuity (if `expected_sequence_number` supplied)
        2. Chain link continuity (`prev_hash == expected_prev_hash` if supplied)
        3. Content integrity (`entry_hash == compute_entry_hash()`)
        4. Authenticity (`verify_signature()`)
        """
        if expected_sequence_number is not None and int(self.sequence_number) != int(expected_sequence_number):
            return (
                False,
                f"Sequence number mismatch at entry {self.id}: expected {expected_sequence_number}, got {self.sequence_number}",
            )

        if expected_prev_hash is not None and not hmac.compare_digest(
            str(self.prev_hash or ""), str(expected_prev_hash)
        ):
            return (
                False,
                f"Broken hash chain link at entry {self.id} (seq {self.sequence_number}): "
                f"prev_hash {self.prev_hash!r} != expected {expected_prev_hash!r}",
            )

        recomputed_hash = self.compute_entry_hash()
        if not hmac.compare_digest(str(self.entry_hash or ""), recomputed_hash):
            return (
                False,
                f"Entry hash mismatch (payload tampered) at entry {self.id} (seq {self.sequence_number}): "
                f"stored {self.entry_hash!r} != computed {recomputed_hash!r}",
            )

        if not self.verify_signature(secret_key=secret_key, previous_secret_keys=previous_secret_keys):
            return (
                False,
                f"HMAC-SHA256 signature verification failed at entry {self.id} (seq {self.sequence_number})",
            )

        return True, None


@event.listens_for(AuditLog, "before_insert")
def _auto_seal_audit_log_before_insert(mapper: Any, connection: Any, target: AuditLog) -> None:
    """
    Automatically link and sign `AuditLog` entries before INSERT when not already
    explicitly chained by `SOC2AuditService` or a test fixture.
    Supports multi-row flush batches (`db.add_all([...])`) via `connection.info["_audit_batch_tails"]`.
    """
    if not target.id:
        target.id = str(uuid.uuid4())
    if not target.occurred_at:
        target.occurred_at = datetime.now(timezone.utc)
    if not target.category:
        target.category = infer_audit_category(target.event)
    if not target.severity:
        target.severity = AuditSeverity.INFO.value
    if not target.status:
        target.status = "SUCCESS"

    explicit_chain = getattr(target, "_explicit_chain", False)
    explicit_hash = getattr(target, "_explicit_entry_hash", False)
    explicit_hmac = getattr(target, "_explicit_hmac", False)

    org_key = target.organization_id or "__global__"
    batch_tails: Dict[str, Tuple[int, str]] = connection.info.setdefault("_audit_batch_tails", {})

    if not explicit_chain:
        try:
            dialect_name = getattr(getattr(connection, "dialect", None), "name", "")
            if dialect_name == "postgresql":
                connection.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:org_key))"),
                    {"org_key": f"kobits_audit:{org_key}"},
                )
            if target.organization_id:
                row = connection.execute(
                    text(
                        "SELECT sequence_number, entry_hash FROM audit_logs "
                        "WHERE organization_id = :org_id "
                        "ORDER BY sequence_number DESC LIMIT 1"
                    ),
                    {"org_id": target.organization_id},
                ).fetchone()
            else:
                row = connection.execute(
                    text(
                        "SELECT sequence_number, entry_hash FROM audit_logs "
                        "WHERE organization_id IS NULL "
                        "ORDER BY sequence_number DESC LIMIT 1"
                    )
                ).fetchone()

            db_seq = int(row[0]) if (row and row[0] is not None and row[1]) else 0
            db_hash = str(row[1]) if (row and row[0] is not None and row[1]) else GENESIS_HASH

            mem_seq, mem_hash = batch_tails.get(org_key, (0, GENESIS_HASH))
            if mem_seq >= db_seq and mem_seq > 0:
                base_seq, base_hash = mem_seq, mem_hash
            elif db_seq > 0:
                base_seq, base_hash = db_seq, db_hash
            else:
                base_seq, base_hash = 0, GENESIS_HASH

            target.sequence_number = base_seq + 1
            target.prev_hash = base_hash
        except Exception:
            if not target.sequence_number:
                target.sequence_number = 1
            if not target.prev_hash:
                target.prev_hash = GENESIS_HASH

    if not explicit_hash or not explicit_chain:
        target.entry_hash = target.compute_entry_hash()
    if not explicit_hmac or not explicit_chain:
        target.hmac_signature = target.compute_hmac_signature()

    batch_tails[org_key] = (int(target.sequence_number or 1), str(target.entry_hash or GENESIS_HASH))


@event.listens_for(AuditLog, "after_insert")
def _clear_audit_batch_tail_after_insert(mapper: Any, connection: Any, target: AuditLog) -> None:
    """Clear in-flush batch tail cache once INSERT SQL has executed on the connection."""
    connection.info.pop("_audit_batch_tails", None)