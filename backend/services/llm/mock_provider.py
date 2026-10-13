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
            prompt_lower = (user_prompt or "").lower()
            if any(k in prompt_lower for k in ("keyboard", "ui", "frontend", "html", "css", "button", "landing", "website", "screen", "widget")):
                output = {
                    "status": "SUCCESS", 
                    "summary": "Task decomposition complete: Interactive UI component", 
                    "artifacts": {
                        "tasks": [
                            {"title": "Implement interactive frontend component", "agent": "FRONTEND_ENGINEER", "description": "Construct HTML layout, CSS styling, and interactive JavaScript handlers."}
                        ]
                    }
                }
            else:
                output = {
                    "status": "SUCCESS", 
                    "summary": "Task decomposition complete", 
                    "artifacts": {
                        "tasks": [
                            {"title": "Implement mock endpoint", "agent": "BACKEND_ENGINEER", "description": "Build the mock endpoint for testing."}
                        ]
                    }
                }
        elif "FRONTEND_ENGINEER" in header:
            if tool_executor:
                prompt_lower = (user_prompt or "").lower()
                if "keyboard" in prompt_lower:
                    await tool_executor("repository_write", {
                        "path": "frontend/keyboard.html",
                        "content": "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n  <meta charset=\"UTF-8\">\n  <title>Virtual Keyboard</title>\n  <link rel=\"stylesheet\" href=\"keyboard.css\">\n</head>\n<body>\n  <div class=\"keyboard-app\">\n    <h2>Virtual Keyboard</h2>\n    <textarea id=\"keyboard-input\" placeholder=\"Click keys to type...\"></textarea>\n    <div id=\"keyboard-keys\" class=\"keyboard-grid\"></div>\n  </div>\n  <script src=\"keyboard.js\"></script>\n</body>\n</html>\n"
                    })
                    await tool_executor("repository_write", {
                        "path": "frontend/keyboard.css",
                        "content": "body { margin: 0; background: #09090b; color: #f4f4f5; font-family: system-ui, sans-serif; display: flex; justify-content: center; padding: 40px; }\n.keyboard-app { max-width: 600px; width: 100%; }\n#keyboard-input { width: 100%; height: 80px; background: #18181b; border: 1px solid #27272a; color: #f4f4f5; padding: 12px; border-radius: 8px; font-size: 16px; margin-bottom: 16px; box-sizing: border-box; resize: vertical; }\n.keyboard-grid { display: grid; grid-template-columns: repeat(10, 1fr); gap: 6px; }\n.key-btn { background: #27272a; border: 1px solid #3f3f46; color: #fafafa; padding: 14px 6px; border-radius: 6px; font-weight: 600; text-align: center; cursor: pointer; user-select: none; transition: background 0.15s; }\n.key-btn:hover { background: #3f3f46; }\n.key-btn:active { background: #52525b; }\n.key-space { grid-column: span 6; }\n.key-backspace { grid-column: span 2; }\n.key-clear { grid-column: span 2; }\n"
                    })
                    await tool_executor("repository_write", {
                        "path": "frontend/keyboard.js",
                        "content": "document.addEventListener('DOMContentLoaded', () => {\n  const input = document.getElementById('keyboard-input');\n  const grid = document.getElementById('keyboard-keys');\n  const layout = ['Q','W','E','R','T','Y','U','I','O','P','A','S','D','F','G','H','J','K','L',';','Z','X','C','V','B','N','M',',','.','/'];\n  layout.forEach(char => {\n    const b = document.createElement('div');\n    b.className = 'key-btn';\n    b.textContent = char;\n    b.onclick = () => { input.value += char; };\n    grid.appendChild(b);\n  });\n  const space = document.createElement('div');\n  space.className = 'key-btn key-space';\n  space.textContent = 'Space';\n  space.onclick = () => { input.value += ' '; };\n  grid.appendChild(space);\n  const back = document.createElement('div');\n  back.className = 'key-btn key-backspace';\n  back.textContent = '⌫';\n  back.onclick = () => { input.value = input.value.slice(0, -1); };\n  grid.appendChild(back);\n  const clr = document.createElement('div');\n  clr.className = 'key-btn key-clear';\n  clr.textContent = 'Clear';\n  clr.onclick = () => { input.value = ''; };\n  grid.appendChild(clr);\n});\n"
                    })
                else:
                    await tool_executor("repository_write", {
                        "path": "frontend/index.html",
                        "content": "<!DOCTYPE html>\n<html><head><title>App</title><link rel='stylesheet' href='styles.css'></head><body><div id='app'><h1>Kobits Application</h1></div><script src='app.js'></script></body></html>\n"
                    })
                    await tool_executor("repository_write", {
                        "path": "frontend/styles.css",
                        "content": "body { font-family: system-ui, sans-serif; background: #09090b; color: #fafafa; padding: 24px; }\n"
                    })
                    await tool_executor("repository_write", {
                        "path": "frontend/app.js",
                        "content": "console.log('App initialized');\n"
                    })
            output = {"status": "SUCCESS", "summary": "Frontend components created", "artifacts": {"files": ["frontend/keyboard.html"] if "keyboard" in (user_prompt or "").lower() else ["frontend/index.html"]}}
        elif "BACKEND_ENGINEER" in header:
            if tool_executor:
                await tool_executor("repository_write", {"path": "backend/api.py", "content": "def endpoint():\n    return {'status': 'ok'}\n"})
            output = {"status": "SUCCESS", "summary": "Backend implemented", "artifacts": {"swagger": "/api/docs"}}
        else:
            output = {"status": "SUCCESS", "summary": "Task completed successfully", "artifacts": {"evidence": "Task execution completed."}}
            
        output["_usage"] = {"input_tokens": 10, "output_tokens": 10}
        return output
