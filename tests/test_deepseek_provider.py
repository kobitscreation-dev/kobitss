import pytest
import pytest_asyncio
import json
import uuid
import httpx
from unittest.mock import AsyncMock, patch, MagicMock

from backend.services.llm.deepseek_provider import DeepSeekProvider
from backend.services.llm.provider_circuit_breaker import get_circuit_breaker

from backend.services.llm import get_llm_provider
from backend.services.llm.mock_provider import MockProvider
from backend.services.llm.anthropic_provider import AnthropicProvider
from backend.core.config import settings


@pytest.fixture(autouse=True)
def reset_breaker():
    get_circuit_breaker("DEEPSEEK").record_success()
    yield

@pytest.mark.asyncio
async def test_provider_initialization(monkeypatch):
    # 1. Unconfigured provider
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", None)
    monkeypatch.setattr(settings, "REAL_AI_TEST", False)
    
    provider = DeepSeekProvider(api_key=None)
    assert provider.configured is False
    res = await provider.generate_structured_output("sys", "user", {})
    assert res["status"] == "FAILED"
    assert "missing DEEPSEEK_API_KEY" in res["summary"]

    # 2. Configured provider
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-123")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    if hasattr(settings, "LLM_BASE_URL"):
        monkeypatch.setattr(settings, "LLM_BASE_URL", None)
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "test-key-123")
    monkeypatch.setattr(settings, "DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    provider2 = DeepSeekProvider(api_key=None)
    assert provider2.configured is True
    assert provider2.base_url == "https://api.deepseek.com"
    
    # 3. REAL_AI_TEST=True with no key raises ValueError
    monkeypatch.setattr(settings, "REAL_AI_TEST", True)
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", None)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY is not set"):
        DeepSeekProvider(api_key=None)

@pytest.mark.asyncio
async def test_request_construction_and_structured_output(monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    if hasattr(settings, "LLM_BASE_URL"):
        monkeypatch.setattr(settings, "LLM_BASE_URL", None)
    monkeypatch.setattr(settings, "DEEPSEEK_BASE_URL", "https://example.test/v1")
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "sk-test-key-1234567890")
    provider = DeepSeekProvider(api_key=None)
    
    mock_payload = {
        "id": "chatcmpl-1",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "```json\n{\"status\": \"SUCCESS\", \"summary\": \"Secure profile endpoint added.\", \"findings\": [], \"changes\": [\"profile.py\"], \"recommendations\": [], \"risks\": [], \"next_actions\": [], \"artifacts\": {\"endpoint\": \"/profile\"}, \"requires_approval\": false}\n```"
                }
            }
        ],
        "usage": {
            "prompt_tokens": 200,
            "completion_tokens": 100,
            "total_tokens": 300
        }
    }
    
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_payload
    
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        
        schema = {"type": "object"}
        res = await provider.generate_structured_output(
            system_prompt="You are Core.",
            user_prompt="Add profile endpoint.",
            schema=schema,
            model="deepseek-flash"
        )
        
        assert res["status"] == "SUCCESS"
        assert res["summary"] == "Secure profile endpoint added."
        assert res["artifacts"]["endpoint"] == "/profile"
        assert "_usage" in res
        assert res["_usage"]["input_tokens"] == 200
        assert res["_usage"]["output_tokens"] == 100
        assert res["_usage"]["total_tokens"] == 300
        assert res["_usage"]["estimated_cost"] > 0
        assert res["_usage"]["model"] == "deepseek-flash"
        
        # Verify request construction
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert "https://example.test/v1/chat/completions" in args[0]
        assert kwargs["headers"]["Authorization"] == "Bearer sk-test-key-1234567890"
        assert kwargs["json"]["model"] == "deepseek-flash"
        assert kwargs["json"]["messages"][0]["content"] == "You are Core."

@pytest.mark.asyncio
async def test_tool_calling_multi_turn():
    provider = DeepSeekProvider(api_key="sk-test-key-1234567890", max_retries=1)
    
    # Turn 1: Tool call request
    resp1 = MagicMock()
    resp1.status_code = 200
    resp1.json.return_value = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_123",
                            "type": "function",
                            "function": {
                                "name": "repository_read",
                                "arguments": json.dumps({"path": "main.py"})
                            }
                        }
                    ]
                }
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20}
    }
    
    # Turn 2: Final structured output
    resp2 = MagicMock()
    resp2.status_code = 200
    resp2.json.return_value = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps({
                        "status": "SUCCESS",
                        "summary": "Read main.py and verified structure.",
                        "findings": [],
                        "changes": [],
                        "recommendations": [],
                        "risks": [],
                        "next_actions": [],
                        "artifacts": {},
                        "requires_approval": False
                    })
                }
            }
        ],
        "usage": {"prompt_tokens": 150, "completion_tokens": 50}
    }
    
    executed_tools = []
    async def mock_tool_executor(name: str, args: dict):
        executed_tools.append((name, args))
        return {"content": "print('hello world')"}
        
    tools = [{
        "name": "repository_read",
        "description": "Read file",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}
    }]
    
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [resp1, resp2]
        
        res = await provider.generate_structured_output(
            system_prompt="You are Core.",
            user_prompt="Inspect main.py",
            schema={},
            tools=tools,
            tool_executor=mock_tool_executor
        )
        
        assert res["status"] == "SUCCESS"
        assert len(executed_tools) == 1
        assert executed_tools[0][0] == "repository_read"
        assert executed_tools[0][1]["path"] == "main.py"
        assert res["_usage"]["tool_calls"] == 1
        assert res["_usage"]["total_tokens"] == 320

