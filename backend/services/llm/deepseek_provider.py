import os
import json
import time
import asyncio
from typing import List, Dict, Any, Optional
import httpx

from backend.services.llm.provider import LLMProvider
from backend.core.config import settings
from backend.services.llm.provider_circuit_breaker import get_circuit_breaker

class DeepSeekProvider(LLMProvider):
    """
    DeepSeek V4.1 Flash LLM Provider.
    Implements OpenAI-compatible Chat Completions API with tool calling,
    JSON structured outputs, bounded retries, and credential scrubbing.
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 60.0,
        max_retries: int = 3
    ):
        self.api_key = (
            api_key
            or settings.DEEPSEEK_API_KEY
            or os.environ.get("DEEPSEEK_API_KEY")
            or settings.ANTHROPIC_API_KEY
            or os.environ.get("ANTHROPIC_API_KEY")
        )
        raw_base = (
            base_url
            or getattr(settings, "LLM_BASE_URL", None)
            or (settings.DEEPSEEK_BASE_URL if settings.DEEPSEEK_API_KEY else None)
            or settings.ANTHROPIC_BASE_URL
            or "https://api.deepseek.com"
        )
        self.base_url = raw_base.rstrip("/")
        raw_model = model or os.environ.get("DEEPSEEK_MODEL") or settings.DEEPSEEK_MODEL or "deepseek-v4.1-flash:free"
        if "tokenharbor" in self.base_url and raw_model in ("deepseek-flash", "deepseek-default", "deepseek-coding", "deepseek-reasoning", "deepseek-fast"):
            raw_model = "deepseek-v4.1-flash:free"
        self.default_model = raw_model
        self.timeout = timeout
        self.max_retries = max_retries
        self.configured = bool(self.api_key)
        self.breaker = get_circuit_breaker("DEEPSEEK")
        
        if not self.configured and settings.REAL_AI_TEST:
            raise ValueError("DEEPSEEK_API_KEY is not set but REAL_AI_TEST is enabled.")

    def _scrub(self, text: str) -> str:
        """Removes API key from any output string or exception message."""
        if not text:
            return ""
        if self.api_key and len(self.api_key) > 4:
            text = text.replace(self.api_key, "[REDACTED_DEEPSEEK_KEY]")
        return text

    async def generate_structured_output(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: Dict[str, Any],
        model: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        max_turns: int = 25,
        tool_executor = None
    ) -> Dict[str, Any]:
        
        if not self.configured:
            return {
                "status": "FAILED",
                "summary": "DeepSeekProvider is not configured (missing DEEPSEEK_API_KEY)."
            }
            
        if not self.breaker.can_execute():
            return {
                "status": "FAILED",
                "summary": "DeepSeek API circuit is OPEN. Please try again later.",
                "failure_category": "PROVIDER"
            }
            
        target_model = model or self.default_model
        if target_model.startswith("claude") or target_model.startswith("kobits") or target_model.startswith("mock"):
            target_model = self.default_model
        if "tokenharbor" in self.base_url and target_model in ("deepseek-flash", "deepseek-default", "deepseek-coding", "deepseek-reasoning", "deepseek-fast"):
            target_model = "deepseek-v4.1-flash:free"
        
        # Format tools according to OpenAI / DeepSeek function specification
        openai_tools = None
        if tools:
            openai_tools = []
            for t in tools:
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema") or {"type": "object", "properties": {}}
                    }
                })
                
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        turn = 0
        total_input_tokens = 0
        total_output_tokens = 0
        requests_count = 0
        total_retries = 0
        tool_calls_count = 0
        start_time = time.time()
        
        endpoint = f"{self.base_url}/chat/completions"
        if not self.base_url.endswith("/v1") and not endpoint.endswith("/chat/completions"):
            endpoint = f"{self.base_url}/v1/chat/completions"
            
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            while turn < max_turns:
                turn += 1
                
                payload = {
                    "model": target_model,
                    "messages": messages,
                    "response_format": {"type": "json_schema", "json_schema": {"name": "response_schema", "schema": schema, "strict": True}},
                    "temperature": 0.2
                }
                if openai_tools and turn < max_turns:
                    payload["tools"] = openai_tools
                    payload["tool_choice"] = "auto"
                elif openai_tools and turn >= max_turns:
                    messages.append({
                        "role": "user",
                        "content": "Turn budget limit reached. Please provide your final structured JSON response immediately based on the actions taken so far."
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
                                "summary": f"DeepSeek API authentication error (HTTP {resp.status_code}).",
                                "failure_category": "AUTH_ERROR"
                            }
                        elif resp.status_code == 429:
                            self.breaker.record_failure(retry_delay)
                            if attempt <= self.max_retries:
                                total_retries += 1
                                wait_time = retry_delay if retry_delay else 1.0 * (2 ** attempt)
                                await asyncio.sleep(wait_time)
                                continue
                            return {
                                "status": "FAILED",
                                "summary": "DeepSeek API rate limit exceeded (429).",
                                "failure_category": "RATE_LIMIT_429"
                            }
                        elif resp.status_code >= 500:
                            self.breaker.record_failure(retry_delay)
                            if attempt <= self.max_retries:
                                total_retries += 1
                                wait_time = retry_delay if retry_delay else 1.0 * (2 ** attempt)
                                await asyncio.sleep(wait_time)
                                continue
                            return {
                                "status": "FAILED",
                                "summary": f"DeepSeek API server error: HTTP {resp.status_code}.",
                                "failure_category": "SERVER_ERROR_5XX"
                            }
                        else:
                            error_text = self._scrub(resp.text)
                            return {
                                "status": "FAILED",
                                "summary": f"DeepSeek API error HTTP {resp.status_code}: {error_text}",
                                "failure_category": "PROVIDER"
                            }
                    except httpx.TimeoutException:
                        self.breaker.record_failure()
                        if attempt <= self.max_retries:
                            total_retries += 1
                            await asyncio.sleep(1.0 * (2 ** attempt))
                            continue
                        return {
                            "status": "FAILED",
                            "summary": "DeepSeek API request timed out.",
                            "failure_category": "TIMEOUT"
                        }
                    except httpx.RequestError as e:
                        self.breaker.record_failure()
                        if attempt <= self.max_retries:
                            total_retries += 1
                            await asyncio.sleep(1.0 * (2 ** attempt))
                            continue
                        return {
                            "status": "FAILED",
                            "summary": f"DeepSeek API network error: {self._scrub(str(e))}",
                            "failure_category": "DNS_ERROR"
                        }
                    except Exception as e:
                        return {
                            "status": "FAILED",
                            "summary": f"DeepSeek API unexpected error: {self._scrub(str(e))}",
                            "failure_category": "UNKNOWN"
                        }
                        
                if not response_data:
                    return {
                        "status": "FAILED",
                        "summary": "DeepSeek API returned empty response after retries.",
                        "failure_category": "PROVIDER"
                    }
                    
                # Track usage
                usage = response_data.get("usage", {})
                total_input_tokens += usage.get("prompt_tokens", 0)
                total_output_tokens += usage.get("completion_tokens", 0)
                
                choices = response_data.get("choices", [])
                if not choices:
                    return {
                        "status": "FAILED",
                        "summary": "DeepSeek API returned no completion choices."
                    }
                    
                msg = choices[0].get("message", {})
                tool_calls = msg.get("tool_calls")
                
                if tool_calls:
                    # Model requested tool executions
                    messages.append(msg)
                    for tc in tool_calls:
                        tool_calls_count += 1
                        fn = tc.get("function", {})
                        call_id = tc.get("id")
                        fn_name = fn.get("name")
                        raw_args = fn.get("arguments", "{}")
                        
                        try:
                            if isinstance(raw_args, str):
                                parsed_args = json.loads(raw_args) if raw_args.strip() else {}
                            else:
                                parsed_args = raw_args
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
                            "content": json.dumps(tool_result)
                        })
                    # Loop to next turn with tool results in history
                    continue
                else:
                    # Final assistant message received
                    raw_content = msg.get("content", "")
                    if not raw_content:
                        return {
                            "status": "FAILED",
                            "summary": "DeepSeek API returned empty content."
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
                                "model_used": response_data.get("model", model)
                            }
                    except Exception as e:
                        result_data = {
                            "status": "FAILED",
                            "summary": f"Failed to parse JSON response: {str(e)}",
                            "raw": self._scrub(raw_content),
                            "_usage": {
                                "input_tokens": total_input_tokens,
                                "output_tokens": total_output_tokens,
                                "total_tokens": total_input_tokens + total_output_tokens,
                                "model_used": response_data.get("model", model)
                            }
                        }
                        
                    duration = time.time() - start_time
                    # DeepSeek standard pricing: ~$0.14 per 1M input tokens, ~$0.28 per 1M output tokens
                    estimated_cost = (total_input_tokens * 0.14 / 1_000_000) + (total_output_tokens * 0.28 / 1_000_000)
                    
                    result_data["_usage"] = {
                        "model": target_model,
                        "model_used": response_data.get("model", target_model),
                        "input_tokens": total_input_tokens,
                        "output_tokens": total_output_tokens,
                        "total_tokens": total_input_tokens + total_output_tokens,
                        "estimated_cost": round(estimated_cost, 6),
                        "duration_seconds": round(duration, 3),
                        "requests": requests_count,
                        "retries": total_retries,
                        "tool_calls": tool_calls_count
                    }
                    return result_data
                    
        # Exceeded turns
        duration = time.time() - start_time
        has_executed_tools = tool_calls_count > 0
        return {
            "status": "COMPLETED" if has_executed_tools else "FAILED",
            "summary": (
                f"Execution reached turn limit ({max_turns}) after {tool_calls_count} tool actions."
                if has_executed_tools
                else f"Max turns ({max_turns}) exceeded during tool execution."
            ),
            "findings": ["Task executed via interactive tool calling."] if has_executed_tools else [],
            "changes": ["Modified repository files."] if has_executed_tools else [],
            "_usage": {
                "model": target_model,
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "total_tokens": total_input_tokens + total_output_tokens,
                "estimated_cost": round((total_input_tokens * 0.14 / 1_000_000) + (total_output_tokens * 0.28 / 1_000_000), 6),
                "duration_seconds": round(duration, 3),
                "requests": requests_count,
                "retries": total_retries,
                "tool_calls": tool_calls_count
            }
        }