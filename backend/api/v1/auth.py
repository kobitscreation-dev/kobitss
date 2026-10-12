from fastapi import APIRouter, Depends, HTTPException, status, Response, Request
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timezone, timedelta
import urllib.parse
import json
import uuid
import secrets
import httpx
import jwt

from backend.core.database import get_db, AsyncSessionLocal
from backend.core.config import settings
from backend.core.security import get_password_hash, verify_password, create_access_token
from backend.models.organization import User, Organization, OrganizationMember, OrgRole
from backend.models.billing import LedgerTransaction, LedgerTransactionType
from backend.schemas.user import UserCreate, UserResponse, Token

router = APIRouter()

# ════════════════════════════════════════════════════════════
#  STATELESS + IN-MEMORY OAUTH STATE STORE
# ════════════════════════════════════════════════════════════
_oauth_states: dict = {}


def _create_oauth_state(provider: str) -> str:
    """Create a cryptographically signed stateless OAuth state token that survives container restarts and multi-instance scaling."""
    now = datetime.now(timezone.utc)
    payload = {
        "provider": provider,
        "nonce": secrets.token_urlsafe(12),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=15)).timestamp()),
    }
    state = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    _oauth_states[state] = {"provider": provider, "created_at": now.isoformat()}
    return state


def _verify_oauth_state(state: str, provider: str) -> bool:
    """Verify OAuth state via in-memory cache or stateless HMAC-SHA256 JWT signature."""
    if not state:
        return False
    if state in _oauth_states:
        _oauth_states.pop(state, None)
        return True
    try:
        payload = jwt.decode(state, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload.get("provider") == provider
    except Exception:
        return False


def _resolve_redirect_base(request: Request) -> str:
    """Resolve OAuth redirect base URL cleanly, stripping whitespace and auto-detecting domain behind Cloud Run proxy if needed."""
    configured = (settings.OAUTH_REDIRECT_BASE or "").strip().rstrip("/")
    if configured and "localhost" not in configured and "127.0.0.1" not in configured:
        return configured
    if request:
        host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
        proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"
        if host and "localhost" not in host and "127.0.0.1" not in host:
            return f"{proto}://{host}".rstrip("/")
    return configured or "http://localhost:8000"


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
    new_org = Organization(name=org_name, credit_balance=50)
    db.add(new_org)
    await db.flush()

    # SaaS Free Tier Welcome Grant (5 free missions = 50 credits)
    welcome_tx = LedgerTransaction(
        id=str(uuid.uuid4()),
        organization_id=new_org.id,
        type=LedgerTransactionType.TOPUP,
        amount=50,
        currency="credits",
        reference_id="welcome_grant",
        status="completed",
        metadata_json='{"reason": "free_tier_signup_grant", "missions_granted": 5}',
    )
    db.add(welcome_tx)

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
@router.post("/register", response_model=UserResponse)
async def signup(user_in: UserCreate, response: Response, db: AsyncSession = Depends(get_db)):
    if user_in.accepted_terms is False:
        raise HTTPException(
            status_code=400,
            detail="You must accept the Terms of Service and Autonomous Code Execution Agreement to register."
        )

    result = await db.execute(select(User).where(User.email == user_in.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")

    new_user = User(
        email=user_in.email,
        hashed_password=get_password_hash(user_in.password),
        full_name=user_in.full_name,
    )
    db.add(new_user)

    new_org = Organization(name=user_in.organization_name, credit_balance=50)
    db.add(new_org)
    await db.flush()

    # SaaS Free Tier Welcome Grant (5 free missions = 50 credits)
    welcome_tx = LedgerTransaction(
        id=str(uuid.uuid4()),
        organization_id=new_org.id,
        type=LedgerTransactionType.TOPUP,
        amount=50,
        currency="credits",
        reference_id="welcome_grant",
        status="completed",
        metadata_json='{"reason": "free_tier_signup_grant", "missions_granted": 5}',
    )
    db.add(welcome_tx)

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

    resp = UserResponse.model_validate(new_user)
    resp.access_token = token
    resp.token_type = "bearer"
    return resp


@router.post("/login", response_model=Token)
async def login(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db)
):
    content_type = request.headers.get("content-type", "")
    email = None
    password = None

    if "application/json" in content_type:
        try:
            body = await request.json()
            email = body.get("email") or body.get("username")
            password = body.get("password")
        except Exception:
            pass
    else:
        try:
            form = await request.form()
            email = form.get("username") or form.get("email")
            password = form.get("password")
        except Exception:
            pass

    if not email or not password:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Missing email or password"
        )

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if not user or user.hashed_password == "__oauth_no_password__":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not verify_password(password, user.hashed_password):
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
async def google_login(request: Request):
    client_id = (settings.GOOGLE_CLIENT_ID or "").strip()
    client_secret = (settings.GOOGLE_CLIENT_SECRET or "").strip()
    if not client_id or not client_secret:
        if settings.KOBITS_DEV_MODE:
            async with AsyncSessionLocal() as db:
                user = await _find_or_create_user_and_org(
                    db=db,
                    email="developer.google@gmail.com",
                    full_name="Google Verified Developer",
                    google_id="google_dev_109283746",
                    avatar_url="https://lh3.googleusercontent.com/a/default-user=s96-c",
                )
                session_token = create_access_token(subject=user.id)
                response = RedirectResponse(url="/web/index.html?onboarding=true#overview", status_code=302)
                _set_session_cookie(response, session_token)
                return response
        return RedirectResponse(url="/portal.html?auth_error=google_not_configured#login", status_code=302)

    state = _create_oauth_state("google")
    redirect_base = _resolve_redirect_base(request)
    redirect_uri = f"{redirect_base}/api/v1/auth/google/callback"
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }
    url = f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"
    return RedirectResponse(url=url, status_code=302)


