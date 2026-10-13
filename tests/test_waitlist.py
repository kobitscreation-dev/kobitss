import pytest
from httpx import AsyncClient, ASGITransport
from backend.main import app
from backend.models.waitlist import WaitlistLead
from backend.models.organization import User


@pytest.mark.asyncio
async def test_waitlist_apply_success(db_session):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/api/v1/waitlist/apply",
            json={
                "email": "pilot@example.com",
                "full_name": "Ada Lovelace",
                "company": "Analytical Engine Co",
                "use_case": "Automated regression refactoring",
                "github_handle": "ada-love"
            }
        )
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "success"
        assert data["email"] == "pilot@example.com"

        # Duplicate submission is handled idempotently
        response2 = await ac.post(
            "/api/v1/waitlist/apply",
            json={
                "email": "pilot@example.com",
                "full_name": "Ada Lovelace"
            }
        )
        assert response2.status_code == 201
        assert response2.json()["status"] == "received"
