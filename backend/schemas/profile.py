"""
Pydantic v2 schemas for the user profile endpoint.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import (
    BaseModel,
    EmailStr,
    Field,
    HttpUrl,
    model_validator,
)


class ProfileResponse(BaseModel):
    """Full read-only view of the authenticated user's profile."""

    id: str
    email: EmailStr
    full_name: Optional[str] = None
    avatar_url: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProfileUpdateRequest(BaseModel):
    """
    Partial update payload for the authenticated user's profile.

    Rules:
    - At least one field must be provided.
    - If ``new_password`` is given, ``current_password`` is required for verification.
    """

    full_name: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=120,
        description="Display name (1-120 chars).",
    )
    avatar_url: Optional[HttpUrl] = Field(
        default=None,
        description="Publicly-accessible URL to the user's avatar image.",
    )
    current_password: Optional[str] = Field(
        default=None,
        description="Current password – required when changing password.",
    )
    new_password: Optional[str] = Field(
        default=None,
        min_length=8,
        max_length=128,
        description="New password (8-128 chars).",
    )

    @model_validator(mode="after")
    def _validate_password_pair(self) -> "ProfileUpdateRequest":
        """Ensure current_password accompanies new_password."""
        if self.new_password and not self.current_password:
            raise ValueError(
                "current_password is required when setting a new_password."
            )
        return self


class PasswordChangeRequest(BaseModel):
    """Dedicated payload for the /change-password sub-resource."""

    current_password: str = Field(description="The user's existing password.")
    new_password: str = Field(
        min_length=8,
        max_length=128,
        description="Desired new password (8-128 chars).",
    )
    confirm_new_password: str = Field(
        description="Must match new_password exactly.",
    )

    @model_validator(mode="after")
    def _passwords_must_match(self) -> "PasswordChangeRequest":
        if self.new_password != self.confirm_new_password:
            raise ValueError("new_password and confirm_new_password do not match.")
        return self