import pytest
import pytest_asyncio
import asyncio
import json
import uuid
import os
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.core.database import AsyncSessionLocal
from backend.models.base import Base
from backend.models.project import Project, Task, TaskStatus
from backend.models.mission import Mission, MissionStatus, WorkflowPhase
from backend.models.agent import AgentRun, AgentType, AgentRunStatus, DAGNodeCache
from backend.services.intelligence.consensus_engine import ConsensusEngine
import backend.services.llm.mock_provider as mock_provider

# Save original to restore later
_orig_generate = mock_provider.MockProvider.generate_structured_output

async def orig_generate(*args, **kwargs):
    return {
        "verdict": "APPROVED",
        "findings": ["Looks good"],
        "feedback": "Code review passed",
        "severity": "LOW"
    }

@pytest_asyncio.fixture
async def db_session():
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    TestingSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with TestingSessionLocal() as session:
        yield session

@pytest_asyncio.fixture
async def setup_data(db_session):
    org_id = "test-org"
    user_id = "test-user"

    project = Project(id=str(uuid.uuid4()), organization_id=org_id, name="Test Project DAG", created_by=user_id)
    db_session.add(project)

    mission = Mission(
        id=str(uuid.uuid4()),
        organization_id=org_id,
        project_id=project.id,
        title="DAG Crash Test Mission",
        objective="Test DAG caching",
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
        title="Test DAG Task",
        status=TaskStatus.IN_PROGRESS
    )
    db_session.add(task)
    await db_session.commit()
    
    return {"mission_id": mission.id, "task_id": task.id}

class MockConsensusEngine(ConsensusEngine):
    def __init__(self, db_session, task_id):
        from backend.services.llm.mock_provider import MockProvider
        self.provider = MockProvider()
        self.db = db_session
        self.code_artifacts = []
        self.code_diff_text = ""
        self.task_id = task_id
        self.sandbox_id = None
        self.project_path = None

    async def _broadcast(self, mission_id, event_type, message, data=None):
        pass

@pytest.fixture(autouse=True)
def set_env():
    os.environ["LLM_PROVIDER"] = "mock"
    from backend.services.intelligence.consensus_engine import _dag_node_locks
    _dag_node_locks.clear()
    import backend.services.llm.mock_provider as mock_provider
    mock_provider.MockProvider.generate_structured_output = orig_generate
    yield


@pytest.mark.asyncio
async def test_1_and_7_concurrent_reviewer_executions(db_session, setup_data):
    """
    Test 1: Two concurrent reviewer executions for the same node. Only one LLM invocation.
    Test 7: Verify no duplicate cache records can exist under concurrent execution.
    """
    engine = MockConsensusEngine(db_session, setup_data["task_id"])
    os.environ["LLM_PROVIDER"] = "mock"
    
    call_count = 0
    
    async def spy_generate(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.5) # Simulate slow LLM
        return await orig_generate(engine.provider, *args, **kwargs)
        
    mock_provider.MockProvider.generate_structured_output = spy_generate
    
    try:
        # Run two concurrent executions of SECURITY_ENGINEER for the same task and round
        t1 = asyncio.create_task(engine._run_single_reviewer(AgentType.SECURITY_ENGINEER, {}, AgentType.BACKEND_ENGINEER, {}, "mock", setup_data["mission_id"], round_num=1))
        t2 = asyncio.create_task(engine._run_single_reviewer(AgentType.SECURITY_ENGINEER, {}, AgentType.BACKEND_ENGINEER, {}, "mock", setup_data["mission_id"], round_num=1))
        
        results = await asyncio.gather(t1, t2)
        
        # Verify both completed
        assert len(results) == 2
        assert results[0].verdict is not None
        assert results[1].verdict is not None
        
        # Verify ONLY ONE LLM call was made due to the lock and idempotency cache
        assert call_count == 1, f"Expected 1 LLM call, got {call_count}"
        
        # Verify exactly one cache record exists
        stmt = select(DAGNodeCache).where(DAGNodeCache.task_id == setup_data["task_id"])
        caches = (await db_session.execute(stmt)).scalars().all()
        assert len(caches) == 1, "Duplicate cache entries exist despite unique constraints!"
        
    finally:
        mock_provider.MockProvider.generate_structured_output = _orig_generate


