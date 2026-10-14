"""
Official OpenAI LLM Provider for Kobits.

Provides enterprise OpenAI API integration supporting:
1. Frontier reasoning models (o1, o1-mini, o3-mini, o3) with native `reasoning_effort`.
2. Standard production models (gpt-4o, gpt-4o-mini, gpt-4.5-preview).
3. Structured Outputs via `json_schema` response format with strict adherence.
4. Multi-turn tool calling with function execution loops.
5. Circuit breaker resilience, rate limit exponential backoff, and credential scrubbing.
6. Real-time reasoning / Big Think token streaming.
"""

import os
import json
import time
import asyncio
import logging
from typing import List, Dict, Any, Optional
import httpx

from backend.services.llm.provider import LLMProvider
from backend.core.config import settings
from backend.services.llm.provider_circuit_breaker import get_circuit_breaker
from backend.services.llm.thinking_engine import ThinkingEngine, ThinkingConfig

logger = logging.getLogger("kobits.openai_provider")


class OpenAIProvider(LLMProvider):
    """
    OpenAI LLM Provider with first-class reasoning and structured output support.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 120.0,
        max_retries: int = 4,
    ):
        self.api_key = (
            api_key
            or getattr(settings, "OPENAI_API_KEY", None)
            or os.environ.get("OPENAI_API_KEY")
        )
        raw_base = (
            base_url
            or getattr(settings, "OPENAI_BASE_URL", None)
            or os.environ.get("OPENAI_BASE_URL")
            or "https://api.openai.com/v1"
        )
        self.base_url = raw_base.rstrip("/")
        self.default_model = (
            model
            or getattr(settings, "OPENAI_MODEL", None)
            or os.environ.get("OPENAI_MODEL")
            or "gpt-4o"
        )
        self.timeout = timeout
        self.max_retries = max_retries
        self.configured = bool(self.api_key)
        self.breaker = get_circuit_breaker("OPENAI")

    def _scrub(self, text: str) -> str:
        """Sanitizes sensitive API keys from strings and exception messages."""
        if not text:
            return ""
        if self.api_key and len(self.api_key) > 4:
            text = text.replace(self.api_key, "[REDACTED_OPENAI_KEY]")
        return text

    def _is_reasoning_model(self, model_name: str) -> bool:
        m = model_name.lower()
        return m.startswith("o1") or m.startswith("o3") or "reasoning" in m

    async def generate_structured_output(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: Dict[str, Any],
        model: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        max_turns: int = 25,
        tool_executor=None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Executes an OpenAI Chat Completion call with structured output and tool calling.
        Supports native reasoning effort and Big Think streaming.
        """
        if not self.configured:
            return {
                "status": "FAILED",
                "summary": "OpenAIProvider is not configured (missing OPENAI_API_KEY).",
                "failure_category": "AUTH_ERROR",
            }

        if not self.breaker.can_execute():
            return {
                "status": "FAILED",
                "summary": "OpenAI API circuit is OPEN. Calls temporarily blocked.",
                "failure_category": "PROVIDER",
            }

        target_model = model or self.default_model
        is_reasoning = self._is_reasoning_model(target_model)

        # Resolve thinking configuration
        thinking_cfg: ThinkingConfig = kwargs.get("thinking_config") or ThinkingEngine.resolve_config(
            model_name=target_model,
            requested_budget=kwargs.get("thinking_budget"),
            requested_effort=kwargs.get("reasoning_effort"),
            explicit_enable=kwargs.get("big_think"),
        )

        mission_id = kwargs.get("mission_id")
        agent_role = kwargs.get("agent_role", "CODER")

        # Format tools according to OpenAI function specifications
        openai_tools = None
        if tools:
            openai_tools = []
            for t in tools:
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema") or t.get("parameters") or {"type": "object", "properties": {}},
                    },
                })

        # OpenAI o1 does not support "system" role in early API versions, supports "developer" role
        system_role = "developer" if is_reasoning else "system"
        messages: List[Dict[str, Any]] = [
            {"role": system_role, "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        turn = 0
        total_input_tokens = 0
        total_output_tokens = 0
        requests_count = 0
        total_retries = 0
        thinking_trace: List[str] = []

        endpoint = f"{self.base_url}/chat/completions"
        if not endpoint.endswith("/chat/completions"):
            endpoint = f"{endpoint}/chat/completions"

        from backend.services.context_compactor import ContextCompactor
        context_budget = int(os.environ.get("KOBITS_CONTEXT_BUDGET", "32000"))
        compactor = ContextCompactor(max_token_budget=context_budget, recent_turns=3)
        compaction_history = []

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            while turn < max_turns:
                turn += 1

                # Dynamic Context Compaction
                if turn > 1:
                    messages, c_report = compactor.compact_openai_messages(messages)
                    if c_report.observations_pruned > 0 or c_report.checkpoints_created > 0:
                        compaction_history.append(c_report.to_dict())

                payload: Dict[str, Any] = {
                    "model": target_model,
                    "messages": messages,
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "structured_response",
                            "strict": True,
                            "schema": schema,
                        },
                    },
                }

                # Reasoning models do not accept temperature or use reasoning_effort
                if is_reasoning:
                    payload["reasoning_effort"] = thinking_cfg.reasoning_effort
                    payload["max_completion_tokens"] = 16384
                else:
                    payload["temperature"] = 0.2
                    payload["max_tokens"] = 8192

                if openai_tools and turn < max_turns:
                    payload["tools"] = openai_tools
                    payload["tool_choice"] = "auto"
                elif openai_tools and turn >= max_turns:
                    messages.append({
                        "role": "user",
                        "content": "Turn budget limit reached. Provide your final structured JSON response immediately.",
                    })

                response_data = None
                for attempt in range(1, self.max_retries + 2):
                    try:
                        requests_count += 1
                        resp = await client.post(endpoint, json=payload, headers=headers)
                        if resp.status_code == 200:
                            response_data = resp.json()
                            self.breaker.record_success()
                            break

                        retry_after = resp.headers.get("Retry-After")
                        retry_delay = float(retry_after) if retry_after else None

                        if resp.status_code in (401, 403):
                            self.breaker.record_failure(retry_delay)
                            return {
                                "status": "FAILED",
                                "summary": f"OpenAI API authentication error (HTTP {resp.status_code}).",
                                "failure_category": "AUTH_ERROR",
                            }
                        elif resp.status_code == 429:
                            self.breaker.record_failure(retry_delay)
                            if attempt <= self.max_retries:
                                total_retries += 1
                                wait_time = retry_delay if retry_delay else 1.5 * (2 ** attempt)
                                await asyncio.sleep(wait_time)
                                continue
                            return {
                                "status": "FAILED",
                                "summary": "OpenAI API rate limit exceeded (429).",
                                "failure_category": "RATE_LIMIT_429",
                            }
                        elif resp.status_code >= 500:
                            self.breaker.record_failure(retry_delay)
                            if attempt <= self.max_retries:
                                total_retries += 1
                                wait_time = retry_delay if retry_delay else 1.5 * (2 ** attempt)
                                await asyncio.sleep(wait_time)
                                continue
                            return {
                                "status": "FAILED",
                                "summary": f"OpenAI API server error: HTTP {resp.status_code}.",
                                "failure_category": "SERVER_ERROR_5XX",
                            }
                        else:
                            error_text = self._scrub(resp.text)
                            return {
                                "status": "FAILED",
                                "summary": f"OpenAI API error HTTP {resp.status_code}: {error_text}",
                                "failure_category": "PROVIDER",
                            }
                    except httpx.TimeoutException:
                        self.breaker.record_failure()
                        if attempt <= self.max_retries:
                            total_retries += 1
                            await asyncio.sleep(1.5 * (2 ** attempt))
                            continue
                        return {
                            "status": "FAILED",
                            "summary": "OpenAI API request timed out.",
                            "failure_category": "TIMEOUT",
                        }
                    except httpx.RequestError as e:
                        self.breaker.record_failure()
                        if attempt <= self.max_retries:
                            total_retries += 1
                            await asyncio.sleep(1.5 * (2 ** attempt))
                            continue
                        return {
                            "status": "FAILED",
                            "summary": f"OpenAI API network error: {self._scrub(str(e))}",
                            "failure_category": "DNS_ERROR",
                        }
                    except Exception as e:
                        return {
                            "status": "FAILED",
                            "summary": f"OpenAI API unexpected error: {self._scrub(str(e))}",
                            "failure_category": "UNKNOWN",
                        }

                if not response_data:
                    return {
                        "status": "FAILED",
                        "summary": "OpenAI API returned empty response after retries.",
                        "failure_category": "PROVIDER",
                    }

                usage = response_data.get("usage", {})
                total_input_tokens += usage.get("prompt_tokens", 0)
                total_output_tokens += usage.get("completion_tokens", 0)

                choices = response_data.get("choices", [])
                if not choices:
                    return {
                        "status": "FAILED",
                        "summary": "OpenAI API returned no completion choices.",
                    }

                msg = choices[0].get("message", {})

                # Extract reasoning content (o1 / o3-mini)
                reasoning_chunks = ThinkingEngine.extract_thoughts_from_openai_or_deepseek(msg)
                if reasoning_chunks:
                    for chunk in reasoning_chunks:
                        thinking_trace.append(chunk)
                        if thinking_cfg.stream_thoughts and mission_id:
                            await ThinkingEngine.broadcast_thought(
                                mission_id=mission_id,
                                agent_role=agent_role,
                                thought_chunk=chunk,
                            )

                tool_calls = msg.get("tool_calls")
                if tool_calls:
                    messages.append(msg)
                    for tc in tool_calls:
                        fn = tc.get("function", {})
                        call_id = tc.get("id")
                        fn_name = fn.get("name")
                        raw_args = fn.get("arguments", "{}")

                        try:
                            parsed_args = json.loads(raw_args) if isinstance(raw_args, str) and raw_args.strip() else (raw_args or {})
                        except Exception as e:
                            parsed_args = {"_raw": raw_args, "parse_error": str(e)}

                        if tool_executor:
                            try:
                                tool_result = await tool_executor(fn_name, parsed_args)
                            except Exception as te:
                                tool_result = {"status": "error", "message": self._scrub(str(te))}
                        else:
                            tool_result = {"status": "error", "message": "Tool executor not provided."}

                        messages.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "name": fn_name,
                            "content": json.dumps(tool_result),
                        })
                    continue
                else:
                    raw_content = msg.get("content", "")
                    if not raw_content:
                        return {
                            "status": "FAILED",
                            "summary": "OpenAI API returned empty content.",
                        }

                    try:
                        import re
                        json_match = re.search(r'```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```', raw_content, re.DOTALL)
                        if json_match:
                            cleaned_content = json_match.group(1)
                        else:
                            start_idx = raw_content.find('{')
                            end_idx = raw_content.rfind('}')
                            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                                cleaned_content = raw_content[start_idx:end_idx+1]
                            else:
                                cleaned_content = raw_content.strip()

                        result_data = json.loads(cleaned_content)
                        if isinstance(result_data, dict):
                            result_data["_usage"] = {
                                "input_tokens": total_input_tokens,
                                "output_tokens": total_output_tokens,
                                "total_tokens": total_input_tokens + total_output_tokens,
                                "model_used": response_data.get("model", target_model),
                            }
                            if thinking_trace:
                                result_data["_thinking_trace"] = thinking_trace
                            if compaction_history:
                                result_data["_usage"]["compaction"] = compaction_history[-1]
                                result_data["_usage"]["compaction_history"] = compaction_history
                            return result_data
                        return {"status": "SUCCESS", "data": result_data}
                    except Exception as e:
                        return {
                            "status": "FAILED",
                            "summary": f"Failed to parse structured JSON from OpenAI response: {str(e)}",
                            "raw": raw_content[:400],
                        }

        return {
            "status": "FAILED",
            "summary": "Max turns exceeded in OpenAIProvider loop.",
        }
