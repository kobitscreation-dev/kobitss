from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List

from backend.core.database import get_db
from backend.api.deps import get_current_active_user
from backend.models.organization import User
from backend.models.project import Project
from backend.models.agent import AgentRun, AgentType
from backend.schemas.project import AIRequest, AIPlanResponse
from backend.schemas.agent import AgentResponse, AgentRunResponse, ApprovalRequestResponse, ApprovalSubmit
from backend.services.auth_service import get_user_default_org
from backend.services.orchestration import orchestrate_request
from backend.services.agent_registry import AGENT_REGISTRY

router = APIRouter()

@router.get("", response_model=List[AgentResponse])
async def list_agents():
    """List all 21 specialized agents from the registry."""
    agents = []
    for agent_def in AGENT_REGISTRY.values():
        agents.append({
            "id": f"agent_{agent_def.type.value.lower()}",
            "name": agent_def.name,
            "type": agent_def.type.value,
            "description": agent_def.description,
            "capabilities": ", ".join(agent_def.capabilities),
            "status": "IDLE",
            "created_at": "2026-01-01T00:00:00Z"
        })
    return agents


@router.get("/runs", response_model=List[AgentRunResponse])
async def list_agent_runs(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get active and past agent runs for cost and status tracking."""
    # Note: Using get_user_default_org for now to find the user's primary org
    membership = await get_user_default_org(db, current_user.id)
    stmt = select(AgentRun).where(AgentRun.organization_id == membership.organization_id).order_by(AgentRun.started_at.desc())
    result = await db.execute(stmt)
    runs = result.scalars().all()
    return runs


@router.post("/projects/{project_id}/orchestrate", response_model=AIPlanResponse)
async def trigger_orchestration(
    project_id: str,
    request: AIRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Triggers the 21-Agent Orchestrator DAG for a project."""
    membership = await get_user_default_org(db, current_user.id)
    stmt = select(Project).where(
        Project.id == project_id,
        Project.organization_id == membership.organization_id
    )
    result = await db.execute(stmt)
    project = result.scalars().first()
    
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
        
    return await orchestrate_request(db, project, request.prompt, current_user.id)

@router.post("/projects/{project_id}/orchestrate/approve/{plan_id}")
async def approve_orchestration_plan(
    project_id: str,
    plan_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Approves and executes a previously generated DAG plan."""
    from backend.services.orchestration import approve_plan
    try:
        return await approve_plan(plan_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