@pytest.mark.asyncio
async def test_authentication_error_handling():
    provider = DeepSeekProvider(api_key="sk-invalid-secret-key-1234567")
    
    resp_401 = MagicMock()
    resp_401.status_code = 401
    resp_401.text = "Unauthorized: Invalid API key sk-invalid-secret-key-1234567"
    
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = resp_401
        
        res = await provider.generate_structured_output("sys", "user", {})
        assert res["status"] == "FAILED"
        assert "401" in res["summary"]
        assert "authentication error" in res["summary"].lower()
        # Ensure secret is scrubbed
        assert "sk-invalid-secret-key-1234567" not in res["summary"]

@pytest.mark.asyncio
async def test_rate_limit_and_retry_behavior():
    provider = DeepSeekProvider(api_key="sk-test-key-1234567890", max_retries=2)
    
    resp_429 = MagicMock()
    resp_429.status_code = 429
    resp_429.text = "Rate limit exceeded"
    
    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": json.dumps({"status": "SUCCESS", "summary": "Recovered."})}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10}
    }
    
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        # First attempt 429, second attempt succeeds 200
        mock_post.side_effect = [resp_429, resp_200]
        
        res = await provider.generate_structured_output("sys", "user", {})
        assert res["status"] == "SUCCESS"
        assert res["summary"] == "Recovered."
        assert res["_usage"]["retries"] == 1

@pytest.mark.asyncio
async def test_timeout_and_network_error():
    provider = DeepSeekProvider(api_key="sk-test-key-1234567890", max_retries=1)
    
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.TimeoutException("Read timed out")
        res = await provider.generate_structured_output("sys", "user", {})
        assert res["status"] == "FAILED"
        assert "timed out" in res["summary"].lower()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.ConnectError("Connection refused to api.deepseek.com")
        res = await provider.generate_structured_output("sys", "user", {})
        assert res["status"] == "FAILED"
        assert "network error" in res["summary"].lower()

@pytest.mark.asyncio
async def test_provider_switching():
    # Verify switching via settings.LLM_PROVIDER
    settings.REAL_AI_TEST = False
    settings.LLM_PROVIDER = "deepseek"
    p_deepseek = get_llm_provider()
    assert isinstance(p_deepseek, DeepSeekProvider)
    
    settings.LLM_PROVIDER = "anthropic"
    p_anthropic = get_llm_provider()
    from backend.services.llm.anthropic_provider import AnthropicProvider
    assert isinstance(p_anthropic, AnthropicProvider)
    
    settings.LLM_PROVIDER = "mock"
    p_mock = get_llm_provider()
    assert isinstance(p_mock, MockProvider)

@pytest.mark.asyncio
async def test_agent_executor_with_deepseek_integration():
    from backend.services.agent_executor import AgentExecutor
    from backend.models.agent import AgentRun, AgentType
    from backend.core.database import AsyncSessionLocal
    
    settings.LLM_PROVIDER = "deepseek"
    settings.DEEPSEEK_API_KEY = "sk-test-deepseek-secret-12345"
    
    executor = AgentExecutor()
    assert isinstance(executor.provider, DeepSeekProvider)
    
    agent_run = AgentRun(
        id=str(uuid.uuid4()),
        task_id="task_ds_1",
        project_id="proj_ds_1",
        agent_id="Core",
        organization_id="org_ds_1"
    )
    
    mock_payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps({
                        "status": "SUCCESS",
                        "summary": "Implemented with DeepSeek Flash.",
                        "findings": [],
                        "changes": ["routes/profile.py"],
                        "recommendations": [],
                        "risks": [],
                        "next_actions": [],
                        "artifacts": {"lines": 42},
                        "requires_approval": False
                    })
                }
            }
        ],
        "usage": {
            "prompt_tokens": 350,
            "completion_tokens": 120,
            "total_tokens": 470
        }
    }
    
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_payload
    
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        
        input_data = {
            "advanced_intelligence_context": "=== PROJECT CONTEXT ===\nSeparation of concerns.",
            "upstream_artifacts": [{"type": "schema", "table": "users"}]
        }
        
        res = await executor.execute_run(agent_run, AgentType.BACKEND_ENGINEER, input_data)
        
        assert res["status"] == "SUCCESS"
        assert agent_run.status == "COMPLETED"
        assert agent_run.model == settings.DEEPSEEK_MODEL
        assert agent_run.tokens_input == 350
        assert agent_run.tokens_output == 120
        assert agent_run.estimated_cost > 0.0
        
        # Verify secret scrubbing in output
        assert "sk-test-deepseek-secret-12345" not in agent_run.output_text
        assert "sk-test-deepseek-secret-12345" not in agent_run.tool_calls_json
        
    # Reset
    settings.LLM_PROVIDER = "mock"
