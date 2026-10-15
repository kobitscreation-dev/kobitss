import pytest
import inspect
from unittest.mock import AsyncMock, MagicMock, patch
from backend.services.llm.bedrock_provider import BedrockProvider
from backend.services.llm.provider import LLMProvider

def test_bedrock_provider_inherits_and_accepts_kwargs():
    bp = BedrockProvider()
    assert isinstance(bp, LLMProvider)
    sig = inspect.signature(bp.generate_structured_output)
    
    # Must accept mission_id, agent_role, big_think, thinking_budget, and var_kwargs
    params = sig.parameters
    assert "mission_id" in params
    assert "agent_role" in params
    assert "big_think" in params
    assert "thinking_budget" in params
    assert any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())

@pytest.mark.asyncio
async def test_bedrock_provider_invokes_client_with_extra_kwargs():
    bp = BedrockProvider()
    
    mock_response = MagicMock()
    mock_response.content = [MagicMock(type="text", text='{"status": "SUCCESS", "summary": "Audit completed"}')]
    mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)
    
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)
    
    with patch.object(bp, "_get_client", return_value=mock_client):
        result = await bp.generate_structured_output(
            system_prompt="You are a security auditor.",
            user_prompt="Audit repo for OWASP vulnerabilities.",
            schema={"type": "object", "properties": {"status": {"type": "string"}}},
            model="global.anthropic.claude-sonnet-4-6",
            mission_id="test_mission_001",
            agent_role="TECHNICAL_LEAD",
            big_think=True,
            thinking_budget=2048,
            reasoning_effort="high",
            unexpected_custom_arg="safe_passthrough",
        )
        
        assert result.get("status") == "SUCCESS"
        assert result.get("summary") == "Audit completed"
        assert "_usage" in result
        assert result["_usage"]["input_tokens"] == 100
        assert result["_usage"]["output_tokens"] == 50
        
        # Verify client was called
        mock_client.messages.create.assert_called_once()
        call_kwargs = mock_client.messages.create.call_args[1]
        assert "messages" in call_kwargs
        assert "system" in call_kwargs
        assert call_kwargs["model"] == "global.anthropic.claude-sonnet-4-6"
