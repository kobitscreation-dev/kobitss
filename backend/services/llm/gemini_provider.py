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
        self.default_model = model or os.environ.get("GEMINI_MODEL") or "gemini-2.5-pro"
        self.base_url = "https://generativelanguage.googleapis.com/v1beta/models"

    def _normalize_model(self, model: Optional[str]) -> str:
        m = (model or self.default_model).lower()
        if "pro" in m:
            return "gemini-2.5-pro"
        return "gemini-2.5-flash"

    async def generate_structured_output(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: Dict[str, Any],
        model: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        max_turns: int = 10,
        tool_executor=None
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

        async with httpx.AsyncClient(timeout=120.0) as client:
            while turn_count < max_turns:
                turn_count += 1

                req_body: Dict[str, Any] = {
                    "systemInstruction": {
                        "parts": [
                            {"text": f"{system_prompt}\n\nYou MUST respond with valid JSON matching this schema:\n{json.dumps(schema)}"}
                        ]
                    },
                    "contents": contents,
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "temperature": 0.2
                    }
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
                        if "text" in p:
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
                        return parsed
                    except Exception:
                        return {
                            "status": "SUCCESS",
                            "summary": text_out[:500],
                            "raw": text_out,
                            "_usage": {
                                "input_tokens": total_input_tokens,
                                "output_tokens": total_output_tokens
                            }
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
