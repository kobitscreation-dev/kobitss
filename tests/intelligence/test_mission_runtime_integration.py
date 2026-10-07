import pytest
from unittest.mock import patch, AsyncMock
from sqlalchemy.ext.asyncio import AsyncSession
import uuid
import json

from backend.models.mission import Mission, MissionStatus, WorkflowPhase
from backend.services.mission_runtime import MissionRuntime
from backend.models.intelligence import StrategyType
from backend.core.database import AsyncSessionLocal, engine
from backend.models.base import Base
import pytest_asyncio



@pytest.mark.asyncio
async def test_adaptive_planner_invoked():
    org_id = 'test-org'
    user_id = 'test-user'
    
    async with AsyncSessionLocal() as db:
        from backend.models.organization import Organization, OrganizationMember, OrgRole
        from backend.models.project import Project
        org = Organization(id=org_id, name='Test Org')
        db.add(org)
        mem = OrganizationMember(user_id=user_id, organization_id=org_id, role=OrgRole.ADMIN)
        db.add(mem)
        proj = Project(id='test-proj', name='Test', organization_id=org_id, created_by=user_id)
        db.add(proj)

        m = Mission(
            id=str(uuid.uuid4()),
            project_id=proj.id,
            organization_id=org_id,
            created_by=user_id,
            title='Test Planner Invocation',
            objective='Test Objective',
            status=MissionStatus.ACTIVE,
            phase=WorkflowPhase.INTAKE
        )
        db.add(m)
        await db.commit()
        mission_id = m.id

    with patch('backend.services.mission_runtime.AdaptivePlanner') as mock_planner_cls, \
         patch('backend.services.mission_runtime.RiskAnalyzer') as mock_risk_cls, \
         patch('backend.services.mission_runtime.execute_agent_run', new_callable=AsyncMock) as mock_execute:
        
        mock_execute.return_value = {"status": "SUCCESS"}
        mock_planner = mock_planner_cls.return_value
        mock_planner.select_strategy = AsyncMock(return_value=StrategyType.SEQUENTIAL)
        mock_planner.select_team = AsyncMock(return_value=['Agent1', 'Axiom'])
        
        mock_risk = mock_risk_cls.return_value
        mock_risk.analyze_change_impact.return_value = {'risk_profile': 'LOW'}
        
        # We only want to execute one phase to avoid infinite loop
        # so we mock _handle_analysis to break the loop or just let the mock execute
        # Wait, if we mock execute_agent_run, the tasks will "succeed", and the state machine will advance to PLANNING, etc.
        # This can still cause an infinite loop if we don't mock it completely.
        # But wait, it's better to just raise an Exception in mock_execute to break the loop after INTAKE.
        # Or mock _handle_analysis.
        with patch.object(MissionRuntime, '_handle_analysis', new=AsyncMock(return_value='ERROR')):
            runtime = MissionRuntime(mission_id, org_id, user_id)
            await runtime.execute()
        
        mock_planner_cls.assert_called()
        mock_risk_cls.assert_called()
        mock_planner.select_strategy.assert_called_once()
        mock_planner.select_team.assert_called_once()
        mock_risk.analyze_change_impact.assert_called_once()
        
        async with AsyncSessionLocal() as db:
            m = await db.get(Mission, mission_id)
            assert m.phase == WorkflowPhase.ANALYSIS
            assert m.workflow_strategy == 'SEQUENTIAL'
            team = json.loads(m.team_composition_json)
            assert "Agent1" in team
            assert "Axiom" in team
