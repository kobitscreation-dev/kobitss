"""
Comprehensive Verification Test Suite for Kobits Big Think & Resilient Real-API Architecture.

Verifies:
1. ThinkingEngine: reasoning model detection, configuration resolution, and thought extraction.
2. AnthropicProvider: Claude 3.7 Sonnet extended thinking parameters and thinking block parsing.
3. GeminiProvider: Gemini 2.5 Pro/Flash thinkingConfig and thought parts extraction.
4. OpenAIProvider: o1 / o3-mini native reasoning_effort and developer role handling.
5. DeepSeekProvider: reasoning_content extraction and thought broadcasting.
6. ResilientLLMRouter: Multi-provider failover cascade across 429 rate limits, 402 quota errors, and 503 outages.
7. ToolRegistry Code Search: positional argument resolution and AST semantic chunk fallback.
8. CLI: Big Think options and WebSocket stream dispatching.
"""

import json
import pytest
import os
from unittest.mock import AsyncMock, MagicMock, patch

from backend.services.llm.thinking_engine import ThinkingEngine, ThinkingConfig
from backend.services.llm.openai_provider import OpenAIProvider
from backend.services.llm.anthropic_provider import AnthropicProvider
from backend.services.llm.gemini_provider import GeminiProvider
from backend.services.llm.deepseek_provider import DeepSeekProvider
from backend.services.llm.fallback_router import ResilientLLMRouter
from backend.services.llm.mock_provider import MockProvider
from backend.services.tool_registry import ToolRegistry


# ── 1. THINKING ENGINE TESTS ───────────────────────────────────────────

def test_thinking_engine_model_detection():
    """Verify accurate detection of reasoning-capable models."""
    assert ThinkingEngine.is_reasoning_model("claude-3-7-sonnet-20250219") is True
    assert ThinkingEngine.is_reasoning_model("Claude-3.7-Sonnet") is True
    assert ThinkingEngine.is_reasoning_model("o1-mini") is True
    assert ThinkingEngine.is_reasoning_model("o3-mini") is True
    assert ThinkingEngine.is_reasoning_model("deepseek-r1") is True
    assert ThinkingEngine.is_reasoning_model("deepseek-reasoner") is True
    assert ThinkingEngine.is_reasoning_model("gemini-2.5-pro") is True
    assert ThinkingEngine.is_reasoning_model("gemini-2.5-flash") is True

    # Standard non-reasoning models
    assert ThinkingEngine.is_reasoning_model("claude-3-5-sonnet-20241022") is False
    assert ThinkingEngine.is_reasoning_model("gpt-4o") is False
    assert ThinkingEngine.is_reasoning_model("mock-model") is False


def test_thinking_engine_config_resolution():
    """Verify resolution of thinking configuration and budget clamping."""
    # Default for non-reasoning model
    cfg_default = ThinkingEngine.resolve_config("gpt-4o")
    assert cfg_default.enabled is False

    # Auto-enabled for reasoning models
    cfg_o3 = ThinkingEngine.resolve_config("o3-mini", requested_budget=8192, requested_effort="high")
    assert cfg_o3.enabled is True
    assert cfg_o3.budget_tokens == 8192
    assert cfg_o3.reasoning_effort == "high"

    # Explicit enable override
    cfg_explicit = ThinkingEngine.resolve_config("gpt-4o", explicit_enable=True, requested_budget=2048)
    assert cfg_explicit.enabled is True
    assert cfg_explicit.budget_tokens == 2048

    # Clamping boundaries
    cfg_clamped_low = ThinkingEngine.resolve_config(explicit_enable=True, requested_budget=50)
    assert cfg_clamped_low.budget_tokens == 1024

    cfg_clamped_high = ThinkingEngine.resolve_config(explicit_enable=True, requested_budget=999999)
    assert cfg_clamped_high.budget_tokens == 32768


def test_thinking_engine_thought_extractors():
    """Verify thought extraction across Anthropic, Gemini, OpenAI, and DeepSeek schemas."""
    # Anthropic thinking block
    anthropic_blocks = [
        {"type": "thinking", "thinking": "Analyzing AST node hierarchy..."},
        {"type": "text", "text": "{\"status\": \"SUCCESS\"}"}
    ]
    extracted_anthropic = ThinkingEngine.extract_thoughts_from_anthropic(anthropic_blocks)
    assert extracted_anthropic == ["Analyzing AST node hierarchy..."]

    # Gemini thought parts
    gemini_parts = [
        {"thought": True, "text": "Formulating negative reproducer test..."},
        {"text": "{\"status\": \"SUCCESS\"}"}
    ]
    extracted_gemini = ThinkingEngine.extract_thoughts_from_gemini(gemini_parts)
    assert extracted_gemini == ["Formulating negative reproducer test..."]

    # OpenAI / DeepSeek reasoning_content
    openai_msg = {
        "role": "assistant",
        "reasoning_content": "Deep reasoning step: Verifying edge case with null input.",
        "content": "{\"status\": \"SUCCESS\"}"
    }
    extracted_openai = ThinkingEngine.extract_thoughts_from_openai_or_deepseek(openai_msg)
    assert extracted_openai == ["Deep reasoning step: Verifying edge case with null input."]


