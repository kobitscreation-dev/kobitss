import os
from backend.services.llm.provider import LLMProvider
from backend.services.llm.anthropic_provider import AnthropicProvider
from backend.services.llm.gemini_provider import GeminiProvider
from backend.services.llm.deepseek_provider import DeepSeekProvider
from backend.services.llm.mock_provider import MockProvider
from backend.services.llm.bedrock_provider import BedrockProvider
from backend.core.config import settings


def detect_configured_provider() -> str:
    """Intelligently detects which LLM provider has credentials configured."""
    explicit = settings.LLM_PROVIDER or os.environ.get("LLM_PROVIDER")
    if explicit and explicit.lower() != "auto":
        return explicit.lower()

    if settings.DEEPSEEK_API_KEY or os.environ.get("DEEPSEEK_API_KEY"):
        return "deepseek"
    if settings.ANTHROPIC_API_KEY or os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if settings.AWS_BEDROCK_API_KEY or os.environ.get("AWS_BEDROCK_API_KEY") or settings.AWS_ACCESS_KEY_ID or os.environ.get("AWS_ACCESS_KEY_ID"):
        return "bedrock"
    if settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"

    return "mock"


def get_llm_provider(scenarios=None) -> LLMProvider:
    provider_name = detect_configured_provider()
    
    if provider_name == "mock":
        return MockProvider(deterministic_scenarios=scenarios)
    elif provider_name == "deepseek":
        return DeepSeekProvider()
    elif provider_name == "gemini":
        return GeminiProvider()
    elif provider_name == "openai":
        return DeepSeekProvider(
            api_key=os.environ.get("OPENAI_API_KEY"),
            base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
        )
    elif provider_name == "anthropic":
        anthropic_key = settings.ANTHROPIC_API_KEY or os.environ.get("ANTHROPIC_API_KEY")
        if anthropic_key and anthropic_key.startswith("ABSK"):
            return BedrockProvider(api_key=anthropic_key)
        return AnthropicProvider()
    elif provider_name in ["bedrock", "aws", "aws_bedrock"]:
        bedrock_key = settings.AWS_BEDROCK_API_KEY or os.environ.get("AWS_BEDROCK_API_KEY") or settings.ANTHROPIC_API_KEY or os.environ.get("ANTHROPIC_API_KEY")
        return BedrockProvider(api_key=bedrock_key)
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {provider_name}")
