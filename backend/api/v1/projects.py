from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import List
import uuid

from backend.core.database import get_db
from backend.api.deps import get_current_active_user
from backend.models.organization import User, OrganizationMember, OrgRole
from backend.models.project import Project, ProjectStatus, Activity, ActivityType, Task, TaskStatus
from backend.models.agent import ApprovalRequest, ApprovalStatus
from backend.schemas.project import (
    ProjectCreate, ProjectUpdate, ProjectResponse,
    TaskCreate, TaskUpdate, TaskResponse,
    ActivityResponse,
)
from backend.schemas.agent import ApprovalRequestResponse, ApprovalSubmit
from backend.services.auth_service import get_user_default_org, get_user_org_membership, require_role
from backend.services.activity_service import log_activity

router = APIRouter()


# ══════════════════════════════════════════
#  PROJECTS
# ══════════════════════════════════════════

@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    project_in: ProjectCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    membership = await get_user_default_org(db, current_user.id)
    require_role(membership, OrgRole.MEMBER)

    project = Project(
        name=project_in.name,
        description=project_in.description,
        tech_stack=project_in.tech_stack,
        repository_url=project_in.repository_url,
        organization_id=membership.organization_id,
        created_by=current_user.id,
    )
    db.add(project)
    await db.flush()

    await log_activity(
        db,
        organization_id=membership.organization_id,
        activity_type=ActivityType.PROJECT_CREATED,
        title=f"Project '{project.name}' created",
        project_id=project.id,
        user_id=current_user.id,
    )

    await db.commit()
    await db.refresh(project)
    return project


