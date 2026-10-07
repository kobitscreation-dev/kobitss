from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field, ValidationError

class ImplementationTaskDef(BaseModel):
    title: str = Field(..., min_length=3)
    agent: str = Field(..., min_length=2)
    description: str = Field(..., min_length=10)
    phase: Optional[str] = None
    expected_output: Optional[str] = None
    success_criteria: Optional[List[str]] = Field(default_factory=list)
    allowed_tools: Optional[List[str]] = Field(default_factory=list)

def parse_and_validate_tasks(model_output: Dict[str, Any]) -> tuple[List[ImplementationTaskDef], Dict[str, Any]]:
    """Extracts and validates task definitions from model output."""
    tasks = []
    telemetry = {
        "TASK_DECOMPOSITION_SOURCE": "none",
        "PARSED_TASK_COUNT": 0,
        "VALIDATED_TASK_COUNT": 0
    }
    
    # Try to extract from artifacts.task_decomposition, artifacts.tasks, or decomposition
    raw_tasks = None
    if "artifacts" in model_output and isinstance(model_output["artifacts"], dict):
        if "task_decomposition" in model_output["artifacts"]:
            raw_tasks = model_output["artifacts"]["task_decomposition"]
            telemetry["TASK_DECOMPOSITION_SOURCE"] = "artifacts.task_decomposition"
        elif "tasks" in model_output["artifacts"]:
            raw_tasks = model_output["artifacts"]["tasks"]
            telemetry["TASK_DECOMPOSITION_SOURCE"] = "artifacts.tasks"
            
    if raw_tasks is None and "decomposition" in model_output:
        raw_tasks = model_output["decomposition"]
        telemetry["TASK_DECOMPOSITION_SOURCE"] = "decomposition"
        
    if raw_tasks is None:
        raise ValueError("TASK_DECOMPOSITION_MISSING: No task decomposition found in model output")
        
    if not isinstance(raw_tasks, list):
        raise ValueError("Tasks field is not a list")
        
    telemetry["PARSED_TASK_COUNT"] = len(raw_tasks)
    if len(raw_tasks) == 0:
        raise ValueError("TASK_DECOMPOSITION_MISSING: Task decomposition array is empty")
        
    for idx, t in enumerate(raw_tasks):
        if not isinstance(t, dict):
            raise ValueError(f"Task at index {idx} is not an object")
        
        # Normalize fields if necessary
        title = t.get("title") or t.get("name")
        description = t.get("description") or t.get("details")
        agent = t.get("agent", "BACKEND_ENGINEER")
        
        try:
            parsed = ImplementationTaskDef(
                title=title, 
                agent=agent, 
                description=description,
                phase=t.get("phase"),
                expected_output=t.get("expected_output"),
                success_criteria=t.get("success_criteria") or t.get("acceptance_criteria") or [],
                allowed_tools=t.get("allowed_tools") or []
            )
            tasks.append(parsed)
        except ValidationError as e:
            raise ValueError(f"Task at index {idx} failed schema validation: {str(e)}")
            
    telemetry["VALIDATED_TASK_COUNT"] = len(tasks)
    return tasks, telemetry
