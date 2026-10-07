from fastapi import APIRouter, Depends, HTTPException, status, Response, Request
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timezone
import json
import uuid
import secrets
import httpx

from backend.core.database import get_db
from backend.core.config import settings
from backend.core.security import get_password_hash, verify_password, create_access_token
from backend.models.organization import User, Organization, OrganizationMember, OrgRole
from backend.schemas.user import UserCreate, UserResponse, Token

router = APIRouter()

# ════════════════════════════════════════════════════════════
#  IN-MEMORY OAUTH STATE STORE (production: use Redis or DB)
# ════════════════════════════════════════════════════════════
_oauth_states: dict = {}


def _set_session_cookie(response: Response, token: str):
    """Set a secure HttpOnly session cookie."""
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=settings.SESSION_COOKIE_SECURE,
        samesite=settings.SESSION_COOKIE_SAMESITE,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )


async def _find_or_create_user_and_org(
    db: AsyncSession,
    email: str,
    full_name: str = None,
    google_id: str = None,
    github_id: str = None,
    avatar_url: str = None,
) -> User:
    """Find existing user by OAuth ID or email, or create a new one with an organization."""
    user = None

    # Try to find by provider ID first (most specific match)
    if google_id:
        result = await db.execute(select(User).where(User.google_id == google_id))
        user = result.scalar_one_or_none()
    if not user and github_id:
        result = await db.execute(select(User).where(User.github_id == github_id))
        user = result.scalar_one_or_none()

    # If not found by provider ID, try email
    if not user:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

    if user:
        # Link OAuth identity to existing account
        if google_id and not user.google_id:
            user.google_id = google_id
        if github_id and not user.github_id:
            user.github_id = github_id
        if avatar_url and not user.avatar_url:
            user.avatar_url = avatar_url
        if full_name and not user.full_name:
            user.full_name = full_name
        await db.commit()
        return user

    # Create new user + organization
    new_user = User(
        email=email,
        hashed_password="__oauth_no_password__",  # OAuth users don't have a password
        full_name=full_name,
        google_id=google_id,
        github_id=github_id,
        avatar_url=avatar_url,
    )
    db.add(new_user)

    org_name = (full_name or email.split("@")[0]) + "'s Workspace"
    new_org = Organization(name=org_name)
    db.add(new_org)
    await db.flush()

    membership = OrganizationMember(
        user_id=new_user.id,
        organization_id=new_org.id,
        role=OrgRole.OWNER,
    )
    db.add(membership)
    await db.commit()
    await db.refresh(new_user)
    return new_user


# ════════════════════════════════════════════════════════════
#  EMAIL AUTH
# ════════════════════════════════════════════════════════════

@router.post("/signup", response_model=UserResponse)
async def signup(user_in: UserCreate, response: Response, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == user_in.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")

    new_user = User(
        email=user_in.email,
        hashed_password=get_password_hash(user_in.password),
        full_name=user_in.full_name,
    )
    db.add(new_user)

    new_org = Organization(name=user_in.organization_name)
    db.add(new_org)
    await db.flush()

    membership = OrganizationMember(
        user_id=new_user.id,
        organization_id=new_org.id,
        role=OrgRole.OWNER,
    )
    db.add(membership)
    await db.commit()
    await db.refresh(new_user)

    # Set session cookie
    token = create_access_token(subject=new_user.id)
    _set_session_cookie(response, token)

    return new_user


@router.post("/login", response_model=Token)
async def login(response: Response, form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == form_data.username))
    user = result.scalar_one_or_none()

    if not user or user.hashed_password == "__oauth_no_password__":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(subject=user.id)
    _set_session_cookie(response, access_token)

    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(
        key=settings.SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=settings.SESSION_COOKIE_SECURE,
        samesite=settings.SESSION_COOKIE_SAMESITE,
    )
    return {"status": "success", "message": "Logged out"}


@router.get("/me")
async def get_current_user_info(request: Request, db: AsyncSession = Depends(get_db)):
    """Return the current authenticated user's profile. Used by the frontend to verify the session."""
    from backend.api.deps import get_current_user
    try:
        user = await get_current_user(
            token=request.cookies.get(settings.SESSION_COOKIE_NAME)
                  or (request.headers.get("Authorization", "").replace("Bearer ", "") or None),
            db=db,
        )
    except HTTPException:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Get user's organizations
    result = await db.execute(
        select(OrganizationMember, Organization)
        .join(Organization, OrganizationMember.organization_id == Organization.id)
        .where(OrganizationMember.user_id == user.id)
    )
    memberships = result.all()

    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "avatar_url": user.avatar_url,
        "is_active": user.is_active,
        "has_google": user.google_id is not None,
        "has_github": user.github_id is not None,
        "organizations": [
            {"id": org.id, "name": org.name, "role": mem.role.value}
            for mem, org in memberships
        ],
    }


@router.get("/dev-mode")
async def get_dev_mode():
    return {"dev_mode": settings.KOBITS_DEV_MODE}


# ════════════════════════════════════════════════════════════
#  GOOGLE OAUTH
# ════════════════════════════════════════════════════════════

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


