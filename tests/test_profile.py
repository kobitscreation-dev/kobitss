"""
Comprehensive integration tests for the /api/v1/profile endpoints.

Covers:
  - Unauthenticated access (401)
  - GET profile (authenticated)
  - PUT profile – field updates, password changes, validation errors
  - POST /change-password – success, mismatch, wrong current password
  - DELETE (soft-delete) account

All tests run against an in-memory SQLite test database spun up by the
conftest.py setup_db fixture (autouse).
"""
from __future__ import annotations

import os

# ── Must be set before any backend module is imported ──────────────────────
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_kobits.db")

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

# Import the FastAPI application AFTER setting the DB env var
from backend.main import app  # noqa: E402

pytestmark = pytest.mark.asyncio

# ─────────────────────────────────────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────────────────────────────────────

_BASE_URL = "http://test"
_REGISTER_URL = "/api/v1/auth/register"
_LOGIN_URL = "/api/v1/auth/login"
_PROFILE_URL = "/api/v1/profile/"
_CHANGE_PW_URL = "/api/v1/profile/change-password"

_EMAIL = "testprofile@example.com"
_PASSWORD = "TestPass123!"
_FULL_NAME = "Test User"
_ORG = "Test Org"


# ─────────────────────────────────────────────────────────────────────────────
#  Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def registered_user() -> dict:
    """Register a fresh user and return the registration response JSON."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=_BASE_URL
    ) as client:
        resp = await client.post(
            _REGISTER_URL,
            json={
                "email": _EMAIL,
                "password": _PASSWORD,
                "full_name": _FULL_NAME,
                "organization_name": _ORG,
            },
        )
        assert resp.status_code in (200, 201), (
            f"Registration failed ({resp.status_code}): {resp.text}"
        )
        return resp.json()


@pytest_asyncio.fixture
async def auth_headers(registered_user: dict) -> dict:  # noqa: F811
    """Log in as the registered user and return Bearer auth headers."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=_BASE_URL
    ) as client:
        resp = await client.post(
            _LOGIN_URL,
            data={"username": _EMAIL, "password": _PASSWORD},
        )
        assert resp.status_code == 200, (
            f"Login failed ({resp.status_code}): {resp.text}"
        )
        token = resp.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}


# ─────────────────────────────────────────────────────────────────────────────
#  1. Unauthenticated access → 401
# ─────────────────────────────────────────────────────────────────────────────


async def test_get_profile_unauthenticated():
    """
    GET /api/v1/profile/ without a token must return 401.

    KOBITS_DEV_MODE bypasses auth in the local .env; we force it off for this
    test so that the auth guard is exercised properly.
    """
    from backend.core.config import settings

    original_dev_mode = settings.KOBITS_DEV_MODE
    settings.KOBITS_DEV_MODE = False
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=_BASE_URL
        ) as client:
            resp = await client.get(_PROFILE_URL)
        assert resp.status_code == 401, (
            f"Expected 401 without token (KOBITS_DEV_MODE=False), got {resp.status_code}: {resp.text}"
        )
    finally:
        settings.KOBITS_DEV_MODE = original_dev_mode


# ─────────────────────────────────────────────────────────────────────────────
#  2. Authenticated GET profile
# ─────────────────────────────────────────────────────────────────────────────


async def test_get_profile_authenticated(registered_user: dict, auth_headers: dict):
    """GET /api/v1/profile/ with valid token returns the user's profile."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=_BASE_URL
    ) as client:
        resp = await client.get(_PROFILE_URL, headers=auth_headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["email"] == _EMAIL
    assert body["full_name"] == _FULL_NAME
    assert body["is_active"] is True
    assert "id" in body
    assert "created_at" in body
    assert "updated_at" in body


# ─────────────────────────────────────────────────────────────────────────────
#  3. PUT – update full_name
# ─────────────────────────────────────────────────────────────────────────────


async def test_update_profile_full_name(registered_user: dict, auth_headers: dict):
    """PUT /api/v1/profile/ with a new full_name should update and return it."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=_BASE_URL
    ) as client:
        resp = await client.put(
            _PROFILE_URL,
            json={"full_name": "Updated Name"},
            headers=auth_headers,
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["full_name"] == "Updated Name"


# ─────────────────────────────────────────────────────────────────────────────
#  4. PUT – no fields provided → 422
# ─────────────────────────────────────────────────────────────────────────────


async def test_update_profile_no_fields(registered_user: dict, auth_headers: dict):
    """PUT with an empty payload (no update fields) must return 422."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=_BASE_URL
    ) as client:
        resp = await client.put(
            _PROFILE_URL,
            json={},
            headers=auth_headers,
        )

    assert resp.status_code == 422, resp.text


# ─────────────────────────────────────────────────────────────────────────────
#  5. PUT – successful password change via profile update
# ─────────────────────────────────────────────────────────────────────────────


async def test_update_profile_password_change_success(
    registered_user: dict, auth_headers: dict
):
    """
    PUT /api/v1/profile/ with current_password + new_password should succeed,
    and the user should then be able to log in with the new password.
    """
    new_pw = "NewSecure456!"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=_BASE_URL
    ) as client:
        # Change password
        resp = await client.put(
            _PROFILE_URL,
            json={"current_password": _PASSWORD, "new_password": new_pw},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text

        # Log in with new password
        login_resp = await client.post(
            _LOGIN_URL,
            data={"username": _EMAIL, "password": new_pw},
        )
    assert login_resp.status_code == 200, login_resp.text
    assert "access_token" in login_resp.json()


# ─────────────────────────────────────────────────────────────────────────────
#  6. PUT – wrong current password → 400
# ─────────────────────────────────────────────────────────────────────────────


async def test_update_profile_password_wrong_current(
    registered_user: dict, auth_headers: dict
):
    """PUT with an incorrect current_password must return 400."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=_BASE_URL
    ) as client:
        resp = await client.put(
            _PROFILE_URL,
            json={"current_password": "WrongPass!", "new_password": "NewSecure456!"},
            headers=auth_headers,
        )

    assert resp.status_code == 400, resp.text
    assert "incorrect" in resp.json()["detail"].lower()


