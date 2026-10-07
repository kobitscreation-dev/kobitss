import pytest
import pytest_asyncio
import os
import uuid
from backend.core.database import AsyncSessionLocal, engine
from backend.models.base import Base
from backend.benchmarks.benchmark_harness import BenchmarkHarness
from backend.core.config import settings




@pytest.mark.asyncio
async def test_standard_profile_benchmark():
    harness = BenchmarkHarness()
    org_id = str(uuid.uuid4())
    proj_id = str(uuid.uuid4())
    
    report = await harness.run_standard_profile_benchmark(org_id, proj_id)
    assert report['provider'] == settings.LLM_PROVIDER
    assert 'mission_id' in report
    assert len(report['flow']) > 0

@pytest.mark.asyncio
async def test_memory_benchmark():
    harness = BenchmarkHarness()
    org_id = str(uuid.uuid4())
    proj_id = str(uuid.uuid4())
    
    report = await harness.run_memory_benchmark(org_id, proj_id)
    assert 'Memory benchmark seeded successfully.' in report['flow']
    
@pytest.mark.asyncio
async def test_generate_report():
    harness = BenchmarkHarness()
    await harness.generate_final_report()
    assert os.path.exists('benchmark_report.json')