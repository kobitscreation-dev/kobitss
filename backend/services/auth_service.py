import contextvars
from typing import Optional, List, Tuple, Union
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import HTTPException, Request, status
from backend.core.security import (
    create_access_token,
    decode_access_token,
    get_password_hash,
    verify_password,
)
from backend.models.organization import (
    User,
    OrganizationMember,
    OrgRole,
    Permission,
    ROLE_HIERARCHY,
    ROLE_PERMISSIONS,
)


_active_org_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "_active_org_id_var", default=None
)


def set_active_org_context(org_id: Optional[str]) -> None:
    """Set the active organization ID for the current async request context."""
    _active_org_id_var.set(org_id.strip() if org_id and org_id.strip() else None)


async def get_user_org_membership(
    db: AsyncSession, user_id: str, organization_id: str
) -> OrganizationMember:
    """Verify user belongs to the organization. Logs high-severity SOC-2 alert and raises 403 if not."""
    result = await db.execute(
        select(OrganizationMember).where(
            OrganizationMember.user_id == user_id,
            OrganizationMember.organization_id == organization_id,
        )
    )
    membership = result.scalar_one_or_none()
    if not membership:
        try:
            from backend.models.audit_log import AuditEvent
            from backend.services.soc2_audit_service import SOC2AuditService

            await SOC2AuditService.log_rbac_event(
                db,
                event=AuditEvent.RBAC_ACCESS_DENIED,
                user_id=user_id,
                organization_id=organization_id,
                actor_role="NON_MEMBER",
                required_role_or_permission="ORGANIZATION_MEMBER",
                detail={
                    "reason": "cross_tenant_access_attempt",
                    "severity": "HIGH",
                    "target_organization_id": organization_id,
                },
            )
            await db.commit()
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this organization",
        )
    return membership


async def get_user_default_org(
    db: AsyncSession, user_id: str, preferred_org_id: Optional[str] = None
) -> OrganizationMember:
    """Get the organization a user belongs to (default workspace), auto-provisioning one if missing."""
    explicit_ctx_org = _active_org_id_var.get(None)
    effective_preferred = preferred_org_id or explicit_ctx_org
    if effective_preferred:
        result = await db.execute(
            select(OrganizationMember).where(
                OrganizationMember.user_id == user_id,
                OrganizationMember.organization_id == effective_preferred,
            )
        )
        membership = result.scalars().first()
        if membership:
            return membership
        if explicit_ctx_org and explicit_ctx_org == effective_preferred:
            try:
                from backend.models.audit_log import AuditEvent
                from backend.services.soc2_audit_service import SOC2AuditService

                await SOC2AuditService.log_rbac_event(
                    db,
                    event=AuditEvent.RBAC_ACCESS_DENIED,
                    user_id=user_id,
                    organization_id=effective_preferred,
                    actor_role="NON_MEMBER",
                    required_role_or_permission="ORGANIZATION_MEMBER",
                    detail={
                        "reason": "cross_tenant_access_attempt",
                        "severity": "HIGH",
                        "target_organization_id": effective_preferred,
                    },
                )
                await db.commit()
            except Exception:
                pass
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this organization",
            )

    result = await db.execute(
        select(OrganizationMember).where(OrganizationMember.user_id == user_id)
    )
    membership = result.scalars().first()
    if not membership:
        import uuid
        from backend.models.organization import Organization
        org = Organization(
            id=str(uuid.uuid4()),
            name="Personal Workspace",
            credit_balance=50,
        )
        db.add(org)
        await db.flush()
        membership = OrganizationMember(
            user_id=user_id,
            organization_id=org.id,
            role=OrgRole.OWNER,
        )
        db.add(membership)
        await db.commit()
        await db.refresh(membership)
    return membership


def require_role(membership: OrganizationMember, minimum_role: Union[OrgRole, str]) -> OrgRole:
    """
    Check user has at least the minimum required role.
    Hierarchy: OWNER (50) > ADMIN (40) > LEAD_ENGINEER (30) > MEMBER (20) > READ_ONLY_REVIEWER / VIEWER (10).
    """
    actor_role = OrgRole.normalize(membership.role)
    target_role = OrgRole.normalize(minimum_role)
    if ROLE_HIERARCHY.get(actor_role, 0) < ROLE_HIERARCHY.get(target_role, 0):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Requires at least {target_role.value} role (current role: {actor_role.value})",
        )
    return actor_role


