import os
import shutil
import tempfile
import uuid
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, MagicMock

from backend.main import app
from backend.core.database import AsyncSessionLocal
from backend.models.organization import Organization, User, OrganizationMember
from backend.models.project import Project
from backend.models.mission import Mission, MissionStatus, WorkflowPhase
from backend.services.sandbox_manager import SandboxManager, SandboxSession


@pytest.fixture
async def setup_preview_mission(tmp_path):
    """Setup a temporary sandbox folder and corresponding mission in DB."""
    u_suffix = uuid.uuid4().hex[:6]
    sandbox_dir = tmp_path / f"sandbox_preview_{u_suffix}"
    sandbox_dir.mkdir(parents=True, exist_ok=True)

    # Create dummy static files inside sandbox
    index_html = sandbox_dir / "index.html"
    index_html.write_text("<!DOCTYPE html><html><body><h1>Kobits Preview App</h1></body></html>", encoding="utf-8")

    js_file = sandbox_dir / "bundle.js"
    js_file.write_text("console.log('Kobits Preview JS');", encoding="utf-8")

    css_file = sandbox_dir / "style.css"
    css_file.write_text("body { background: #000; color: #fff; }", encoding="utf-8")

    json_file = sandbox_dir / "config.json"
    json_file.write_text('{"app": "Kobits Demo", "version": "1.0.0"}', encoding="utf-8")

    async with AsyncSessionLocal() as db:
        user = User(email=f"preview_{u_suffix}@kobits.space", full_name="Preview Tester", hashed_password="pw")
        db.add(user)
        await db.commit()
        await db.refresh(user)

        org = Organization(name=f"Preview Org {u_suffix}")
        db.add(org)
        await db.commit()
        await db.refresh(org)

        member = OrganizationMember(organization_id=org.id, user_id=user.id, role="OWNER")
        db.add(member)

        project = Project(name="Preview Project", organization_id=org.id, created_by=user.id)
        db.add(project)
        await db.commit()
        await db.refresh(project)

        mission = Mission(
            organization_id=org.id,
            project_id=project.id,
            title="Preview Test Mission",
            objective="Build a reactive web app with live preview",
            status=MissionStatus.EXECUTING,
            phase=WorkflowPhase.IMPLEMENTATION,
            active_branch=f"feature/preview-{u_suffix}",
            created_by=user.id,
        )
        db.add(mission)
        await db.commit()
        await db.refresh(mission)

    # Register an active sandbox session in SandboxManager
    session = SandboxSession(
        session_id=mission.id,
        project_root=str(sandbox_dir),
        sandbox_dir=str(sandbox_dir),
        branch_name=mission.active_branch,
    )
    SandboxManager._sessions[mission.id] = session

    try:
        yield {
            "mission": mission,
            "sandbox_dir": str(sandbox_dir),
            "user": user,
            "project": project,
        }
    finally:
        SandboxManager._sessions.pop(mission.id, None)


@pytest.mark.asyncio
async def test_preview_info_nonexistent_mission():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/preview/non-existent-mission-{uuid.uuid4().hex}/info")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_preview_info_no_sandbox():
    u_suffix = uuid.uuid4().hex[:6]
    async with AsyncSessionLocal() as db:
        user = User(email=f"nosandbox_{u_suffix}@kobits.space", full_name="No Sandbox Tester", hashed_password="pw")
        db.add(user)
        await db.commit()
        await db.refresh(user)

        org = Organization(name=f"No Sandbox Org {u_suffix}")
        db.add(org)
        await db.commit()
        await db.refresh(org)

        project = Project(name="No Sandbox Project", organization_id=org.id, created_by=user.id)
        db.add(project)
        await db.commit()
        await db.refresh(project)

        mission = Mission(
            organization_id=org.id,
            project_id=project.id,
            title="No Sandbox Mission",
            objective="Initial prompt",
            status=MissionStatus.PLANNING,
            phase=WorkflowPhase.ANALYSIS,
            active_branch=f"feature/none-{u_suffix}",
            created_by=user.id,
        )
        db.add(mission)
        await db.commit()
        await db.refresh(mission)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/preview/{mission.id}/info")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mission_id"] == mission.id
        assert data["sandbox_found"] is False
        assert data["status"] == "pending_creation"

        # Serving preview asset without sandbox should return the placeholder page
        asset_resp = await ac.get(f"/api/v1/preview/{mission.id}/")
        assert asset_resp.status_code == 200
        assert "Ephemeral Web Preview Initializing" in asset_resp.text


