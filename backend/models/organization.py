from typing import Dict, FrozenSet, List, Optional, Set, Union
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Boolean, ForeignKey, Enum, DateTime, Integer
from datetime import datetime, timezone
import enum
import uuid


from backend.models.base import Base


class Permission(str, enum.Enum):
    """Granular enterprise RBAC permissions for SOC2 Type II access control."""
    # Organization & RBAC management
    ORG_READ = "org:read"
    ORG_MANAGE = "org:manage"
    ORG_WRITE = "org:manage"
    RBAC_READ = "rbac:read"
    RBAC_ASSIGN = "rbac:assign"
    RBAC_WRITE = "rbac:assign"
    API_KEY_MANAGE = "api_key:manage"
    BILLING_MANAGE = "billing:manage"
    ORG_BILLING = "billing:manage"

    # SOC2 Audit & Compliance
    AUDIT_READ = "audit:read"
    AUDIT_VERIFY = "audit:verify"
    AUDIT_EXPORT = "audit:export"

    # Projects & Missions
    PROJECT_READ = "project:read"
    PROJECT_WRITE = "project:write"
    PROJECT_DELETE = "project:delete"
    MISSION_READ = "mission:read"
    MISSION_EXECUTE = "mission:execute"
    MISSION_APPROVE = "mission:approve"

    # Workspace, Files, Code, Terminal & Network/IP
    FILE_READ = "file:read"
    CODE_WRITE = "code:write"
    CODE_DELETE = "code:delete"
    COMMAND_EXECUTE = "command:execute"
    IP_ACCESS = "ip:access"
    TOOL_EXECUTE = "tool:execute"


class OrgRole(str, enum.Enum):
    """
    Enterprise RBAC Roles:
    - ADMIN ("Admin"): Full organization, RBAC, API key, billing, audit export, and engineering control.
    - LEAD_ENGINEER ("Lead Engineer"): Full engineering, project/mission execution, code change, terminal & tool execution, and audit verification.
    - READ_ONLY_REVIEWER ("Read-Only Reviewer"): Read-only access to projects, missions, repository files, and SOC2 audit logs/verification. Cannot mutate state or execute commands.

    Legacy roles preserved for 100% backward compatibility:
    - OWNER: Super-admin / founder role (rank >= ADMIN).
    - MEMBER: Standard engineer role.
    - VIEWER: Legacy alias equivalent in rank to READ_ONLY_REVIEWER.
    """
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    LEAD_ENGINEER = "LEAD_ENGINEER"
    MEMBER = "MEMBER"
    READ_ONLY_REVIEWER = "READ_ONLY_REVIEWER"
    VIEWER = "VIEWER"

    @classmethod
    def normalize(cls, value: Union["OrgRole", str, None]) -> "OrgRole":
        """Normalize enum values, display labels, or case-insensitive role names to OrgRole."""
        if isinstance(value, cls):
            return value
        if value is None:
            raise ValueError("Role cannot be None")
        cleaned = str(value).strip()
        upper = cleaned.upper().replace("-", "_").replace(" ", "_")
        aliases = {
            "OWNER": cls.OWNER,
            "ADMIN": cls.ADMIN,
            "ADMINISTRATOR": cls.ADMIN,
            "AGENCY_ADMIN": cls.ADMIN,
            "LEAD_ENGINEER": cls.LEAD_ENGINEER,
            "LEAD": cls.LEAD_ENGINEER,
            "ENGINEER": cls.LEAD_ENGINEER,
            "MEMBER": cls.MEMBER,
            "READ_ONLY_REVIEWER": cls.READ_ONLY_REVIEWER,
            "READONLY_REVIEWER": cls.READ_ONLY_REVIEWER,
            "REVIEWER": cls.READ_ONLY_REVIEWER,
            "READ_ONLY": cls.READ_ONLY_REVIEWER,
            "CLIENT": cls.READ_ONLY_REVIEWER,
            "CLIENT_EXEC": cls.READ_ONLY_REVIEWER,
            "VIEWER": cls.VIEWER,
        }
        if upper in aliases:
            return aliases[upper]
        raise ValueError(f"Unknown RBAC role: {value!r}")

    @property
    def display_name(self) -> str:
        labels = {
            OrgRole.OWNER: "Owner",
            OrgRole.ADMIN: "Admin",
            OrgRole.LEAD_ENGINEER: "Lead Engineer",
            OrgRole.MEMBER: "Member",
            OrgRole.READ_ONLY_REVIEWER: "Read-Only Reviewer",
            OrgRole.VIEWER: "Viewer",
        }
        return labels[self]

    @property
    def rank(self) -> int:
        return ROLE_HIERARCHY[self]

    @property
    def is_read_only(self) -> bool:
        return self in (OrgRole.READ_ONLY_REVIEWER, OrgRole.VIEWER)

    @property
    def permissions(self) -> FrozenSet[Permission]:
        return ROLE_PERMISSIONS[self]

    def has_permission(self, permission: Union[Permission, str]) -> bool:
        perm = Permission(permission) if not isinstance(permission, Permission) else permission
        return perm in ROLE_PERMISSIONS[self]

    def has_minimum_role(self, minimum_role: Union["OrgRole", str]) -> bool:
        target = OrgRole.normalize(minimum_role)
        return self.rank >= target.rank


