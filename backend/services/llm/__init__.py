import os
from typing import List, Tuple, Optional
from backend.services.llm.provider import LLMProvider
from backend.services.llm.anthropic_provider import AnthropicProvider
from backend.services.llm.gemini_provider import GeminiProvider
from backend.services.llm.deepseek_provider import DeepSeekProvider
from backend.services.llm.openai_provider import OpenAIProvider
from backend.services.llm.mock_provider import MockProvider
from backend.services.llm.bedrock_provider import BedrockProvider
from backend.services.llm.fallback_router import ResilientLLMRouter
from backend.services.llm.thinking_engine import ThinkingEngine, ThinkingConfig
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
    if os.environ.get("OPENAI_API_KEY") or getattr(settings, "OPENAI_API_KEY", None):
        return "openai"
    if settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    if settings.AWS_BEDROCK_API_KEY or os.environ.get("AWS_BEDROCK_API_KEY") or settings.AWS_ACCESS_KEY_ID or os.environ.get("AWS_ACCESS_KEY_ID"):
        return "bedrock"

    return "mock"


def build_provider_instance(name: str, scenarios=None) -> LLMProvider:
    """Constructs a single provider instance by name."""
    norm = name.lower()
    if norm == "mock":
        return MockProvider(deterministic_scenarios=scenarios)
    elif norm == "deepseek":
        return DeepSeekProvider()
    elif norm == "gemini":
        return GeminiProvider()
    elif norm == "openai":
        return OpenAIProvider()
    elif norm == "anthropic":
        anthropic_key = settings.ANTHROPIC_API_KEY or os.environ.get("ANTHROPIC_API_KEY")
        if anthropic_key and anthropic_key.startswith("ABSK"):
            return BedrockProvider(api_key=anthropic_key)
        return AnthropicProvider()
    elif norm in ["bedrock", "aws", "aws_bedrock"]:
        bedrock_key = settings.AWS_BEDROCK_API_KEY or os.environ.get("AWS_BEDROCK_API_KEY") or settings.ANTHROPIC_API_KEY or os.environ.get("ANTHROPIC_API_KEY")
        return BedrockProvider(api_key=bedrock_key)
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {name}")


def get_llm_provider(scenarios=None, use_resilient_router: bool = True) -> LLMProvider:
    """
    Returns the configured LLMProvider.
    When multiple real API keys are configured, wraps them into a ResilientLLMRouter
    to provide automatic, transparent failover across providers on 429 rate limits,
    quota exhaustion, or provider outages.
    """
    primary_name = detect_configured_provider()
    primary_provider = build_provider_instance(primary_name, scenarios=scenarios)

    if primary_name == "mock" or not use_resilient_router:
        return primary_provider

    # Discover any secondary configured providers for the resilient cascade
    candidates = ["anthropic", "openai", "gemini", "deepseek", "bedrock"]
    if primary_name in candidates:
        candidates.remove(primary_name)

    cascade: List[Tuple[str, LLMProvider]] = [(primary_name, primary_provider)]

    for candidate_name in candidates:
        has_key = False
        if candidate_name == "anthropic" and (settings.ANTHROPIC_API_KEY or os.environ.get("ANTHROPIC_API_KEY")):
            has_key = True
        elif candidate_name == "openai" and (os.environ.get("OPENAI_API_KEY") or getattr(settings, "OPENAI_API_KEY", None)):
            has_key = True
        elif candidate_name == "gemini" and (settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY")):
            has_key = True
        elif candidate_name == "deepseek" and (settings.DEEPSEEK_API_KEY or os.environ.get("DEEPSEEK_API_KEY")):
            has_key = True
        elif candidate_name == "bedrock" and (settings.AWS_BEDROCK_API_KEY or os.environ.get("AWS_BEDROCK_API_KEY") or settings.AWS_ACCESS_KEY_ID or os.environ.get("AWS_ACCESS_KEY_ID")):
            has_key = True

        if has_key:
            try:
                sec_instance = build_provider_instance(candidate_name)
                cascade.append((candidate_name, sec_instance))
            except Exception:
                pass

    if len(cascade) > 1:
        return ResilientLLMRouter(providers=cascade)

    return primary_provider
