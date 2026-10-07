import pytest
import pytest_asyncio
import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from backend.models.base import Base
from backend.core.database import engine
from backend.models.organization import Organization
from backend.models.project import Project, Task
from backend.models.mission import Mission, MissionStatus
from backend.models.agent import Agent, AgentType
from backend.models.memory import AgentMemory, MissionMemory
from backend.models.project import ProjectMemory
from backend.models.communication import AgentMessage, AgentArtifact, AgentDecision, AgentLesson
from backend.services.memory_service import store_agent_memory, publish_project_knowledge, store_mission_memory, send_agent_message, publish_artifact, record_decision, record_lesson
from backend.models.communication import MessageType, AgentLessonType
from backend.services.context_builder import build_agent_context



@pytest_asyncio.fixture(scope="function")
async def session(setup_db):
    from backend.core.database import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        yield session

@pytest.mark.asyncio
async def test_agent_intelligence_memory_isolation(session: AsyncSession):
    # Setup
    org = Organization(id="org1", name="Test Org", )
    session.add(org)
    proj1 = Project(id="proj1", organization_id="org1", name="Proj 1", created_by="sys")
    proj2 = Project(id="proj2", organization_id="org1", name="Proj 2", created_by="sys")
    session.add_all([proj1, proj2])
    miss1 = Mission(id="miss1", project_id="proj1", organization_id="org1", title="Mission 1", status=MissionStatus.ACTIVE, created_by="sys", objective="Test")
    task1 = Task(id="task1", project_id="proj1", mission_id="miss1", title="Task 1", created_by="sys")
    session.add_all([miss1, task1])
    agent_core = Agent(id="ag1", name="Core", type=AgentType.BACKEND_ENGINEER)
    agent_sentinel = Agent(id="ag2", name="Sentinel", type=AgentType.SECURITY_ENGINEER)
    session.add_all([agent_core, agent_sentinel])
    await session.commit()

    # 1. Agent Memory
    await store_agent_memory(session, "Core", "org1", "proj1", "convention", "db_access", "Use async sessions")
    # 2. Project Memory
    await publish_project_knowledge(session, "Core", "org1", "proj1", "architecture", "db", "Database uses PostgreSQL", 1.0, None)
    # 3. Mission Memory
    await store_mission_memory(session, "org1", "proj1", "miss1", "milestone", "Database selected")

    # Build context for Core
    ctx_core = await build_agent_context(session, agent_core, proj1, miss1, task1, "org1")
    
    assert "YOUR PRIVATE AGENT MEMORY" in ctx_core
    assert "Use async sessions" in ctx_core
    assert "PROJECT KNOWLEDGE (SHARED)" in ctx_core
    assert "Database uses PostgreSQL" in ctx_core
    assert "MISSION CONTEXT & EVENTS" in ctx_core
    assert "Database selected" in ctx_core

    # Build context for Sentinel (should NOT see Core's private memory)
    ctx_sentinel = await build_agent_context(session, agent_sentinel, proj1, miss1, task1, "org1")
    assert "YOUR PRIVATE AGENT MEMORY" not in ctx_sentinel
    assert "Use async sessions" not in ctx_sentinel
    assert "PROJECT KNOWLEDGE (SHARED)" in ctx_sentinel
    assert "Database uses PostgreSQL" in ctx_sentinel

@pytest.mark.asyncio
async def test_agent_communication_and_artifacts(session: AsyncSession):
    # Setup
    org = Organization(id="org1", name="Test Org", )
    session.add(org)
    proj1 = Project(id="proj1", organization_id="org1", name="Proj 1", created_by="sys")
    session.add(proj1)
    miss1 = Mission(id="miss1", project_id="proj1", organization_id="org1", title="Mission 1", status=MissionStatus.ACTIVE, created_by="sys", objective="Test")
    task1 = Task(id="task1", project_id="proj1", mission_id="miss1", title="Task 1", created_by="sys")
    session.add_all([miss1, task1])
    agent_core = Agent(id="ag1", name="Core", type=AgentType.BACKEND_ENGINEER)
    session.add(agent_core)
    await session.commit()

    # Communication
    await send_agent_message(session, "org1", "miss1", "Forge", "Core", "task1", MessageType.REQUEST, "Please build the API")
    await publish_artifact(session, "org1", "miss1", "task1", "Forge", ["Core"], "Spec", "/specs/api.md", "API Specification")
    
    ctx = await build_agent_context(session, agent_core, proj1, miss1, task1, "org1")
    
    assert "MESSAGES & COMMUNICATION" in ctx
    assert "Please build the API" in ctx
    assert "UPSTREAM ARTIFACTS" in ctx
    assert "API Specification" in ctx

@pytest.mark.asyncio
async def test_agent_lessons_and_decisions(session: AsyncSession):
    # Setup
    org = Organization(id="org1", name="Test Org", )
    session.add(org)
    proj1 = Project(id="proj1", organization_id="org1", name="Proj 1", created_by="sys")
    session.add(proj1)
    miss1 = Mission(id="miss1", project_id="proj1", organization_id="org1", title="Mission 1", status=MissionStatus.ACTIVE, created_by="sys", objective="Test")
    task1 = Task(id="task1", project_id="proj1", mission_id="miss1", title="Task 1", created_by="sys")
    session.add_all([miss1, task1])
    agent_core = Agent(id="ag1", name="Core", type=AgentType.BACKEND_ENGINEER)
    session.add(agent_core)
    await session.commit()

    await record_decision(session, "org1", "proj1", "miss1", "Core", "Use FastAPI", "It is fast")
    await record_lesson(session, "org1", "proj1", "miss1", "Core", AgentLessonType.FAILURE, "Syntax Error", "Always check imports")

    ctx = await build_agent_context(session, agent_core, proj1, miss1, task1, "org1")
    
    assert "RECENT PROJECT DECISIONS" in ctx
    assert "Use FastAPI" in ctx
    assert "LESSONS LEARNED" in ctx
    assert "Always check imports" in ctx

