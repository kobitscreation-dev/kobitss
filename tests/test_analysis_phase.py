import pytest
import uuid
import json
import os
from datetime import datetime, timezone

os.environ["LLM_PROVIDER"] = "mock"

from sqlalchemy.ext.asyncio import AsyncSession
from backend.models.project import Project, Base
from backend.models.mission import Mission, MissionStatus, WorkflowPhase
from backend.services.mission_runtime import MissionRuntime
from backend.services.intelligence.adaptive_planning import AdaptivePlanner
from backend.core.database import AsyncSessionLocal, engine

@pytest.mark.asyncio
async def test_adaptive_planner_validation():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    async with AsyncSessionLocal() as db:
        planner = AdaptivePlanner(db)
        invalid_team = ["Review", "Core"]
        valid_team = planner.validate_phase_team("ANALYSIS", invalid_team)
        assert 'Axiom' in valid_team

@pytest.mark.asyncio
async def test_analysis_phase_transition():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        project_id = str(uuid.uuid4())
        mission_id = str(uuid.uuid4())
        org_id = "test-org"
        user_id = "test-user"
        
        project = Project(id=project_id, organization_id=org_id, name="Test", created_by=user_id)
        db.add(project)
        
        mission = Mission(
            id=mission_id,
            project_id=project_id,
            organization_id=org_id,
            created_by=user_id,
            title="Deterministic Test",
            objective="Test Transition",
            status=MissionStatus.ACTIVE,
            phase=WorkflowPhase.INTAKE,
            team_composition_json=json.dumps(["Review"])
        )
        db.add(mission)
        await db.commit()
        
        runtime = MissionRuntime(mission_id, org_id, user_id)
        
        await runtime._handle_intake(db, mission)
        await db.commit()
        assert mission.phase == WorkflowPhase.ANALYSIS
        
        res = await runtime._handle_analysis(db, mission)
        await db.commit()
        
        current_team = json.loads(mission.team_composition_json)
        assert 'Axiom' in current_team
        
        from sqlalchemy import select
        from backend.models.project import Task, TaskStatus
        
        tasks = (await db.execute(select(Task).where(Task.mission_id == mission_id, Task.phase == WorkflowPhase.ANALYSIS))).scalars().all()
        assert len(tasks) == 2
        
        for t in tasks:
            t.status = TaskStatus.COMPLETED
            db.add(t)
        await db.commit()
        
        res2 = await runtime._handle_analysis(db, mission)
        await db.commit()
        
        assert mission.phase == WorkflowPhase.PLANNING
