"""
Comprehensive tests for Cryptographic API Keys (kb_live_...),
Multi-Tenant Boundary Lockdown, and Zero Cross-Contamination.
"""

import pytest
import uuid
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient, ASGITransport

from backend.main import app
from backend.models.api_key import ApiKey
from backend.models.organization import User, Organization, OrganizationMember, OrgRole
from backend.models.mission import Mission, MissionStatus, MissionPriority, WorkflowPhase
from backend.services.auth_service import create_api_key, verify_api_key, revoke_api_key


@pytest.mark.asyncio
async def test_api_key_generation_and_hashing():
    """Verify cryptographic entropy, prefix, and one-way SHA-256 hashing."""
    raw_key, prefix, hashed = ApiKey.generate_key()
    assert raw_key.startswith("kb_live_")
    assert len(raw_key) == 56  # 8 prefix + 48 hex chars
    assert prefix == raw_key[:12]
    assert hashed == ApiKey.hash_key(raw_key)
    # Ensure plaintext raw_key is NOT the hash
    assert raw_key != hashed


@pytest.mark.asyncio
async def test_api_key_service_lifecycle(db_session):
    """Test create, verify, last_used_at update, and revocation."""
    # Seed user and org
    user = User(id="user_test_1", email="alice@enterprise.com", full_name="Alice Engineer", hashed_password="pw")
    org = Organization(id="org_test_1", name="Enterprise Corp", credit_balance=500)
    db_session.add_all([user, org])
    await db_session.flush()

    membership = OrganizationMember(user_id=user.id, organization_id=org.id, role=OrgRole.OWNER)
    db_session.add(membership)
    await db_session.commit()

    # 1. Create API key
    key_obj, raw_key = await create_api_key(
        db_session,
        user_id=user.id,
        organization_id=org.id,
        name="MacBook CLI Key",
        scopes="read,write,cli",
        expires_in_days=30,
    )
    assert key_obj.id is not None
    assert key_obj.name == "MacBook CLI Key"
    assert key_obj.hashed_key == ApiKey.hash_key(raw_key)
    assert key_obj.is_active is True
    assert key_obj.last_used_at is None

    # 2. Verify valid key
    verified = await verify_api_key(db_session, raw_key)
    assert verified is not None
    assert verified.id == key_obj.id
    assert verified.last_used_at is not None

    # 3. Verify invalid key returns None
    invalid = await verify_api_key(db_session, "kb_live_invalidkey1234567890abcdef1234567890abcdef")
    assert invalid is None

    # 4. Revocation
    revoked = await revoke_api_key(db_session, key_obj.id, org.id)
    assert revoked is True

    # After revocation, verification fails
    assert await verify_api_key(db_session, raw_key) is None


@pytest.mark.asyncio
async def test_api_key_auth_via_rest_headers(db_session):
    """Verify HTTP requests with X-API-Key and Bearer kb_live_ authenticate successfully."""
    user = User(id="user_auth_1", email="bob@cloud.io", full_name="Bob Developer", hashed_password="pw")
    org = Organization(id="org_auth_1", name="Bob Tech", credit_balance=250)
    db_session.add_all([user, org])
    await db_session.flush()

    membership = OrganizationMember(user_id=user.id, organization_id=org.id, role=OrgRole.ADMIN)
    db_session.add(membership)
    await db_session.commit()

    key_obj, raw_key = await create_api_key(db_session, user.id, org.id, name="CLI Key")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # A. Unauthenticated request without headers fails when dev mode is disabled
        # (or authenticate with invalid key)
        resp_bad = await client.get("/api/v1/extension/status", headers={"X-API-Key": "kb_live_badbadbadbadbadbadbadbad"})
        assert resp_bad.status_code == 401

        # B. Authenticate via X-API-Key header
        resp_key_header = await client.get("/api/v1/extension/status", headers={"X-API-Key": raw_key})
        assert resp_key_header.status_code == 200
        data = resp_key_header.json()
        assert data["authenticated"] is True
        assert data["user"]["email"] == "bob@cloud.io"
        assert data["organization"]["name"] == "Bob Tech"
        assert data["organization"]["credit_balance"] == 250

        # C. Authenticate via Authorization: Bearer kb_live_... header
        resp_bearer = await client.get("/api/v1/extension/status", headers={"Authorization": f"Bearer {raw_key}"})
        assert resp_bearer.status_code == 200
        assert resp_bearer.json()["user"]["email"] == "bob@cloud.io"


