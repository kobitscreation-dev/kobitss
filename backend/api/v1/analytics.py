from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_, case, cast, Integer
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, timezone

from backend.core.database import get_db
from backend.api.deps import get_current_active_user
from backend.models.organization import User
from backend.models.project import Project, Task, TaskStatus, Activity, ActivityType, ProjectMemory
from backend.models.agent import AgentRun, AgentRunStatus, ApprovalRequest, ApprovalStatus, Agent, AgentType
from backend.services.auth_service import get_user_default_org

router = APIRouter()

def get_date_filter(since: str) -> Optional[datetime]:
    now = datetime.now(timezone.utc)
    if since == "7d":
        return now - timedelta(days=7)
    elif since == "30d":
        return now - timedelta(days=30)
    elif since == "90d":
        return now - timedelta(days=90)
    return None

def category_from_agent_type(agent_type: str) -> str:
    mapping = {
        AgentType.BACKEND_ENGINEER.value: "Backend",
        AgentType.FRONTEND_ENGINEER.value: "Frontend",
        AgentType.DATABASE_ENGINEER.value: "Database",
        AgentType.SECURITY_ENGINEER.value: "Security",
        AgentType.QA_ENGINEER.value: "QA",
        AgentType.DEVOPS_ENGINEER.value: "DevOps",
        AgentType.AI_ML_ENGINEER.value: "AI/ML",
        AgentType.MOBILE_ENGINEER.value: "Mobile",
        AgentType.INTEGRATIONS_ENGINEER.value: "Integrations",
        AgentType.PRODUCT_MANAGER.value: "Product",
        AgentType.SOLUTION_ARCHITECT.value: "Architecture",
        AgentType.ACCESSIBILITY_ENGINEER.value: "Accessibility",
        AgentType.DATA_ANALYTICS_ENGINEER.value: "Analytics",
        AgentType.DOCUMENTATION_ENGINEER.value: "Documentation",
        AgentType.RELEASE_MANAGER.value: "Release",
        AgentType.SRE.value: "Reliability",
        AgentType.BUSINESS_ANALYST.value: "Business/Growth",
        AgentType.UX_DESIGNER.value: "Design",
        AgentType.CODE_REVIEWER.value: "Code Review",
    }
    return mapping.get(agent_type, "Unclassified")

