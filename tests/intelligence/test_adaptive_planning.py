import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from backend.services.intelligence.adaptive_planning import AdaptivePlanner
from backend.models.intelligence import ProcessMemory

@pytest.mark.asyncio
async def test_adaptive_routing_upgrade_to_pro():
    db = AsyncMock()
    # Mock ProcessMemory with bad history
    bad_memory = ProcessMemory(
        task_category="build_api",
        sample_size=3,
        success_rate=0.6,  # Poor success rate
        avg_corrections=2.0 # Lots of debate rounds
    )
    result_mock = MagicMock()
    result_mock.scalars().first.return_value = bad_memory
    db.execute.return_value = result_mock
    
    planner = AdaptivePlanner(db)
    # Even if complexity is LOW and risk is LOW, it should force PRO because history is bad
    tier = await planner.route_model("build_api", "LOW", "LOW")
    assert tier == "pro"

@pytest.mark.asyncio
async def test_adaptive_routing_downgrade_to_flash():
    db = AsyncMock()
    # Mock ProcessMemory with perfect history
    good_memory = ProcessMemory(
        task_category="update_css",
        sample_size=5,
        success_rate=1.0,   # Perfect
        avg_corrections=0.0 # No corrections needed
    )
    result_mock = MagicMock()
    result_mock.scalars().first.return_value = good_memory
    db.execute.return_value = result_mock
    
    planner = AdaptivePlanner(db)
    # Even if complexity is MEDIUM, if history is perfect, it should downgrade to FLASH
    tier = await planner.route_model("update_css", "MEDIUM", "LOW")
    assert tier == "flash"

@pytest.mark.asyncio
async def test_adaptive_routing_static_fallback():
    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalars().first.return_value = None # No memory yet
    db.execute.return_value = result_mock
    
    planner = AdaptivePlanner(db)
    
    # Standard heuristics apply
    assert await planner.route_model("new_task", "HIGH", "LOW") == "pro"
    assert await planner.route_model("new_task", "LOW", "LOW") == "flash"
    assert await planner.route_model("new_task", "MEDIUM", "MEDIUM") == "default"
