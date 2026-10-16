import json
import logging
import os
from typing import List, Dict, Any, Optional
import httpx

from backend.services.llm.provider import LLMProvider
from backend.core.config import settings
from backend.services.llm.provider_circuit_breaker import get_circuit_breaker

logger = logging.getLogger(__name__)


class GeminiProvider(LLMProvider):
    """
    Google Gemini LLM Provider (Gemini 2.5 Pro / Flash).
    Zero-dependency direct HTTP implementation using httpx.
    Supports:
    - 1M+ token context window
    - Tool calling / Function calling
    - Structured JSON schema generation
    - Resilient error handling and circuit breaker
    """

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or getattr(settings, "GEMINI_API_KEY", None)
        self.configured = bool(self.api_key)
        self.default_model = model or os.environ.get("GEMINI_MODEL") or getattr(settings, "GEMINI_MODEL", "gemini-4-argon")
        self.base_url = "https://generativelanguage.googleapis.com/v1beta/models"

    def _normalize_model(self, model: Optional[str]) -> str:
        m = (model or self.default_model or "").strip()
        if not m:
            return "gemini-4-argon"
        low = m.lower()
        if "argon" in low:
            return "gemini-4-argon"
        if "2.5-pro" in low:
            return "gemini-2.5-pro"
        if "2.5-flash" in low:
            return "gemini-2.5-flash"
        return m

    async def generate_structured_output(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: Dict[str, Any],
        model: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        max_turns: int = 10,
        tool_executor=None,
        **kwargs
    ) -> Dict[str, Any]:
        if not self.configured:
            return {
                "status": "FAILED",
                "summary": "GeminiProvider is not configured (missing GEMINI_API_KEY).",
                "_usage": {"input_tokens": 0, "output_tokens": 0}
            }

        circuit_breaker = get_circuit_breaker("GEMINI")
        if not circuit_breaker.can_execute():
            raise Exception("LLM circuit breaker is OPEN for Gemini. Calls temporarily paused.")

        resolved_model = self._normalize_model(model)
        url = f"{self.base_url}/{resolved_model}:generateContent?key={self.api_key}"

        from backend.services.llm.thinking_engine import ThinkingEngine, ThinkingConfig
        thinking_cfg: ThinkingConfig = kwargs.get("thinking_config") or ThinkingEngine.resolve_config(
            model_name=resolved_model,
            requested_budget=kwargs.get("thinking_budget"),
            requested_effort=kwargs.get("reasoning_effort"),
            explicit_enable=kwargs.get("big_think"),
        )
        mission_id = kwargs.get("mission_id")
        agent_role = kwargs.get("agent_role", "CODER")
        thinking_trace: List[str] = []

        # Build tools if provided
        gemini_tools = []
        if tools:
            func_decls = []
            for t in tools:
                func_decls.append({
                    "name": t.get("name"),
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema") or t.get("parameters") or {"type": "object", "properties": {}}
                })
            if func_decls:
                gemini_tools.append({"functionDeclarations": func_decls})

        contents = [
            {"role": "user", "parts": [{"text": user_prompt}]}
        ]

        turn_count = 0
        total_input_tokens = 0
        total_output_tokens = 0

        from backend.services.context_compactor import ContextCompactor
        context_budget = int(os.environ.get("KOBITS_CONTEXT_BUDGET", "32000"))
        compactor = ContextCompactor(max_token_budget=context_budget, recent_turns=3)
        compaction_history = []

        client_timeout = httpx.Timeout(120.0, connect=5.0)
        async with httpx.AsyncClient(timeout=client_timeout) as client:
            while turn_count < max_turns:
                turn_count += 1

                # Dynamic Context Compaction
                if turn_count > 1:
                    contents, c_report = compactor.compact_gemini_contents(contents)
                    if c_report.observations_pruned > 0 or c_report.checkpoints_created > 0:
                        compaction_history.append(c_report.to_dict())

                gen_config: Dict[str, Any] = {
                    "responseMimeType": "application/json",
                    "temperature": 0.2
                }
                if thinking_cfg.enabled and ThinkingEngine.is_reasoning_model(resolved_model):
                    gen_config["thinkingConfig"] = {
                        "thinkingBudget": thinking_cfg.budget_tokens
                    }

                req_body: Dict[str, Any] = {
                    "systemInstruction": {
                        "parts": [
                            {"text": f"{system_prompt}\n\nYou MUST respond with valid JSON matching this schema:\n{json.dumps(schema)}"}
                        ]
                    },
                    "contents": contents,
                    "generationConfig": gen_config
                }
                if gemini_tools and turn_count < max_turns:
                    req_body["tools"] = gemini_tools

                try:
                    res = await client.post(url, json=req_body)
                    if res.status_code != 200:
                        circuit_breaker.record_failure()
                        return {
                            "status": "ERROR",
                            "summary": f"Gemini API returned status {res.status_code}: {res.text[:300]}",
                            "_usage": {"input_tokens": total_input_tokens, "output_tokens": total_output_tokens}
                        }

                    data = res.json()
                    usage = data.get("usageMetadata", {})
                    total_input_tokens += usage.get("promptTokenCount", 0)
                    total_output_tokens += usage.get("candidatesTokenCount", 0)

                    candidates = data.get("candidates", [])
                    if not candidates:
                        return {
                            "status": "ERROR",
                            "summary": "No candidate returned from Gemini.",
                            "_usage": {"input_tokens": total_input_tokens, "output_tokens": total_output_tokens}
                        }

                    content = candidates[0].get("content", {})
                    parts = content.get("parts", [])

                    # Extract Gemini thoughts
                    g_thoughts = ThinkingEngine.extract_thoughts_from_gemini(parts)
                    if g_thoughts:
                        for gt in g_thoughts:
                            thinking_trace.append(gt)
                            if thinking_cfg.stream_thoughts and mission_id:
                                await ThinkingEngine.broadcast_thought(
                                    mission_id=mission_id,
                                    agent_role=agent_role,
                                    thought_chunk=gt,
                                )

                    function_calls = [p["functionCall"] for p in parts if "functionCall" in p]

                    if function_calls and tool_executor and turn_count < max_turns:
                        # Append model function call to turn history
                        contents.append({"role": "model", "parts": parts})

                        response_parts = []
                        for fc in function_calls:
                            fn_name = fc.get("name")
                            fn_args = fc.get("args", {})
                            try:
                                t_res = await tool_executor(fn_name, fn_args)
                                res_obj = t_res if isinstance(t_res, dict) else {"result": str(t_res)}
                            except Exception as err:
                                res_obj = {"status": "error", "message": str(err)}

                            response_parts.append({
                                "functionResponse": {
                                    "name": fn_name,
                                    "response": res_obj
                                }
                            })

                        contents.append({"role": "user", "parts": response_parts})
                        continue

                    # Final text response
                    text_out = ""
                    for p in parts:
                        if "text" in p and not (p.get("thought") is True):
                            text_out += p["text"]

                    circuit_breaker.record_success()

                    try:
                        clean_text = text_out.strip()
                        if clean_text.startswith("```json"):
                            clean_text = clean_text[7:]
                        if clean_text.startswith("```"):
                            clean_text = clean_text[3:]
                        if clean_text.endswith("```"):
                            clean_text = clean_text[:-3]
                        parsed = json.loads(clean_text.strip())
                        parsed["_usage"] = {
                            "input_tokens": total_input_tokens,
                            "output_tokens": total_output_tokens
                        }
                        if thinking_trace:
                            parsed["_thinking_trace"] = thinking_trace
                        if compaction_history:
                            parsed["_usage"]["compaction"] = compaction_history[-1]
                            parsed["_usage"]["compaction_history"] = compaction_history
                        return parsed
                    except Exception:
                        return {
                            "status": "SUCCESS",
                            "summary": text_out[:500],
                            "raw": text_out,
                            "_usage": {
                                "input_tokens": total_input_tokens,
                                "output_tokens": total_output_tokens
                            },
                            "_thinking_trace": thinking_trace if thinking_trace else []
                        }

                except Exception as exc:
                    circuit_breaker.record_failure()
                    logger.error(f"Gemini execution error: {exc}")
                    return {
                        "status": "ERROR",
                        "summary": f"Gemini invocation failed: {str(exc)}",
                        "_usage": {"input_tokens": total_input_tokens, "output_tokens": total_output_tokens}
                    }

        return {
            "status": "FAILED",
            "summary": "Max turns reached without completing execution.",
            "_usage": {"input_tokens": total_input_tokens, "output_tokens": total_output_tokens}
        }