ROLE_HIERARCHY: Dict[OrgRole, int] = {
    OrgRole.OWNER: 50,
    OrgRole.ADMIN: 40,
    OrgRole.LEAD_ENGINEER: 30,
    OrgRole.MEMBER: 20,
    OrgRole.READ_ONLY_REVIEWER: 10,
    OrgRole.VIEWER: 10,
}

ENTERPRISE_ROLES: List[OrgRole] = [
    OrgRole.ADMIN,
    OrgRole.LEAD_ENGINEER,
    OrgRole.READ_ONLY_REVIEWER,
]

ROLE_PERMISSIONS: Dict[OrgRole, FrozenSet[Permission]] = {
    OrgRole.OWNER: frozenset(Permission),
    OrgRole.ADMIN: frozenset(Permission),
    OrgRole.LEAD_ENGINEER: frozenset({
        Permission.ORG_READ,
        Permission.RBAC_READ,
        Permission.AUDIT_READ,
        Permission.AUDIT_VERIFY,
        Permission.PROJECT_READ,
        Permission.PROJECT_WRITE,
        Permission.PROJECT_DELETE,
        Permission.MISSION_READ,
        Permission.MISSION_EXECUTE,
        Permission.MISSION_APPROVE,
        Permission.FILE_READ,
        Permission.CODE_WRITE,
        Permission.CODE_DELETE,
        Permission.COMMAND_EXECUTE,
        Permission.IP_ACCESS,
        Permission.TOOL_EXECUTE,
    }),
    OrgRole.MEMBER: frozenset({
        Permission.ORG_READ,
        Permission.RBAC_READ,
        Permission.PROJECT_READ,
        Permission.PROJECT_WRITE,
        Permission.MISSION_READ,
        Permission.MISSION_EXECUTE,
        Permission.FILE_READ,
        Permission.CODE_WRITE,
        Permission.CODE_DELETE,
        Permission.COMMAND_EXECUTE,
        Permission.IP_ACCESS,
        Permission.TOOL_EXECUTE,
    }),
    OrgRole.READ_ONLY_REVIEWER: frozenset({
        Permission.ORG_READ,
        Permission.RBAC_READ,
        Permission.AUDIT_READ,
        Permission.AUDIT_VERIFY,
        Permission.PROJECT_READ,
        Permission.MISSION_READ,
        Permission.FILE_READ,
    }),
    OrgRole.VIEWER: frozenset({
        Permission.ORG_READ,
        Permission.RBAC_READ,
        Permission.AUDIT_READ,
        Permission.AUDIT_VERIFY,
        Permission.PROJECT_READ,
        Permission.MISSION_READ,
        Permission.FILE_READ,
    }),
}