@router.get("/google/login")
async def google_login():
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        raise HTTPException(
            status_code=501,
            detail="Google OAuth is not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET environment variables.",
        )

    state = secrets.token_urlsafe(32)
    _oauth_states[state] = {"provider": "google", "created_at": datetime.now(timezone.utc).isoformat()}

    redirect_uri = f"{settings.OAUTH_REDIRECT_BASE}/api/v1/auth/google/callback"
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }
    url = GOOGLE_AUTH_URL + "?" + "&".join(f"{k}={v}" for k, v in params.items())
    return RedirectResponse(url=url)


@router.get("/google/callback")
async def google_callback(code: str = None, state: str = None, error: str = None, db: AsyncSession = Depends(get_db)):
    if error:
        return RedirectResponse(url="/portal.html?auth_error=google_cancelled")

    if not state or state not in _oauth_states:
        return RedirectResponse(url="/portal.html?auth_error=invalid_state")

    del _oauth_states[state]

    redirect_uri = f"{settings.OAUTH_REDIRECT_BASE}/api/v1/auth/google/callback"

    # Exchange code for tokens server-side
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if token_resp.status_code != 200:
            return RedirectResponse(url="/portal.html?auth_error=google_token_failed")

        tokens = token_resp.json()
        access_token = tokens.get("access_token")

        # Fetch user info
        userinfo_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if userinfo_resp.status_code != 200:
            return RedirectResponse(url="/portal.html?auth_error=google_userinfo_failed")

        userinfo = userinfo_resp.json()

    email = userinfo.get("email")
    if not email or not userinfo.get("verified_email", False):
        return RedirectResponse(url="/portal.html?auth_error=email_not_verified")

    user = await _find_or_create_user_and_org(
        db=db,
        email=email,
        full_name=userinfo.get("name"),
        google_id=str(userinfo.get("id")),
        avatar_url=userinfo.get("picture"),
    )

    # Create session and redirect WITHOUT credentials in the URL
    session_token = create_access_token(subject=user.id)
    response = RedirectResponse(url="/portal.html#dashboard", status_code=302)
    _set_session_cookie(response, session_token)
    return response


# ════════════════════════════════════════════════════════════
#  GITHUB OAUTH (for authentication, not repo authorization)
# ════════════════════════════════════════════════════════════

GITHUB_AUTH_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_EMAILS_URL = "https://api.github.com/user/emails"


@router.get("/github/login")
async def github_login():
    if not settings.GITHUB_CLIENT_ID or not settings.GITHUB_CLIENT_SECRET:
        raise HTTPException(
            status_code=501,
            detail="GitHub OAuth is not configured. Set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET environment variables.",
        )

    state = secrets.token_urlsafe(32)
    _oauth_states[state] = {"provider": "github", "created_at": datetime.now(timezone.utc).isoformat()}

    redirect_uri = f"{settings.OAUTH_REDIRECT_BASE}/api/v1/auth/github/callback"
    params = {
        "client_id": settings.GITHUB_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": "user:email",
        "state": state,
    }
    url = GITHUB_AUTH_URL + "?" + "&".join(f"{k}={v}" for k, v in params.items())
    return RedirectResponse(url=url)


@router.get("/github/callback")
async def github_callback(code: str = None, state: str = None, error: str = None, db: AsyncSession = Depends(get_db)):
    if error:
        return RedirectResponse(url="/portal.html?auth_error=github_cancelled")

    if not state or state not in _oauth_states:
        return RedirectResponse(url="/portal.html?auth_error=invalid_state")

    del _oauth_states[state]

    # Exchange code for access token server-side
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            GITHUB_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.GITHUB_CLIENT_ID,
                "client_secret": settings.GITHUB_CLIENT_SECRET,
            },
            headers={"Accept": "application/json"},
        )
        if token_resp.status_code != 200:
            return RedirectResponse(url="/portal.html?auth_error=github_token_failed")

        tokens = token_resp.json()
        gh_access_token = tokens.get("access_token")
        if not gh_access_token:
            return RedirectResponse(url="/portal.html?auth_error=github_token_failed")

        # Fetch GitHub user profile
        user_resp = await client.get(
            GITHUB_USER_URL,
            headers={"Authorization": f"Bearer {gh_access_token}", "Accept": "application/vnd.github.v3+json"},
        )
        if user_resp.status_code != 200:
            return RedirectResponse(url="/portal.html?auth_error=github_userinfo_failed")

        gh_user = user_resp.json()

        # Fetch verified email
        email = gh_user.get("email")
        if not email:
            emails_resp = await client.get(
                GITHUB_EMAILS_URL,
                headers={"Authorization": f"Bearer {gh_access_token}", "Accept": "application/vnd.github.v3+json"},
            )
            if emails_resp.status_code == 200:
                for em in emails_resp.json():
                    if em.get("primary") and em.get("verified"):
                        email = em.get("email")
                        break

        if not email:
            return RedirectResponse(url="/portal.html?auth_error=email_not_verified")

    user = await _find_or_create_user_and_org(
        db=db,
        email=email,
        full_name=gh_user.get("name") or gh_user.get("login"),
        github_id=str(gh_user.get("id")),
        avatar_url=gh_user.get("avatar_url"),
    )

    session_token = create_access_token(subject=user.id)
    response = RedirectResponse(url="/portal.html#dashboard", status_code=302)
    _set_session_cookie(response, session_token)
    return response
get_current_active_user = get_current_user_info
