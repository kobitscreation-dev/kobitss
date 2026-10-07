import pytest
import asyncio
import json
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.models.intelligence import ProcessOutcomeRecord, StrategyType
from backend.models.project import Project, Task, TaskStatus
from backend.models.mission import Mission, MissionStatus, WorkflowPhase
from backend.models.agent import AgentRun, AgentRunStatus, AgentType, Agent
from backend.services.intelligence.adaptive_planning import AdaptivePlanner
from backend.services.agent_executor import execute_agent_run
from backend.services.mission_runtime import MissionRuntime

pytestmark = pytest.mark.asyncio

import pytest_asyncio
from backend.core.database import engine, AsyncSessionLocal
from backend.models.base import Base



@pytest_asyncio.fixture(loop_scope='function')
async def db_session(setup_db):
    async with AsyncSessionLocal() as session:
        yield session

async def _seed_test_data(db: AsyncSession):
    # Org
    from backend.models.organization import Organization, User
    import uuid
    org_id = str(uuid.uuid4())
    org = Organization(id=org_id, name="Test Org")
    
    u_id = str(uuid.uuid4())
    user = User(id=u_id, email=f"{u_id}@test.com", hashed_password="hash")
    
    proj_id = str(uuid.uuid4())
    proj = Project(id=proj_id, organization_id=org_id, name="Test Project", created_by=u_id)
    
    miss_id = str(uuid.uuid4())
    mission = Mission(
        id=miss_id,
        project_id=proj_id,
        organization_id=org_id,
        created_by=u_id,
        title="Quality Mission",
        objective="Ensure highest quality.",
        status=MissionStatus.ACTIVE,
        phase=WorkflowPhase.VALIDATION,
        risk_profile_json=json.dumps({"overall_risk": "HIGH"})
    )
    
    agent = Agent(id="sys", name="sys", type=AgentType.BACKEND_ENGINEER, description="test", capabilities=json.dumps(["python"]), system_prompt="test")
    
    db.add_all([org, user, proj, mission, agent])
    await db.commit()
    return org_id, proj_id, miss_id


async def test_quality_scorecard_metrics(db_session: AsyncSession):
    org_id, proj_id, miss_id = await _seed_test_data(db_session)
    
    record = ProcessOutcomeRecord(
        id="rec1",
        mission_id=miss_id,
        organization_id=org_id,
        task_category="test",
        outcome="SUCCESS",
        mission_type="test",
        team_composition_json="[]",
        strategy_type=StrategyType.SEQUENTIAL,
        correctness_score=0.9,
        security_score=1.0,
        architecture_score=0.8,
        delivery_success=True
    )
    db_session.add(record)
    await db_session.commit()
    
    fetched = await db_session.get(ProcessOutcomeRecord, "rec1")
    assert fetched.correctness_score == 0.9
    assert fetched.security_score == 1.0
    assert fetched.delivery_success is True


async def test_adaptive_model_routing(db_session: AsyncSession):
    planner = AdaptivePlanner(db_session)
    
    model1 = await planner.route_model("test_category", task_complexity="LOW", risk_level="LOW")
    assert model1 == "flash"
    
    model2 = await planner.route_model("test_category", task_complexity="HIGH", risk_level="LOW")
    assert model2 == "pro"
    
    model3 = await planner.route_model("test_category", task_complexity="LOW", risk_level="HIGH")
    assert model3 == "pro"


async def test_multi_hypothesis_planning(db_session: AsyncSession):
    planner = AdaptivePlanner(db_session)
    
    # Low risk -> no multiple hypotheses
    res1 = await planner.plan("test", "LOW", ["python"])
    assert len(res1.hypotheses) == 0
    
    # High risk -> should generate A, B, C hypotheses
    res2 = await planner.plan("test", "HIGH", ["python"])
    assert len(res2.hypotheses) == 3
    assert res2.hypotheses[0]["hypothesis_id"] == "A_CONSERVATIVE"


