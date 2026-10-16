import pytest
from httpx import AsyncClient, ASGITransport
from backend.main import app
from backend.models.user import User
from backend.models.project import Project
from backend.models.organization import Organization
from backend.core.database import AsyncSessionLocal
from sqlalchemy import select


@pytest.mark.asyncio
async def test_discovery_audit_api_endpoint():
    async with AsyncSessionLocal() as db:
        # Find or create a test organization and project
        org = (await db.execute(select(Organization).limit(1))).scalar_one_or_none()
        if not org:
            org = Organization(id="test_org_discovery", name="Discovery Test Org")
            db.add(org)
            await db.flush()

        user = (await db.execute(select(User).limit(1))).scalar_one_or_none()
        if not user:
            user = User(id="test_user_discovery", email="discovery@test.com", hashed_password="pw", full_name="Discovery Tester")
            db.add(user)
            await db.flush()

        project = (await db.execute(select(Project).limit(1))).scalar_one_or_none()
        if not project:
            project = Project(
                id="proj_discovery_test",
                organization_id=org.id,
                created_by=user.id,
                name="Discovery Test Project",
                description="Testing the Kyros-style discovery audit flow",
                repository_url=".",
            )
            db.add(project)
            await db.commit()
            await db.refresh(project)
        project_id = project.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Call discovery audit endpoint (DEV_MODE is enabled in test environment)
        res = await ac.post(f"/api/v1/discovery/{project_id}/audit")
        assert res.status_code == 200, f"Error {res.status_code}: {res.text}"
        data = res.json()
        assert data["project_id"] == project_id
        assert "health_score" in data
        assert "mermaid_diagram" in data
        assert "markdown_report" in data
        assert len(data["milestones"]) == 4

        # Call get report endpoint
        report_res = await ac.get(f"/api/v1/discovery/{project_id}/report")
        assert report_res.status_code == 200
        report_data = report_res.json()
        assert "markdown" in report_data
        assert "mermaid" in report_data
        assert "# Executive Architecture & Codebase Discovery Audit" in report_data["markdown"]
