import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
import asyncio

from backend.main import app
from backend.core.database import get_db
from backend.models.base import Base
from backend.api.deps import get_current_user

# Create a SQLite database for tests
SQLALCHEMY_DATABASE_URL = "sqlite+aiosqlite:///./test_analytics.db"

engine = create_async_engine(SQLALCHEMY_DATABASE_URL, echo=False)
TestingSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Override dependencies
async def override_get_db():
    async with TestingSessionLocal() as session:
        yield session

# Create a mock user
from backend.models.organization import User
mock_user = User(id="test_user", email="test@test.com", full_name="Test", hashed_password="hashed")

async def override_get_current_user():
    return mock_user

app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[get_current_user] = override_get_current_user

from backend.models.project import Project
from backend.models.organization import Organization, OrganizationMember

@pytest.fixture(scope="session", autouse=True)
def setup_db():
    async def init_db():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with TestingSessionLocal() as session:
            org = Organization(id="demo_org", name="Demo Org")
            session.add(org)
            session.add(mock_user)
            member = OrganizationMember(organization_id="demo_org", user_id="test_user", role="OWNER")
            session.add(member)
            project = Project(id="test_project", name="Test", organization_id="demo_org", created_by="test_user")
            session.add(project)
            await session.commit()
    asyncio.run(init_db())
    yield
    async def close_db():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
    asyncio.run(close_db())

client = TestClient(app)

def test_analytics_reports_no_data():
    response = client.get("/api/v1/analytics/reports?project_id=test_project")
    assert response.status_code == 200
    data = response.json()
    assert data["tasks_attempted"] == 0
    assert data["tasks_completed"] == 0

def test_analytics_agents_no_data():
    response = client.get("/api/v1/analytics/agents?project_id=test_project")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 0

def test_analytics_specialization_no_data():
    response = client.get("/api/v1/analytics/specialization?project_id=test_project")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 0

def test_analytics_memory_no_data():
    response = client.get("/api/v1/analytics/memory?project_id=test_project")
    assert response.status_code == 200
    data = response.json()
    assert data["memory_written"] == 0
