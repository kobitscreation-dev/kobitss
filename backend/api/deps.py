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
    """Resolve the current user from a JWT token.
    
    Token sources (checked in order):
    1. KOBITS_DEV_MODE bypass
    2. Explicitly passed token (from cookie or header)
    3. OAuth2 Bearer header (auto-injected by FastAPI)
    """
    # DEV MODE: skip all auth
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
        return user

    if not token:
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
    if user is None:
        raise credentials_exception
    return user


async def get_current_user_from_request(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    """Extract the JWT from cookie or Authorization header, then resolve the user."""
    # Try cookie first (web sessions)
    token = request.cookies.get(settings.SESSION_COOKIE_NAME)

    # Fallback to Authorization header (API clients)
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

    return await get_current_user(token=token, db=db)


async def get_current_active_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    """Primary dependency for protected endpoints. Supports both cookie and Bearer auth."""
    return await get_current_user_from_request(request=request, db=db)