async def test_red_team_critic_loop_injection(db_session: AsyncSession):
    org_id, proj_id, miss_id = await _seed_test_data(db_session)
    
    runtime = MissionRuntime(miss_id, org_id, "u_id")
    mission = await db_session.get(Mission, miss_id)
    
    # Mission starts in VALIDATION phase, risk is HIGH.
    # The handler should dynamically spawn a Red-Team task.
    # We mock execute_agent_run to avoid real network calls hanging the test
    from unittest.mock import patch
    async def mock_execute(*args, **kwargs):
        return {"status": "SUCCESS"}
        
    with patch("backend.services.mission_runtime.execute_agent_run", new=mock_execute):
        res = await runtime._handle_validation(db_session, mission)
    
    tasks = (await db_session.execute(select(Task).where(Task.mission_id == miss_id))).scalars().all()
    titles = [t.title for t in tasks]
    
    assert "Red-Team Specialist Critic" in titles
    assert "QA Testing" in titles


async def test_anti_loop_protection(db_session: AsyncSession):
    org_id, proj_id, miss_id = await _seed_test_data(db_session)
    import uuid
    t_id = str(uuid.uuid4())
    task = Task(id=t_id, mission_id=miss_id, project_id=proj_id, created_by="u", title="t", status=TaskStatus.IN_PROGRESS, phase=WorkflowPhase.IMPLEMENTATION)
    
    run = AgentRun(agent_id="sys", project_id=proj_id, task_id=t_id, organization_id=org_id, status=AgentRunStatus.QUEUED)
    db_session.add(task)
    db_session.add(run)
    await db_session.commit()
    
    # We must patch the Provider to return 3 identical tool calls.
    # Since we use MockProvider, we can inject a mock that always returns a tool call, 
    # but the executor itself catches the AntiLoopException and returns FAILED.
    from backend.services.agent_executor import agent_executor
    
    class LoopProvider:
        async def generate_structured_output(self, system_prompt, user_prompt, schema, model, tools, tool_executor):
            # simulate looping
            for _ in range(4):
                await tool_executor("some_tool", {"arg": 1})
            return {"status": "SUCCESS"}
            
    original = agent_executor.provider
    agent_executor.provider = LoopProvider()
    
    try:
        input_data = {"mission_id": miss_id, "risk_profile": "{}"}
        result = await execute_agent_run(run, AgentType.BACKEND_ENGINEER, input_data, db=db_session)
        assert result["status"] == "FAILED"
        assert "AntiLoopException" in result["summary"]
    finally:
        agent_executor.provider = original


async def test_evidence_first_completion(db_session: AsyncSession):
    org_id, proj_id, miss_id = await _seed_test_data(db_session)
    import uuid
    t_id = str(uuid.uuid4())
    task = Task(id=t_id, mission_id=miss_id, project_id=proj_id, created_by="u", title="t", status=TaskStatus.IN_PROGRESS, phase=WorkflowPhase.IMPLEMENTATION)
    db_session.add(task)
    await db_session.commit()
    
    # We test the logic inside execute_task directly, or simulate the run result.
    # Because execute_task is massive, let's just assert the policy engine requirement.
    from backend.services.intelligence.policy_engine import PolicyEngine
    from backend.models.intelligence import TaskContract
    engine = PolicyEngine(db_session)
    
    contract = TaskContract(
        task_id=t_id,
        success_criteria_json=json.dumps([{"type": "file_exists", "path": "test.py"}]),
        failure_criteria_json=json.dumps([])
    )
    
    # No evidence -> Fail
    is_valid, _ = engine.evaluate_task_contract(contract, [])
    assert is_valid is False
    
    # With evidence -> Pass
    is_valid, _ = engine.evaluate_task_contract(contract, [{'type': 'artifact', 'name': 'test.py', 'value': 'code'}])
    assert is_valid is True
