from typing import Optional, List, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import HTTPException, status
from backend.models.organization import User, OrganizationMember, OrgRole


async def get_user_org_membership(
    db: AsyncSession, user_id: str, organization_id: str
) -> OrganizationMember:
    """Verify user belongs to the organization. Raises 403 if not."""
    result = await db.execute(
        select(OrganizationMember).where(
            OrganizationMember.user_id == user_id,
            OrganizationMember.organization_id == organization_id,
        )
    )
    membership = result.scalar_one_or_none()
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this organization",
        )
    return membership


async def get_user_default_org(
    db: AsyncSession, user_id: str, preferred_org_id: Optional[str] = None
) -> OrganizationMember:
    """Get the organization a user belongs to (default workspace), auto-provisioning one if missing."""
    if preferred_org_id:
        result = await db.execute(
            select(OrganizationMember).where(
                OrganizationMember.user_id == user_id,
                OrganizationMember.organization_id == preferred_org_id,
            )
        )
        membership = result.scalars().first()
        if membership:
            return membership

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


def require_role(membership: OrganizationMember, minimum_role: OrgRole):
    """Check user has at least the minimum required role. Owner > Admin > Member > Viewer."""
    role_hierarchy = {
        OrgRole.OWNER: 4,
        OrgRole.ADMIN: 3,
        OrgRole.MEMBER: 2,
        OrgRole.VIEWER: 1,
    }
    if role_hierarchy.get(membership.role, 0) < role_hierarchy.get(minimum_role, 0):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Requires at least {minimum_role.value} role",
        )


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

