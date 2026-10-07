import json
import asyncio
from typing import List, Dict, Any, Optional
from backend.services.llm.provider import LLMProvider

class MockProvider(LLMProvider):
    def __init__(self, deterministic_scenarios=None):
        self.scenarios = deterministic_scenarios or {}
        # scenarios mapping: e.g., task title -> predefined sequence of tool calls and output
    
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
        
        # Simulate network latency
        await asyncio.sleep(0.1)
        
        task_title = ""
        try:
            input_data = json.loads(user_prompt.split("Task Input:\n")[1].split("\n\nPlease execute")[0])
            task_title = input_data.get("task_title", "")
        except:
            pass
            
        scenario_key = None
        for key in self.scenarios.keys():
            if key == task_title:
                scenario_key = key
                break
                
        scenario = self.scenarios.get(scenario_key) if scenario_key else None
        
        if scenario:
            if "error" in scenario:
                if scenario["error"] == "TIMEOUT":
                    raise TimeoutError("Provider timeout")
                elif scenario["error"] == "INVALID_API_KEY":
                    raise PermissionError("Invalid API Key")
            
            # Execute mock tool calls if requested
            if "tool_calls" in scenario and tool_executor:
                for call in scenario["tool_calls"]:
                    await tool_executor(call["name"], call["args"])
            
            output = scenario.get("output", {"status": "SUCCESS", "summary": "Mock success"})
            output["_usage"] = {"input_tokens": 100, "output_tokens": 50}
            return output
            
        # Default fallback (match primary agent identity header first)
        header = system_prompt.splitlines()[0] if system_prompt else ""
        if "QA_ENGINEER" in header:
            output = {"status": "SUCCESS", "summary": "QA passed", "artifacts": {"test_report": "All tests passed"}}
        elif "SECURITY" in header:
            output = {"status": "SUCCESS", "summary": "Security review passed", "artifacts": {"security_report": "No vulnerabilities"}}
        elif "CODE_REVIEWER" in header:
            output = {"status": "SUCCESS", "summary": "Code review passed", "artifacts": {"review": "Looks good"}}
        elif "TECHNICAL_LEAD" in header or "TECHNICAL_LEAD" in system_prompt:
            output = {
                "status": "SUCCESS", 
                "summary": "Task decomposition complete", 
                "artifacts": {
                    "tasks": [
                        {"title": "Implement mock endpoint", "agent": "BACKEND_ENGINEER", "description": "Build the mock endpoint for testing."}
                    ]
                }
            }
        elif "BACKEND_ENGINEER" in header:
            if tool_executor:
                await tool_executor("repository_write", {"path": "backend/api.py", "content": "def endpoint():\n    return {'status': 'ok'}\n"})
            output = {"status": "SUCCESS", "summary": "Backend implemented", "artifacts": {"swagger": "/api/docs"}}
        else:
            output = {"status": "SUCCESS", "summary": "Task completed successfully", "artifacts": {"evidence": "Task execution completed."}}
            
        output["_usage"] = {"input_tokens": 10, "output_tokens": 10}
        return output
