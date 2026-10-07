import pytest
import pytest_asyncio
import asyncio
import uuid
import json
from datetime import datetime, timezone
from sqlalchemy import select, update

from backend.models.base import Base
from backend.models.mission import Mission, MissionStatus, WorkflowPhase
from backend.models.project import Task, TaskStatus, Project
from backend.models.agent import AgentRun, AgentRunStatus, ApprovalRequest, ApprovalStatus
from backend.services.mission_runtime import MissionRuntime
from backend.api.v1.projects import resolve_approval_request
from backend.schemas.agent import ApprovalSubmit
from fastapi import HTTPException
from unittest.mock import AsyncMock, patch

class MockUser:
    id = "test-user-id"


from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
e2e_engine = create_async_engine('sqlite+aiosqlite:///./test_workflow_e2e.db')
from sqlalchemy.ext.asyncio import AsyncSession
E2EAsyncSessionLocal = async_sessionmaker(class_=AsyncSession, expire_on_commit=False, autocommit=False, autoflush=False, bind=e2e_engine)




@pytest.mark.asyncio
async def test_workflow_e2e():
    org_id = "test-org"
    user_id = "test-user-id"
    
    async with E2EAsyncSessionLocal() as db:
        from backend.models.organization import Organization, OrganizationMember, OrgRole
        org = Organization(id=org_id, name="Test Org")
        db.add(org)
        mem = OrganizationMember(user_id=user_id, organization_id=org_id, role=OrgRole.ADMIN)
        db.add(mem)
        proj = Project(id="test-proj", name="Test", organization_id=org_id, created_by=user_id)
        db.add(proj)
        
        m = Mission(
            id=str(uuid.uuid4()),
            project_id=proj.id,
            organization_id=org_id,
            created_by=user_id,
            title="E2E Test Mission",
            objective="Build a feature",
            status=MissionStatus.ACTIVE,
            phase=WorkflowPhase.INTAKE,
            approval_status=ApprovalStatus.APPROVED
        )
        db.add(m)
        await db.commit()
        mission_id = m.id

    async def mock_execute(run, agent_type, input_data, db):
        task = await db.get(Task, run.task_id)
        return {
            "status": "COMPLETED",
            "summary": "Did mock work",
            "artifacts": {
                "task_decomposition": [
                    {
                        "title": "Mock Impl Task",
                        "agent": "FRONTEND_ENGINEER",
                        "description": "Mock description for the task."
                    }
                ]
            }
        }
        
    async def mock_decision(db, task, run):
        return {"action": "CONTINUE"}
        
    with patch("backend.services.mission_runtime.execute_agent_run", new=mock_execute), \
         patch("backend.services.mission_runtime.OrchestratorDecision.evaluate_task_result", new=mock_decision), \
         patch("backend.services.mission_runtime.GitHubService.create_pull_request", new=AsyncMock(return_value={"id": "mock", "html_url": "http"})), \
             patch("backend.services.mission_runtime.AsyncSessionLocal", E2EAsyncSessionLocal):

        runtime = MissionRuntime(mission_id, org_id, user_id)
        await runtime.execute()
        
        async with E2EAsyncSessionLocal() as db:
            m = await db.get(Mission, mission_id)
            assert m.status == MissionStatus.COMPLETED
            assert m.phase == WorkflowPhase.LEARNING
            
            stmt = select(Task).where(Task.mission_id == mission_id, Task.phase == WorkflowPhase.PLANNING)
            plan_tasks = (await db.execute(stmt)).scalars().all()
            assert len(plan_tasks) == 4
            assert all(t.status == TaskStatus.COMPLETED for t in plan_tasks)

        async with E2EAsyncSessionLocal() as db:
            ar_mock = ApprovalRequest(id="ar-123", project_id="test-proj", organization_id="test-org", agent_run_id="run123", requested_by="system", title="test", description="desc", status=ApprovalStatus.PENDING, risk_level="LOW")
            db.add(ar_mock)
            await db.commit()

            stmt = select(ApprovalRequest).where(ApprovalRequest.project_id == "test-proj")
            ar = (await db.execute(stmt)).scalars().first()
            assert ar is not None
            ar_id = ar.id
            
        async with E2EAsyncSessionLocal() as db:
            await resolve_approval_request(
                project_id="test-proj",
                approval_id=ar_id,
                submission=ApprovalSubmit(approved=True, feedback="Looks good"),
                db=db,
                current_user=MockUser()
            )
            await asyncio.sleep(0.5)

        async with E2EAsyncSessionLocal() as db:
            m = await db.get(Mission, mission_id)
            assert m.status == MissionStatus.COMPLETED
            assert m.phase == WorkflowPhase.LEARNING
            
            stmt = select(Task).where(Task.mission_id == mission_id, Task.phase == WorkflowPhase.IMPLEMENTATION)
