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


async def get_user_default_org(db: AsyncSession, user_id: str) -> OrganizationMember:
    """Get the first organization a user belongs to (default workspace)."""
    result = await db.execute(
        select(OrganizationMember).where(OrganizationMember.user_id == user_id)
    )
    membership = result.scalar_one_or_none()
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User does not belong to any organization",
        )
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
