import pytest
import asyncio
from backend.services.intelligence.consensus_engine import ConsensusEngine, ReviewVerdict

@pytest.mark.asyncio
async def test_dry_run_syntax_error():
    engine = ConsensusEngine()
    
    # 1. Create a bad code artifact with a syntax error
    bad_code_artifacts = [
        {"action": "write", "path": "bad_file.py", "content": "def broken_function(\n    pass"}
    ]
    
    # 2. Run the simulation directly
    success, feedback = await engine._run_dry_run_simulation({}, bad_code_artifacts)
    
    # 3. Assert it failed and caught the error
    assert not success
    assert "Syntax Error" in feedback
    assert "bad_file.py" in feedback

@pytest.mark.asyncio
async def test_dry_run_syntax_success():
    engine = ConsensusEngine()
    
    # 1. Create a good code artifact
    good_code_artifacts = [
        {"action": "write", "path": "good_file.py", "content": "def good_function():\n    return True"}
    ]
    
    # 2. Run the simulation
    success, feedback = await engine._run_dry_run_simulation({}, good_code_artifacts)
    
    # 3. Assert it passed
    assert success
    assert "Syntax OK" in feedback
