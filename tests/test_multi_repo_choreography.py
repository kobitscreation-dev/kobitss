import pytest
from httpx import AsyncClient
import os
import shutil
import uuid
from backend.core.database import engine, AsyncSessionLocal
from backend.models.project import Base

@pytest.mark.asyncio
async def test_multi_repo_choreography():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        
    async with AsyncSessionLocal() as test_db:
        # This test verifies that we can create a mission across multiple repositories
        from backend.models.project import Project
        from backend.models.github import Repository
        from backend.models.organization import Organization, User, OrgRole, OrganizationMember
        
        from backend.api.v1.missions import create_mission, MissionCreate
        
        # 1. Setup Data
        org = Organization(id="org1", name="Test Org")
        user = User(id="usr1", email="test@test.com")
        test_db.add(org)
        test_db.add(user)
        await test_db.flush()
        
        member = OrganizationMember(organization_id="org1", user_id="usr1", role=OrgRole.ADMIN)
        test_db.add(member)
        
        project = Project(id="proj1", organization_id="org1", name="Multi-Repo App", created_by="usr1")
        test_db.add(project)
        await test_db.flush()
        
        # 2. Add two repositories to the project
        repo1 = Repository(id="repo1", project_id="proj1", organization_id="org1", name="backend", github_connection_id="mock", github_repo_id="1", owner="acme", full_name="acme/backend", clone_url="file://C:/Users/Arvind Kumar/.gemini/antigravity/scratch/kobits/test_repo_backend", web_url="x")
        repo2 = Repository(id="repo2", project_id="proj1", organization_id="org1", name="frontend", github_connection_id="mock", github_repo_id="2", owner="acme", full_name="acme/frontend", clone_url="file://C:/Users/Arvind Kumar/.gemini/antigravity/scratch/kobits/test_repo_frontend", web_url="x")
        test_db.add(repo1)
        test_db.add(repo2)
        await test_db.commit()
        
        # Create the mock remote dirs
        os.makedirs("test_repo_backend", exist_ok=True)
        with open("test_repo_backend/backend.py", "w") as f:
            f.write("print('backend')")
        os.makedirs("test_repo_frontend", exist_ok=True)
        with open("test_repo_frontend/frontend.js", "w") as f:
            f.write("console.log('frontend')")
            
        try:
            # 3. Create a mission (no specific repo requested -> auto multi-repo)
            req = MissionCreate(
                title="Full stack feature",
                objective="Add a new feature across backend and frontend"
            )
            
            from fastapi import BackgroundTasks
            mission = await create_mission("proj1", req, BackgroundTasks(), test_db, user)
            
            # Verify mission is created
            assert mission["mission_id"] is not None
            mission_id = mission["mission_id"]
            
            # Verify mission_repositories are linked
            from sqlalchemy import select
            from backend.models.mission import MissionRepository
            res = await test_db.execute(select(MissionRepository).where(MissionRepository.mission_id == mission_id))
            m_repos = res.scalars().all()
            assert len(m_repos) == 2
            
            mount_paths = [mr.mount_path for mr in m_repos]
            assert "backend" in mount_paths
            assert "frontend" in mount_paths
            
            # 4. Initialize Sandbox via MissionRuntime
            from backend.services.mission_runtime import MissionRuntime
            runtime = MissionRuntime(mission_id, org.id, user.id)
            runtime.db = test_db
            import asyncio
            await asyncio.sleep(2)
            
            from backend.services.sandbox_manager import SandboxManager
            from backend.models.mission import Mission
            mission_obj = await test_db.get(Mission, mission_id)
            session = SandboxManager.get_session_by_branch(mission_obj.active_branch)
            assert session is not None
            
            # Verify the multi-repo sandbox structure
            assert os.path.exists(os.path.join(session.sandbox_dir, "backend", "backend.py"))
            assert os.path.exists(os.path.join(session.sandbox_dir, "frontend", "frontend.js"))
            
            
            
            
            # 5. Verify task worktrees work across multi-repo
            os.makedirs(os.path.join(session.sandbox_dir, "backend", ".git"), exist_ok=True)
            os.makedirs(os.path.join(session.sandbox_dir, "frontend", ".git"), exist_ok=True)
            task_session = SandboxManager.create_task_worktree(session.session_id, "task123")
            
            # The task sandbox should have the same multi-repo structure!
            assert os.path.exists(os.path.join(task_session.sandbox_dir, "backend", "backend.py"))
            assert os.path.exists(os.path.join(task_session.sandbox_dir, "frontend", "frontend.js"))
            
            # Make a change in the task sandbox backend
            with open(os.path.join(task_session.sandbox_dir, "backend", "backend.py"), "a") as f:
                f.write("\\nprint('feature added')")
                
            # Merge it back
            res = SandboxManager.merge_task_worktree(session.session_id, task_session.session_id)
            assert res["success"] == True
            
            
                
        finally:
            shutil.rmtree("test_repo_backend", ignore_errors=True)
            shutil.rmtree("test_repo_frontend", ignore_errors=True)
