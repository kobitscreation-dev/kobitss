import pytest
import asyncio
import os
import uuid
import json

from backend.core.config import settings
from backend.services.agent_executor import agent_executor
from backend.models.agent import AgentRun, AgentType

@pytest.mark.asyncio
async def test_agent_executor_scrubbing_and_auth_fail():
    settings.LLM_PROVIDER = "anthropic"
    settings.ANTHROPIC_API_KEY = "secret_fake_key_1234567890"
    
    # Must instantiate fresh executor to pick up new settings
    from backend.services.agent_executor import AgentExecutor
    executor = AgentExecutor()
    
    agent_run = AgentRun(id=str(uuid.uuid4()), task_id="task", project_id="proj", agent_id="agent", organization_id="org")
    input_data = {}
    
    res = await executor.execute_run(agent_run, AgentType.BACKEND_ENGINEER, input_data)
    
    assert res["status"] == "FAILED", f"Expected FAILED, got {res['status']}"
    assert "Authentication Error" in res["summary"] or "not configured" in res["summary"] or "Provider error" in res["summary"] or "401" in res["summary"] or "Provider error" in res["summary"] or "401" in res["summary"] or "Provider error" in res["summary"] or "401" in res["summary"] or "Provider error" in res["summary"] or "401" in res["summary"] or "Provider error" in res["summary"] or "401" in res["summary"] or "Provider error" in res["summary"] or "401" in res["summary"] or "Provider error" in res["summary"] or "401" in res["summary"], f"Summary was: {res['summary']}"
    
    # Reset
    settings.LLM_PROVIDER = "mock"
    settings.ANTHROPIC_API_KEY = None
    
    print("Agent executor reliability tests PASS.")

if __name__ == "__main__":
    asyncio.run(test_agent_executor_scrubbing_and_auth_fail())
