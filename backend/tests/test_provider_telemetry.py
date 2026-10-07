import json
from unittest.mock import patch, MagicMock, AsyncMock
from backend.services.llm.anthropic_provider import AnthropicProvider
from backend.services.llm.deepseek_provider import DeepSeekProvider
from backend.core.config import settings

import pytest
@pytest.mark.asyncio
async def test_anthropic_provider_telemetry():
    provider = AnthropicProvider(api_key="mock", base_url="http://mock")
    
    mock_response_data = {
        "model": "claude-3-5-sonnet-20241022",
        "usage": {
            "input_tokens": 150,
            "output_tokens": 50
        },
        "content": [
            {
                "type": "text",
                "text": "```json\n{\"status\": \"SUCCESS\", \"summary\": \"Tested\"}\n```"
            }
        ]
    }
    
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_response_data
        mock_post.return_value = mock_resp
        
        result = await provider.generate_structured_output(
            system_prompt="sys",
            user_prompt="user",
            schema={},
            model="claude-3-5-sonnet-20241022"
        )
        
        assert result["status"] == "SUCCESS"
        assert "_usage" in result
        usage = result["_usage"]
        assert usage["input_tokens"] == 150
        assert usage["output_tokens"] == 50
        assert usage["total_tokens"] == 200
        assert usage["model_used"] == "claude-3-5-sonnet-20241022"
        assert "estimated_cost" in usage


@pytest.mark.asyncio
async def test_deepseek_provider_telemetry():
    provider = DeepSeekProvider(api_key="mock", base_url="http://mock")
    
    mock_response_data = {
        "model": "deepseek-coder",
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 30
        },
        "choices": [
            {
                "message": {
                    "content": "```json\n{\"status\": \"SUCCESS\", \"summary\": \"Tested Deepseek\"}\n```"
                }
            }
        ]
    }
    
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.json.return_value = mock_response_data
        mock_post.return_value = mock_resp
        
        result = await provider.generate_structured_output(
            system_prompt="sys",
            user_prompt="user",
            schema={},
            model="deepseek-coder"
        )
        
        assert result["status"] == "SUCCESS"
        assert "_usage" in result
        usage = result["_usage"]
        assert usage["input_tokens"] == 120
        assert usage["output_tokens"] == 30
        assert usage["total_tokens"] == 150
        assert usage["model_used"] == "deepseek-coder"
        assert "estimated_cost" in usage
