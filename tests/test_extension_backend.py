"""
Tests for VS Code and IDE Extension Backend Endpoints (/api/v1/extension).
"""

import pytest
import uuid
from httpx import AsyncClient, ASGITransport

from backend.main import app
from backend.models.organization import User, Organization, OrganizationMember, OrgRole
from backend.models.mission import Mission, MissionStatus, WorkflowPhase
from backend.services.auth_service import create_api_key


@pytest.mark.asyncio
async def test_extension_status_and_dispatch(db_session):
    """Test /api/v1/extension/status and /api/v1/extension/dispatch flow."""
    user = User(id="user_ext_1", email="ide_dev@kobits.ai", full_name="IDE Developer")
    org = Organization(id="org_ext_1", name="IDE Innovation Lab", credit_balance=100)
    db_session.add_all([user, org])
    await db_session.flush()
    db_session.add(OrganizationMember(user_id=user.id, organization_id=org.id, role=OrgRole.OWNER))
    await db_session.commit()

    _, raw_key = await create_api_key(db_session, user.id, org.id, name="VSCode Extension Key")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Check status
        status_resp = await client.get("/api/v1/extension/status", headers={"X-API-Key": raw_key})
        assert status_resp.status_code == 200
        status_data = status_resp.json()
        assert status_data["authenticated"] is True
        assert status_data["organization"]["name"] == "IDE Innovation Lab"
        assert status_data["capabilities"]["side_by_side_diff"] is True

        # 2. Dispatch inline mission
        dispatch_resp = await client.post(
            "/api/v1/extension/dispatch",
            json={
                "prompt": "Fix null pointer dereference in auth token refresh handler",
                "file_path": "src/auth/jwt.py",
                "selection_range": "L24-L38",
                "code_context": "def refresh():\n    token = None\n    return token.decode()",
                "branch": "fix/jwt-deref",
                "execution_mode": "autopilot",
            },
            headers={"X-API-Key": raw_key},
        )
        assert dispatch_resp.status_code == 200
        disp_data = dispatch_resp.json()
        assert disp_data["status"] == "success"
        mission_id = disp_data["mission_id"]
        assert disp_data["active_branch"] == "fix/jwt-deref"
        assert f"/ws/terminal/{mission_id}" in disp_data["terminal_ws_url"]

        # 3. Check mission status via extension endpoint
        miss_status_resp = await client.get(
            f"/api/v1/extension/missions/{mission_id}/status",
            headers={"X-API-Key": raw_key},
        )
        assert miss_status_resp.status_code == 200
        m_stat = miss_status_resp.json()
        assert m_stat["id"] == mission_id
        assert m_stat["active_branch"] == "fix/jwt-deref"

        # 4. Check diff endpoint
        diff_resp = await client.get(
            f"/api/v1/extension/missions/{mission_id}/diff",
            headers={"X-API-Key": raw_key},
        )
        assert diff_resp.status_code == 200
        diff_data = diff_resp.json()
        assert "diff_text" in diff_data
        assert "has_changes" in diff_data

        # 5. Check apply endpoint
        apply_resp = await client.post(
            f"/api/v1/extension/missions/{mission_id}/apply",
            json={"dry_run": True},
            headers={"X-API-Key": raw_key},
        )
        assert apply_resp.status_code == 200
        apply_data = apply_resp.json()
        assert apply_data["status"] == "applied"
        assert apply_data["dry_run"] is True

        # 6. Telemetry endpoint
        telem_resp = await client.post(
            "/api/v1/extension/telemetry",
            json={"event": "inline_action_invoked", "metadata": {"lines": 14}},
            headers={"X-API-Key": raw_key},
        )
        assert telem_resp.status_code == 200
        assert telem_resp.json()["status"] == "ok"
