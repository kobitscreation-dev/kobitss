"""
Tests for Frontend Settings API Key Vault End-to-End Workflow.
Simulates the exact API calls made by web/js/views/settings.js when
generating, listing, and revoking API keys.
"""

import pytest
from httpx import AsyncClient, ASGITransport

from backend.main import app
from backend.models.organization import User, Organization, OrganizationMember, OrgRole
from backend.services.auth_service import create_api_key


@pytest.mark.asyncio
async def test_frontend_settings_api_keys_workflow(db_session):
    """
    Simulate full frontend user journey in Settings:
    1. User authenticates with an initial admin key / token.
    2. User views active keys -> lists existing keys.
    3. User clicks 'Generate New Key' with name 'VS Code Laptop' and 90-day expiry.
    4. Server generates 'kb_live_...' key and persists hash.
    5. User lists keys again -> sees new key with prefix, but raw secret is NEVER returned.
    6. User revokes key -> status updates to inactive.
    7. Revoked key immediately fails authentication with 401.
    """
    user = User(id="user_settings_1", email="lead@startup.io", full_name="Engineering Lead")
    org = Organization(id="org_settings_1", name="Startup Workspace", credit_balance=100)
    db_session.add_all([user, org])
    await db_session.flush()
    db_session.add(OrganizationMember(user_id=user.id, organization_id=org.id, role=OrgRole.OWNER))
    await db_session.commit()

    _, bootstrap_key = await create_api_key(db_session, user.id, org.id, name="Bootstrap Key")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Step 2: List keys
        list1 = await client.get("/api/v1/auth/api-keys", headers={"X-API-Key": bootstrap_key})
        assert list1.status_code == 200
        assert len(list1.json()) >= 1

        # Step 3 & 4: Generate new key from Settings modal
        create_resp = await client.post(
            "/api/v1/auth/api-keys",
            json={"name": "VS Code Laptop", "scopes": "read,write,cli,extension", "expires_in_days": 90},
            headers={"X-API-Key": bootstrap_key},
        )
        assert create_resp.status_code == 200
        created_data = create_resp.json()
        assert created_data["name"] == "VS Code Laptop"
        raw_laptop_key = created_data["key"]
        laptop_key_id = created_data["id"]
        assert raw_laptop_key.startswith("kb_live_")

        # Step 5: Verify laptop key can authenticate
        laptop_auth_resp = await client.get("/api/v1/extension/status", headers={"X-API-Key": raw_laptop_key})
        assert laptop_auth_resp.status_code == 200
        assert laptop_auth_resp.json()["organization"]["name"] == "Startup Workspace"

        # List keys again: check security (raw key not present in listing)
        list2 = await client.get("/api/v1/auth/api-keys", headers={"X-API-Key": bootstrap_key})
        assert list2.status_code == 200
        keys_after = list2.json()
        laptop_entry = next((k for k in keys_after if k["id"] == laptop_key_id), None)
        assert laptop_entry is not None
        assert "key" not in laptop_entry
        assert laptop_entry["key_prefix"] == raw_laptop_key[:12]
        assert laptop_entry["is_active"] is True

        # Step 6: Revoke key from Settings table
        revoke_resp = await client.delete(f"/api/v1/auth/api-keys/{laptop_key_id}", headers={"X-API-Key": bootstrap_key})
        assert revoke_resp.status_code == 200
        assert revoke_resp.json()["status"] == "success"

        # Step 7: Revoked key immediately returns 401
        laptop_failed_auth = await client.get("/api/v1/extension/status", headers={"X-API-Key": raw_laptop_key})
        assert laptop_failed_auth.status_code == 401
        assert "Invalid or revoked API key" in laptop_failed_auth.json()["detail"]
