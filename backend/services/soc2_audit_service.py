"""
SOC2 Type II Tamper-Evident Audit Service.

Tracks and cryptographically signs every:
- File read (`AuditEvent.FILE_READ`)
- Command executed (`AuditEvent.COMMAND_EXECUTE` / `AuditEvent.COMMAND_BLOCKED`)
- IP accessed (`AuditEvent.IP_ACCESS`)
- Code change (`AuditEvent.CODE_CHANGE` / `CODE_WRITE` / `CODE_EDIT` / `CODE_DELETE`)
- RBAC & authentication event (`AuditEvent.RBAC_*`, `AuditEvent.USER_*`)

Every entry is linked into a per-organization SHA-256 hash chain (`prev_hash` -> `entry_hash`)
and signed with HMAC-SHA256 (`hmac_signature`) so any post-hoc modification, deletion,
insertion, or reordering of rows is deterministically detected by `verify_audit_chain()`.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import threading
import weakref
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from fastapi import Request
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import settings
from backend.models.audit_log import (
    GENESIS_HASH,
    AuditCategory,
    AuditEvent,
    AuditLog,
    AuditSeverity,
    _get_hmac_secret,
    compute_code_change_hash,
    compute_sha256_hex,
    infer_audit_category,
    normalize_audit_timestamp,
)

logger = logging.getLogger(__name__)

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


class SOC2AuditService:
    """
    Enterprise SOC2 Type II tamper-evident audit logging and chain verification service.
    """

    _async_locks: Dict[str, asyncio.Lock] = {}
    _active_writer_sessions: Dict[str, Tuple[int, Any, asyncio.Event]] = {}
    _sync_lock = threading.Lock()
    # Staged entries recorded from synchronous sandbox/tool execution contexts
    _staged_entries: List[Dict[str, Any]] = []
    # In-memory hash chain mirror for synchronous callers (e.g., SandboxManager without DB session)
    _in_memory_chains: Dict[str, List[AuditLog]] = {}

    @classmethod
    def _get_org_lock(cls, organization_id: Optional[str]) -> asyncio.Lock:
        key = organization_id or "__global__"
        if key not in cls._async_locks:
            cls._async_locks[key] = asyncio.Lock()
        return cls._async_locks[key]

    @staticmethod
    def extract_client_ip(request: Optional[Request] = None, fallback_ip: Optional[str] = None) -> Optional[str]:
        """Extract client IP from X-Forwarded-For, X-Real-IP, or request.client.host."""
        if request is not None:
            forwarded_for = request.headers.get("X-Forwarded-For")
            if forwarded_for:
                return forwarded_for.split(",")[0].strip()[:64]
            real_ip = request.headers.get("X-Real-IP")
            if real_ip:
                return real_ip.strip()[:64]
            if request.client and request.client.host:
                return request.client.host[:64]
        return fallback_ip[:64] if fallback_ip else None

    @staticmethod
    def extract_user_agent(request: Optional[Request] = None, fallback_ua: Optional[str] = None) -> Optional[str]:
        """Extract User-Agent header (truncated to 512 chars)."""
        if request is not None:
            ua = request.headers.get("User-Agent")
            if ua:
                return ua[:512]
        return fallback_ua[:512] if fallback_ua else None

    @staticmethod
    def _serialize_detail(detail: Optional[Union[str, Dict[str, Any]]]) -> Optional[str]:
        if detail is None:
            return None
        if isinstance(detail, str):
            return detail
        return json.dumps(detail, sort_keys=True, default=str)

    @classmethod
    async def _acquire_pg_advisory_lock(
        cls,
        db: AsyncSession,
        organization_id: Optional[str],
    ) -> bool:
        """
        Acquire a PostgreSQL transaction-scoped advisory lock (`pg_advisory_xact_lock`)
        keyed on `organization_id` so multiple OS worker processes writing simultaneously
        to a shared PostgreSQL cluster serialize sequence numbers across transactions.
        Returns True if the lock SQL was executed (PostgreSQL dialect), False otherwise.
        """
        bind = getattr(db, "bind", None) or getattr(getattr(db, "sync_session", None), "bind", None)
        dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
        if not dialect_name:
            try:
                conn = await db.connection()
                dialect_name = getattr(getattr(conn, "dialect", None), "name", "")
            except Exception:
                dialect_name = ""
        if dialect_name == "postgresql":
            org_key = f"kobits_audit:{organization_id or '__global__'}"
            await db.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:org_key))"),
                {"org_key": org_key},
            )
            return True
        return False

    @classmethod
    async def _resolve_chain_tail(
        cls,
        db: AsyncSession,
        organization_id: Optional[str],
    ) -> Tuple[int, str]:
        """
        Determine `(next_sequence_number, prev_hash)` for `organization_id`, flushing any
        unsealed pending `AuditLog` objects in `db.new` first so they are chained in order.
        """
        await cls._acquire_pg_advisory_lock(db, organization_id)

        if any(isinstance(obj, AuditLog) for obj in db.new):
            await db.flush()

        if organization_id is not None:
            stmt = (
                select(AuditLog)
                .execution_options(populate_existing=True)
                .where(AuditLog.organization_id == organization_id)
                .order_by(AuditLog.sequence_number.desc())
                .limit(1)
            )
        else:
            stmt = (
                select(AuditLog)
                .execution_options(populate_existing=True)
                .where(AuditLog.organization_id.is_(None))
                .order_by(AuditLog.sequence_number.desc())
                .limit(1)
            )

        res = await db.execute(stmt)
        last_entry = res.scalars().first()
        if last_entry is not None and last_entry.entry_hash:
            return int(last_entry.sequence_number) + 1, str(last_entry.entry_hash)
        return 1, GENESIS_HASH

    @classmethod
    async def log_event(
        cls,
        db: AsyncSession,
        *,
        event: str,
        user_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        actor_role: Optional[str] = None,
        session_id: Optional[str] = None,
        mission_id: Optional[str] = None,
        project_id: Optional[str] = None,
        category: Optional[str] = None,
        severity: Optional[str] = None,
        status: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        file_path: Optional[str] = None,
        command_executed: Optional[str] = None,
        code_change_hash: Optional[str] = None,
        content_hash: Optional[str] = None,
        detail: Optional[Union[str, Dict[str, Any]]] = None,
        request: Optional[Request] = None,
        secret_key: Optional[str] = None,
    ) -> AuditLog:
        """
        Append a cryptographically chained and HMAC-SHA256 signed `AuditLog` entry.
        Serializes concurrent `AsyncSession` writers per organization across the
        `flush()` -> `commit()`/`rollback()` window so concurrent sessions never
        read stale chain tails or collide on sequence numbers.
        """
        resolved_ip = cls.extract_client_ip(request, fallback_ip=ip_address)
        resolved_ua = cls.extract_user_agent(request, fallback_ua=user_agent)
        serialized_detail = cls._serialize_detail(detail)

        role_str: Optional[str] = None
        if actor_role is not None:
            role_str = actor_role.value if hasattr(actor_role, "value") else str(actor_role)

        org_key = organization_id or "__global__"
        lock = cls._get_org_lock(organization_id)
        sync_sess = getattr(db, "sync_session", None)
        curr_sess_id = id(sync_sess) if sync_sess is not None else id(db)

        while True:
            wait_evt: Optional[asyncio.Event] = None
            async with lock:
                active = cls._active_writer_sessions.get(org_key)
                if active is not None:
                    active_sess_id, active_ref, done_evt = active
                    active_sync_sess = active_ref() if callable(active_ref) else None
                    if (
                        active_sess_id != curr_sess_id
                        and active_sync_sess is not None
                        and active_sync_sess.in_transaction()
                        and not done_evt.is_set()
                    ):
                        wait_evt = done_evt
                    else:
                        cls._active_writer_sessions.pop(org_key, None)
                        active = None

                if wait_evt is None:
                    next_seq, prev_hash = await cls._resolve_chain_tail(db, organization_id)
                    entry = AuditLog(
                        user_id=user_id,
                        organization_id=organization_id,
                        actor_role=role_str,
                        session_id=session_id,
                        mission_id=mission_id,
                        project_id=project_id,
                        event=event,
                        category=category or infer_audit_category(event),
                        severity=severity,
                        status=status,
                        ip_address=resolved_ip,
                        user_agent=resolved_ua,
                        resource_type=resource_type,
                        resource_id=resource_id,
                        file_path=file_path.replace("\\", "/") if file_path else None,
                        command_executed=command_executed,
                        code_change_hash=code_change_hash,
                        content_hash=content_hash,
                        detail=serialized_detail,
                        sequence_number=next_seq,
                        prev_hash=prev_hash,
                    )
                    entry.seal(prev_hash=prev_hash, sequence_number=next_seq, secret_key=secret_key)
                    db.add(entry)
                    await db.flush()

                    if sync_sess is not None and (active is None or active[0] != curr_sess_id):
                        done_evt = asyncio.Event()
                        cls._active_writer_sessions[org_key] = (
                            curr_sess_id,
                            weakref.ref(sync_sess),
                            done_evt,
                        )

                        def _on_tx_end(
                            session: Any,
                            transaction: Any,
                            _org_key: str = org_key,
                            _sess_id: int = curr_sess_id,
                            _evt: asyncio.Event = done_evt,
                        ) -> None:
                            if not session.in_transaction():
                                _evt.set()
                                cur = cls._active_writer_sessions.get(_org_key)
                                if cur is not None and cur[0] == _sess_id:
                                    cls._active_writer_sessions.pop(_org_key, None)

                        from sqlalchemy import event as sa_event
                        sa_event.listen(sync_sess, "after_transaction_end", _on_tx_end)

                    return entry

            try:
                await asyncio.wait_for(wait_evt.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                cls._active_writer_sessions.pop(org_key, None)


    @classmethod
    async def log_file_read(
        cls,
        db: AsyncSession,
        *,
        file_path: str,
        content: Optional[Union[str, bytes]] = None,
        user_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        actor_role: Optional[str] = None,
        session_id: Optional[str] = None,
        mission_id: Optional[str] = None,
        project_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        status: str = "SUCCESS",
        detail: Optional[Union[str, Dict[str, Any]]] = None,
        request: Optional[Request] = None,
    ) -> AuditLog:
        """Record a signed SOC2 audit entry for a repository/sandbox file read."""
        normalized_path = (file_path or "").replace("\\", "/")
        content_digest = compute_sha256_hex(content) if content is not None else None
        payload_detail: Dict[str, Any] = {"file_path": normalized_path}
        if content is not None:
            payload_detail["byte_length"] = len(content.encode("utf-8") if isinstance(content, str) else content)
            payload_detail["content_sha256"] = content_digest
        if isinstance(detail, dict):
            payload_detail.update(detail)
        elif isinstance(detail, str) and detail:
            payload_detail["message"] = detail

        return await cls.log_event(
            db,
            event=AuditEvent.FILE_READ,
            category=AuditCategory.FILE_ACCESS.value,
            user_id=user_id,
            organization_id=organization_id,
            actor_role=actor_role,
            session_id=session_id,
            mission_id=mission_id,
            project_id=project_id,
            ip_address=ip_address,
            user_agent=user_agent,
            resource_type="file",
            resource_id=normalized_path,
            file_path=normalized_path,
            content_hash=content_digest,
            status=status,
            detail=payload_detail,
            request=request,
        )

    @classmethod
    async def log_command_executed(
        cls,
        db: AsyncSession,
        *,
        command: str,
        exit_code: Optional[int] = 0,
        output_snippet: Optional[str] = None,
        blocked: bool = False,
        user_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        actor_role: Optional[str] = None,
        session_id: Optional[str] = None,
        mission_id: Optional[str] = None,
        project_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        detail: Optional[Union[str, Dict[str, Any]]] = None,
        request: Optional[Request] = None,
    ) -> AuditLog:
        """Record a signed SOC2 audit entry for a terminal/sandbox command execution."""
        event_type = AuditEvent.COMMAND_BLOCKED if blocked else AuditEvent.COMMAND_EXECUTE
        severity = AuditSeverity.HIGH.value if blocked else (
            AuditSeverity.WARNING.value if (exit_code is not None and exit_code != 0) else AuditSeverity.INFO.value
        )
        status = "DENIED" if blocked else ("SUCCESS" if (exit_code in (None, 0)) else "FAILURE")
        cmd_hash = compute_sha256_hex(command)
        payload_detail: Dict[str, Any] = {
            "command": command,
            "command_sha256": cmd_hash,
            "exit_code": exit_code,
            "blocked": blocked,
        }
        if output_snippet is not None:
            payload_detail["output_sha256"] = compute_sha256_hex(output_snippet)
            payload_detail["output_preview"] = output_snippet[:256]
        if isinstance(detail, dict):
            payload_detail.update(detail)
        elif isinstance(detail, str) and detail:
            payload_detail["message"] = detail

        return await cls.log_event(
            db,
            event=event_type,
            category=AuditCategory.COMMAND_EXECUTION.value,
            severity=severity,
            status=status,
            user_id=user_id,
            organization_id=organization_id,
            actor_role=actor_role,
            session_id=session_id,
            mission_id=mission_id,
            project_id=project_id,
            ip_address=ip_address,
            user_agent=user_agent,
            resource_type="command",
            resource_id=cmd_hash[:16],
            command_executed=command,
            content_hash=cmd_hash,
            detail=payload_detail,
            request=request,
        )

    @classmethod
    async def log_ip_access(
        cls,
        db: AsyncSession,
        *,
        ip_address: str,
        endpoint_or_url: Optional[str] = None,
        target_url: Optional[str] = None,
        method: str = "GET",
        user_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        actor_role: Optional[str] = None,
        session_id: Optional[str] = None,
        mission_id: Optional[str] = None,
        project_id: Optional[str] = None,
        user_agent: Optional[str] = None,
        status: str = "SUCCESS",
        detail: Optional[Union[str, Dict[str, Any]]] = None,
        request: Optional[Request] = None,
    ) -> AuditLog:
        """Record a signed SOC2 audit entry for an inbound client IP or outbound network IP/URL access."""
        resolved_ip = cls.extract_client_ip(request, fallback_ip=ip_address) or ip_address
        target = endpoint_or_url or target_url or (str(request.url.path) if request is not None else "unknown")
        payload_detail: Dict[str, Any] = {
            "ip_address": resolved_ip,
            "target": target,
            "method": method,
        }
        if isinstance(detail, dict):
            payload_detail.update(detail)
        elif isinstance(detail, str) and detail:
            payload_detail["message"] = detail

        return await cls.log_event(
            db,
            event=AuditEvent.IP_ACCESS,
            category=AuditCategory.NETWORK_ACCESS.value,
            status=status,
            user_id=user_id,
            organization_id=organization_id,
            actor_role=actor_role,
            session_id=session_id,
            mission_id=mission_id,
            project_id=project_id,
            ip_address=resolved_ip,
            user_agent=user_agent,
            resource_type="network_endpoint",
            resource_id=target[:256],
            content_hash=compute_sha256_hex(f"{resolved_ip}:{method}:{target}"),
            detail=payload_detail,
            request=request,
        )

    @classmethod
    async def log_code_change(
        cls,
        db: AsyncSession,
        *,
        file_path: str,
        before_content: Optional[str] = None,
        after_content: Optional[str] = None,
        operation: str = "write",
        user_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        actor_role: Optional[str] = None,
        session_id: Optional[str] = None,
        mission_id: Optional[str] = None,
        project_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        status: str = "SUCCESS",
        detail: Optional[Union[str, Dict[str, Any]]] = None,
        request: Optional[Request] = None,
    ) -> AuditLog:
        """
        Record a cryptographically signed code modification (`code_change_hash` + `content_hash`).
        """
        normalized_path = (file_path or "").replace("\\", "/")
        change_hash = compute_code_change_hash(
            file_path=normalized_path,
            before_content=before_content,
            after_content=after_content,
            operation=operation,
        )
        after_hash = compute_sha256_hex(after_content) if after_content is not None else None
        before_hash = compute_sha256_hex(before_content) if before_content is not None else None

        op_lower = operation.lower()
        if op_lower == "delete":
            event_type = AuditEvent.CODE_DELETE
        elif op_lower == "edit":
            event_type = AuditEvent.CODE_EDIT
        elif op_lower == "write":
            event_type = AuditEvent.CODE_WRITE
        else:
            event_type = AuditEvent.CODE_CHANGE

        payload_detail: Dict[str, Any] = {
            "file_path": normalized_path,
            "operation": operation,
            "before_sha256": before_hash,
            "after_sha256": after_hash,
            "code_change_hash": change_hash,
        }
        if isinstance(detail, dict):
            payload_detail.update(detail)
        elif isinstance(detail, str) and detail:
            payload_detail["message"] = detail

        return await cls.log_event(
            db,
            event=event_type,
            category=AuditCategory.CODE_CHANGE.value,
            status=status,
            user_id=user_id,
            organization_id=organization_id,
            actor_role=actor_role,
            session_id=session_id,
            mission_id=mission_id,
            project_id=project_id,
            ip_address=ip_address,
            user_agent=user_agent,
            resource_type="code_file",
            resource_id=normalized_path,
            file_path=normalized_path,
            code_change_hash=change_hash,
            content_hash=after_hash or before_hash,
            detail=payload_detail,
            request=request,
        )

    @classmethod
    async def log_rbac_event(
        cls,
        db: AsyncSession,
        *,
        event: str,
        user_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        actor_role: Optional[str] = None,
        target_user_id: Optional[str] = None,
        target_role: Optional[str] = None,
        required_role_or_permission: Optional[str] = None,
        resource_type: str = "rbac",
        resource_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        status: Optional[str] = None,
        detail: Optional[Union[str, Dict[str, Any]]] = None,
        request: Optional[Request] = None,
    ) -> AuditLog:
        """Record an enterprise RBAC role change or access control decision."""
        is_denied = event == AuditEvent.RBAC_ACCESS_DENIED or status == "DENIED"
        payload_detail: Dict[str, Any] = {}
        if target_user_id is not None:
            payload_detail["target_user_id"] = target_user_id
        if target_role is not None:
            payload_detail["target_role"] = target_role
        if required_role_or_permission is not None:
            payload_detail["required"] = required_role_or_permission
        if isinstance(detail, dict):
            payload_detail.update(detail)
        elif isinstance(detail, str) and detail:
            payload_detail["message"] = detail

        return await cls.log_event(
            db,
            event=event,
            category=AuditCategory.RBAC.value,
            severity=AuditSeverity.HIGH.value if is_denied else AuditSeverity.INFO.value,
            status="DENIED" if is_denied else (status or "SUCCESS"),
            user_id=user_id,
            organization_id=organization_id,
            actor_role=actor_role,
            ip_address=ip_address,
            user_agent=user_agent,
            resource_type=resource_type,
            resource_id=resource_id or target_user_id,
            detail=payload_detail,
            request=request,
        )

    # ------------------------------------------------------------------
    # Synchronous recording & staging for SandboxManager / sync tools
    # ------------------------------------------------------------------

    @classmethod
    def _append_sync_entry(cls, kwargs: Dict[str, Any]) -> AuditLog:
        """
        Thread-safely create, hash-chain, and sign an `AuditLog` in the synchronous
        in-memory mirror AND stage its parameters for async DB persistence.
        """
        with cls._sync_lock:
            org_key = kwargs.get("organization_id") or "__global__"
            chain = cls._in_memory_chains.setdefault(org_key, [])
            if chain:
                next_seq = int(chain[-1].sequence_number) + 1
                prev_hash = str(chain[-1].entry_hash)
            else:
                next_seq = 1
                prev_hash = GENESIS_HASH

            entry_kwargs = dict(kwargs)
            entry_kwargs["sequence_number"] = next_seq
            entry_kwargs["prev_hash"] = prev_hash
            entry = AuditLog(**entry_kwargs)
            entry.seal(prev_hash=prev_hash, sequence_number=next_seq)
            chain.append(entry)
            cls._staged_entries.append(dict(kwargs))
            return entry

    @classmethod
    def record_file_read_sync(
        cls,
        *,
        file_path: str,
        content: Optional[Union[str, bytes]] = None,
        user_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        actor_role: Optional[str] = None,
        session_id: Optional[str] = None,
        mission_id: Optional[str] = None,
        project_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        status: str = "SUCCESS",
        detail: Optional[Dict[str, Any]] = None,
    ) -> AuditLog:
        normalized_path = (file_path or "").replace("\\", "/")
        content_digest = compute_sha256_hex(content) if content is not None else None
        payload_detail: Dict[str, Any] = {"file_path": normalized_path}
        if content is not None:
            payload_detail["byte_length"] = len(content.encode("utf-8") if isinstance(content, str) else content)
            payload_detail["content_sha256"] = content_digest
        if detail:
            payload_detail.update(detail)
        return cls._append_sync_entry(
            {
                "event": AuditEvent.FILE_READ,
                "category": AuditCategory.FILE_ACCESS.value,
                "user_id": user_id,
                "organization_id": organization_id,
                "actor_role": actor_role,
                "session_id": session_id,
                "mission_id": mission_id,
                "project_id": project_id,
                "ip_address": ip_address,
                "resource_type": "file",
                "resource_id": normalized_path,
                "file_path": normalized_path,
                "content_hash": content_digest,
                "status": status,
                "detail": cls._serialize_detail(payload_detail),
            }
        )

    @classmethod
    def record_command_executed_sync(
        cls,
        *,
        command: str,
        exit_code: Optional[int] = 0,
        output_snippet: Optional[str] = None,
        blocked: bool = False,
        user_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        actor_role: Optional[str] = None,
        session_id: Optional[str] = None,
        mission_id: Optional[str] = None,
        project_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
    ) -> AuditLog:
        event_type = AuditEvent.COMMAND_BLOCKED if blocked else AuditEvent.COMMAND_EXECUTE
        severity = AuditSeverity.HIGH.value if blocked else (
            AuditSeverity.WARNING.value if (exit_code is not None and exit_code != 0) else AuditSeverity.INFO.value
        )
        status = "DENIED" if blocked else ("SUCCESS" if (exit_code in (None, 0)) else "FAILURE")
        cmd_hash = compute_sha256_hex(command)
        payload_detail: Dict[str, Any] = {
            "command": command,
            "command_sha256": cmd_hash,
            "exit_code": exit_code,
            "blocked": blocked,
        }
        if output_snippet is not None:
            payload_detail["output_sha256"] = compute_sha256_hex(output_snippet)
            payload_detail["output_preview"] = output_snippet[:256]
        if detail:
            payload_detail.update(detail)
        return cls._append_sync_entry(
            {
                "event": event_type,
                "category": AuditCategory.COMMAND_EXECUTION.value,
                "severity": severity,
                "status": status,
                "user_id": user_id,
                "organization_id": organization_id,
                "actor_role": actor_role,
                "session_id": session_id,
                "mission_id": mission_id,
                "project_id": project_id,
                "ip_address": ip_address,
                "resource_type": "command",
                "resource_id": cmd_hash[:16],
                "command_executed": command,
                "content_hash": cmd_hash,
                "detail": cls._serialize_detail(payload_detail),
            }
        )

    @classmethod
    def record_code_change_sync(
        cls,
        *,
        file_path: str,
        before_content: Optional[str] = None,
        after_content: Optional[str] = None,
        operation: str = "write",
        user_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        actor_role: Optional[str] = None,
        session_id: Optional[str] = None,
        mission_id: Optional[str] = None,
        project_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        status: str = "SUCCESS",
        detail: Optional[Dict[str, Any]] = None,
    ) -> AuditLog:
        normalized_path = (file_path or "").replace("\\", "/")
        change_hash = compute_code_change_hash(
            file_path=normalized_path,
            before_content=before_content,
            after_content=after_content,
            operation=operation,
        )
        after_hash = compute_sha256_hex(after_content) if after_content is not None else None
        before_hash = compute_sha256_hex(before_content) if before_content is not None else None
        op_lower = operation.lower()
        if op_lower == "delete":
            event_type = AuditEvent.CODE_DELETE
        elif op_lower == "edit":
            event_type = AuditEvent.CODE_EDIT
        elif op_lower == "write":
            event_type = AuditEvent.CODE_WRITE
        else:
            event_type = AuditEvent.CODE_CHANGE

        payload_detail: Dict[str, Any] = {
            "file_path": normalized_path,
            "operation": operation,
            "before_sha256": before_hash,
            "after_sha256": after_hash,
            "code_change_hash": change_hash,
        }
        if detail:
            payload_detail.update(detail)
        return cls._append_sync_entry(
            {
                "event": event_type,
                "category": AuditCategory.CODE_CHANGE.value,
                "status": status,
                "user_id": user_id,
                "organization_id": organization_id,
                "actor_role": actor_role,
                "session_id": session_id,
                "mission_id": mission_id,
                "project_id": project_id,
                "ip_address": ip_address,
                "resource_type": "code_file",
                "resource_id": normalized_path,
                "file_path": normalized_path,
                "code_change_hash": change_hash,
                "content_hash": after_hash or before_hash,
                "detail": cls._serialize_detail(payload_detail),
            }
        )

    @classmethod
    def record_ip_access_sync(
        cls,
        *,
        ip_address: str,
        endpoint_or_url: str = "",
        method: str = "GET",
        user_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        actor_role: Optional[str] = None,
        session_id: Optional[str] = None,
        mission_id: Optional[str] = None,
        project_id: Optional[str] = None,
        status: str = "SUCCESS",
        detail: Optional[Dict[str, Any]] = None,
    ) -> AuditLog:
        payload_detail: Dict[str, Any] = {
            "ip_address": ip_address,
            "target": endpoint_or_url,
            "method": method,
        }
        if detail:
            payload_detail.update(detail)
        return cls._append_sync_entry(
            {
                "event": AuditEvent.IP_ACCESS,
                "category": AuditCategory.NETWORK_ACCESS.value,
                "status": status,
                "user_id": user_id,
                "organization_id": organization_id,
                "actor_role": actor_role,
                "session_id": session_id,
                "mission_id": mission_id,
                "project_id": project_id,
                "ip_address": ip_address,
                "resource_type": "network_endpoint",
                "resource_id": endpoint_or_url[:256],
                "content_hash": compute_sha256_hex(f"{ip_address}:{method}:{endpoint_or_url}"),
                "detail": cls._serialize_detail(payload_detail),
            }
        )

    @classmethod
    def get_in_memory_chain(cls, organization_id: Optional[str] = None) -> List[AuditLog]:
        """Return a copy of the synchronous in-memory audit chain for `organization_id`."""
        with cls._sync_lock:
            org_key = organization_id or "__global__"
            return list(cls._in_memory_chains.get(org_key, []))

    @classmethod
    def reset_in_memory_state(cls) -> None:
        """Clear in-memory chains and staged entries (useful for isolated unit tests)."""
        with cls._sync_lock:
            cls._staged_entries.clear()
            cls._in_memory_chains.clear()
            cls._active_writer_sessions.clear()

    @classmethod
    async def flush_staged_to_db(
        cls,
        db: AsyncSession,
        *,
        default_user_id: Optional[str] = None,
        default_organization_id: Optional[str] = None,
        default_actor_role: Optional[str] = None,
        default_ip_address: Optional[str] = None,
        user_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        actor_role: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> List[AuditLog]:
        """
        Drain any synchronously staged audit entries and persist them to `db`
        with full per-organization hash chaining and HMAC signing.
        """
        eff_user_id = default_user_id or user_id
        eff_org_id = default_organization_id or organization_id
        eff_role = default_actor_role or actor_role
        eff_ip = default_ip_address or ip_address

        with cls._sync_lock:
            if not cls._staged_entries:
                return []
            batch = list(cls._staged_entries)
            cls._staged_entries.clear()

        persisted: List[AuditLog] = []
        for item in batch:
            item_kwargs = dict(item)
            if not item_kwargs.get("user_id") and eff_user_id:
                item_kwargs["user_id"] = eff_user_id
            if not item_kwargs.get("organization_id") and eff_org_id:
                item_kwargs["organization_id"] = eff_org_id
            if not item_kwargs.get("actor_role") and eff_role:
                item_kwargs["actor_role"] = eff_role
            if not item_kwargs.get("ip_address") and eff_ip:
                item_kwargs["ip_address"] = eff_ip
            entry = await cls.log_event(db, **item_kwargs)
            persisted.append(entry)
        return persisted

    # ------------------------------------------------------------------
    # Cryptographic Hash-Chain Verification & Tamper Detection
    # ------------------------------------------------------------------

    @classmethod
    async def list_audit_logs(
        cls,
        db: AsyncSession,
        *,
        organization_id: Optional[str] = None,
        category: Optional[str] = None,
        event: Optional[str] = None,
        user_id: Optional[str] = None,
        actor_role: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ):
        """Query ordered SOC2 audit logs for an organization and return (entries, count)."""
        stmt = select(AuditLog).execution_options(populate_existing=True)
        if organization_id is not None:
            stmt = stmt.where(AuditLog.organization_id == organization_id)
        if category:
            stmt = stmt.where(AuditLog.category == category.upper())
        if event:
            stmt = stmt.where(AuditLog.event == event)
        if user_id:
            stmt = stmt.where(AuditLog.user_id == user_id)
        if actor_role:
            stmt = stmt.where(AuditLog.actor_role == actor_role)
        stmt = stmt.order_by(AuditLog.sequence_number.asc()).offset(max(0, offset)).limit(max(1, limit))
        entries = list((await db.execute(stmt)).scalars().all())
        return entries, len(entries)

    @classmethod
    def verify_entries_sequence(
        cls,
        entries: Sequence[AuditLog],
        *,
        secret_key: Optional[str] = None,
        previous_secret_keys: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        """
        Deterministically verify an ordered sequence of `AuditLog` entries for:
        1. Sequence continuity (`1, 2, ..., N`)
        2. Genesis hash (`prev_hash == GENESIS_HASH` on sequence 1)
        3. Hash chain linkage (`entry[i].prev_hash == entry[i-1].entry_hash`)
        4. Entry payload hash integrity (`entry.entry_hash == entry.compute_entry_hash()`)
        5. HMAC-SHA256 authenticity (`entry.verify_signature()`)
        6. Signed `code_change_hash` validity on code modification events
        """
        errors: List[str] = []
        tampered_entries: List[Dict[str, Any]] = []
        expected_prev_hash = GENESIS_HASH
        expected_seq = 1
        verified_count = 0
        chain_intact = True
        hmac_valid = True

        for entry in entries:
            entry_errors: List[str] = []
            reason_codes: List[str] = []

            if int(entry.sequence_number) != expected_seq:
                chain_intact = False
                reason_codes.append(f"sequence_gap:expected_{expected_seq}_got_{entry.sequence_number}")
                entry_errors.append(
                    f"Sequence gap/reorder at entry {entry.id}: expected sequence_number={expected_seq}, got {entry.sequence_number}"
                )

            if not hmac.compare_digest(str(entry.prev_hash or ""), expected_prev_hash):
                chain_intact = False
                reason_codes.append("prev_hash_mismatch")
                entry_errors.append(
                    f"Broken chain link at entry {entry.id} (seq {entry.sequence_number}): "
                    f"prev_hash={entry.prev_hash!r} != expected={expected_prev_hash!r}"
                )

            recomputed_hash = entry.compute_entry_hash()
            if not hmac.compare_digest(str(entry.entry_hash or ""), recomputed_hash):
                chain_intact = False
                reason_codes.append("entry_hash_mismatch")
                entry_errors.append(
                    f"Payload hash mismatch at entry {entry.id} (seq {entry.sequence_number}): "
                    f"stored entry_hash={entry.entry_hash!r} != computed={recomputed_hash!r}"
                )

            if not entry.verify_signature(
                secret_key=secret_key,
                previous_secret_keys=previous_secret_keys,
            ):
                hmac_valid = False
                reason_codes.append("hmac_signature_mismatch")
                entry_errors.append(
                    f"HMAC-SHA256 signature invalid at entry {entry.id} (seq {entry.sequence_number})"
                )

            if entry.category == AuditCategory.CODE_CHANGE.value or str(entry.event or "").startswith("code."):
                if not entry.code_change_hash or not _SHA256_HEX_RE.match(str(entry.code_change_hash)):
                    chain_intact = False
                    reason_codes.append("invalid_code_change_hash")
                    entry_errors.append(
                        f"Missing or malformed SHA-256 code_change_hash at code change entry {entry.id}"
                    )

            if entry_errors:
                errors.extend(entry_errors)
                tampered_entries.append(
                    {
                        "id": entry.id,
                        "sequence_number": entry.sequence_number,
                        "event": entry.event,
                        "reasons": reason_codes,
                        "messages": entry_errors,
                    }
                )
            else:
                verified_count += 1

            # Chain linkage for the next entry must be checked against the recomputed hash of the current entry
            # (or stored entry_hash if intact) so that tampering with entry i invalidates entry i+1's prev_hash link
            expected_prev_hash = recomputed_hash
            expected_seq = int(entry.sequence_number) + 1

        head_hash = str(entries[-1].entry_hash) if entries else GENESIS_HASH
        first_tampered_seq = tampered_entries[0]["sequence_number"] if tampered_entries else None
        return {
            "valid": len(errors) == 0,
            "chain_intact": chain_intact,
            "hmac_valid": hmac_valid,
            "total_entries": len(entries),
            "verified_entries": verified_count,
            "tampered_count": len(tampered_entries),
            "first_tampered_sequence": first_tampered_seq,
            "tampered_entries": tampered_entries,
            "errors": errors,
            "genesis_hash": GENESIS_HASH,
            "head_hash": head_hash,
            "head_entry_hash": head_hash,
            "verified_at": normalize_audit_timestamp(datetime.now(timezone.utc)),
        }

    @classmethod
    async def verify_audit_chain(
        cls,
        db: AsyncSession,
        *,
        organization_id: Optional[str] = None,
        secret_key: Optional[str] = None,
        previous_secret_keys: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        """
        Verify the persisted database audit log hash chain for `organization_id`
        (or the global `organization_id IS NULL` chain if `organization_id` is None).
        """
        if organization_id is not None:
            stmt = (
                select(AuditLog)
                .execution_options(populate_existing=True)
                .where(AuditLog.organization_id == organization_id)
                .order_by(AuditLog.sequence_number.asc(), AuditLog.occurred_at.asc())
            )
        else:
            stmt = (
                select(AuditLog)
                .execution_options(populate_existing=True)
                .where(AuditLog.organization_id.is_(None))
                .order_by(AuditLog.sequence_number.asc(), AuditLog.occurred_at.asc())
            )

        res = await db.execute(stmt)
        entries = list(res.scalars().all())
        result = cls.verify_entries_sequence(
            entries,
            secret_key=secret_key,
            previous_secret_keys=previous_secret_keys,
        )
        result["organization_id"] = organization_id
        return result

    @classmethod
    async def generate_soc2_report(
        cls,
        db: AsyncSession,
        *,
        organization_id: Optional[str],
        secret_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Generate a cryptographically attested SOC2 Type II compliance report
        covering chain integrity, file reads, commands executed, IPs accessed,
        signed code changes, and RBAC enforcement telemetry.
        """
        if organization_id is not None:
            stmt = (
                select(AuditLog)
                .execution_options(populate_existing=True)
                .where(AuditLog.organization_id == organization_id)
                .order_by(AuditLog.sequence_number.asc())
            )
        else:
            stmt = (
                select(AuditLog)
                .execution_options(populate_existing=True)
                .where(AuditLog.organization_id.is_(None))
                .order_by(AuditLog.sequence_number.asc())
            )

        res = await db.execute(stmt)
        entries = list(res.scalars().all())
        verification = cls.verify_entries_sequence(entries, secret_key=secret_key)

        category_counts: Dict[str, int] = {}
        role_counts: Dict[str, int] = {}
        files_read: List[Dict[str, Any]] = []
        commands_executed: List[Dict[str, Any]] = []
        ips_accessed: List[str] = []
        code_changes: List[Dict[str, Any]] = []
        rbac_denials: List[Dict[str, Any]] = []

        seen_ips = set()
        for e in entries:
            cat = e.category or "UNKNOWN"
            category_counts[cat] = category_counts.get(cat, 0) + 1
            if e.actor_role:
                role_counts[e.actor_role] = role_counts.get(e.actor_role, 0) + 1
            if e.ip_address and e.ip_address not in seen_ips:
                seen_ips.add(e.ip_address)
                ips_accessed.append(e.ip_address)

            if e.event == AuditEvent.FILE_READ or e.category == AuditCategory.FILE_ACCESS.value:
                files_read.append(
                    {
                        "id": e.id,
                        "sequence_number": e.sequence_number,
                        "user_id": e.user_id,
                        "actor_role": e.actor_role,
                        "file_path": e.file_path,
                        "content_hash": e.content_hash,
                        "occurred_at": normalize_audit_timestamp(e.occurred_at),
                    }
                )
            elif e.category == AuditCategory.COMMAND_EXECUTION.value or str(e.event).startswith("command."):
                commands_executed.append(
                    {
                        "id": e.id,
                        "sequence_number": e.sequence_number,
                        "user_id": e.user_id,
                        "actor_role": e.actor_role,
                        "command_executed": e.command_executed,
                        "status": e.status,
                        "content_hash": e.content_hash,
                        "occurred_at": normalize_audit_timestamp(e.occurred_at),
                    }
                )
            elif e.category == AuditCategory.CODE_CHANGE.value or str(e.event).startswith("code."):
                code_changes.append(
                    {
                        "id": e.id,
                        "sequence_number": e.sequence_number,
                        "user_id": e.user_id,
                        "actor_role": e.actor_role,
                        "event": e.event,
                        "file_path": e.file_path,
                        "code_change_hash": e.code_change_hash,
                        "content_hash": e.content_hash,
                        "occurred_at": normalize_audit_timestamp(e.occurred_at),
                    }
                )
            elif e.event == AuditEvent.RBAC_ACCESS_DENIED or e.status == "DENIED":
                rbac_denials.append(
                    {
                        "id": e.id,
                        "sequence_number": e.sequence_number,
                        "user_id": e.user_id,
                        "actor_role": e.actor_role,
                        "event": e.event,
                        "detail": e.detail,
                        "occurred_at": normalize_audit_timestamp(e.occurred_at),
                    }
                )

        report_core = {
            "standard": "SOC2_TYPE_II",
            "status": "COMPLIANT" if verification["valid"] else "NON_COMPLIANT_TAMPERED",
            "organization_id": organization_id,
            "chain_integrity": verification,
            "summary": {
                "total_events": len(entries),
                "category_breakdown": category_counts,
                "role_breakdown": role_counts,
                "files_read_count": len(files_read),
                "commands_executed_count": len(commands_executed),
                "unique_ips_count": len(ips_accessed),
                "signed_code_changes_count": len(code_changes),
                "rbac_access_denials_count": len(rbac_denials),
            },
            "tracked_files_read": [f["file_path"] for f in files_read if f.get("file_path")],
            "tracked_commands_executed": [c["command_executed"] for c in commands_executed if c.get("command_executed")],
            "tracked_ips_accessed": ips_accessed,
            "signed_code_changes": code_changes,
            "evidence": {
                "files_read": files_read,
                "commands_executed": commands_executed,
                "ips_accessed": ips_accessed,
                "signed_code_changes": code_changes,
                "rbac_access_denials": rbac_denials,
            },
        }
        canonical_report = json.dumps(report_core, sort_keys=True, separators=(",", ":"))
        attestation_hmac = hmac.new(
            _get_hmac_secret(secret_key),
            canonical_report.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        report_core["attestation_signature"] = attestation_hmac
        report_core["report_signature"] = attestation_hmac
        return report_core
