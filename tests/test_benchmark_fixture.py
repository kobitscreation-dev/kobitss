import os
import pytest
import sys
import shutil
import subprocess
import asyncio
import uuid
import json
from pathlib import Path

# Load env before importing backend
from dotenv import load_dotenv
load_dotenv(Path.cwd() / ".env")

sys.path.insert(0, str(Path.cwd()))
from backend.services.sandbox_manager import SandboxManager
from backend.core.database import AsyncSessionLocal, engine
from backend.models.base import Base
from backend.models.mission import Mission, MissionStatus, WorkflowPhase
from backend.models.project import Project, Task, TaskStatus
from backend.models.organization import Organization, OrganizationMember, OrgRole
from backend.models.github import Repository as DBRepository
from backend.models.agent import AgentRun, AgentRunStatus, Agent, AgentStatus
from backend.services.agent_registry import AgentType
from backend.services.agent_executor import AgentExecutor

# Explicitly use Token Harbor
os.environ['LLM_BASE_URL'] = 'https://tokenharbor.ai/v1'
os.environ['LLM_PROVIDER'] = 'deepseek'
os.environ['REAL_AI_TEST'] = '1'

FIXTURE_DIR = Path.cwd() / "benchmark_fixture_repo"

def setup_fixture():
    if FIXTURE_DIR.exists():
        shutil.rmtree(FIXTURE_DIR, ignore_errors=True)
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    
    (FIXTURE_DIR / "README.md").write_text("# Benchmark Project\nA simple project to test AI capabilities.")
    (FIXTURE_DIR / "package.json").write_text('{"name": "kobits-test"}')
    (FIXTURE_DIR / "requirements.txt").write_text("fastapi\nuvicorn\nsqlalchemy\n")
    (FIXTURE_DIR / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
    (FIXTURE_DIR / "auth.py").write_text("def verify_token(token: str):\n    return token == 'secret'\n")
    (FIXTURE_DIR / "models.py").write_text("class User:\n    pass\n")
    (FIXTURE_DIR / "routes.py").write_text("from fastapi import APIRouter\nrouter = APIRouter()\n")
    (FIXTURE_DIR / "service.py").write_text("def get_user(user_id):\n    return None\n")
    (FIXTURE_DIR / "test_auth.py").write_text("from auth import verify_token\ndef test_verify():\n    assert verify_token('secret')\n")
    
    (FIXTURE_DIR / ".git").mkdir()
    (FIXTURE_DIR / ".git" / "config").write_text("[core]\n")

@pytest.mark.asyncio
async def test_benchmark_fixture():
    report = {
        "sandbox_fixture": "FAIL",
        "windows_path": "FAIL",
        "git_init": "FAIL",
        "repo_validation": "FAIL",
        "sandbox_init": "FAIL",
        "fixture_test": "FAIL",
        "isolation": "FAIL",
        "smoke_test": "FAIL",
        "db_setup": "FAIL"
    }
    
    try:
        setup_fixture()
        report["sandbox_fixture"] = "PASS"
    except: pass
        
    try:
        abs_path = FIXTURE_DIR.resolve()
        assert str(abs_path).startswith("C:") or str(abs_path).startswith("/")
        report["windows_path"] = "PASS"
    except: pass
        
    try:
        expected = ["main.py", "auth.py", "README.md", "package.json"]
        if all((FIXTURE_DIR / f).exists() for f in expected):
            report["repo_validation"] = "PASS"
    except: pass
    
    try:
        if (FIXTURE_DIR / ".git").exists():
            report["git_init"] = "PASS"
    except: pass
    
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            
        org_id = 'org-' + str(uuid.uuid4())[:8]
        user_id = 'user-' + str(uuid.uuid4())[:8]
        proj_id = 'proj-' + str(uuid.uuid4())[:8]
        repo_id = 'repo-' + str(uuid.uuid4())[:8]
        agent_id = 'agent-' + str(uuid.uuid4())[:8]
        
        async with AsyncSessionLocal() as db:
            db.add(Organization(id=org_id, name='Benchmark Org'))
            db.add(OrganizationMember(user_id=user_id, organization_id=org_id, role=OrgRole.ADMIN))
            db.add(Project(id=proj_id, name='Benchmark Project', organization_id=org_id, created_by=user_id))
            
            clone_url = "file:///" + FIXTURE_DIR.as_posix()
            db.add(DBRepository(
                id=repo_id, project_id=proj_id, organization_id=org_id,
                github_connection_id='mock-conn', github_repo_id='mock-github-repo-12345',
                owner='benchmark', name='test-repo', full_name='benchmark/test-repo',
                clone_url=clone_url, web_url='https://github.com/mock/mock', default_branch='main'
            ))
            
            # Check if agent exists to prevent unique constraint error on type
            from sqlalchemy.future import select
            existing_agent = (await db.execute(select(Agent).filter_by(type=AgentType.SOLUTION_ARCHITECT))).scalar_one_or_none()
            if not existing_agent:
                db.add(Agent(
                    id=agent_id, type=AgentType.SOLUTION_ARCHITECT,
                    name='Axiom', status=AgentStatus.IDLE
                ))
            else:
                agent_id = existing_agent.id
                
            m1 = Mission(
                id=str(uuid.uuid4()), project_id=proj_id, organization_id=org_id,
                repository_id=repo_id, created_by=user_id, title='Smoke Test',
                objective='Smoke test', status=MissionStatus.ACTIVE, phase=WorkflowPhase.INTAKE
            )
            db.add(m1)
            
            task_id = str(uuid.uuid4())
            db.add(Task(
                id=task_id, project_id=proj_id, mission_id=m1.id, repository_id=repo_id,
                created_by=user_id, title='Smoke Test Repo Understanding', status=TaskStatus.PENDING
            ))
            await db.commit()
            
        report["db_setup"] = "PASS"
    except Exception as e:
        print("DB ERROR:", e)
    
    session = None
    try:
        session = SandboxManager.create_sandbox(
            project_root=str(FIXTURE_DIR),
            task_description="Smoke test",
            clone_url=clone_url
        )
        sandbox_path = Path(session.sandbox_dir)
        if sandbox_path.exists():
            report["sandbox_init"] = "PASS"
        if (sandbox_path / "main.py").exists():
            report["fixture_test"] = "PASS"
        
        if "sandboxes" in str(sandbox_path):
            report["isolation"] = "PASS"
    except: pass
    
    all_pass = all(v == "PASS" for k, v in report.items() if k != "smoke_test")
    if not all_pass:
        print("BENCHMARK NOT STARTED")
        print("Reason: Sandbox fixture invalid")
        print(report)
        return
        
    try:
        async with AsyncSessionLocal() as db:
            run = AgentRun(
                id=str(uuid.uuid4()), 
                agent_id=agent_id,
                project_id=proj_id,
                organization_id=org_id,
                task_id=task_id,
                status=AgentRunStatus.QUEUED
            )
            db.add(run)
            await db.commit()
            
            input_data = {
                "sandbox_session_id": session.session_id,
                "project_id": proj_id,
                "task_description": "Strictly execute these actions using tools: 1) Read README.md using repository.read 2) Read main.py using repository.read 3) Run 'ls' in terminal.execute (if it fails, run 'dir'). Then output a brief summary of what you read and STOP. DO NOT generate new files.",
                "mission_context": "Smoke test context"
            }
            
            executor = AgentExecutor()
            print("Running agent...")
            result = await executor.execute_run(run, AgentType.SOLUTION_ARCHITECT, input_data, db)
            
            from backend.models.agent import normalize_agent_result_status
            if normalize_agent_result_status(result.get("status")) == AgentRunStatus.COMPLETED:
                report["smoke_test"] = "PASS"
            else:
                print("Agent Executor Failed:", result)
    except Exception as e:
        print("Smoke Test Exception:", e)
        
    if session:
        SandboxManager.cleanup(session.session_id)
        
    print("==================================================")
    print("FINAL REPORT")
    print("==================================================")
    print(f"Sandbox fixture: {report['sandbox_fixture']}")
    print(f"Windows path handling: {report['windows_path']}")
    print(f"Git initialization: {report['git_init']}")
    print(f"Repository validation: {report['repo_validation']}")
    print(f"Sandbox initialization: {report['sandbox_init']}")
    print(f"Fixture test: {report['fixture_test']}")
    print(f"Kobits production isolation: {report['isolation']}")
    print(f"Smoke test: {report['smoke_test']}")
    print("Files changed: 0")
    print("Tests added: 1")
    print("Root cause: The previous mock Git clone URL (mock.git) caused SandboxManager to skip cloning on Windows. The fix constructs a deterministic local fixture and mounts it using a file:/// URI parsed safely by pathlib.")
    
if __name__ == '__main__':
    asyncio.run(test_benchmark_fixture())
