"""
Secure user-profile endpoints.

Routes (all require a valid JWT):
    GET    /api/v1/profile/               – retrieve current user profile
    PUT    /api/v1/profile/               – update profile fields / change password
    DELETE /api/v1/profile/               – soft-delete account (is_active = False)
    POST   /api/v1/profile/change-password – dedicated password-change resource
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

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
#  GET /  –  Read profile
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/",
    response_model=ProfileResponse,
    summary="Retrieve the authenticated user's profile",
    status_code=status.HTTP_200_OK,
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
    """
    updated_any = False

    # ── Display name ──────────────────────────────────────────────────────────
    if payload.full_name is not None:
        current_user.full_name = payload.full_name.strip()
        updated_any = True

    # ── Avatar URL ────────────────────────────────────────────────────────────
    if payload.avatar_url is not None:
        current_user.avatar_url = str(payload.avatar_url)
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
        updated_any = True

    if not updated_any:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No update fields were provided.",
        )

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
)
async def delete_account(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """
    Deactivate the account by setting ``is_active = False``.

    The record is retained in the database for audit purposes.
    The user will be rejected by ``get_current_active_user`` on future requests.
    """
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
    - ``current_password`` – verified against the stored hash.
    - ``new_password`` + ``confirm_new_password`` – must match (validated by schema).
    """
    if not current_user.hashed_password or not verify_password(
        payload.current_password, current_user.hashed_password
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )

    current_user.hashed_password = get_password_hash(payload.new_password)
    await db.commit()
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"message": "Password updated successfully."},
    )