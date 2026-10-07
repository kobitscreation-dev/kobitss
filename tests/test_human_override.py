import pytest
import pytest_asyncio
import asyncio
import json
import uuid
from datetime import datetime, timezone
from sqlalchemy import select

from backend.core.database import AsyncSessionLocal
from backend.models.mission import Mission, MissionStatus, WorkflowPhase
from backend.models.project import Project, Task, TaskStatus
from backend.models.debate_log import DebateLog, ConsensusVerdict
from backend.models.agent import AgentRun, AgentRunStatus, AgentType
from backend.services.intelligence.consensus_engine import ConsensusEngine
from backend.services.mission_runtime import MissionRuntime
from backend.services.agent_executor import execute_agent_run

class MockWS:
    async def send_json(self, data):
        pass

from backend.models.base import Base

@pytest_asyncio.fixture
async def db_session():
    # Use an in-memory SQLite database for testing
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    TestingSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with TestingSessionLocal() as session:
        yield session
        await session.rollback()

@pytest.mark.asyncio
async def test_human_override_resume(db_session):
    # Setup project and mission
    org_id = "test-org"
    user_id = "test-user"
    
    project = Project(id=str(uuid.uuid4()), organization_id=org_id, name="Test Project", created_by=user_id)
    db_session.add(project)
    
    mission = Mission(
        id=str(uuid.uuid4()),
        organization_id=org_id,
        project_id=project.id,
        title="Override Test Mission",
        objective="Test human in the loop",
        status=MissionStatus.EXECUTING,
        phase=WorkflowPhase.IMPLEMENTATION,
        created_by=user_id
    )
    db_session.add(mission)
    
    task = Task(
        id=str(uuid.uuid4()),
        project_id=project.id,
        mission_id=mission.id,
        created_by=user_id,
        title="Test Coder Task",
        status=TaskStatus.BLOCKED,
        metadata_json=json.dumps({"human_override_comment": "The security agent is wrong. Ignore it."})
    )
    db_session.add(task)
    
    # Create the previous BLOCKED debate log
    dl = DebateLog(
        task_id=task.id,
        mission_id=mission.id,
        coder_agent_type="BACKEND_ENGINEER",
        round_number=1,
        coder_output_json=json.dumps({"status": "SUCCESS", "summary": "Initial buggy code", "artifacts": {}}),
        reviewer_verdicts_json=json.dumps([
            {"agent_type": "SECURITY_ENGINEER", "agent_name": "Aegis", "verdict": "REJECTED", "severity": "CRITICAL", "feedback": "SQL injection detected!"}
        ]),
        consensus=ConsensusVerdict.BLOCKED,
        consensus_reached=False,
        revision_prompt="Fix it",
        duration_ms=100
    )
    db_session.add(dl)
    await db_session.commit()

    # Now let's simulate what execute_agent_run does
    input_data = {
        "mission_id": mission.id,
        "task_title": task.title,
        "task_description": "Write a login endpoint",
        "human_override_comment": "The security agent is wrong. Ignore it."
    }
    
    agent_run = AgentRun(
        id=str(uuid.uuid4()),
        task_id=task.id,
        project_id=project.id,
        organization_id=org_id,
        agent_id=AgentType.BACKEND_ENGINEER.value,
        status=AgentRunStatus.QUEUED
    )
    db_session.add(agent_run)
    await db_session.commit()
    
    # Run the executor! It should:
    # 1. Skip coder because existing logs exist
    # 2. Enter consensus_engine
    # 3. Detect human override and BLOCKED verdict
    # 4. Trigger coder revision (which calls the mock LLM, resulting in success)
    
    import os
    os.environ["LLM_PROVIDER"] = "mock"
    
    result = await execute_agent_run(agent_run, AgentType.BACKEND_ENGINEER, input_data, db=db_session)
    
    print("Executor Result:", json.dumps(result, indent=2))
    
    # Assertions
    # Wait, the mock LLM will return a success JSON!
    assert result.get("status") == "SUCCESS", "Result should be SUCCESS from the mock LLM"
    
    debate_summary = result.get("_debate", {})
    assert debate_summary.get("final_verdict") == "APPROVED", "Debate should reach APPROVED status"
    
    # Let's check the database logs
    stmt = select(DebateLog).where(DebateLog.task_id == task.id).order_by(DebateLog.round_number)
    logs = (await db_session.execute(stmt)).scalars().all()
    
    assert len(logs) > 1, "Should have created a new round log"
    assert logs[0].consensus == ConsensusVerdict.NEEDS_REVISION, "First round should be wiped to NEEDS_REVISION"
    assert logs[-1].consensus == ConsensusVerdict.APPROVED, "Last round should be APPROVED"
    
    print("Test passed successfully! Human override successfully resumed a blocked debate.")
