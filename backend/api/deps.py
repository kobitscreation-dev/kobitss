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
    # 1. High-priority API Key header (CLI, VS Code extension, CI/CD)
    api_key_header = request.headers.get("X-API-Key") or request.headers.get("x-api-key")
    if api_key_header and api_key_header.strip():
        return await get_current_user(token=api_key_header.strip(), db=db)

    # 2. Authorization header (Bearer token or Bearer kb_live_...)
    auth_header = request.headers.get("Authorization", "").strip()
    if auth_header:
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        else:
            token = auth_header
        return await get_current_user(token=token, db=db)

    # 3. Cookie fallback (Browser sessions)
    token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    return await get_current_user(token=token, db=db)


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