@router.get("", response_model=List[ProjectResponse])
async def list_projects(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    membership = await get_user_default_org(db, current_user.id)
    result = await db.execute(
        select(Project)
        .where(Project.organization_id == membership.organization_id)
        .order_by(Project.created_at.desc())
    )
    return result.scalars().all()


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    membership = await get_user_default_org(db, current_user.id)
    result = await db.execute(
        select(Project).where(
            Project.id == project_id,
            Project.organization_id == membership.organization_id,
        )
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    project_in: ProjectUpdate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    membership = await get_user_default_org(db, current_user.id)
    require_role(membership, OrgRole.MEMBER)

    result = await db.execute(
        select(Project).where(
            Project.id == project_id,
            Project.organization_id == membership.organization_id,
        )
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    update_data = project_in.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(project, key, value)

    await log_activity(
        db,
        organization_id=membership.organization_id,
        activity_type=ActivityType.PROJECT_UPDATED,
        title=f"Project '{project.name}' updated",
        project_id=project.id,
        user_id=current_user.id,
    )

    await db.commit()
    await db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    membership = await get_user_default_org(db, current_user.id)
    require_role(membership, OrgRole.ADMIN)

    result = await db.execute(
        select(Project).where(
            Project.id == project_id,
            Project.organization_id == membership.organization_id,
        )
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    await db.delete(project)
    await db.commit()


# ══════════════════════════════════════════
#  TASKS
# ══════════════════════════════════════════

@router.post("/{project_id}/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    project_id: str,
    task_in: TaskCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    membership = await get_user_default_org(db, current_user.id)

    # Verify user has access to this project
    result = await db.execute(
        select(Project).where(
            Project.id == project_id,
            Project.organization_id == membership.organization_id,
        )
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    task = Task(
        project_id=project_id,
        title=task_in.title,
        description=task_in.description,
        priority=task_in.priority,
        parent_task_id=task_in.parent_task_id,
        created_by=current_user.id,
    )
    db.add(task)
    await db.flush()

    await log_activity(
        db,
        organization_id=membership.organization_id,
        activity_type=ActivityType.TASK_CREATED,
        title=f"Task '{task.title}' created",
        project_id=project_id,
        user_id=current_user.id,
    )

    await db.commit()
    await db.refresh(task)
    return task


@router.get("/{project_id}/tasks", response_model=List[TaskResponse])
async def list_tasks(
    project_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    membership = await get_user_default_org(db, current_user.id)

    # Verify org access via project
    result = await db.execute(
        select(Project).where(
            Project.id == project_id,
            Project.organization_id == membership.organization_id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")

    result = await db.execute(
        select(Task)
        .where(Task.project_id == project_id)
        .order_by(Task.created_at.desc())
    )
    return result.scalars().all()


@router.get("/{project_id}/tasks/{task_id}", response_model=TaskResponse)
async def get_task(
    project_id: str,
    task_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    membership = await get_user_default_org(db, current_user.id)

    # Verify org access via project
    result = await db.execute(
        select(Project).where(
            Project.id == project_id,
            Project.organization_id == membership.organization_id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")

    result = await db.execute(
        select(Task).where(
            Task.id == task_id,
            Task.project_id == project_id
        )
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.post("/{project_id}/tasks/{task_id}/cancel", response_model=TaskResponse)
async def cancel_task(
    project_id: str,
    task_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    membership = await get_user_default_org(db, current_user.id)
    result = await db.execute(
        select(Project).where(
            Project.id == project_id,
            Project.organization_id == membership.organization_id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")

    result = await db.execute(
        select(Task).where(
            Task.id == task_id,
            Task.project_id == project_id
        )
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status in [TaskStatus.COMPLETED, TaskStatus.CANCELLED]:
        raise HTTPException(status_code=400, detail=f"Cannot cancel task in status {task.status.value}")
    task.status = TaskStatus.CANCELLED
    await db.commit()
    await db.refresh(task)
    return task


# ══════════════════════════════════════════
#  ACTIVITIES
# ══════════════════════════════════════════

@router.get("/{project_id}/activities", response_model=List[ActivityResponse])
async def list_project_activities(
    project_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    membership = await get_user_default_org(db, current_user.id)

    result = await db.execute(
        select(Activity)
        .where(
            Activity.organization_id == membership.organization_id,
            Activity.project_id == project_id,
        )
        .order_by(Activity.created_at.desc())
        .limit(100)
    )
    return result.scalars().all()


# ══════════════════════════════════════════
#  DASHBOARD STATS
# ══════════════════════════════════════════

@router.get("/stats/overview")
async def dashboard_stats(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Aggregated stats for the current user's organization dashboard."""
    membership = await get_user_default_org(db, current_user.id)
    org_id = membership.organization_id

    # Count projects
    project_count = await db.execute(
        select(func.count(Project.id)).where(Project.organization_id == org_id)
    )

    # Count tasks by status
    total_tasks = await db.execute(
        select(func.count(Task.id))
        .join(Project, Task.project_id == Project.id)
        .where(Project.organization_id == org_id)
    )
    completed_tasks = await db.execute(
        select(func.count(Task.id))
        .join(Project, Task.project_id == Project.id)
        .where(Project.organization_id == org_id, Task.status == TaskStatus.COMPLETED)
    )

    return {
        "projects": project_count.scalar() or 0,
        "total_tasks": total_tasks.scalar() or 0,
        "completed_tasks": completed_tasks.scalar() or 0,
        "active_agents": 0,  # Will be populated once agents are running
    }
# Append to the end of backend/api/v1/projects.py
from backend.models.agent import ApprovalRequest, ApprovalStatus

@router.get("/{project_id}/approvals", response_model=List[ApprovalRequestResponse])
async def get_approval_requests(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get all human approval requests for the project."""
    membership = await get_user_org_membership(db, current_user.id, (await get_user_default_org(db, current_user.id)).organization_id)
    
    stmt = select(ApprovalRequest).where(
        ApprovalRequest.project_id == project_id,
        ApprovalRequest.organization_id == membership.organization_id
    ).order_by(ApprovalRequest.created_at.desc())
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/{project_id}/approvals/{approval_id}", response_model=ApprovalRequestResponse)
async def resolve_approval_request(
    project_id: str,
    approval_id: str,
    submission: ApprovalSubmit,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Approve or reject a pending agent action."""
    membership = await get_user_org_membership(db, current_user.id, (await get_user_default_org(db, current_user.id)).organization_id)
    require_role(membership, OrgRole.ADMIN) # Only admins can approve
    
    stmt = select(ApprovalRequest).where(
        ApprovalRequest.id == approval_id,
        ApprovalRequest.project_id == project_id,
        ApprovalRequest.organization_id == membership.organization_id
    )
    result = await db.execute(stmt)
    approval = result.scalars().first()
    
    if not approval:
        raise HTTPException(status_code=404, detail="Approval request not found")
        
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=400, detail="Approval already resolved")
        
    approval.status = ApprovalStatus.APPROVED if submission.approved else ApprovalStatus.REJECTED
    approval.feedback = submission.feedback
    approval.resolved_by_user_id = current_user.id
    
    # Trigger MissionRuntime if this was a mission-level approval gate
    from backend.models.mission import Mission, MissionStatus
    stmt_m = select(Mission).where(Mission.project_id == project_id, Mission.status == MissionStatus.AWAITING_APPROVAL)
    m_res = await db.execute(stmt_m)
    for m in m_res.scalars().all():
        if m.phase.value in approval.title: # Quick heuristic based on title "Gate: PHASE Approval Required"
            if submission.approved:
                m.status = MissionStatus.ACTIVE
                await db.commit() # Save ACTIVE status before triggering runtime
                from backend.services.mission_runtime import MissionRuntime
                import asyncio
                asyncio.create_task(MissionRuntime(m.id, m.organization_id, current_user.id).execute())
            else:
                m.status = MissionStatus.CANCELLED
    
    await db.commit()
    await db.refresh(approval)
    return approval

@router.get("/{project_id}/activities", response_model=List[ActivityResponse])
async def list_project_activities(
    project_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    membership = await get_user_default_org(db, current_user.id)
    
    # Verify project
    result = await db.execute(
        select(Project).where(
            Project.id == project_id,
            Project.organization_id == membership.organization_id
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")
        
    result = await db.execute(
        select(Activity)
        .where(Activity.project_id == project_id)
        .order_by(Activity.created_at.desc())
        .limit(100)
    )
    return result.scalars().all()


from pydantic import BaseModel, Field
import json
from backend.services.llm import get_llm_provider
from backend.models.mission import Mission, MissionStatus, WorkflowPhase

class DeliverableDef(BaseModel):
    title: str = Field(description="Title of the deliverable mission, e.g. 'Auth System'")
    description: str = Field(description="Detailed objective of this mission")

@router.post("/{project_id}/plan")
async def plan_project(
    project_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
        
    provider = get_llm_provider()
    
    sys_prompt = "You are an expert AI Architect (like Kyros). Break down the user's project into 2-4 sequential Sprints. For each Sprint, define 1-3 core Deliverables. Return an array of Sprints, where each Sprint has a title (e.g. 'Sprint 1') and an array of Deliverables (title, description)."
    usr_prompt = f"Project Name: {project.name}\nDescription: {project.description}"
    
    schema = {
        "type": "object",
        "properties": {
            "sprints": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Sprint name, e.g. 'Sprint 1: Foundation'"},
                        "deliverables": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string", "description": "e.g. 'Auth System'"},
                                    "description": {"type": "string"}
                                },
                                "required": ["title", "description"]
                            }
                        }
                    },
                    "required": ["title", "deliverables"]
                }
            }
        },
        "required": ["sprints"]
    }
    
    res = await provider.generate_structured_output(sys_prompt, usr_prompt, schema, model="gpt-5.6-luna")
    
    sprints = res.get("sprints", [])
    if not sprints:
        sprints = [{"title": "Sprint 1", "deliverables": [{"title": "Initial Implementation", "description": project.description}]}]
        
    created_missions = []
    import uuid
    is_first = True
    for sprint in sprints:
        sprint_title = sprint.get("title", "Sprint")
        for d in sprint.get("deliverables", []):
            m = Mission(
                id=str(uuid.uuid4()),
                organization_id=project.organization_id,
                project_id=project.id,
                repository_id=project.repository_id,
                created_by=current_user.id,
                title=f"[{sprint_title}] {d.get('title')}",
                objective=d.get('description'),
                status=MissionStatus.ACTIVE if is_first else MissionStatus.AWAITING_APPROVAL,
                phase=WorkflowPhase.INTAKE if is_first else WorkflowPhase.INTAKE
            )
            db.add(m)
            created_missions.append(m)
            is_first = False
        
    await db.commit()
    
    # Trigger the first mission async
    from backend.services.mission_runtime import MissionRuntime
    import asyncio
    first_mission = created_missions[0]
    runtime = MissionRuntime(first_mission.id)
    asyncio.create_task(runtime.execute())
    
    return {"message": "Project planned successfully", "deliverables_count": len(created_missions)}
from backend.models.mission import Milestone
from backend.schemas.project import MilestoneCreate, MilestoneResponse

@router.post("/{project_id}/milestones", response_model=MilestoneResponse, status_code=status.HTTP_201_CREATED)
async def create_milestone(
    project_id: str,
    milestone_in: MilestoneCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    membership = await get_user_default_org(db, current_user.id)
    project = await db.get(Project, project_id)
    if not project or project.organization_id != membership.organization_id:
        raise HTTPException(status_code=404, detail="Project not found")

    milestone = Milestone(
        id=str(uuid.uuid4()),
        project_id=project_id,
        title=milestone_in.title,
        description=milestone_in.description,
        order=milestone_in.order,
        start_time=milestone_in.start_time,
        completed_time=milestone_in.completed_time
    )
    db.add(milestone)
    await db.commit()
    await db.refresh(milestone)
    return milestone

@router.get("/{project_id}/milestones", response_model=list[MilestoneResponse])
async def list_milestones(
    project_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    membership = await get_user_default_org(db, current_user.id)
    project = await db.get(Project, project_id)
    if not project or project.organization_id != membership.organization_id:
        raise HTTPException(status_code=404, detail="Project not found")

    result = await db.execute(select(Milestone).where(Milestone.project_id == project_id).order_by(Milestone.order))
    return result.scalars().all()