@router.get("/reports")
async def get_reports_overview(
    project_id: str,
    since: str = Query("all", description="Time filter: 7d, 30d, 90d, all"),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    membership = await get_user_default_org(db, current_user.id)
    org_id = membership.organization_id

    # Verify project
    res = await db.execute(select(Project).where(Project.id == project_id, Project.organization_id == org_id))
    if not res.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")

    date_limit = get_date_filter(since)
    
    # Task stats
    q_tasks = select(
        func.count().label("total"),
        func.sum(cast(Task.status == TaskStatus.COMPLETED, Integer)).label("completed"),
        func.sum(cast(Task.is_correction == True, Integer)).label("corrected"),
        func.sum(cast(Task.status == TaskStatus.FAILED, Integer)).label("failed")
    ).where(Task.project_id == project_id)
    if date_limit:
        q_tasks = q_tasks.where(Task.created_at >= date_limit)
        
    res_tasks = await db.execute(q_tasks)
    tasks_row = res_tasks.one()

    # Approvals stats
    q_approvals = select(
        func.count().label("total"),
        func.sum(cast(ApprovalRequest.status == ApprovalStatus.PENDING, Integer)).label("pending")
    ).where(ApprovalRequest.project_id == project_id)
    if date_limit:
        q_approvals = q_approvals.where(ApprovalRequest.created_at >= date_limit)
        
    res_approvals = await db.execute(q_approvals)
    app_row = res_approvals.one()
    
    return {
        "tasks_attempted": tasks_row.total or 0,
        "tasks_completed": tasks_row.completed or 0,
        "tasks_corrected": tasks_row.corrected or 0,
        "tasks_failed": tasks_row.failed or 0,
        "approvals_requested": app_row.total or 0,
        "approvals_pending": app_row.pending or 0,
        "security_findings": 0, # Placeholder
        "review_findings": 0,   # Placeholder
        "missions_completed": 0, # Derived from activity in robust impl
        "missions_failed": 0
    }

@router.get("/agents")
async def get_agent_performance(
    project_id: str,
    since: str = Query("all"),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    membership = await get_user_default_org(db, current_user.id)
    org_id = membership.organization_id

    # Verify project
    res = await db.execute(select(Project).where(Project.id == project_id, Project.organization_id == org_id))
    if not res.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")

    date_limit = get_date_filter(since)
    
    # We will compute basic task stats grouped by agent
    q = select(
        Task.assigned_agent_id,
        func.count(Task.id).label("attempted"),
        func.sum(cast(Task.status == TaskStatus.COMPLETED, Integer)).label("completed"),
        func.sum(cast(Task.status == TaskStatus.FAILED, Integer)).label("failed"),
        func.sum(cast(Task.is_correction == True, Integer)).label("corrections"),
        func.sum(cast(and_(Task.status == TaskStatus.COMPLETED, Task.is_correction == False), Integer)).label("first_pass"),
        func.avg(Task.attempt_count).label("avg_attempts"),
        func.sum(cast(Task.requires_approval == True, Integer)).label("human_interventions")
    ).where(Task.project_id == project_id, Task.assigned_agent_id.isnot(None))
    
    if date_limit:
        q = q.where(Task.created_at >= date_limit)
        
    q = q.group_by(Task.assigned_agent_id)
    res = await db.execute(q)
    task_rows = res.all()
    
    # We also need agent run stats (latency, tokens, cost)
    q_runs = select(
        AgentRun.agent_id,
        func.avg(func.extract('epoch', AgentRun.completed_at) - func.extract('epoch', AgentRun.started_at)).label("avg_latency"),
        func.avg(AgentRun.tokens_input + AgentRun.tokens_output).label("avg_tokens"),
        func.avg(AgentRun.estimated_cost).label("avg_cost")
    ).where(AgentRun.project_id == project_id, AgentRun.status == AgentRunStatus.COMPLETED)
    
    if date_limit:
        q_runs = q_runs.where(AgentRun.started_at >= date_limit)
        
    q_runs = q_runs.group_by(AgentRun.agent_id)
    res_runs = await db.execute(q_runs)
    run_rows = {row.agent_id: row for row in res_runs.all()}
    
    # Agent info
    q_agents = select(Agent).where(Agent.id.in_([r.assigned_agent_id for r in task_rows]))
    res_agents = await db.execute(q_agents)
    agents_map = {a.id: a for a in res_agents.scalars().all()}
    
    result = []
    for row in task_rows:
        agent_id = row.assigned_agent_id
        agent = agents_map.get(agent_id)
        if not agent:
            continue
            
        run_data = run_rows.get(agent_id)
        
        attempted = row.attempted or 0
        completed = row.completed or 0
        first_pass = row.first_pass or 0
        corrections = row.corrections or 0
        failed = row.failed or 0
        human = row.human_interventions or 0
        
        result.append({
            "agent_id": agent_id,
            "agent_name": agent.name,
            "agent_type": agent.type.value,
            "tasks_attempted": attempted,
            "success_rate": (completed / attempted) if attempted > 0 else None,
            "first_pass_rate": (first_pass / completed) if completed > 0 else None,
            "correction_rate": (corrections / attempted) if attempted > 0 else None,
            "failure_rate": (failed / attempted) if attempted > 0 else None,
            "avg_attempts": row.avg_attempts,
            "human_intervention_rate": (human / attempted) if attempted > 0 else None,
            "avg_latency": run_data.avg_latency if run_data else None,
            "avg_tokens": run_data.avg_tokens if run_data else None,
            "avg_cost": run_data.avg_cost if run_data else None,
            "avg_tool_calls": None, 
            "memory_utilization": None, 
            "review_acceptance_rate": None,
        })
    
    return result

@router.get("/specialization")
async def get_agent_specialization(
    project_id: str,
    since: str = Query("all"),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    membership = await get_user_default_org(db, current_user.id)
    org_id = membership.organization_id

    # Verify project
    res = await db.execute(select(Project).where(Project.id == project_id, Project.organization_id == org_id))
    if not res.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")

    date_limit = get_date_filter(since)
    
    q = select(
        Task.assigned_agent_id,
        func.count(Task.id).label("attempted"),
        func.sum(cast(Task.status == TaskStatus.COMPLETED, Integer)).label("completed"),
        func.sum(cast(and_(Task.status == TaskStatus.COMPLETED, Task.is_correction == False), Integer)).label("first_pass"),
        func.sum(cast(Task.is_correction == True, Integer)).label("corrections")
    ).where(Task.project_id == project_id, Task.assigned_agent_id.isnot(None))
    
    if date_limit:
        q = q.where(Task.created_at >= date_limit)
        
    q = q.group_by(Task.assigned_agent_id)
    res = await db.execute(q)
    task_rows = res.all()
    
    q_agents = select(Agent).where(Agent.id.in_([r.assigned_agent_id for r in task_rows]))
    res_agents = await db.execute(q_agents)
    agents_map = {a.id: a for a in res_agents.scalars().all()}
    
    specializations = {}
    
    for row in task_rows:
        agent_id = row.assigned_agent_id
        agent = agents_map.get(agent_id)
        if not agent:
            continue
            
        category = category_from_agent_type(agent.type.value)
        
        attempted = row.attempted or 0
        completed = row.completed or 0
        first_pass = row.first_pass or 0
        corrections = row.corrections or 0
        
        if agent_id not in specializations:
            specializations[agent_id] = {
                "agent_id": agent_id,
                "agent_name": agent.name,
                "categories": []
            }
            
        specializations[agent_id]["categories"].append({
            "category": category,
            "attempted": attempted,
            "success_rate": (completed / attempted) if attempted > 0 else None,
            "first_pass_rate": (first_pass / completed) if completed > 0 else None,
            "correction_rate": (corrections / attempted) if attempted > 0 else None,
        })
        
    return list(specializations.values())

@router.get("/memory")
async def get_memory_effectiveness(
    project_id: str,
    since: str = Query("all"),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    membership = await get_user_default_org(db, current_user.id)
    org_id = membership.organization_id

    # Verify project
    res = await db.execute(select(Project).where(Project.id == project_id, Project.organization_id == org_id))
    if not res.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")

    date_limit = get_date_filter(since)
    
    q = select(func.count().label("written")).where(ProjectMemory.project_id == project_id)
    
    if date_limit:
        q = q.where(ProjectMemory.created_at >= date_limit)
        
    res = await db.execute(q)
    mem_row = res.one()
    
    return {
        "memory_written": mem_row.written or 0,
        "memory_retrieved": 0,
        "cross_mission_retrieval": 0,
        "agent_specific_retrieval": 0,
        "project_level_retrieval": 0
    }