@pytest.mark.asyncio
async def test_multi_tenant_isolation_lockdown(db_session):
    """
    CRITICAL ENTERPRISE AUDIT TEST:
    Assert Tenant A using Key A CANNOT read, list, or dispatch missions in Tenant B's workspace.
    """
    # Tenant A
    user_a = User(id="user_tenant_a", email="alice@company-a.com", full_name="Alice A")
    org_a = Organization(id="org_tenant_a", name="Company A Inc", credit_balance=100)
    db_session.add_all([user_a, org_a])
    await db_session.flush()
    db_session.add(OrganizationMember(user_id=user_a.id, organization_id=org_a.id, role=OrgRole.OWNER))

    # Tenant B
    user_b = User(id="user_tenant_b", email="mallory@company-b.com", full_name="Mallory B")
    org_b = Organization(id="org_tenant_b", name="Company B Corp", credit_balance=50)
    db_session.add_all([user_b, org_b])
    await db_session.flush()
    db_session.add(OrganizationMember(user_id=user_b.id, organization_id=org_b.id, role=OrgRole.OWNER))

    # Secret Mission in Tenant A
    mission_a = Mission(
        id=str(uuid.uuid4()),
        organization_id=org_a.id,
        project_id="proj_a",
        created_by=user_a.id,
        title="Top Secret Proprietary Engine A",
        objective="Confidential IP for Company A",
        status=MissionStatus.ACTIVE,
        phase=WorkflowPhase.IMPLEMENTATION,
    )
    db_session.add(mission_a)
    await db_session.commit()

    # Generate keys for each tenant
    key_a, raw_key_a = await create_api_key(db_session, user_a.id, org_a.id, name="Company A Key")
    key_b, raw_key_b = await create_api_key(db_session, user_b.id, org_b.id, name="Company B Key")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Tenant A can view Mission A status
        resp_a = await client.get(
            f"/api/v1/extension/missions/{mission_a.id}/status",
            headers={"X-API-Key": raw_key_a},
        )
        assert resp_a.status_code == 200
        assert resp_a.json()["title"] == "Top Secret Proprietary Engine A"

        # Tenant B using Key B attempts to access Mission A -> MUST BE STRICT 404 NOT FOUND
        resp_b = await client.get(
            f"/api/v1/extension/missions/{mission_a.id}/status",
            headers={"X-API-Key": raw_key_b},
        )
        assert resp_b.status_code == 404
        assert resp_b.json()["detail"] == "Mission not found"

        # Tenant B diff access to Mission A -> STRICT 404
        resp_diff_b = await client.get(
            f"/api/v1/extension/missions/{mission_a.id}/diff",
            headers={"X-API-Key": raw_key_b},
        )
        assert resp_diff_b.status_code == 404

        # Tenant B apply changes to Mission A -> STRICT 404
        resp_apply_b = await client.post(
            f"/api/v1/extension/missions/{mission_a.id}/apply",
            json={"dry_run": True},
            headers={"X-API-Key": raw_key_b},
        )
        assert resp_apply_b.status_code == 404


@pytest.mark.asyncio
async def test_api_keys_rest_crud(db_session):
    """Test REST API routes for creating, listing, and revoking API keys."""
    user = User(id="user_crud_1", email="dev@platform.org", full_name="Platform Engineer")
    org = Organization(id="org_crud_1", name="Platform Org", credit_balance=300)
    db_session.add_all([user, org])
    await db_session.flush()
    db_session.add(OrganizationMember(user_id=user.id, organization_id=org.id, role=OrgRole.OWNER))
    await db_session.commit()

    # Generate bootstrap key
    _, raw_admin_key = await create_api_key(db_session, user.id, org.id, name="Admin Key")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create key via POST
        create_resp = await client.post(
            "/api/v1/auth/api-keys",
            json={"name": "GitHub Actions Key", "scopes": "read,write,cli", "expires_in_days": 90},
            headers={"X-API-Key": raw_admin_key},
        )
        assert create_resp.status_code == 200
        new_key_data = create_resp.json()
        assert new_key_data["name"] == "GitHub Actions Key"
        assert new_key_data["key"].startswith("kb_live_")
        created_id = new_key_data["id"]

        # List keys via GET
        list_resp = await client.get("/api/v1/auth/api-keys", headers={"X-API-Key": raw_admin_key})
        assert list_resp.status_code == 200
        keys_list = list_resp.json()
        assert len(keys_list) >= 2
        # Ensure raw key is NOT exposed in list response
        for k in keys_list:
            assert "key" not in k or k.get("key") is None
            assert "key_prefix" in k

        # Revoke key via DELETE
        del_resp = await client.delete(f"/api/v1/auth/api-keys/{created_id}", headers={"X-API-Key": raw_admin_key})
        assert del_resp.status_code == 200
        assert del_resp.json()["status"] == "success"
