from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime


class AgentResponse(BaseModel):
    id: str
    name: str
    type: str
    description: Optional[str]
    capabilities: Optional[str]
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AgentRunResponse(BaseModel):
    id: str
    agent_id: str
    project_id: str
    task_id: Optional[str]
    status: str
    input_text: Optional[str]
    output_text: Optional[str]
    tool_calls_json: Optional[str]
    model: Optional[str]
    tokens_input: int
    tokens_output: int
    estimated_cost: float
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    error: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class ApprovalRequestResponse(BaseModel):
    id: str
    project_id: str
    agent_run_id: str
    requested_by: str
    status: str
    title: str
    description: str
    risk_level: str
    affected_services: Optional[str]
    validation_results: Optional[str]
    resolved_by_user_id: Optional[str]
    resolved_at: Optional[datetime]
    feedback: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class ApprovalSubmit(BaseModel):
    approved: bool
    feedback: Optional[str] = None


# ── Agent Contract Schemas ──

class AgentInputSchema(BaseModel):
    project_id: str
    task_id: Optional[str]
    objective: str
    project_context: str
    repository_context: str
    previous_results: str
    relevant_memory: str
    constraints: str

class AgentOutputSchema(BaseModel):
    status: str # SUCCESS, FAILED, BLOCKED
    summary: str
    findings: List[str]
    changes: List[str]
    recommendations: List[str]
    risks: List[str]
    next_actions: List[str]
    artifacts: Dict[str, str] # e.g. {"architecture.md": "..."}
    requires_approval: bool
