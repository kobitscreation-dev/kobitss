import json
import time
import asyncio
from typing import List, Dict, Any, Optional
import httpx

from backend.services.llm.provider import LLMProvider
from backend.core.config import settings
from backend.services.llm.provider_circuit_breaker import get_circuit_breaker

class AnthropicProvider(LLMProvider):
    """
    Anthropic (Claude) LLM Provider.
    Implements structured tool calling using the official messages API format.
    Includes timeout boundaries and circuit breakers.
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 120.0,
        max_retries: int = 4
    ):
        self.api_key = api_key or settings.ANTHROPIC_API_KEY
        raw_url = (base_url or getattr(settings, "ANTHROPIC_BASE_URL", None) or "https://api.anthropic.com/v1").rstrip("/")
        if not raw_url.endswith("/v1"):
            raw_url = f"{raw_url}/v1"
        self.base_url = raw_url
        self.default_model = model or settings.ANTHROPIC_MODEL or "claude-3-5-sonnet-20241022"
        self.timeout = timeout
        self.max_retries = max_retries

    async def generate_structured_output(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: Dict[str, Any],
        model: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        max_turns: int = 10,
        tool_executor=None
    ) -> Dict[str, Any]:
        """
        Executes a Claude API call, forcing structured JSON output and optionally handling tool execution.
        """
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY is not configured.")

        circuit_breaker = get_circuit_breaker("ANTHROPIC")
        if not circuit_breaker.can_execute():
            raise Exception("LLM circuit breaker is OPEN. API calls blocked.")

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }

        # Format system prompt and inject instruction to output pure JSON matching schema
        full_system = (
            f"{system_prompt}\n\n"
            f"You MUST respond with valid JSON matching this schema:\n{json.dumps(schema)}\n"
            "Keep your JSON concise (under 2000 tokens), escape any quotes inside strings, and do not include any text outside the JSON object."
        )

        messages = [
            {"role": "user", "content": user_prompt}
        ]

        # Convert our generic tool format to Anthropic's tool format
        anthropic_tools = []
        if tools:
            for t in tools:
                anthropic_tools.append({
                    "name": t.get("name"),
                    "description": t.get("description"),
                    "input_schema": t.get("input_schema") or t.get("parameters") or {"type": "object", "properties": {}}
                })

        # Initialize turn tracking
        turn_count = 0
        total_input_tokens = 0
        total_output_tokens = 0
        
        while turn_count < max_turns:
            turn_count += 1
            
            for attempt in range(self.max_retries):
                payload = {
                    "model": model or self.default_model,
                    "max_tokens": 8192,
                    "system": full_system,
                    "messages": messages,
                    "temperature": 0.0
                }
                if anthropic_tools and turn_count < max_turns:
                    payload["tools"] = anthropic_tools

                try:
                    async with httpx.AsyncClient(timeout=self.timeout) as client:
                        resp = await client.post(f"{self.base_url}/messages", headers=headers, json=payload)
                        
                        if resp.status_code != 200:
                            if resp.status_code == 402 and (model or self.default_model) != "deepseek-v4.1-flash:free":
                                model = "deepseek-v4.1-flash:free"
                                self.default_model = "deepseek-v4.1-flash:free"
                                continue
                            circuit_breaker.record_failure()
                            if resp.status_code in [429, 500, 502, 503, 504]:
                                await asyncio.sleep(2 ** attempt)
                                continue
                            raise Exception(f"Anthropic API Error: {resp.status_code} - {resp.text}")

                        data = resp.json()
                        usage = data.get("usage", {})
                        total_input_tokens += usage.get("input_tokens", 0)
                        total_output_tokens += usage.get("output_tokens", 0)
                        
                        content_blocks = data.get("content", [])
                        
                        text_content = ""
                        tool_calls = []
                        
                        for block in content_blocks:
                            if block["type"] == "text":
                                text_content += block["text"]
                            elif block["type"] == "tool_use":
                                tool_calls.append(block)

                        # Handle intermediate tool calls if present
                        if tool_calls and tool_executor and turn_count < max_turns:
                            messages.append({"role": "assistant", "content": content_blocks})
                            
                            tool_results = []
                            for tool in tool_calls:
                                try:
                                    res = await tool_executor(tool["name"], tool["input"])
                                    content_str = str(res)
                                except Exception as e:
                                    content_str = f"Error executing tool {tool['name']}: {str(e)}"
                                
                                tool_results.append({
                                    "type": "tool_result",
                                    "tool_use_id": tool["id"],
                                    "content": content_str
                                })
                            if turn_count == max_turns - 1:
                                tool_results.append({
                                    "type": "text",
                                    "text": "Tool execution limit reached. Do NOT call any more tools. Output your final valid JSON response matching the schema now."
                                })
                            messages.append({"role": "user", "content": tool_results})
                            
                            # Break the attempt loop to proceed to the next turn in the while loop
                            break

                        # No more tools (or max turns reached), parse JSON
                        circuit_breaker.record_success()
                        
                        cleaned = text_content.strip()
                        if "```json" in cleaned:
                            cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
                        elif "```" in cleaned:
                            cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()
                        elif not cleaned.startswith("{") and "{" in cleaned and "}" in cleaned:
                            start_idx = cleaned.find("{")
                            end_idx = cleaned.rfind("}")
                            if start_idx < end_idx:
                                cleaned = cleaned[start_idx : end_idx + 1]
                        
                        try:
                            parsed_data = json.loads(cleaned, strict=False)
                            if isinstance(parsed_data, dict):
                                estimated_cost = (total_input_tokens * 3.0 / 1_000_000) + (total_output_tokens * 15.0 / 1_000_000)
                                parsed_data["_usage"] = {
                                    "input_tokens": total_input_tokens,
                                    "output_tokens": total_output_tokens,
                                    "total_tokens": total_input_tokens + total_output_tokens,
                                    "estimated_cost": estimated_cost,
                                    "model_used": data.get("model", model or self.default_model)
                                }
                            return parsed_data
                        except json.JSONDecodeError:
                            import os
                            if os.environ.get("KOBITS_VERBOSE") == "1":
                                print(f"DEBUG: Failed to parse JSON from: {repr(cleaned[:300])}")
                            raise

                except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPError) as net_err:
                    circuit_breaker.record_failure()
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    raise Exception(f"Anthropic API network/timeout error after {self.max_retries} attempts: {repr(net_err)}")
                except json.JSONDecodeError as e:
                    # Malformed JSON, retry concisely without bloating context with broken output
                    if attempt < self.max_retries - 1:
                        messages = [
                            {"role": "user", "content": f"{user_prompt}\n\nIMPORTANT: Your previous response had a JSON syntax error ({str(e)}). Respond ONLY with a compact, valid JSON object (under 1500 tokens) matching the required schema."}
                        ]
                        await asyncio.sleep(1)
                        continue
                    raise
                except Exception as e:
                    circuit_breaker.record_failure()
                    raise
            else:
                # If the for-attempt loop exhausted without breaking or returning
                raise Exception("Max retries exceeded for AnthropicProvider")
        
        raise Exception("Max turns exceeded for AnthropicProvider")