@pytest.mark.asyncio
async def test_preview_info_ready(setup_preview_mission):
    data = setup_preview_mission
    mission = data["mission"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/preview/{mission.id}/info")
        assert resp.status_code == 200
        info = resp.json()
        assert info["sandbox_found"] is True
        assert info["status"] == "ready"
        assert info["entry_point"] == "index.html"
        assert info["files_count"] >= 4
        assert "index.html" in info["files_sample"]


@pytest.mark.asyncio
async def test_preview_serve_static_assets(setup_preview_mission):
    data = setup_preview_mission
    mission = data["mission"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Root / index.html
        resp = await ac.get(f"/api/v1/preview/{mission.id}/")
        assert resp.status_code == 200
        assert "Kobits Preview App" in resp.text
        assert "text/html" in resp.headers["content-type"]
        assert resp.headers.get("x-kobits-preview") == "sandbox-microvm"

        # 2. JavaScript file
        resp_js = await ac.get(f"/api/v1/preview/{mission.id}/bundle.js")
        assert resp_js.status_code == 200
        assert "Kobits Preview JS" in resp_js.text
        assert "application/javascript" in resp_js.headers["content-type"]

        # 3. CSS file
        resp_css = await ac.get(f"/api/v1/preview/{mission.id}/style.css")
        assert resp_css.status_code == 200
        assert "background: #000" in resp_css.text
        assert "text/css" in resp_css.headers["content-type"]

        # 4. JSON file
        resp_json = await ac.get(f"/api/v1/preview/{mission.id}/config.json")
        assert resp_json.status_code == 200
        assert resp_json.json()["app"] == "Kobits Demo"
        assert "application/json" in resp_json.headers["content-type"]


@pytest.mark.asyncio
async def test_preview_directory_traversal_protection(setup_preview_mission):
    from fastapi import HTTPException
    from backend.api.v1.preview import serve_preview_asset

    data = setup_preview_mission
    mission = data["mission"]

    async with AsyncSessionLocal() as db:
        with pytest.raises(HTTPException) as exc_info:
            await serve_preview_asset(mission_id=mission.id, file_path="../secret.txt", db=db)
        assert exc_info.value.status_code == 403
        assert "Directory traversal" in exc_info.value.detail

        with pytest.raises(HTTPException) as exc_info2:
            await serve_preview_asset(mission_id=mission.id, file_path="nested/../../secret.txt", db=db)
        assert exc_info2.value.status_code == 403


@pytest.mark.asyncio
async def test_preview_spa_fallback(setup_preview_mission):
    data = setup_preview_mission
    mission = data["mission"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Route without extension (client-side route) should return index.html
        resp = await ac.get(f"/api/v1/preview/{mission.id}/app/dashboard/profile")
        assert resp.status_code == 200
        assert "Kobits Preview App" in resp.text

        # Route WITH extension that doesn't exist should return 404
        resp_404 = await ac.get(f"/api/v1/preview/{mission.id}/images/missing_logo.png")
        assert resp_404.status_code == 404


@pytest.mark.asyncio
async def test_preview_port_configuration_and_proxy(setup_preview_mission):
    from unittest.mock import AsyncMock
    data = setup_preview_mission
    mission = data["mission"]
    sandbox_dir = data["sandbox_dir"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Configure port
        resp = await ac.post(
            f"/api/v1/preview/{mission.id}/port",
            json={"port": 5173, "active": True},
        )
        assert resp.status_code == 200
        assert resp.json()["port"] == 5173
        assert resp.json()["active"] is True

        # Verify config was written to .kobits_preview.json
        cfg_file = os.path.join(sandbox_dir, ".kobits_preview.json")
        assert os.path.isfile(cfg_file)

        # Verify info reflects dev_server
        info_resp = await ac.get(f"/api/v1/preview/{mission.id}/info")
        assert info_resp.json()["dev_server"]["active"] is True
        assert info_resp.json()["dev_server"]["port"] == 5173

        # Test proxy behavior with mock response targeting preview's httpx client
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"<html>Dev Server Active</html>"
        mock_response.headers = {"content-type": "text/html; charset=utf-8"}

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("backend.api.v1.preview.httpx.AsyncClient", return_value=mock_client):
            proxy_resp = await ac.get(f"/api/v1/preview/{mission.id}/")
            assert proxy_resp.status_code == 200
            assert "Dev Server Active" in proxy_resp.text


@pytest.mark.asyncio
async def test_preview_trailing_slash_redirect(setup_preview_mission):
    """Ensure accessing root preview without trailing slash redirects with 307 to preserve base URL."""
    data = setup_preview_mission
    mission = data["mission"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/preview/{mission.id}", follow_redirects=False)
        assert resp.status_code == 307
        assert resp.headers["location"] == f"/api/v1/preview/{mission.id}/"


@pytest.mark.asyncio
async def test_preview_base_href_injection(setup_preview_mission):
    """Ensure HTML responses contain base href tag for relative asset resolution."""
    data = setup_preview_mission
    mission = data["mission"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/preview/{mission.id}/")
        assert resp.status_code == 200
        assert f'<base href="/api/v1/preview/{mission.id}/">' in resp.text

