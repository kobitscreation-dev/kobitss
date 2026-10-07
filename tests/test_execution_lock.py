import pytest
import pytest_asyncio
import asyncio
import uuid
import json
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, update
from backend.core.database import engine, AsyncSessionLocal
from backend.models.base import Base
from backend.models.mission import Mission, MissionStatus, WorkflowPhase
from backend.models.project import Task, TaskStatus, Project
from backend.models.organization import Organization, OrganizationMember, OrgRole
from backend.services.mission_runtime import MissionRuntime
from unittest.mock import patch, AsyncMock



async def create_base_mission():
    org_id = "test-org"
    user_id = "test-user"
    async with AsyncSessionLocal() as db:
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
            title="Lock Test Mission",
            objective="Test locking",
            status=MissionStatus.ACTIVE,
            phase=WorkflowPhase.INTAKE
        )
        db.add(m)
        await db.commit()
        return m.id, org_id, user_id

@pytest.mark.asyncio
async def test_concurrent_execution():
    mission_id, org_id, user_id = await create_base_mission()
    
    runtime_a = MissionRuntime(mission_id, org_id, user_id)
    runtime_b = MissionRuntime(mission_id, org_id, user_id)
    
    # We will patch _handle_intake to just wait a bit, so they overlap
    async def mock_handle_intake(self, db, mission):
        await asyncio.sleep(0.5)
        return "HALT"
        
    with patch.object(MissionRuntime, '_handle_intake', new=mock_handle_intake):
        # Run both concurrently
        await asyncio.gather(
            runtime_a.execute(),
            runtime_b.execute()
        )
        
    # One acquires lock and finishes. The other fails to acquire lock and returns. Both exit cleanly.
    async with AsyncSessionLocal() as db:
        m = await db.get(Mission, mission_id)
        assert m.execution_lock_id is None

@pytest.mark.asyncio
async def test_live_lock_protection():
    mission_id, org_id, user_id = await create_base_mission()
    
    # Set a live lock
    async with AsyncSessionLocal() as db:
        stmt = update(Mission).where(Mission.id == mission_id).values(
            execution_lock_id="live-lock-123",
            execution_lock_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5)
        )
        await db.execute(stmt)
        await db.commit()
        
    runtime_b = MissionRuntime(mission_id, org_id, user_id)
    
    async def mock_handle_intake(*args):
        # Should not be called
        assert False, "Should not acquire live lock"
        
    with patch.object(MissionRuntime, '_handle_intake', new=mock_handle_intake):
        await runtime_b.execute()
        
    async with AsyncSessionLocal() as db:
        m = await db.get(Mission, mission_id)
        assert m.execution_lock_id == "live-lock-123"

@pytest.mark.asyncio
async def test_stale_lock_recovery():
    mission_id, org_id, user_id = await create_base_mission()
    
    # Set a stale lock
    async with AsyncSessionLocal() as db:
        stmt = update(Mission).where(Mission.id == mission_id).values(
            execution_lock_id="stale-lock-123",
            execution_lock_expires_at=datetime.now(timezone.utc) - timedelta(minutes=5)
        )
        await db.execute(stmt)
        await db.commit()
        
    runtime_b = MissionRuntime(mission_id, org_id, user_id)
    
    call_count = 0
    async def mock_handle_intake(self, db_session, mission):
        nonlocal call_count
        call_count += 1
        return "HALT"
        
    with patch.object(MissionRuntime, '_handle_intake', new=mock_handle_intake):
        await runtime_b.execute()
        
    assert call_count == 1
    
    # Verify the lock was acquired (and then released because of HALT)
    async with AsyncSessionLocal() as db:
        m = await db.get(Mission, mission_id)
        assert m.execution_lock_id is None