# ─────────────────────────────────────────────────────────────────────────────
#  7. PUT – new_password without current_password → 422 (schema validation)
# ─────────────────────────────────────────────────────────────────────────────


async def test_update_profile_new_password_without_current(
    registered_user: dict, auth_headers: dict
):
    """Providing new_password without current_password must fail at validation (422)."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=_BASE_URL
    ) as client:
        resp = await client.put(
            _PROFILE_URL,
            json={"new_password": "NewSecure456!"},
            headers=auth_headers,
        )

    assert resp.status_code == 422, resp.text


# ─────────────────────────────────────────────────────────────────────────────
#  8. POST /change-password – success
# ─────────────────────────────────────────────────────────────────────────────


async def test_change_password_success(registered_user: dict, auth_headers: dict):
    """POST /api/v1/profile/change-password with valid payload must return 200."""
    new_pw = "ChangedPass789!"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=_BASE_URL
    ) as client:
        resp = await client.post(
            _CHANGE_PW_URL,
            json={
                "current_password": _PASSWORD,
                "new_password": new_pw,
                "confirm_new_password": new_pw,
            },
            headers=auth_headers,
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["message"] == "Password updated successfully."


# ─────────────────────────────────────────────────────────────────────────────
#  9. POST /change-password – mismatched new/confirm → 422
# ─────────────────────────────────────────────────────────────────────────────


async def test_change_password_mismatch(registered_user: dict, auth_headers: dict):
    """Mismatched new_password / confirm_new_password must return 422."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=_BASE_URL
    ) as client:
        resp = await client.post(
            _CHANGE_PW_URL,
            json={
                "current_password": _PASSWORD,
                "new_password": "NewSecure456!",
                "confirm_new_password": "DIFFERENT999!",
            },
            headers=auth_headers,
        )

    assert resp.status_code == 422, resp.text


# ─────────────────────────────────────────────────────────────────────────────
#  10. POST /change-password – wrong current password → 400
# ─────────────────────────────────────────────────────────────────────────────


async def test_change_password_wrong_current(registered_user: dict, auth_headers: dict):
    """Incorrect current_password on /change-password must return 400."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=_BASE_URL
    ) as client:
        resp = await client.post(
            _CHANGE_PW_URL,
            json={
                "current_password": "CompletelyWrong!",
                "new_password": "NewSecure456!",
                "confirm_new_password": "NewSecure456!",
            },
            headers=auth_headers,
        )

    assert resp.status_code == 400, resp.text
    assert "incorrect" in resp.json()["detail"].lower()


# ─────────────────────────────────────────────────────────────────────────────
#  11. DELETE – soft-delete account
# ─────────────────────────────────────────────────────────────────────────────


async def test_delete_account(registered_user: dict, auth_headers: dict):
    """
    DELETE /api/v1/profile/ should:
    1. Return 200 with a confirmation message.
    2. Cause subsequent authenticated requests to fail with 401 (inactive user).
    """
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=_BASE_URL
    ) as client:
        # Soft-delete the account
        del_resp = await client.delete(_PROFILE_URL, headers=auth_headers)
        assert del_resp.status_code == 200, del_resp.text
        assert del_resp.json()["message"] == "Account deleted successfully."

        # Token is still structurally valid but user is inactive → 401
        get_resp = await client.get(_PROFILE_URL, headers=auth_headers)

    assert get_resp.status_code == 401, (
        f"Expected 401 for inactive account, got {get_resp.status_code}: {get_resp.text}"
    )