def require_permission(membership: OrganizationMember, permission: Union[Permission, str]) -> Permission:
    """Check user's role grants the specified granular Permission."""
    actor_role = OrgRole.normalize(membership.role)
    perm = Permission(permission) if not isinstance(permission, Permission) else permission
    if not actor_role.has_permission(perm):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Role {actor_role.value} lacks required permission '{perm.value}'",
        )
    return perm


async def enforce_role_with_audit(
    db: AsyncSession,
    membership: OrganizationMember,
    minimum_role: Union[OrgRole, str],
    *,
    request: Optional[Request] = None,
    action: Optional[str] = None,
) -> OrgRole:
    """Enforce minimum RBAC role and record a signed SOC2 audit log entry on denial."""
    actor_role = OrgRole.normalize(membership.role)
    target_role = OrgRole.normalize(minimum_role)
    if ROLE_HIERARCHY.get(actor_role, 0) < ROLE_HIERARCHY.get(target_role, 0):
        from backend.models.audit_log import AuditEvent
        from backend.services.soc2_audit_service import SOC2AuditService

        await SOC2AuditService.log_rbac_event(
            db,
            event=AuditEvent.RBAC_ACCESS_DENIED,
            user_id=membership.user_id,
            organization_id=membership.organization_id,
            actor_role=actor_role.value,
            required_role_or_permission=target_role.value,
            detail={"action": action or "role_check", "reason": "insufficient_role"},
            request=request,
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Requires at least {target_role.value} role (current role: {actor_role.value})",
        )
    return actor_role


async def enforce_permission_with_audit(
    db: AsyncSession,
    membership: OrganizationMember,
    permission: Union[Permission, str],
    *,
    request: Optional[Request] = None,
    action: Optional[str] = None,
) -> OrgRole:
    """Enforce granular Permission and record a signed SOC2 audit log entry on denial."""
    actor_role = OrgRole.normalize(membership.role)
    perm = Permission(permission) if not isinstance(permission, Permission) else permission
    if not actor_role.has_permission(perm):
        from backend.models.audit_log import AuditEvent
        from backend.services.soc2_audit_service import SOC2AuditService

        await SOC2AuditService.log_rbac_event(
            db,
            event=AuditEvent.RBAC_ACCESS_DENIED,
            user_id=membership.user_id,
            organization_id=membership.organization_id,
            actor_role=actor_role.value,
            required_role_or_permission=perm.value,
            detail={"action": action or "permission_check", "reason": "missing_permission"},
            request=request,
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Role {actor_role.value} lacks required permission '{perm.value}'",
        )
    return actor_role


READ_ONLY_TOOLS = frozenset({
    "repository_read",
    "repository.read",
    "read_file",
    "list_directory",
    "search_code",
    "codebase_search",
    "codebase.search",
    "memory_retrieve",
    "memory.retrieve",
    "git_status",
    "git.status",
    "git_log",
    "git.log",
    "git_diff",
    "git.diff",
    "browser_screenshot",
    "browser.screenshot",
    "lsp_find_definition",
    "lsp.find_definition",
    "lsp_goto_definition",
    "lsp.goto_definition",
    "lsp_find_references",
    "lsp.find_references",
    "lsp_get_diagnostics",
    "lsp.get_diagnostics",
    "lsp_get_symbol_graph",
    "lsp.get_symbol_graph",
    "lsp_document_symbols",
    "lsp.document_symbols",
})

CODE_WRITE_TOOLS = frozenset({
    "repository_write",
    "repository.write",
    "write_file",
    "repository_edit",
    "repository.edit",
    "edit_file_block",
    "repository_delete",
    "repository.delete",
    "delete_file",
    "git_commit",
    "git.commit",
    "git_branch",
    "git.branch",
    "git_checkout",
    "git.checkout",
    "git_stash",
    "git.stash",
})

COMMAND_EXECUTION_TOOLS = frozenset({
    "terminal_execute",
    "terminal.execute",
    "run_command",
    "tests_run",
    "tests.run",
    "sandbox_exec",
    "sandbox.exec",
    "git_run",
    "git.run",
    "git_push",
    "git.push",
    "git_pull",
    "git.pull",
    "git_PR_create",
    "github.create_pr",
    "database.query",
    "database_query",
    "browser_click",
    "browser.click",
    "browser_type",
    "browser.type",
})

IP_ACCESS_TOOLS = frozenset({
    "browser_navigate",
    "browser.navigate",
    "web_search",
    "web.search",
})