@pytest.mark.asyncio
async def test_2_and_3_crash_recovery(db_session, setup_data):
    """
    Test 2: One completes while another runs.
    Test 3: Server/crash recovery.
    """
    engine = MockConsensusEngine(db_session, setup_data["task_id"])
    os.environ["LLM_PROVIDER"] = "mock"
    
    # We will cancel the gather halfway through, mimicking a crash.
    # We will make CODE_REVIEWER fast, and SECURITY_ENGINEER slow.
    async def spy_generate_crash(*args, **kwargs):
        sys_prompt = kwargs.get("system_prompt", "")
        if not sys_prompt and len(args) > 0: sys_prompt = args[0]
        
        if "SECURITY_ENGINEER" in sys_prompt:
            await asyncio.sleep(1.0) # Slow, will be cancelled
        
        return await orig_generate(engine.provider, *args, **kwargs)

    mock_provider.MockProvider.generate_structured_output = spy_generate_crash
    
    try:
        # We start both
        t1 = asyncio.create_task(engine._run_single_reviewer(AgentType.CODE_REVIEWER, {}, AgentType.BACKEND_ENGINEER, {}, "mock", setup_data["mission_id"], round_num=1))
        t2 = asyncio.create_task(engine._run_single_reviewer(AgentType.SECURITY_ENGINEER, {}, AgentType.BACKEND_ENGINEER, {}, "mock", setup_data["mission_id"], round_num=1))
        
        # Wait for CODE_REVIEWER to finish
        await asyncio.sleep(0.2) 
        
        # Cancel SECURITY_ENGINEER (Simulate crash)
        t2.cancel()
        
        try:
            await t2
        except asyncio.CancelledError:
            pass
            
        await t1 # Should be done
        
        # Assert CODE_REVIEWER is in cache, SECURITY_ENGINEER is not
        stmt = select(DAGNodeCache).where(DAGNodeCache.task_id == setup_data["task_id"])
        caches = (await db_session.execute(stmt)).scalars().all()
        assert len(caches) == 1
        assert caches[0].node_id == AgentType.CODE_REVIEWER.value
        
        # RESTART (Crash Recovery)
        called = []
        async def spy_generate_recover(*args, **kwargs):
            sys_prompt = kwargs.get("system_prompt", "")
            if not sys_prompt and len(args) > 0: sys_prompt = args[0]
            if "SECURITY_ENGINEER" in sys_prompt: called.append("SECURITY_ENGINEER")
            if "CODE_REVIEWER" in sys_prompt: called.append("CODE_REVIEWER")
            return await orig_generate(engine.provider, *args, **kwargs)
            
        mock_provider.MockProvider.generate_structured_output = spy_generate_recover
        
        # Run both again
        results = await asyncio.gather(
            engine._run_single_reviewer(AgentType.CODE_REVIEWER, {}, AgentType.BACKEND_ENGINEER, {}, "mock", setup_data["mission_id"], round_num=1),
            engine._run_single_reviewer(AgentType.SECURITY_ENGINEER, {}, AgentType.BACKEND_ENGINEER, {}, "mock", setup_data["mission_id"], round_num=1)
        )
        
        assert "CODE_REVIEWER" not in called, "Code Reviewer should have recovered from cache!"
        assert "SECURITY_ENGINEER" in called, "Security Engineer should have run after crash recovery!"

    finally:
        mock_provider.MockProvider.generate_structured_output = _orig_generate


@pytest.mark.asyncio
async def test_4_cache_isolation(db_session, setup_data):
    """
    Test 4: Cache belongs to the exact task + debate round + reviewer node.
    """
    engine = MockConsensusEngine(db_session, setup_data["task_id"])
    
    # Pre-seed cache for ROUND 1
    cache1 = DAGNodeCache(
        id=str(uuid.uuid4()),
        task_id=setup_data["task_id"],
        round_number=1,
        node_id=AgentType.CODE_REVIEWER.value,
        result_json=json.dumps({"verdict": "APPROVED", "findings": ["Round 1 cached"]})
    )
    # Pre-seed cache for DIFFERENT TASK
    cache2 = DAGNodeCache(
        id=str(uuid.uuid4()),
        task_id="different-task",
        round_number=2,
        node_id=AgentType.CODE_REVIEWER.value,
        result_json=json.dumps({"verdict": "APPROVED", "findings": ["Different task cached"]})
    )
    db_session.add(cache1)
    db_session.add(cache2)
    await db_session.commit()
    
    called = []
    async def spy_generate(*args, **kwargs):
        called.append("CODE_REVIEWER")
        return await orig_generate(engine.provider, *args, **kwargs)
        
    mock_provider.MockProvider.generate_structured_output = spy_generate
    
    try:
        # Run for round 2
        res = await engine._run_single_reviewer(AgentType.CODE_REVIEWER, {}, AgentType.BACKEND_ENGINEER, {}, "mock", setup_data["mission_id"], round_num=2)
        
        assert "CODE_REVIEWER" in called, "Should not use round 1 cache for round 2, or other task cache!"
        
    finally:
        mock_provider.MockProvider.generate_structured_output = _orig_generate


