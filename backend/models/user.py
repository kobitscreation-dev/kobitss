"""
User & Enterprise RBAC model exports.

Provides a canonical import surface (`backend.models.user`) for `User`,
`Organization`, `OrganizationMember`, `OrgRole`, `Permission`,
`ROLE_HIERARCHY`, `ROLE_PERMISSIONS`, and `ENTERPRISE_ROLES`.
"""
from backend.models.organization import (
    ENTERPRISE_ROLES,
    ROLE_HIERARCHY,
    ROLE_PERMISSIONS,
    OrgRole,
    Organization,
    OrganizationMember,
    Permission,
    User,
)

# Alias for callers expecting Role or UserRole
Role = OrgRole
UserRole = OrgRole

__all__ = [
    "User",
    "Organization",
    "OrganizationMember",
    "OrgRole",
    "Role",
    "UserRole",
    "Permission",
    "ROLE_HIERARCHY",
    "ROLE_PERMISSIONS",
    "ENTERPRISE_ROLES",
]
