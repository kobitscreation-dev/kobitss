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
