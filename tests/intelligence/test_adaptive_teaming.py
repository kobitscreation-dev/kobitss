import pytest
import pytest_asyncio
import json
from sqlalchemy.ext.asyncio import AsyncSession
from backend.models.intelligence import ProcessMemory, StrategyType
from backend.services.intelligence.adaptive_planning import AdaptivePlanner
from backend.core.database import AsyncSessionLocal, engine
from backend.models.base import Base



@pytest.mark.asyncio
async def test_adaptive_teaming():
    async with AsyncSessionLocal() as db_session:
        planner = AdaptivePlanner(db_session)
        
        # Insert mock historical memory
        pm = ProcessMemory(
            organization_id='org_1',
            task_category='payment_integration',
            strategy_type=StrategyType.SPECIALIST_FIRST,
            team_composition_json=json.dumps(['Forge', 'Axiom', 'Bridge', 'Core']),
            success_rate=0.9,
            confidence=0.8, sample_size=5, parallelization_json='{}'
        )
        db_session.add(pm)
        await db_session.commit()
        
        # Test strategy selection uses memory
        strategy = await planner.select_strategy('payment_integration', 'HIGH')
        assert strategy == StrategyType.SPECIALIST_FIRST
        
        # Test team selection uses memory
        team = await planner.select_team('payment_integration', ['python'], strategy)
        assert team == ['Forge', 'Axiom', 'Bridge', 'Core']
        
        # Test fallback
        strategy = await planner.select_strategy('unknown_task', 'CRITICAL')
        assert strategy == StrategyType.RISK_FIRST