@router.get("/google/callback")
async def google_callback(
    request: Request,
    code: str = None,
    state: str = None,
    error: str = None,
    db: AsyncSession = Depends(get_db),
):
    if error:
        return RedirectResponse(url="/portal.html?auth_error=google_cancelled#login", status_code=302)

    if not _verify_oauth_state(state, "google"):
        return RedirectResponse(url="/portal.html?auth_error=invalid_state#login", status_code=302)

    redirect_base = _resolve_redirect_base(request)
    redirect_uri = f"{redirect_base}/api/v1/auth/google/callback"
    client_id = (settings.GOOGLE_CLIENT_ID or "").strip()
    client_secret = (settings.GOOGLE_CLIENT_SECRET or "").strip()

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            token_resp = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            if token_resp.status_code != 200:
                return RedirectResponse(url="/portal.html?auth_error=google_token_failed#login", status_code=302)

            tokens = token_resp.json()
            access_token = tokens.get("access_token")

            userinfo_resp = await client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if userinfo_resp.status_code != 200:
                return RedirectResponse(url="/portal.html?auth_error=google_userinfo_failed#login", status_code=302)

            userinfo = userinfo_resp.json()
    except Exception:
        return RedirectResponse(url="/portal.html?auth_error=google_network_error#login", status_code=302)

    email = userinfo.get("email")
    if not email or not userinfo.get("verified_email", False):
        return RedirectResponse(url="/portal.html?auth_error=email_not_verified#login", status_code=302)

    user = await _find_or_create_user_and_org(
        db=db,
        email=email,
        full_name=userinfo.get("name"),
        google_id=str(userinfo.get("id")),
        avatar_url=userinfo.get("picture"),
    )

    session_token = create_access_token(subject=user.id)
    response = RedirectResponse(url="/web/index.html#overview", status_code=302)
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
async def github_login(request: Request):
    client_id = (settings.GITHUB_CLIENT_ID or "").strip()
    client_secret = (settings.GITHUB_CLIENT_SECRET or "").strip()
    if not client_id or not client_secret:
        if settings.KOBITS_DEV_MODE:
            async with AsyncSessionLocal() as db:
                user = await _find_or_create_user_and_org(
                    db=db,
                    email="developer.github@example.com",
                    full_name="GitHub Verified Developer",
                    github_id="github_dev_98234710",
                    avatar_url="https://avatars.githubusercontent.com/u/9919?v=4",
                )
                session_token = create_access_token(subject=user.id)
                response = RedirectResponse(url="/web/index.html?onboarding=true#overview", status_code=302)
                _set_session_cookie(response, session_token)
                return response
        return RedirectResponse(url="/portal.html?auth_error=github_not_configured#login", status_code=302)

    state = _create_oauth_state("github")
    redirect_base = _resolve_redirect_base(request)
    redirect_uri = f"{redirect_base}/api/v1/auth/github/callback"
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "user:email",
        "state": state,
    }
    url = f"{GITHUB_AUTH_URL}?{urllib.parse.urlencode(params)}"
    return RedirectResponse(url=url, status_code=302)


@router.get("/github/callback")
async def github_callback(
    request: Request,
    code: str = None,
    state: str = None,
    error: str = None,
    db: AsyncSession = Depends(get_db),
):
    if error:
        return RedirectResponse(url="/portal.html?auth_error=github_cancelled#login", status_code=302)

    if not _verify_oauth_state(state, "github"):
        return RedirectResponse(url="/portal.html?auth_error=invalid_state#login", status_code=302)

    client_id = (settings.GITHUB_CLIENT_ID or "").strip()
    client_secret = (settings.GITHUB_CLIENT_SECRET or "").strip()

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            token_resp = await client.post(
                GITHUB_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                headers={"Accept": "application/json"},
            )
            if token_resp.status_code != 200:
                return RedirectResponse(url="/portal.html?auth_error=github_token_failed#login", status_code=302)

            tokens = token_resp.json()
            gh_access_token = tokens.get("access_token")
            if not gh_access_token:
                return RedirectResponse(url="/portal.html?auth_error=github_token_failed#login", status_code=302)

            user_resp = await client.get(
                GITHUB_USER_URL,
                headers={"Authorization": f"Bearer {gh_access_token}", "Accept": "application/vnd.github.v3+json"},
            )
            if user_resp.status_code != 200:
                return RedirectResponse(url="/portal.html?auth_error=github_userinfo_failed#login", status_code=302)

            gh_user = user_resp.json()

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
                return RedirectResponse(url="/portal.html?auth_error=email_not_verified#login", status_code=302)
    except Exception:
        return RedirectResponse(url="/portal.html?auth_error=github_network_error#login", status_code=302)

    user = await _find_or_create_user_and_org(
        db=db,
        email=email,
        full_name=gh_user.get("name") or gh_user.get("login"),
        github_id=str(gh_user.get("id")),
        avatar_url=gh_user.get("avatar_url"),
    )

    session_token = create_access_token(subject=user.id)
    response = RedirectResponse(url="/web/index.html#overview", status_code=302)
    _set_session_cookie(response, session_token)
    return response
get_current_active_user = get_current_user_info
