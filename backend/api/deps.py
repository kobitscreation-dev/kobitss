from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
import jwt
from jwt.exceptions import InvalidTokenError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.core.database import get_db
from backend.models.organization import User
from backend.core.config import settings

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/v1/auth/login", auto_error=False)


async def get_current_user(token: str = None, db: AsyncSession = Depends(get_db)) -> User:
    """Resolve the current user from an API Key, JWT token, or dev bypass.
    
    Token sources (checked in order):
    1. Cryptographic API Key ('kb_live_...')
    2. Explicitly passed JWT token (from cookie or header)
    3. KOBITS_DEV_MODE bypass
    """
    if token and token.strip().startswith("kb_live_"):
        from backend.services.auth_service import verify_api_key
        api_key = await verify_api_key(db, token.strip())
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or revoked API key",
                headers={"WWW-Authenticate": "Bearer"},
            )
        result = await db.execute(select(User).filter(User.id == api_key.user_id))
        user = result.scalar_one_or_none()
        if not user or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User associated with API key is inactive or not found",
                headers={"WWW-Authenticate": "Bearer"},
            )
        setattr(user, "_active_org_id", api_key.organization_id)
        setattr(user, "_api_key", api_key)
        return user

    if not token:
        if settings.KOBITS_DEV_MODE:
            result = await db.execute(select(User).limit(1))
            user = result.scalar_one_or_none()
            if not user:
                from backend.models.organization import Organization, OrganizationMember, OrgRole
                user = User(id="dev_user_1", email="dev@kobits.ai", full_name="Dev User", hashed_password="x")
                org = Organization(id="dev_org_1", name="Dev Workspace")
                db.add(user)
                db.add(org)
                await db.flush()
                member = OrganizationMember(user_id=user.id, organization_id=org.id, role=OrgRole.ADMIN)
                db.add(member)
                await db.commit()
                await db.refresh(user)
            else:
                from backend.services.auth_service import get_user_default_org
                await get_user_default_org(db, user.id)
            return user
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except InvalidTokenError:
        raise credentials_exception

    result = await db.execute(select(User).filter(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None and payload.get("email"):
        result_by_email = await db.execute(select(User).filter(User.email == payload.get("email")))
        user = result_by_email.scalar_one_or_none()

    if user is None and payload.get("email"):
        # Ephemeral container rehydration: restore verified user & workspace across restarts
        from backend.models.organization import Organization, OrganizationMember, OrgRole
        import uuid
        email = payload["email"]
        full_name = payload.get("name") or email.split("@")[0]
        user = User(
            id=user_id,
            email=email,
            hashed_password="__oauth_no_password__",
            full_name=full_name,
            google_id=payload.get("google_id"),
            github_id=payload.get("github_id"),
            avatar_url=payload.get("avatar_url"),
            is_active=True,
        )
        db.add(user)
        org_id = str(uuid.uuid5(uuid.NAMESPACE_OID, email.lower().strip()))
        existing_org = await db.get(Organization, org_id)
        if not existing_org:
            org = Organization(id=org_id, name=f"{full_name}'s Workspace", credit_balance=50)
            db.add(org)
        try:
            await db.flush()
            db.add(OrganizationMember(user_id=user.id, organization_id=org_id, role=OrgRole.OWNER))
            await db.commit()
            await db.refresh(user)
        except Exception:
            await db.rollback()
            result = await db.execute(select(User).filter(User.email == email))
            user = result.scalar_one_or_none()

    if user is None:
        raise credentials_exception
    return user


async def get_current_user_from_request(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    """Extract the API key, JWT Bearer, or cookie, then resolve the user."""
    from backend.services.auth_service import set_active_org_context

    explicit_org = (
        request.headers.get("X-Organization-Id")
        or request.headers.get("x-organization-id")
        or request.headers.get("X-Org-Id")
        or request.headers.get("x-org-id")
    )
    set_active_org_context(explicit_org)

    # 1. High-priority API Key header (CLI, VS Code extension, CI/CD)
    api_key_header = request.headers.get("X-API-Key") or request.headers.get("x-api-key")
    if api_key_header and api_key_header.strip():
        user = await get_current_user(token=api_key_header.strip(), db=db)
        if explicit_org and explicit_org.strip():
            setattr(user, "_active_org_id", explicit_org.strip())
        return user

    # 2. Authorization header (Bearer token or Bearer kb_live_...)
    auth_header = request.headers.get("Authorization", "").strip()
    if auth_header:
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        else:
            token = auth_header
        user = await get_current_user(token=token, db=db)
        if explicit_org and explicit_org.strip():
            setattr(user, "_active_org_id", explicit_org.strip())
        return user

    # 3. Cookie fallback (Browser sessions)
    token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    user = await get_current_user(token=token, db=db)
    if explicit_org and explicit_org.strip():
        setattr(user, "_active_org_id", explicit_org.strip())
    return user


async def get_current_active_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    """Primary dependency for protected endpoints. Supports both cookie and Bearer auth.
    
    Raises 401 if the account has been deactivated (is_active = False).
    """
    user = await get_current_user_from_request(request=request, db=db)
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is inactive.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_any_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    """Admin-only dependency: resolves the current user WITHOUT enforcing is_active.

    Use this ONLY in admin/internal endpoints that legitimately need to access
    deactivated accounts (e.g., admin panel, GDPR hard-delete verification).
    Regular API routes MUST use get_current_active_user instead.
    """
    return await get_current_user_from_request(request=request, db=db)


async def get_current_org_member(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Resolve the user's active OrganizationMember context.
    Honors an explicit `X-Organization-Id` header (with strict membership check)
    or falls back to the user's active/default organization workspace.
    """
    from backend.services.auth_service import get_user_default_org, get_user_org_membership

    explicit_org_id = request.headers.get("X-Organization-Id") or request.headers.get("x-organization-id")
    if explicit_org_id and explicit_org_id.strip():
        membership = await get_user_org_membership(db, current_user.id, explicit_org_id.strip())
    else:
        preferred = getattr(current_user, "_active_org_id", None)
        membership = await get_user_default_org(db, current_user.id, preferred_org_id=preferred)

    setattr(current_user, "_active_org_id", membership.organization_id)
    setattr(current_user, "_active_role", membership.role)
    return membership


class RoleChecker:
    """
    FastAPI dependency enforcing Enterprise RBAC minimum role and/or granular Permission.
    Automatically records a signed SOC2 `rbac.access_denied` audit log entry on 403.
    """

    def __init__(self, minimum_role=None, required_permission=None) -> None:
        self.minimum_role = minimum_role
        self.required_permission = required_permission

    async def __call__(
        self,
        request: Request,
        current_user: User = Depends(get_current_active_user),
        db: AsyncSession = Depends(get_db),
    ):
        from backend.services.auth_service import (
            enforce_permission_with_audit,
            enforce_role_with_audit,
        )

        membership = await get_current_org_member(request=request, current_user=current_user, db=db)
        action = f"{request.method} {request.url.path}"
        if self.minimum_role is not None:
            await enforce_role_with_audit(
                db,
                membership,
                self.minimum_role,
                request=request,
                action=action,
            )
        if self.required_permission is not None:
            await enforce_permission_with_audit(
                db,
                membership,
                self.required_permission,
                request=request,
                action=action,
            )
        return membership


from backend.models.organization import OrgRole, Permission  # noqa: E402
from backend.services.auth_service import (  # noqa: E402
    enforce_permission_with_audit,
    enforce_role_with_audit,
    get_user_default_org,
    get_user_org_membership,
)

require_admin = RoleChecker(minimum_role=OrgRole.ADMIN)
require_lead_engineer = RoleChecker(minimum_role=OrgRole.LEAD_ENGINEER)
require_member = RoleChecker(minimum_role=OrgRole.MEMBER)
require_reviewer = RoleChecker(minimum_role=OrgRole.READ_ONLY_REVIEWER)


