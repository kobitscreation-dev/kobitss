import asyncio
import os
import sys

from backend.core.config import settings
from backend.services.llm import get_llm_provider
from backend.services.llm.mock_provider import MockProvider
from backend.services.llm.anthropic_provider import AnthropicProvider
from backend.services.llm.gemini_provider import GeminiProvider

def reset_settings(original_provider, original_anthropic, original_gemini, original_real_test):
    settings.LLM_PROVIDER = original_provider
    settings.ANTHROPIC_API_KEY = original_anthropic
    settings.GEMINI_API_KEY = original_gemini
    settings.REAL_AI_TEST = original_real_test

async def run_tests():
    original_provider = settings.LLM_PROVIDER
    original_anthropic = settings.ANTHROPIC_API_KEY
    original_gemini = settings.GEMINI_API_KEY
    original_real_test = settings.REAL_AI_TEST
    
    try:
        print("Testing mock provider default...")
        settings.LLM_PROVIDER = "mock"
        provider = get_llm_provider()
        assert isinstance(provider, MockProvider)
        
        print("Testing anthropic graceful missing key...")
        settings.LLM_PROVIDER = "anthropic"
        settings.ANTHROPIC_API_KEY = None
        settings.REAL_AI_TEST = False
        provider = get_llm_provider()
        assert isinstance(provider, AnthropicProvider)
        assert not provider.configured
        
        print("Testing anthropic graceful execution...")
        res = await provider.generate_structured_output("sys", "user", {}, "model")
        assert res["status"] == "FAILED"
        assert "not configured" in res["summary"]
        
        print("Testing anthropic hard crash in real test...")
        settings.REAL_AI_TEST = True
        try:
            get_llm_provider()
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "ANTHROPIC_API_KEY is not set" in str(e)
            
        print("Testing gemini graceful missing key...")
        settings.LLM_PROVIDER = "gemini"
        settings.GEMINI_API_KEY = None
        settings.REAL_AI_TEST = False
        provider = get_llm_provider()
        assert isinstance(provider, GeminiProvider)
        assert not provider.configured
        
        print("Testing mock provider execution...")
        settings.LLM_PROVIDER = "mock"
        provider = get_llm_provider()
        res = await provider.generate_structured_output("sys", "user", {}, "model")
        assert res["status"] == "SUCCESS"
        assert "_usage" in res
        
        print("All LLM Provider tests PASS.")
        
    finally:
        reset_settings(original_provider, original_anthropic, original_gemini, original_real_test)

if __name__ == "__main__":
    asyncio.run(run_tests())