def check_tool_rbac(
    actor_role: Union[OrgRole, str, None],
    tool_name: str,
) -> Tuple[bool, Optional[str]]:
    """
    Validate whether `actor_role` is authorized to execute `tool_name`.
    If `actor_role` is None (e.g. internal background system worker), execution is permitted.
    """
    if actor_role is None:
        return True, None

    role_enum = OrgRole.normalize(actor_role)
    normalized_tool = (tool_name or "").strip()
    underscored_tool = normalized_tool.replace(".", "_")

    if normalized_tool in READ_ONLY_TOOLS or underscored_tool in READ_ONLY_TOOLS:
        if role_enum.has_permission(Permission.FILE_READ):
            return True, None
        return False, f"Role {role_enum.value} is not permitted to read repository files ({tool_name})"

    if normalized_tool in CODE_WRITE_TOOLS or underscored_tool in CODE_WRITE_TOOLS:
        if role_enum.has_permission(Permission.CODE_WRITE):
            return True, None
        return False, f"Role {role_enum.value} is read-only and cannot modify code via '{tool_name}'"

    if normalized_tool in COMMAND_EXECUTION_TOOLS or underscored_tool in COMMAND_EXECUTION_TOOLS:
        if role_enum.has_permission(Permission.COMMAND_EXECUTE):
            return True, None
        return False, f"Role {role_enum.value} is not permitted to execute commands via '{tool_name}'"

    if normalized_tool in IP_ACCESS_TOOLS or underscored_tool in IP_ACCESS_TOOLS:
        if role_enum.has_permission(Permission.IP_ACCESS):
            return True, None
        return False, f"Role {role_enum.value} is not permitted to initiate external IP/network access via '{tool_name}'"

    if role_enum.has_permission(Permission.TOOL_EXECUTE):
        return True, None
    return False, f"Role {role_enum.value} is not permitted to execute tool '{tool_name}'"




# ═════════════════════════════════════════════════════════════════════
#  CRYPTOGRAPHIC API KEY MANAGEMENT (CLI / VS CODE EXTENSION / CI/CD)
# ═════════════════════════════════════════════════════════════════════

from datetime import datetime, timezone, timedelta
from typing import Optional, List, Tuple
from backend.models.api_key import ApiKey


async def create_api_key(
    db: AsyncSession,
    user_id: str,
    organization_id: str,
    name: str = "Default Key",
    scopes: str = "read,write,cli,extension",
    expires_in_days: Optional[int] = None,
) -> Tuple[ApiKey, str]:
    """
    Generate and persist a new high-entropy API key.
    Returns (api_key_record, raw_secret_key).
    """
    raw_key, key_prefix, hashed_key = ApiKey.generate_key()
    expires_at = None
    if expires_in_days and expires_in_days > 0:
        expires_at = datetime.now(timezone.utc) + timedelta(days=expires_in_days)

    api_key = ApiKey(
        name=name,
        key_prefix=key_prefix,
        hashed_key=hashed_key,
        user_id=user_id,
        organization_id=organization_id,
        scopes=scopes,
        is_active=True,
        expires_at=expires_at,
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)
    return api_key, raw_key


async def list_api_keys(
    db: AsyncSession,
    organization_id: str,
    user_id: Optional[str] = None,
) -> List[ApiKey]:
    """List active and historical API keys for an organization."""
    stmt = select(ApiKey).where(ApiKey.organization_id == organization_id)
    if user_id:
        stmt = stmt.where(ApiKey.user_id == user_id)
    stmt = stmt.order_by(ApiKey.created_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def revoke_api_key(
    db: AsyncSession,
    key_id: str,
    organization_id: str,
) -> bool:
    """Revoke an API key by ID within an organization."""
    stmt = select(ApiKey).where(ApiKey.id == key_id, ApiKey.organization_id == organization_id)
    result = await db.execute(stmt)
    key = result.scalar_one_or_none()
    if not key:
        return False
    key.is_active = False
    await db.commit()
    return True


async def verify_api_key(
    db: AsyncSession,
    raw_key: str,
) -> Optional[ApiKey]:
    """Verify a raw API key (kb_live_...) against the database hash and check expiry."""
    if not raw_key or not raw_key.startswith("kb_live_"):
        return None

    hashed = ApiKey.hash_key(raw_key)
    stmt = select(ApiKey).where(ApiKey.hashed_key == hashed, ApiKey.is_active == True)
    result = await db.execute(stmt)
    key = result.scalar_one_or_none()
    if not key:
        return None

    now = datetime.now(timezone.utc)
    if key.expires_at:
        exp = key.expires_at if key.expires_at.tzinfo else key.expires_at.replace(tzinfo=timezone.utc)
        if exp < now:
            key.is_active = False
            await db.commit()
            return None

    key.last_used_at = now
    await db.commit()
    return key

