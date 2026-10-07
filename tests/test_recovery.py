import pytest
import asyncio
import os
import json
import uuid
import shutil
import tempfile
import sys
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from backend.models.base import Base
from backend.models.mission import Mission, WorkflowPhase, MissionStatus
from backend.models.agent import Agent, AgentType, AgentRun, AgentRunStatus
from backend.models.project import Task, TaskStatus
from backend.models.debate_log import DebateLog, ConsensusVerdict
from backend.services.intelligence.consensus_engine import ConsensusEngine, DebateRound, ReviewerResult
from backend.services.sandbox_manager import SandboxManager, SandboxSession
from backend.services.mission_runtime import MissionRuntime

class MockProvider:
    async def generate_structured_output(self, *args, **kwargs):
        return {"status": "SUCCESS"}

import random
async def setup_test_db():
    db_name = f"memdb{random.randint(1, 10000)}"
    engine = create_async_engine(f"sqlite+aiosqlite:///file:{db_name}?mode=memory&cache=shared")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)

async def create_test_data(session):
    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    proj_id = str(uuid.uuid4())
    mission_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())

    session.add_all([
        Agent(id="system", type=AgentType.BACKEND_ENGINEER.value, name="Backend", system_prompt="test"),
        Mission(id=mission_id, organization_id=org_id, project_id=proj_id, created_by=user_id, title="Test Mission", objective="Test"),
        Task(id=task_id, mission_id=mission_id, project_id=proj_id, title="Test Task", description="Test", created_by=user_id)
    ])
    await session.commit()
    return user_id, org_id, proj_id, mission_id, task_id

@pytest.mark.asyncio
async def test_sandbox_persistence():
    print("Running: test_sandbox_persistence...")
    
    old_root = SandboxManager.SANDBOX_ROOT
    temp_dir = tempfile.mkdtemp()
    SandboxManager.SANDBOX_ROOT = temp_dir
    
    try:
        dummy_proj = os.path.join(temp_dir, "dummy_proj")
        os.makedirs(dummy_proj)
        
        session = SandboxManager.create_sandbox(project_root=dummy_proj, branch_name="kobits/test/123")
        session_id = session.session_id
        
        meta_path = os.path.join(session.sandbox_dir, ".kobits_sandbox.json")
        assert os.path.exists(meta_path), "Metadata JSON was not created!"
        
        SandboxManager._sessions.clear()
        
        recovered_session = SandboxManager.get_session_by_branch("kobits/test/123")
        assert recovered_session is not None, "Failed to recover session from disk!"
        assert recovered_session.session_id == session_id, "Recovered session ID mismatch!"
        assert recovered_session.branch_name == "kobits/test/123"
        print("  [PASS] Sandbox session successfully recovered from disk after restart.")
        
    finally:
        SandboxManager.SANDBOX_ROOT = old_root
        shutil.rmtree(temp_dir, ignore_errors=True)

@pytest.mark.asyncio
async def test_missing_sandbox_safety_block():
    print("Running: test_missing_sandbox_safety_block...")
    engine, session_factory = await setup_test_db()
    async with session_factory() as db:
        user_id, org_id, proj_id, mission_id, task_id = await create_test_data(db)
        
        mission = await db.get(Mission, mission_id)
        mission.active_branch = "kobits/missing/123"
        task = await db.get(Task, task_id)
        task.status = TaskStatus.PENDING
        await db.commit()
        
        runtime = MissionRuntime(mission_id=mission_id, organization_id=org_id, user_id=user_id)
        # Mock the db inside for the test if it opens sessions, but actually we can just pass the engine or override it.
        # MissionRuntime uses `async with get_db_session() as db:` which is global. 
        # But wait, I'm using an in-memory DB in tests! 
        # Let's mock `get_db_session` in the test.
        import backend.services.mission_runtime
        backend.services.mission_runtime.AsyncSessionLocal = session_factory
        
        success = await runtime.execute_task(task_id)
        assert not success, "Task execution should have been blocked!"
        
        # Check run status in DB
        from sqlalchemy import select
        stmt = select(AgentRun).where(AgentRun.task_id == task_id)
        run = (await db.execute(stmt)).scalars().first()
        assert run.status == AgentRunStatus.FAILED, f"Run status should be FAILED, got {run.status}"
        assert "CRITICAL RECOVERY ERROR" in run.error, "Run error didn't mention recovery error"
        print("  [PASS] Missing sandbox safely blocked execution (No unisolated writes).")

@pytest.mark.asyncio
async def test_large_json_persistence():
    print("Running: test_large_json_persistence...")
    engine, session_factory = await setup_test_db()
    async with session_factory() as db:
        _, _, _, mission_id, task_id = await create_test_data(db)
        
        engine_service = ConsensusEngine(db=db)
        
        large_content = "X" * 60000 
        artifacts = [{"path": "big_file.py", "content": large_content, "action": "write"}]
        engine_service.code_artifacts = artifacts
        
        coder_output = {"status": "SUCCESS", "summary": "Y" * 15000}
        
        debate_round = DebateRound(
            round_number=1,
            coder_output=coder_output,
            reviews=[],
            consensus_reached=False
        )
        
        await engine_service._persist_round(task_id, mission_id, AgentType.BACKEND_ENGINEER, debate_round)
        
        from sqlalchemy import select
        stmt = select(DebateLog).where(DebateLog.task_id == task_id)
        logs = (await db.execute(stmt)).scalars().all()
        assert len(logs) == 1
        
        parsed_coder = json.loads(logs[0].coder_output_json)
        assert len(parsed_coder["summary"]) == 15000, "Coder output was truncated!"
        
        parsed_artifacts = json.loads(logs[0].code_artifacts_json)
        assert len(parsed_artifacts[0]["content"]) == 60000, "Artifacts were truncated!"
        print("  [PASS] Large JSON checkpoints persist and load without truncation errors.")

async def main():
    print("==================================================")
    print("  CRASH RECOVERY & ISOLATION SAFETY TESTS")
    print("==================================================")
    try:
        await test_sandbox_persistence()
        await test_missing_sandbox_safety_block()
        await test_large_json_persistence()
        print("\nAll crash recovery tests passed successfully.")
    except AssertionError as e:
        print(f"\n[FAIL] {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