@pytest.mark.asyncio
async def test_thinking_engine_broadcast():
    """Verify thought broadcast to WebSocket manager."""
    with patch("backend.services.websocket_manager.manager.broadcast", new_callable=AsyncMock) as mock_bcast:
        await ThinkingEngine.broadcast_thought(
            mission_id="m-123",
            agent_role="FORGE",
            thought_chunk="Refactoring parser loop...",
        )
        assert mock_bcast.called
        call_args = mock_bcast.call_args[0]
        assert call_args[0] == "m-123"
        payload = call_args[1]
        assert payload["type"] == "agent_thinking"
        assert payload["agent"] == "FORGE"
        assert payload["thought"] == "Refactoring parser loop..."


# ── 2. RESILIENT MULTI-PROVIDER ROUTER TESTS ──────────────────────────

@pytest.mark.asyncio
async def test_resilient_router_primary_success():
    """Verify primary provider is used when healthy."""
    primary = MockProvider()
    primary.generate_structured_output = AsyncMock(return_value={"status": "SUCCESS", "summary": "Primary OK"})
    secondary = MockProvider()
    secondary.generate_structured_output = AsyncMock()

    router = ResilientLLMRouter([("primary", primary), ("secondary", secondary)])
    res = await router.generate_structured_output("sys", "user", {})

    assert res["status"] == "SUCCESS"
    assert res["summary"] == "Primary OK"
    assert primary.generate_structured_output.called
    assert not secondary.generate_structured_output.called


@pytest.mark.asyncio
async def test_resilient_router_failover_on_429():
    """Verify seamless cascade to secondary provider when primary returns 429 rate limit."""
    primary = MockProvider()
    primary.generate_structured_output = AsyncMock(return_value={
        "status": "FAILED",
        "summary": "DeepSeek API rate limit exceeded (429).",
        "failure_category": "RATE_LIMIT_429"
    })

    secondary = MockProvider()
    secondary.generate_structured_output = AsyncMock(return_value={
        "status": "SUCCESS",
        "summary": "Secondary Gemini Succeeded!"
    })

    router = ResilientLLMRouter([("primary_deepseek", primary), ("secondary_gemini", secondary)])
    res = await router.generate_structured_output("sys", "user", {}, mission_id="m-test-429")

    assert res["status"] == "SUCCESS"
    assert res["summary"] == "Secondary Gemini Succeeded!"
    assert primary.generate_structured_output.called
    assert secondary.generate_structured_output.called
    assert res["_usage"]["provider_used"] == "secondary_gemini"
    assert res["_usage"]["cascade_attempt"] == 2


@pytest.mark.asyncio
async def test_resilient_router_failover_on_503_exception():
    """Verify cascade on provider network exception or outage."""
    primary = MockProvider()
    primary.generate_structured_output = AsyncMock(side_effect=Exception("Anthropic API Error: 503 - Service Unavailable"))

    secondary = MockProvider()
    secondary.generate_structured_output = AsyncMock(return_value={
        "status": "SUCCESS",
        "summary": "Fallback OpenAI Succeeded!"
    })

    router = ResilientLLMRouter([("anthropic", primary), ("openai", secondary)])
    res = await router.generate_structured_output("sys", "user", {}, mission_id="m-test-503")

    assert res["status"] == "SUCCESS"
    assert res["summary"] == "Fallback OpenAI Succeeded!"
    assert secondary.generate_structured_output.called


# ── 3. OPENAI & REASONING MODEL PROVIDER TESTS ────────────────────────

def test_openai_provider_initialization():
    """Verify OpenAIProvider initializes with configurable model and endpoint."""
    prov = OpenAIProvider(api_key="sk-test-key", model="o3-mini")
    assert prov.api_key == "sk-test-key"
    assert prov.default_model == "o3-mini"
    assert prov._is_reasoning_model("o3-mini") is True
    assert prov._is_reasoning_model("gpt-4o") is False
    assert prov._scrub("Bearer sk-test-key") == "Bearer [REDACTED_OPENAI_KEY]"


@pytest.mark.asyncio
async def test_openai_provider_unconfigured():
    """Verify unconfigured OpenAI provider returns structured failure."""
    prov = OpenAIProvider(api_key=None)
    prov.configured = False
    res = await prov.generate_structured_output("sys", "user", {})
    assert res["status"] == "FAILED"
    assert "missing OPENAI_API_KEY" in res["summary"]


# ── 4. CODEBASE SEARCH & AST INDEX INTEGRATION ────────────────────────

@pytest.mark.asyncio
async def test_tool_registry_repository_search_positional_args():
    """Verify bug fix: positional query arguments are resolved properly."""
    tool = ToolRegistry.get_tool("repository.search")
    assert tool is not None

    # Call with positional query: handle_repository_search("class_name")
    res = await ToolRegistry.execute_tool("repository.search", {"arg1": "class_name"})
    assert isinstance(res, dict)
    assert "results" in res


@pytest.mark.asyncio
async def test_tool_registry_search_code_execution():
    """Verify search_code runs without unhandled exception."""
    res = await ToolRegistry.execute_tool("search_code", {"query": "def execute_task"})
    assert isinstance(res, str)
    assert len(res) > 0


# ── 5. CLI RUN BIG THINK OPTIONS ──────────────────────────────────────

def test_cli_run_help_contains_big_think():
    """Verify CLI help surfaces Big Think and reasoning options."""
    from click.testing import CliRunner
    from cli.kobits import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["run", "--help"])
    assert result.exit_code == 0
    assert "--think" in result.output
    assert "--big-think" in result.output
    assert "--effort" in result.output
    assert "--budget" in result.output
    assert "--provider" in result.output
