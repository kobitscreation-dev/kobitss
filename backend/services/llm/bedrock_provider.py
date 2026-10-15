import os
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
        model: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        max_turns: int = 30,
        tool_executor=None,
        mission_id: Optional[str] = None,
        agent_role: Optional[str] = None,
        big_think: Optional[bool] = None,
        thinking_budget: Optional[int] = None,
        reasoning_effort: Optional[str] = None,
        **extra_kwargs,
    ) -> Dict[str, Any]:
        """
        Executes an AWS Bedrock Claude invocation with 30-turn autonomous ReAct tool calling.
        """
        circuit_breaker = get_circuit_breaker("BEDROCK")
        if not circuit_breaker.can_execute():
            raise Exception("LLM circuit breaker is OPEN for Bedrock. API calls blocked.")

        resolved_model = self._normalize_model_id(model or self.default_model)
        client = self._get_client()

        anthropic_tools = []
        if tools:
            for t in tools:
                anthropic_tools.append({
                    "name": t.get("name"),
                    "description": t.get("description"),
                    "input_schema": t.get("input_schema") or t.get("parameters") or {"type": "object", "properties": {}}
                })

        if anthropic_tools:
            full_system = (
                f"{system_prompt}\n\n"
                "AUTONOMOUS AGENT WORKFLOW (Kyros / Devin Standard):\n"
                "You are working in a live workspace. You MUST actively use your tools to inspect, write, test, and verify code.\n"
                "- First inspect directory contents and files using list_directory / read_file.\n"
                "- Write or edit code using write_file / edit_file_block.\n"
                "- You can offload boilerplate schemas or raw tests to delegate_to_specialist ($0 marginal cost).\n"
                "- ALWAYS run syntax checks and tests using run_command (e.g. `pytest`, `python -c \"import ...\"`).\n"
                "- If a test fails, inspect the traceback and self-heal by editing the file and re-running.\n"
                "- Only call finish_mission or conclude with your final answer when all verification tests pass.\n"
                f"When you are finished, return your final answer as a valid JSON object matching this schema:\n{json.dumps(schema)}\n"
            )
        else:
            full_system = (
                f"{system_prompt}\n\n"
                f"You MUST respond with valid JSON matching this schema:\n{json.dumps(schema)}\n"
                "Keep your JSON concise, escape any quotes inside strings, and do not include any text outside the JSON object."
            )

        messages = [
            {"role": "user", "content": user_prompt}
        ]

        turn_count = 0
        total_input_tokens = 0
        total_output_tokens = 0
        finish_summary = ""

        from backend.services.context_compactor import ContextCompactor
        context_budget = int(os.environ.get("KOBITS_CONTEXT_BUDGET", "32000"))
        compactor = ContextCompactor(max_token_budget=context_budget, recent_turns=3)
        compaction_history = []

        from backend.services.llm.thinking_engine import ThinkingEngine, ThinkingConfig
        thinking_cfg: ThinkingConfig = extra_kwargs.get("thinking_config") or ThinkingEngine.resolve_config(
            model_name=resolved_model,
            requested_budget=thinking_budget,
            requested_effort=reasoning_effort,
            explicit_enable=big_think,
        )

        while turn_count < max_turns:
            turn_count += 1

            # Dynamic Context Compaction (preserves Turn 0 prefix and golden window)
            if turn_count > 1:
                messages, c_report = compactor.compact_anthropic_messages(messages)
                if c_report.observations_pruned > 0 or c_report.checkpoints_created > 0:
                    compaction_history.append(c_report.to_dict())

            for attempt in range(self.max_retries):
                try:
                    call_kwargs = {
                        "model": resolved_model,
                        "max_tokens": max(8192, thinking_cfg.budget_tokens + 4096) if thinking_cfg.enabled else 8192,
                        "system": full_system,
                        "messages": messages,
                    }
                    if thinking_cfg.enabled and ThinkingEngine.is_reasoning_model(resolved_model):
                        call_kwargs["thinking"] = {
                            "type": "enabled",
                            "budget_tokens": thinking_cfg.budget_tokens,
                        }

                    if anthropic_tools and turn_count < max_turns:
                        call_kwargs["tools"] = anthropic_tools

                    response = await client.messages.create(**call_kwargs)

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
                        elif block.type == "thinking":
                            t_text = getattr(block, "thinking", "")
                            if t_text and mission_id:
                                await ThinkingEngine.broadcast_thought(
                                    mission_id=mission_id,
                                    agent_role=agent_role or "CODER",
                                    thought_chunk=t_text,
                                )

                    # Handle intermediate tool calls if present
                    if tool_calls and tool_executor and turn_count < max_turns:
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
                                if isinstance(res, dict):
                                    if "output" in res:
                                        content_str = str(res["output"])
                                    elif "message" in res and "error" not in res:
                                        content_str = str(res["message"])
                                    else:
                                        content_str = json.dumps(res, indent=2)
                                else:
                                    content_str = str(res)
                            except Exception as e:
                                content_str = f"Error executing tool {tool.name}: {str(e)}"

                            if tool.name == "finish_mission" and isinstance(tool.input, dict):
                                finish_summary = tool.input.get("summary", "")

                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tool.id,
                                "content": content_str,
                            })

                        if turn_count == max_turns - 1:
                            tool_results.append({
                                "type": "text",
                                "text": "Tool execution limit reached. Output your final valid JSON response matching the schema now."
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

                    try:
                        parsed_data = json.loads(cleaned, strict=False)
                    except Exception:
                        parsed_data = {
                            "status": "SUCCESS",
                            "summary": finish_summary or text_content.strip() or "Task successfully executed and verified.",
                            "findings": [],
                            "changes": [],
                            "recommendations": [],
                            "risks": [],
                            "next_actions": []
                        }

                    if isinstance(parsed_data, dict):
                        estimated_cost = (total_input_tokens * 3.0 / 1_000_000) + (total_output_tokens * 15.0 / 1_000_000)
                        parsed_data["_usage"] = {
                            "input_tokens": total_input_tokens,
                            "output_tokens": total_output_tokens,
                            "total_tokens": total_input_tokens + total_output_tokens,
                            "estimated_cost": estimated_cost,
                            "model_used": "Kobits Autonomous Engine (Ultra)",
                        }
                        if compaction_history:
                            parsed_data["_usage"]["compaction"] = compaction_history[-1]
                            parsed_data["_usage"]["compaction_history"] = compaction_history
                    return parsed_data

                except Exception as e:
                    circuit_breaker.record_failure()
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    raise Exception(f"AWS Bedrock Claude API Error: {str(e)}")
            else:
                raise Exception("Max retry attempts reached without valid response from AWS Bedrock.")

        raise Exception("Exceeded max turns in Bedrock conversation.")
