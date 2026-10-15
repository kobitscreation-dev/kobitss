import asyncio
import json
import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from backend.main import app
from backend.core.database import get_db
import backend.core.database as db_mod
from backend.api.deps import get_current_active_user
from backend.models.base import Base
from backend.models.organization import User, Organization, OrganizationMember
from backend.models.project import Project, ProjectStatus
from backend.models.mission import Mission, MissionStatus, WorkflowPhase
from backend.services.websocket_manager import manager


DB_PATH = "./test_dogfooding.db"
test_engine = create_async_engine(f"sqlite+aiosqlite:///{DB_PATH}", echo=False)
TestSession = sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

NOW = datetime(2026, 10, 15, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
async def dogfood_env():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    u_id = f"user_{uuid.uuid4().hex[:6]}"
    user = User(id=u_id, email=f"{u_id}@dogfood.test", full_name="Dogfood Tester", hashed_password="pw")

    async with TestSession() as s:
        org = Organization(id=f"org_{u_id}", name="Dogfood Workspace", credit_balance=50)
        s.add_all([user, org, OrganizationMember(organization_id=org.id, user_id=user.id, role="OWNER")])
        await s.commit()

    async def _override_db():
        async with TestSession() as s:
            yield s

    async def _override_user():
        return user

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_active_user] = _override_user
    orig_session_local = db_mod.AsyncSessionLocal
    db_mod.AsyncSessionLocal = TestSession

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, user, org.id

    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_active_user, None)
    db_mod.AsyncSessionLocal = orig_session_local


@pytest.mark.asyncio
async def test_project_auto_provisioning_on_empty_org(dogfood_env):
    """Ensure personal org and default project are automatically created so the user is never blocked."""
    client, user, org_id = dogfood_env

    # 1. Calling /api/v1/projects with 0 existing projects auto-provisions Primary Engineering Workspace
    res = await client.get("/api/v1/projects")
    assert res.status_code == 200, res.text
    projects = res.json()
    assert len(projects) >= 1
    assert projects[0]["name"] == "Primary Engineering Workspace"
    proj_id = projects[0]["id"]

    # 2. GET /api/v1/projects/default returns the auto-provisioned workspace
    res_def = await client.get("/api/v1/projects/default")
    assert res_def.status_code == 200
    assert res_def.json()["id"] == proj_id

    # 3. GET /api/v1/org/dashboard returns the workspace
    res_dash = await client.get("/api/v1/org/dashboard")
    assert res_dash.status_code == 200
    dash_data = res_dash.json()
    assert dash_data["project"]["id"] == proj_id


@pytest.mark.asyncio
async def test_mission_creation_modal_endpoints(dogfood_env):
    """Verify both POST /api/v1/missions/ and POST /api/v1/missions/projects/{pid}/missions work without mocks."""
    client, user, org_id = dogfood_env

    # Path A: Direct intake POST /api/v1/missions/
    res_root = await client.post(
        "/api/v1/missions/",
        json={
            "title": "Dogfood Root Mission",
            "objective": "Verify direct intake endpoint with autopilot mode",
            "branch": "main",
            "execution_mode": "autopilot",
            "auto_execute": False,
        },
    )
    assert res_root.status_code == 201, res_root.text
    data_root = res_root.json()
    assert data_root["title"] == "Dogfood Root Mission"
    assert "mission_id" in data_root

    # Path B: Project-specific intake POST /api/v1/missions/projects/{pid}/missions with pid='default'
    res_proj = await client.post(
        "/api/v1/missions/projects/default/missions",
        json={
            "title": "Dogfood Project Mission",
            "objective": "Verify project path intake endpoint with autopilot mode",
            "branch": "main",
            "execution_mode": "autopilot",
            "auto_execute": False,
        },
    )
    assert res_proj.status_code == 201, res_proj.text
    data_proj = res_proj.json()
    assert data_proj["title"] == "Dogfood Project Mission"
    assert "mission_id" in data_proj


@pytest.mark.asyncio
async def test_diff_and_apply_flow(dogfood_env):
    """Ensure GET /diff returns diff_text and POST /apply executes cleanly."""
    client, user, org_id = dogfood_env

    # Create a mission
    res = await client.post(
        "/api/v1/missions/",
        json={
            "title": "Diff & Apply Mission",
            "objective": "Verify diff rendering and codebase apply",
            "auto_execute": False,
        },
    )
    assert res.status_code == 201
    m_id = res.json()["mission_id"]

    # GET /diff
    diff_res = await client.get(f"/api/v1/missions/{m_id}/diff")
    assert diff_res.status_code == 200
    diff_data = diff_res.json()
    assert "diff_text" in diff_data
    assert "files" in diff_data
    assert isinstance(diff_data["diff_text"], str)

    # POST /apply (dry run)
    apply_res = await client.post(f"/api/v1/missions/{m_id}/apply", json={"dry_run": True, "force": False})
    assert apply_res.status_code == 200
    assert "status" in apply_res.json()


@pytest.mark.asyncio
async def test_preview_placeholder_endpoint(dogfood_env):
    """Ensure GET /api/v1/preview/{mission_id}/ serves HTML placeholder without auth errors."""
    client, user, org_id = dogfood_env

    # Create a mission
    res = await client.post(
        "/api/v1/missions/",
        json={"title": "Preview Test", "objective": "Test preview iframe rendering", "auto_execute": False},
    )
    assert res.status_code == 201
    m_id = res.json()["mission_id"]

    # Preview iframe URL
    prev_res = await client.get(f"/api/v1/preview/{m_id}/")
    assert prev_res.status_code == 200
    assert "text/html" in prev_res.headers.get("content-type", "")
    assert "Building" in prev_res.text or "Preview" in prev_res.text

    # Preview info API
    info_res = await client.get(f"/api/v1/preview/{m_id}/info")
    assert info_res.status_code == 200
    assert info_res.json()["mission_id"] == m_id
