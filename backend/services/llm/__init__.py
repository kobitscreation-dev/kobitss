import os
from backend.services.llm.provider import LLMProvider
from backend.services.llm.anthropic_provider import AnthropicProvider
from backend.services.llm.gemini_provider import GeminiProvider
from backend.services.llm.deepseek_provider import DeepSeekProvider
from backend.services.llm.mock_provider import MockProvider
from backend.services.llm.bedrock_provider import BedrockProvider
from backend.core.config import settings

def get_llm_provider(scenarios=None) -> LLMProvider:
    provider_name = (os.environ.get("LLM_PROVIDER") or settings.LLM_PROVIDER).lower()
    
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY") or settings.ANTHROPIC_API_KEY
    bedrock_key = os.environ.get("AWS_BEDROCK_API_KEY") or settings.AWS_BEDROCK_API_KEY
    if provider_name in ["bedrock", "aws", "aws_bedrock"] or (anthropic_key and anthropic_key.startswith("ABSK")):
        return BedrockProvider(api_key=bedrock_key or anthropic_key)
    elif provider_name == "anthropic":
        return AnthropicProvider()
    elif provider_name == "gemini":
        return GeminiProvider()
    elif provider_name == "deepseek":
        return DeepSeekProvider()
    elif provider_name == "mock":
        return MockProvider(deterministic_scenarios=scenarios)
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {provider_name}")