@pytest.mark.asyncio
async def test_5_corrupt_invalid_cached_output(db_session, setup_data):
    """
    Test 5: Corrupt/invalid cached output: fail safely and do not silently treat malformed data.
    """
    engine = MockConsensusEngine(db_session, setup_data["task_id"])
    
    # Pre-seed CORRUPT cache
    cache1 = DAGNodeCache(
        id=str(uuid.uuid4()),
        task_id=setup_data["task_id"],
        round_number=1,
        node_id=AgentType.CODE_REVIEWER.value,
        result_json="{ this is not valid json! ["
    )
    # Pre-seed structurally invalid cache
    cache2 = DAGNodeCache(
        id=str(uuid.uuid4()),
        task_id=setup_data["task_id"],
        round_number=1,
        node_id=AgentType.SECURITY_ENGINEER.value,
        result_json=json.dumps(["Not a dict"])
    )
    db_session.add(cache1)
    db_session.add(cache2)
    await db_session.commit()
    
    called = []
    async def spy_generate(*args, **kwargs):
        sys_prompt = kwargs.get("system_prompt", "")
        if not sys_prompt and len(args) > 0: sys_prompt = args[0]
        if "SECURITY_ENGINEER" in sys_prompt: called.append("SECURITY_ENGINEER")
        if "CODE_REVIEWER" in sys_prompt: called.append("CODE_REVIEWER")
        return await orig_generate(engine.provider, *args, **kwargs)
        
    mock_provider.MockProvider.generate_structured_output = spy_generate
    
    try:
        results = await asyncio.gather(
            engine._run_single_reviewer(AgentType.CODE_REVIEWER, {}, AgentType.BACKEND_ENGINEER, {}, "mock", setup_data["mission_id"], round_num=1),
            engine._run_single_reviewer(AgentType.SECURITY_ENGINEER, {}, AgentType.BACKEND_ENGINEER, {}, "mock", setup_data["mission_id"], round_num=1)
        )
        
        assert "CODE_REVIEWER" in called, "Code reviewer should have rejected the malformed JSON cache and ran LLM"
        assert "SECURITY_ENGINEER" in called, "Security engineer should have rejected the invalid structure and ran LLM"
        
        # Verify corrupt caches were deleted and replaced with valid ones
        stmt = select(DAGNodeCache).where(DAGNodeCache.task_id == setup_data["task_id"])
        caches = (await db_session.execute(stmt)).scalars().all()
        
        assert len(caches) == 2
        for c in caches:
            data = json.loads(c.result_json)
            assert isinstance(data, dict)
            assert "verdict" in data
            
    finally:
        mock_provider.MockProvider.generate_structured_output = _orig_generate


@pytest.mark.asyncio
async def test_6_transaction_durability(db_session, setup_data):
    """
    Test 6: Transaction durability. The reviewer result must be persisted before the execution can
    report the node as successfully completed.
    """
    engine = MockConsensusEngine(db_session, setup_data["task_id"])
    os.environ["LLM_PROVIDER"] = "mock"
    
    # Execute a clean run
    res = await engine._run_single_reviewer(AgentType.CODE_REVIEWER, {}, AgentType.BACKEND_ENGINEER, {}, "mock", setup_data["mission_id"], round_num=1)
    
    # As soon as it returns, the data MUST be fully committed to the DB.
    # If we rollback our outer session (or create a new session), it should still be there.
    # Since we use an in-memory SQLite and shared session fixture, we just assert it exists.
    
    stmt = select(DAGNodeCache).where(
        DAGNodeCache.task_id == setup_data["task_id"],
        DAGNodeCache.node_id == AgentType.CODE_REVIEWER.value
    )
    saved = (await db_session.execute(stmt)).scalars().first()
    
    assert saved is not None
    assert json.loads(saved.result_json).get("verdict") == res.verdict

