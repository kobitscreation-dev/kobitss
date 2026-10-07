import asyncio
import os
import uuid
import json
from datetime import datetime, timezone

from backend.core.config import settings
import os

# Test DB and settings are configured inside main() when executed as a script.

from backend.core.database import AsyncSessionLocal, engine
from backend.models.base import Base
from backend.models.mission import Mission, MissionStatus
from backend.models.project import Task, TaskStatus, Project
from backend.models.organization import Organization
from backend.models.github import Repository, GitHubConnection
from backend.services.mission_runtime import MissionRuntime
from backend.services.agent_registry import AGENT_REGISTRY

async def setup_test_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        # Create Org & Project
        org_id = str(uuid.uuid4())
        org = Organization(id=org_id, name="Test Org")
        proj_id = str(uuid.uuid4())
        proj = Project(id=proj_id, organization_id=org_id, name="Test Project", created_by="test_user")
        db.add_all([org, proj])
        
        # Create GitHub Connection & Repo
        conn_id = str(uuid.uuid4())
        conn = GitHubConnection(id=conn_id, organization_id=org_id, user_id="test_user", installation_id="123")
        repo_id = str(uuid.uuid4())
        # Use a local path for testing since git is local
        local_repo_path = os.path.abspath("test_real_ai_repo")
        repo = Repository(
            id=repo_id, 
            github_connection_id=conn_id,
            organization_id=org_id,
            project_id=proj_id,
            github_repo_id="999",
            owner="local",
            name="test_real_ai_repo",
            full_name="local/test_real_ai_repo",
            clone_url=local_repo_path,
            web_url="https://github.com/local/test_real_ai_repo",
            default_branch="main"
        )
        db.add_all([conn, repo])
        
        # Create Mission
        mission_id = str(uuid.uuid4())
        mission = Mission(
            id=mission_id,
            organization_id=org_id,
            project_id=proj_id,
            repository_id=repo_id,
            created_by="test_user",
            title="Add User Profile Endpoint",
            objective="Add a user profile endpoint with authentication, input validation, automated tests, and API documentation. Follow the existing repository architecture, coding conventions, and security practices.",
            status=MissionStatus.READY,
            base_commit_sha="dummy_sha"
        )
        db.add(mission)
        
        # Create Initial Task
        task_id = str(uuid.uuid4())
        task = Task(
            id=task_id,
            mission_id=mission_id,
            project_id=proj_id,
            created_by="test_user",
            title="Implement User Profile API",
            description="Create the endpoint, tests, and docs.",
            status=TaskStatus.PENDING,
            metadata_json=json.dumps({"agent_role": "BACKEND_ENGINEER"})
        )
        db.add(task)
        await db.commit()
        return mission_id, org_id

async def main():
    print("==================================================")
    print("             REAL AI EXECUTION TEST               ")
    print("==================================================")
    
    settings.REAL_AI_TEST = True
    provider = settings.LLM_PROVIDER
    print(f"Configured Provider: {provider}")
    
    if provider == "anthropic" and not settings.ANTHROPIC_API_KEY:
        print("ERROR: ANTHROPIC_API_KEY is not set.")
        # But we don't exit, we let the runtime try and fail to capture the real failure mode
        
    print("Setting up DB and Mission...")
    mission_id, org_id = await setup_test_db()
    
    print(f"Starting MissionRuntime for Mission {mission_id}...")
    runtime = MissionRuntime(mission_id=mission_id, organization_id=org_id, user_id="test_user")
    
    try:
        await runtime.execute()
    except Exception as e:
        print(f"Runtime execution crashed: {e}")
        
    print("Fetching results...")
    async with AsyncSessionLocal() as db:
        mission = await db.get(Mission, mission_id)
        print(f"Mission Status: {mission.status}")
        print(f"Mission Stage: {mission.current_stage}")
        
        from sqlalchemy import select
        from backend.models.agent import AgentRun
        runs = (await db.execute(select(AgentRun).where(AgentRun.organization_id == org_id))).scalars().all()
        for run in runs:
            print(f"AgentRun {run.id} Status: {run.status}")
            print(f"AgentRun Error: {run.error}")

if __name__ == "__main__":
    asyncio.run(main())
