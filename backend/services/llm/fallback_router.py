"""
Resilient LLM Router with Multi-Provider Failover Cascade.

Ensures zero-downtime mission execution:
When real-world frontier LLMs encounter 429 Rate Limits, 402 Quota Depletion,
503 Provider Outages, or Circuit Breaker trips, this router automatically
cascades execution to the next configured fallback provider in priority order.
"""

import os
import logging
from typing import List, Dict, Any, Optional, Tuple

from backend.services.llm.provider import LLMProvider
from backend.core.config import settings

logger = logging.getLogger("kobits.llm_fallback_router")


class ResilientLLMRouter(LLMProvider):
    """
    Wraps primary and secondary providers into an auto-cascading resilient engine.
    """

    def __init__(self, providers: Optional[List[Tuple[str, LLMProvider]]] = None):
        self.providers: List[Tuple[str, LLMProvider]] = providers or []

    def add_provider(self, name: str, provider: LLMProvider):
        self.providers.append((name, provider))

    @property
    def primary_name(self) -> str:
        return self.providers[0][0] if self.providers else "none"

    @property
    def primary_provider(self) -> Optional[LLMProvider]:
        return self.providers[0][1] if self.providers else None

    def is_recoverable_error(self, err_msg: str, failure_cat: Optional[str]) -> bool:
        """Determines if a provider failure should trigger automatic fallback."""
        low = (err_msg or "").lower()
        cat = (failure_cat or "").upper()

        if cat in ("RATE_LIMIT_429", "SERVER_ERROR_5XX", "TIMEOUT", "DNS_ERROR", "PROVIDER", "AUTH_ERROR"):
            return True

        indicators = (
            "429", "rate limit", "rate_limit", "quota", "overloaded",
            "500", "502", "503", "504", "server error", "circuit",
            "timeout", "timed out", "connection", "insufficient_quota",
            "credits", "credit balance", "billing", "unauthorized", "401", "402", "403"
        )
        return any(ind in low for ind in indicators)

    async def generate_structured_output(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: Dict[str, Any],
        model: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        max_turns: int = 15,
        tool_executor=None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Executes request against primary provider, automatically cascading to fallbacks on failure.
        """
        if not self.providers:
            raise ValueError("ResilientLLMRouter has no providers configured.")

        mission_id = kwargs.get("mission_id")
        last_error_summary = "No providers succeeded."
        last_failure_cat = "PROVIDER"

        for idx, (p_name, provider) in enumerate(self.providers):
            try:
                # If cascading to a fallback provider, let provider use its own native default model
                provider_model = model if idx == 0 else None
                # Call provider
                result = await provider.generate_structured_output(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    schema=schema,
                    model=provider_model,
                    tools=tools,
                    max_turns=max_turns,
                    tool_executor=tool_executor,
                    **kwargs,
                )

                # Check if provider returned soft failure
                if isinstance(result, dict) and result.get("status") in ("FAILED", "ERROR"):
                    result["status"] = "FAILED"
                    summary = result.get("summary", "")
                    cat = result.get("failure_category")
                    if self.is_recoverable_error(summary, cat) and idx < len(self.providers) - 1:
                        next_name = self.providers[idx + 1][0]
                        logger.warning(
                            f"[ResilientLLMRouter] Provider '{p_name}' returned error: {summary}. "
                            f"Cascading to fallback provider '{next_name}'."
                        )
                        if mission_id:
                            await self._notify_fallback(mission_id, p_name, next_name, summary)
                        last_error_summary = summary
                        last_failure_cat = cat or "PROVIDER"
                        continue
                    if idx > 0 and last_error_summary and last_error_summary != "No providers succeeded.":
                        result["summary"] = f"{last_error_summary} | Fallback ({p_name}): {summary}"

                # Successful execution or non-recoverable error
                if isinstance(result, dict):
                    if "_usage" not in result:
                        result["_usage"] = {}
                    result["_usage"]["provider_used"] = p_name
                    result["_usage"]["cascade_attempt"] = idx + 1
                return result

            except Exception as exc:
                exc_str = str(exc)
                logger.warning(f"[ResilientLLMRouter] Provider '{p_name}' raised exception: {exc_str}")
                if self.is_recoverable_error(exc_str, None) and idx < len(self.providers) - 1:
                    next_name = self.providers[idx + 1][0]
                    if mission_id:
                        await self._notify_fallback(mission_id, p_name, next_name, exc_str)
                    last_error_summary = exc_str
                    continue
                else:
                    if idx == len(self.providers) - 1:
                        raise exc
                    next_name = self.providers[idx + 1][0]
                    if mission_id:
                        await self._notify_fallback(mission_id, p_name, next_name, exc_str)
                    continue

        return {
            "status": "FAILED",
            "summary": f"All configured providers in fallback chain failed. Last error: {last_error_summary}",
            "failure_category": last_failure_cat,
        }

    async def _notify_fallback(self, mission_id: str, failed_provider: str, next_provider: str, reason: str):
        """Broadcasts fallback event to live terminal WebSocket."""
        try:
            from backend.services.websocket_manager import manager
            await manager.broadcast(mission_id, {
                "type": "provider_fallback",
                "mission_id": mission_id,
                "failed_provider": failed_provider,
                "next_provider": next_provider,
                "reason": reason[:150],
                "message": (
                    f"⚠️ Primary provider ({failed_provider}) encountered error: {reason[:80]}... "
                    f"Automatically cascading to fallback provider ({next_provider})."
                ),
            })
        except Exception:
            pass
