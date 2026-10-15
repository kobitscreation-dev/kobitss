"""
Thinking Engine for Kobits Autonomous Software Engineering.

Provides first-class "Big Think" / Extended Reasoning orchestration:
1. Detects reasoning-capable models (Claude 3.7 Sonnet, OpenAI o1/o3-mini, Gemini 2.5 Pro/Flash, DeepSeek-R1).
2. Manages thinking budgets and reasoning effort levels across providers.
3. Formats thinking parameters for each provider's native protocol:
   - Anthropic: `thinking: {"type": "enabled", "budget_tokens": ...}` + `temperature: 1.0`
   - Gemini: `thinkingConfig: {"thinkingBudget": ...}`
   - OpenAI: `reasoning_effort: "low" | "medium" | "high"`
   - DeepSeek: `reasoning_content` extraction
4. Extracts and aggregates chain-of-thought traces.
5. Streams real-time thinking tokens to WebSocket clients and the live terminal.
"""

import os
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable

logger = logging.getLogger("kobits.thinking_engine")

# Models known to have native reasoning / extended thinking
REASONING_MODELS = {
    # Anthropic Claude 3.7+
    "claude-3-7-sonnet",
    "claude-3-7-sonnet-20250219",
    "claude-3.7-sonnet",
    # OpenAI Reasoning Models
    "o1",
    "o1-preview",
    "o1-mini",
    "o3-mini",
    "o3",
    # DeepSeek Reasoning Models
    "deepseek-r1",
    "deepseek-reasoner",
    "deepseek-v4.1-flash:free",
    # Google Gemini & Argon Frontier Reasoning Models
    "gemini-4-argon",
    "argon",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
}


@dataclass
class ThinkingConfig:
    """Configuration for Big Think / Extended Reasoning."""
    enabled: bool = False
    budget_tokens: int = 4096
    reasoning_effort: str = "medium"  # "low", "medium", "high"
    stream_thoughts: bool = True
    min_budget: int = 1024
    max_budget: int = 32768

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "budget_tokens": self.budget_tokens,
            "reasoning_effort": self.reasoning_effort,
            "stream_thoughts": self.stream_thoughts,
        }


class ThinkingEngine:
    """Central manager for Big Think reasoning modes."""

    @staticmethod
    def is_reasoning_model(model_name: Optional[str]) -> bool:
        if not model_name:
            return False
        clean = model_name.lower().strip()
        for rm in REASONING_MODELS:
            if rm in clean:
                return True
        return any(k in clean for k in ("thinking", "reasoner", "-r1", "o1-", "o3-", "3-7-sonnet", "3.7-sonnet", "2.5-pro"))

    @classmethod
    def resolve_config(
        cls,
        model_name: Optional[str] = None,
        requested_budget: Optional[int] = None,
        requested_effort: Optional[str] = None,
        explicit_enable: Optional[bool] = None,
    ) -> ThinkingConfig:
        """
        Resolves thinking configuration based on model capabilities, env vars, and caller preferences.
        """
        env_big_think = os.environ.get("BIG_THINK", "").lower() in ("1", "true", "yes", "on")
        env_budget = os.environ.get("THINKING_BUDGET")
        env_effort = os.environ.get("REASONING_EFFORT")

        # Determine if enabled
        enabled = False
        if explicit_enable is not None:
            enabled = explicit_enable
        elif env_big_think:
            enabled = True
        elif cls.is_reasoning_model(model_name):
            enabled = True

        # Determine budget
        budget = 4096
        if requested_budget is not None and requested_budget > 0:
            budget = requested_budget
        elif env_budget and env_budget.isdigit():
            budget = int(env_budget)

        # Clamp budget to safe limits
        budget = max(1024, min(budget, 32768))

        # Determine reasoning effort
        effort = requested_effort or env_effort or "medium"
        if effort not in ("low", "medium", "high"):
            effort = "medium"

        return ThinkingConfig(
            enabled=enabled,
            budget_tokens=budget,
            reasoning_effort=effort,
            stream_thoughts=True,
        )

    @classmethod
    async def broadcast_thought(
        cls,
        mission_id: Optional[str],
        agent_role: str,
        thought_chunk: str,
        stage: Optional[str] = None,
    ):
        """
        Broadcasts an agent thinking trace to connected WebSocket clients and live terminal.
        """
        if not mission_id or not thought_chunk:
            return

        clean_thought = thought_chunk.strip()
        if not clean_thought:
            return

        try:
            from backend.services.websocket_manager import manager
            await manager.broadcast(mission_id, {
                "type": "agent_thinking",
                "mission_id": mission_id,
                "agent": agent_role,
                "stage": stage or "reasoning",
                "thought": clean_thought,
                "message": f"[{agent_role}] Thinking: {clean_thought[:180]}...",
            })
        except Exception as e:
            logger.debug(f"Failed to broadcast agent thought: {e}")

    @staticmethod
    def extract_thoughts_from_anthropic(content_blocks: List[Dict[str, Any]]) -> List[str]:
        """Extracts thinking blocks from Anthropic Claude response."""
        thoughts = []
        for block in content_blocks:
            if isinstance(block, dict) and block.get("type") == "thinking":
                thought_text = block.get("thinking", "")
                if thought_text:
                    thoughts.append(thought_text)
        return thoughts

    @staticmethod
    def extract_thoughts_from_gemini(parts: List[Dict[str, Any]]) -> List[str]:
        """Extracts thought parts from Gemini response."""
        thoughts = []
        for p in parts:
            if isinstance(p, dict):
                if p.get("thought") is True or "thought" in p:
                    text = p.get("text") or str(p.get("thought"))
                    if text:
                        thoughts.append(text)
        return thoughts

    @staticmethod
    def extract_thoughts_from_openai_or_deepseek(message: Dict[str, Any]) -> List[str]:
        """Extracts reasoning_content from DeepSeek/OpenAI response message."""
        thoughts = []
        if isinstance(message, dict):
            reasoning = message.get("reasoning_content") or message.get("reasoning")
            if reasoning and isinstance(reasoning, str):
                thoughts.append(reasoning)
        return thoughts