def get_role_matrix_summary() -> Dict[str, Dict[str, object]]:
    """Return a structured dictionary of all Enterprise and Legacy RBAC roles and their permissions."""
    roles_dict: Dict[str, Dict[str, object]] = {}
    for r in OrgRole:
        roles_dict[r.value] = {
            "role": r.value,
            "display_name": r.display_name,
            "rank": r.rank,
            "is_enterprise_tier": r in ENTERPRISE_ROLES,
            "is_read_only": r.is_read_only,
            "permissions": sorted(p.value for p in r.permissions),
        }
    return {
        "enterprise_roles": [r.value for r in ENTERPRISE_ROLES],
        "roles": roles_dict,
    }



class User(Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    hashed_password: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    full_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # OAuth identity fields (nullable — a user may have zero, one, or multiple linked providers)
    google_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, unique=True)
    github_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, unique=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    memberships: Mapped[List["OrganizationMember"]] = relationship(back_populates="user", cascade="all, delete-orphan")


_ORG_METADATA_CACHE: Dict[str, Dict[str, object]] = {}


class Organization(Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String)
    credit_balance: Mapped[int] = mapped_column(Integer, default=0)
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)

    members: Mapped[List["OrganizationMember"]] = relationship(back_populates="organization", cascade="all, delete-orphan")

    def __init__(self, **kwargs):
        slug = kwargs.pop("slug", None)
        description = kwargs.pop("description", None)
        is_active = kwargs.pop("is_active", True)
        super().__init__(**kwargs)
        meta = _ORG_METADATA_CACHE.setdefault(self.id or self.name or "default", {})
        if slug is not None:
            meta["slug"] = slug
            self._slug = slug
        if description is not None:
            meta["description"] = description
            self._description = description
        meta["is_active"] = is_active
        self._is_active = is_active

    @property
    def slug(self) -> str:
        val = getattr(self, "_slug", None)
        if val:
            return str(val)
        cached = _ORG_METADATA_CACHE.get(self.id or "", {}).get("slug") or _ORG_METADATA_CACHE.get(self.name or "", {}).get("slug")
        if cached:
            return str(cached)
        import re
        return re.sub(r"[^a-z0-9]+", "-", (self.name or "org").lower()).strip("-") or "org"

    @slug.setter
    def slug(self, value: Optional[str]) -> None:
        self._slug = value
        if self.id:
            _ORG_METADATA_CACHE.setdefault(self.id, {})["slug"] = value

    @property
    def description(self) -> Optional[str]:
        val = getattr(self, "_description", None)
        if val is not None:
            return str(val)
        cached = _ORG_METADATA_CACHE.get(self.id or "", {}).get("description") or _ORG_METADATA_CACHE.get(self.name or "", {}).get("description")
        if cached is not None:
            return str(cached)
        return None

    @description.setter
    def description(self, value: Optional[str]) -> None:
        self._description = value
        if self.id:
            _ORG_METADATA_CACHE.setdefault(self.id, {})["description"] = value

    @property
    def is_active(self) -> bool:
        return bool(getattr(self, "_is_active", True))

    @is_active.setter
    def is_active(self, value: bool) -> None:
        self._is_active = bool(value)


class OrganizationMember(Base):
    __tablename__ = "organization_members"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    role: Mapped[OrgRole] = mapped_column(Enum(OrgRole), default=OrgRole.MEMBER)

    user: Mapped["User"] = relationship(back_populates="memberships")
    organization: Mapped["Organization"] = relationship(back_populates="members")

    def has_permission(self, permission: Union[Permission, str]) -> bool:
        role_enum = OrgRole.normalize(self.role)
        return role_enum.has_permission(permission)

    def has_minimum_role(self, minimum_role: Union[OrgRole, str]) -> bool:
        role_enum = OrgRole.normalize(self.role)
        return role_enum.has_minimum_role(minimum_role)

    @property
    def is_read_only(self) -> bool:
        role_enum = OrgRole.normalize(self.role)
        return role_enum.is_read_only


OrgMember = OrganizationMember


