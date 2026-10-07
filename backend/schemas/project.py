from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


# ── Project Schemas ──

class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    tech_stack: Optional[str] = None
    repository_url: Optional[str] = None

class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    progress: Optional[int] = None
    health_score: Optional[int] = None
    tech_stack: Optional[str] = None
    repository_url: Optional[str] = None

class ProjectResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    status: str
    progress: int
    health_score: int
    repository_url: Optional[str]
    tech_stack: Optional[str]
    organization_id: str
    created_by: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Milestone Schemas ──

class MilestoneCreate(BaseModel):
    title: str
    description: Optional[str] = None
    order: int = 0
    start_time: Optional[datetime] = None
    completed_time: Optional[datetime] = None

class MilestoneResponse(BaseModel):
    id: str
    project_id: str
    title: str
    description: Optional[str] = None
    order: int
    status: str
    progress: int
    start_time: Optional[datetime] = None
    completed_time: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Task Schemas ──

class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = None
    priority: str = "MEDIUM"
    parent_task_id: Optional[str] = None

class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    assigned_agent_id: Optional[str] = None

class TaskResponse(BaseModel):
    id: str
    project_id: str
    title: str
    description: Optional[str] = None
    status: str
    priority: str
    assigned_agent_id: Optional[str] = None
    created_by: str
    parent_task_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    mission_id: Optional[str] = None
    milestone_id: Optional[str] = None
    risk_level: Optional[str] = None
    is_correction: Optional[bool] = False
    finding_id: Optional[str] = None
    parent_correction_task_id: Optional[str] = None
    attempt_count: Optional[int] = 1
    dependencies_json: Optional[str] = None
    input_context_json: Optional[str] = None
    expected_output: Optional[str] = None
    requires_approval: Optional[bool] = False
    metadata_json: Optional[str] = None

    model_config = {"from_attributes": True}


# ── Activity Schemas ──

class ActivityResponse(BaseModel):
    id: str
    organization_id: str
    project_id: Optional[str]
    user_id: Optional[str]
    type: str
    title: str
    description: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Notification Schemas ──

class NotificationResponse(BaseModel):
    id: str
    type: str
    title: str
    message: Optional[str]
    read: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ── AI Orchestration Schemas ──

class AIRequest(BaseModel):
    """User submits a natural language request to the AI orchestrator."""
    prompt: str
    project_id: str

class AITaskPlan(BaseModel):
    title: str
    description: str
    agent: str  # AgentType name
    priority: str = "MEDIUM"

class AIPlanResponse(BaseModel):
    summary: str
    goal: str
    plan_id: Optional[str] = None
    tasks: List[AITaskPlan]
    risks: List[str] = []
    requires_approval: bool = True


# ── Organization Schemas ──

class OrganizationResponse(BaseModel):
    id: str
    name: str
    created_at: datetime

    model_config = {"from_attributes": True}

class MemberResponse(BaseModel):
    user_id: str
    organization_id: str
    role: str
    email: Optional[str] = None
    full_name: Optional[str] = None

    model_config = {"from_attributes": True}


# ── Memory Schemas ──

class MemoryCreate(BaseModel):
    category: str
    key: str
    value: str

class MemoryResponse(BaseModel):
    id: str
    project_id: str
    category: str
    key: str
    value: str
    created_at: datetime

    model_config = {"from_attributes": True}
