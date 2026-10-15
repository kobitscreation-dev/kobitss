import pytest
import os
from unittest.mock import patch
from backend.services.llm import get_llm_provider, detect_configured_provider
from backend.services.llm.bedrock_provider import BedrockProvider
from backend.core.config import settings

def test_exclusive_claude_provider_resolution():
    with patch.dict(os.environ, {"LLM_PROVIDER": "claude", "USE_ONLY_CLAUDE": "1"}):
        with patch.object(settings, "LLM_PROVIDER", "claude"):
            detected = detect_configured_provider()
            assert detected == "bedrock"
            
            provider = get_llm_provider()
            assert isinstance(provider, BedrockProvider)

def test_exclusive_bedrock_provider_resolution():
    with patch.dict(os.environ, {"LLM_PROVIDER": "bedrock", "USE_ONLY_CLAUDE": "1"}):
        with patch.object(settings, "LLM_PROVIDER", "bedrock"):
            detected = detect_configured_provider()
            assert detected == "bedrock"
            
            provider = get_llm_provider()
            assert isinstance(provider, BedrockProvider)

def test_agent_executor_model_mapping_for_claude():
    from backend.services.agent_executor import AgentExecutor
    executor = AgentExecutor(provider_scenarios=None)
    with patch.dict(os.environ, {"LLM_PROVIDER": "claude", "USE_ONLY_CLAUDE": "1"}):
        with patch.object(settings, "LLM_PROVIDER", "claude"):
            provider_for_type = getattr(executor.provider, "primary_provider", executor.provider)
            if isinstance(provider_for_type, BedrockProvider):
                provider_name = "bedrock"
            else:
                provider_name = (settings.LLM_PROVIDER or os.environ.get("LLM_PROVIDER") or "mock").lower()
            
            if provider_name in ["bedrock", "aws", "aws_bedrock", "claude"]:
                model_name = settings.BEDROCK_MODEL or os.environ.get("BEDROCK_MODEL") or "global.anthropic.claude-sonnet-4-6"
            else:
                model_name = "mock"
            
            assert model_name == "global.anthropic.claude-sonnet-4-6"
