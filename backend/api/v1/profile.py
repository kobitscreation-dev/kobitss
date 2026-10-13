"""
Secure user-profile endpoints.
================================
All routes require a valid JWT (cookie or Authorization header).

Routes
------
    GET    /api/v1/profile/               – retrieve current user profile
    PUT    /api/v1/profile/               – update profile fields / change password
    DELETE /api/v1/profile/               – soft-delete account (is_active = False)
    POST   /api/v1/profile/change-password – dedicated password-change resource

Security
--------
- JWT authentication via ``get_current_active_user`` dependency on every route.
- bcrypt password verification (rounds=12).
- Soft-delete preserves audit record; subsequent requests return 401.
- Append-only AuditLog entries written atomically within each mutation transaction.
- POST /change-password is guarded by a per-user sliding-window rate limiter
  (5 attempts per 15 minutes) to prevent brute-force password changes.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_active_user
from backend.core.database import get_db
from backend.core.security import get_password_hash, verify_password
from backend.models.organization import User
from backend.schemas.profile import (
    PasswordChangeRequest,
    ProfileResponse,
    ProfileUpdateRequest,
)
from backend.services.profile_audit import ProfileAuditService
from backend.services.user_rate_limiter import ChangePasswordRateLimiter

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
#  GET /  –  Read profile
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/",
    response_model=ProfileResponse,
    summary="Retrieve the authenticated user's profile",
    status_code=status.HTTP_200_OK,
    tags=["Profile"],
)
async def get_profile(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> ProfileResponse:
    """Return all public profile fields for the currently authenticated user."""
    return ProfileResponse.model_validate(current_user)


# ─────────────────────────────────────────────────────────────────────────────
#  PUT /  –  Update profile
# ─────────────────────────────────────────────────────────────────────────────


@router.put(
    "/",
    response_model=ProfileResponse,
    summary="Update profile fields or change password",
    status_code=status.HTTP_200_OK,
    tags=["Profile"],
)
async def update_profile(
    payload: ProfileUpdateRequest,
    request: Request,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> ProfileResponse:
    """
    Partially update the authenticated user's profile.

    - Provide ``full_name`` and/or ``avatar_url`` to update display information.
    - Provide ``current_password`` + ``new_password`` to change the password.
    - At least one of the above must be non-null or a 422 is returned.

    Audit events produced
    ---------------------
    - ``profile.update`` — for full_name / avatar_url changes
    - ``profile.password_change`` — when new_password is provided
    """
    audit = ProfileAuditService(db, request)
    changed_fields: list[str] = []
    updated_any = False

    # ── Display name ──────────────────────────────────────────────────────────
    if payload.full_name is not None:
        current_user.full_name = payload.full_name.strip()
        changed_fields.append("full_name")
        updated_any = True

    # ── Avatar URL ────────────────────────────────────────────────────────────
    if payload.avatar_url is not None:
        current_user.avatar_url = str(payload.avatar_url)
        changed_fields.append("avatar_url")
        updated_any = True

    # ── Password change ───────────────────────────────────────────────────────
    if payload.new_password is not None:
        # current_password presence is already validated by the Pydantic schema;
        # here we verify it against the stored hash.
        if not current_user.hashed_password or not verify_password(
            payload.current_password, current_user.hashed_password
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is incorrect.",
            )
        current_user.hashed_password = get_password_hash(payload.new_password)
        changed_fields.append("password")
        updated_any = True

    if not updated_any:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No update fields were provided.",
        )

    # ── Audit ─────────────────────────────────────────────────────────────────
    non_pw_fields = [f for f in changed_fields if f != "password"]
    if non_pw_fields:
        audit.record_profile_update(str(current_user.id), non_pw_fields)
    if "password" in changed_fields:
        audit.record_password_change(str(current_user.id))

    await db.commit()
    await db.refresh(current_user)
    return ProfileResponse.model_validate(current_user)


# ─────────────────────────────────────────────────────────────────────────────
#  DELETE /  –  Soft-delete account
# ─────────────────────────────────────────────────────────────────────────────


@router.delete(
    "/",
    summary="Deactivate (soft-delete) the authenticated user's account",
    status_code=status.HTTP_200_OK,
    tags=["Profile"],
)
async def delete_account(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """
    Deactivate the account by setting ``is_active = False``.

    The record is retained in the database for audit purposes and GDPR export
    window compliance.  The user will be rejected by ``get_current_active_user``
    on all future requests, effectively revoking the session.

    Audit events produced
    ---------------------
    - ``account.deactivate``
    """
    audit = ProfileAuditService(db, request)
    audit.record_account_deactivate(str(current_user.id))

    current_user.is_active = False
    await db.commit()
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"message": "Account deleted successfully."},
    )


# ─────────────────────────────────────────────────────────────────────────────
#  POST /change-password  –  Dedicated password-change resource
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/change-password",
    summary="Change password via a dedicated sub-resource",
    status_code=status.HTTP_200_OK,
    tags=["Profile"],
)
async def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """
    Change password using the dedicated change-password flow.

    Requires:
    - ``current_password`` – verified against the stored bcrypt hash.
    - ``new_password`` + ``confirm_new_password`` – must match (schema-validated).

    Rate limiting
    -------------
    This endpoint is protected by a per-user sliding-window rate limiter:
    **5 attempts per 15 minutes** per user ID.  Exceeding the limit returns
    HTTP 429 with a ``Retry-After`` header.

    Audit events produced
    ---------------------
    - ``profile.password_change`` (via ``change_password_resource`` method)
    """
    user_id = str(current_user.id)

    # ── Rate limit check (before bcrypt to prevent timing attacks) ────────────
    ChangePasswordRateLimiter.check(user_id)

    # ── Verify current password ───────────────────────────────────────────────
    if not current_user.hashed_password or not verify_password(
        payload.current_password, current_user.hashed_password
    ):
        # Record failed attempt against the rate limiter before raising
        ChangePasswordRateLimiter.record(user_id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )

    # ── Apply new password ────────────────────────────────────────────────────
    current_user.hashed_password = get_password_hash(payload.new_password)

    # ── Audit ─────────────────────────────────────────────────────────────────
    audit = ProfileAuditService(db, request)
    audit.record_dedicated_password_change(user_id)

    await db.commit()

    # Reset rate limiter on successful change (legitimate user unblocked)
    ChangePasswordRateLimiter.reset(user_id)

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"message": "Password updated successfully."},
    )