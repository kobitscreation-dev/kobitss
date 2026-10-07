import json
import time
import asyncio
from typing import List, Dict, Any, Optional

from backend.services.llm.provider import LLMProvider
from backend.core.config import settings
from backend.services.llm.provider_circuit_breaker import get_circuit_breaker


class BedrockProvider(LLMProvider):
    """
    AWS Bedrock Anthropic Claude LLM Provider.
    Supports AWS Bedrock Claude models (Claude 3 Opus, Claude 3.5 Sonnet, Claude 3.7 Sonnet, Claude 3 Haiku).
    Authenticates via:
    1. AWS Bedrock API Key (settings.AWS_BEDROCK_API_KEY)
    2. AWS IAM Credentials (settings.AWS_ACCESS_KEY_ID, settings.AWS_SECRET_ACCESS_KEY, settings.AWS_REGION)
    """

    def __init__(
        self,
        aws_access_key: Optional[str] = None,
        aws_secret_key: Optional[str] = None,
        aws_region: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 120.0,
        max_retries: int = 4
    ):
        self.aws_access_key = aws_access_key or getattr(settings, "AWS_ACCESS_KEY_ID", None)
        self.aws_secret_key = aws_secret_key or getattr(settings, "AWS_SECRET_ACCESS_KEY", None)
        self.aws_region = aws_region or getattr(settings, "AWS_REGION", "ap-southeast-2")
        self.api_key = api_key or getattr(settings, "AWS_BEDROCK_API_KEY", None) or getattr(settings, "ANTHROPIC_API_KEY", None)
        
        # Default AWS Bedrock model identifier:
        # e.g. "anthropic.claude-3-opus-20240229-v1:0" or "anthropic.claude-3-5-sonnet-20241022-v2:0"
        self.default_model = model or getattr(settings, "BEDROCK_MODEL", None) or getattr(settings, "ANTHROPIC_MODEL", None) or "anthropic.claude-3-opus-20240229-v1:0"
        self.timeout = timeout
        self.max_retries = max_retries

    def _get_client(self):
        from anthropic import AsyncAnthropicBedrock
        kwargs = {"aws_region": self.aws_region, "timeout": self.timeout}
        if self.aws_access_key and self.aws_secret_key:
            kwargs["aws_access_key"] = self.aws_access_key
            kwargs["aws_secret_key"] = self.aws_secret_key
        if self.api_key:
            kwargs["api_key"] = self.api_key
        return AsyncAnthropicBedrock(**kwargs)

    def _normalize_model_id(self, model_id: str) -> str:
        """Map common friendly names to AWS Bedrock model IDs."""
        mapping = {
            "claude-sonnet-4.6": "global.anthropic.claude-sonnet-4-6",
            "claude-sonnet-4-6": "global.anthropic.claude-sonnet-4-6",
            "sonnet-4.6": "global.anthropic.claude-sonnet-4-6",
            "claude-haiku-4.5": "au.anthropic.claude-haiku-4-5-20251001-v1:0",
            "haiku-4.5": "au.anthropic.claude-haiku-4-5-20251001-v1:0",
            "claude-opus-5.5": "global.anthropic.claude-opus-5-5",
            "opus-5.5": "global.anthropic.claude-opus-5-5",
            "opus 5.5": "global.anthropic.claude-opus-5-5",
            "claude-opus-5": "global.anthropic.claude-opus-5",
            "claude-3-opus": "anthropic.claude-3-opus-20240229-v1:0",
            "claude-opus": "global.anthropic.claude-opus-5-5",
        }
        return mapping.get(model_id.lower(), model_id)

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
        Executes an AWS Bedrock Claude invocation with structured output and tool calling.
        """
        circuit_breaker = get_circuit_breaker("BEDROCK")
        if not circuit_breaker.can_execute():
            raise Exception("LLM circuit breaker is OPEN for Bedrock. API calls blocked.")

        resolved_model = self._normalize_model_id(model or self.default_model)
        client = self._get_client()

        full_system = (
            f"{system_prompt}\n\n"
            f"You MUST respond with valid JSON matching this schema:\n{json.dumps(schema)}\n"
            "Keep your JSON concise, escape any quotes inside strings, and do not include any text outside the JSON object."
        )

        messages = [
            {"role": "user", "content": user_prompt}
        ]

        anthropic_tools = []
        if tools:
            for t in tools:
                anthropic_tools.append({
                    "name": t.get("name"),
                    "description": t.get("description"),
                    "input_schema": t.get("input_schema") or t.get("parameters") or {"type": "object", "properties": {}}
                })

        turn_count = 0
        total_input_tokens = 0
        total_output_tokens = 0

        while turn_count < max_turns:
            turn_count += 1

            for attempt in range(self.max_retries):
                try:
                    kwargs = {
                        "model": resolved_model,
                        "max_tokens": 8192,
                        "system": full_system,
                        "messages": messages,
                    }
                    if anthropic_tools and turn_count < max_turns:
                        kwargs["tools"] = anthropic_tools

                    response = await client.messages.create(**kwargs)

                    if response.usage:
                        total_input_tokens += getattr(response.usage, "input_tokens", 0)
                        total_output_tokens += getattr(response.usage, "output_tokens", 0)

                    content_blocks = response.content
                    text_content = ""
                    tool_calls = []

                    for block in content_blocks:
                        if block.type == "text":
                            text_content += block.text
                        elif block.type == "tool_use":
                            tool_calls.append(block)

                    # Handle intermediate tool calls if present
                    if tool_calls and tool_executor and turn_count < max_turns:
                        # Append assistant message with raw blocks
                        assistant_content = []
                        for block in content_blocks:
                            if block.type == "text":
                                assistant_content.append({"type": "text", "text": block.text})
                            elif block.type == "tool_use":
                                assistant_content.append({
                                    "type": "tool_use",
                                    "id": block.id,
                                    "name": block.name,
                                    "input": block.input,
                                })
                        messages.append({"role": "assistant", "content": assistant_content})

                        tool_results = []
                        for tool in tool_calls:
                            try:
                                res = await tool_executor(tool.name, tool.input)
                                content_str = str(res)
                            except Exception as e:
                                content_str = f"Error executing tool {tool.name}: {str(e)}"

                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tool.id,
                                "content": content_str,
                            })

                        if turn_count == max_turns - 1:
                            tool_results.append({
                                "type": "text",
                                "text": "Tool execution limit reached. Do NOT call any more tools. Output your final valid JSON response matching the schema now."
                            })

                        messages.append({"role": "user", "content": tool_results})
                        break

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

                    parsed_data = json.loads(cleaned, strict=False)
                    if isinstance(parsed_data, dict):
                        estimated_cost = (total_input_tokens * 3.0 / 1_000_000) + (total_output_tokens * 15.0 / 1_000_000)
                        parsed_data["_usage"] = {
                            "input_tokens": total_input_tokens,
                            "output_tokens": total_output_tokens,
                            "total_tokens": total_input_tokens + total_output_tokens,
                            "estimated_cost": estimated_cost,
                            "model_used": resolved_model,
                        }
                    return parsed_data

                except json.JSONDecodeError as e:
                    if attempt < self.max_retries - 1:
                        messages = [
                            {"role": "user", "content": f"{user_prompt}\n\nIMPORTANT: Your previous response had a JSON syntax error ({str(e)}). Respond ONLY with a compact, valid JSON object matching the required schema."}
                        ]
                        await asyncio.sleep(1)
                        continue
                    raise
                except Exception as e:
                    circuit_breaker.record_failure()
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    raise Exception(f"AWS Bedrock Claude API Error: {str(e)}")
            else:
                raise Exception("Max retry attempts reached without valid response from AWS Bedrock.")

        raise Exception("Exceeded max turns in Bedrock conversation.")
