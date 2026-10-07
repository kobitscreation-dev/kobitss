"""
Builder API — REST endpoints for the Real Builder Agent.
Lets users trigger builds, inspect sandbox sessions, view diffs and PRs.
"""

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional, List

from backend.core.database import get_db
from backend.api.deps import get_current_active_user
from backend.models.organization import User
from backend.models.project import Project, Activity, ActivityType
from backend.models.agent import AgentType
from backend.services.auth_service import get_user_default_org
from backend.services.sandbox_manager import SandboxManager
from backend.services.builder_agent import builder_agent

import json
import os

router = APIRouter()


# ── Schemas ─────────────────────────────────────────────────────

class BuildRequest(BaseModel):
    task: str
    agent_type: str = "BACKEND_ENGINEER"


class BuildResponse(BaseModel):
    session_id: str
    branch: str
    status: str
    files_changed: List[str]
    build_log: list
    pr: dict


class SandboxResponse(BaseModel):
    session_id: str
    branch_name: str
    sandbox_dir: str
    files_changed: List[str]
    status: str
    created_at: str


# ── Background task runner ──────────────────────────────────────

_build_results: dict = {}  # session_id -> result


async def _run_build_in_background(
    project_root: str,
    task: str,
    agent_type_str: str,
    project_id: str,
    org_id: str,
    user_id: str,
    session_key: str,
):
    """Run the builder agent in background and store the result."""
    from backend.core.database import AsyncSessionLocal

    try:
        agent_type = AgentType(agent_type_str)
    except ValueError:
        agent_type = AgentType.BACKEND_ENGINEER

    # Log activity
    async with AsyncSessionLocal() as db:
        activity = Activity(
            organization_id=org_id,
            project_id=project_id,
            user_id=user_id,
            type=ActivityType.AGENT_STARTED,
            title=f"Builder Agent Started ({agent_type.value})",
            description=f"Building: {task[:80]}...",
            metadata_json=json.dumps({"agent": agent_type.value}),
        )
        db.add(activity)
        await db.commit()

    # Run the build
    result = await builder_agent.build(
        project_root=project_root,
        task_description=task,
        agent_type=agent_type,
    )

    _build_results[session_key] = result

    # Log completion
    async with AsyncSessionLocal() as db:
        activity = Activity(
            organization_id=org_id,
            project_id=project_id,
            user_id=user_id,
            type=ActivityType.AGENT_COMPLETED,
            title=f"Builder Agent Completed",
            description=f"Branch: {result.get('branch', 'N/A')} | Files: {len(result.get('files_changed', []))}",
            metadata_json=json.dumps({
                "session_id": result.get("session_id"),
                "files_changed": result.get("files_changed", []),
                "status": result.get("status"),
            }),
        )
        db.add(activity)
        await db.commit()


# ── Endpoints ───────────────────────────────────────────────────

@router.post("/{project_id}/build", response_model=dict)
async def trigger_build(
    project_id: str,
    request: BuildRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Trigger the Builder Agent to implement a task.
    Creates a sandbox, runs the agentic loop, and creates a PR.
    """
    membership = await get_user_default_org(db, current_user.id)
    stmt = select(Project).where(
        Project.id == project_id,
        Project.organization_id == membership.organization_id,
    )
    result = await db.execute(stmt)
    project = result.scalars().first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Determine project root — for now use the kobits project itself
    project_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )

    # Generate a session key to track this build
    import uuid
    session_key = str(uuid.uuid4())[:12]

    # Kick off the build in the background
    background_tasks.add_task(
        _run_build_in_background,
        project_root=project_root,
        task=request.task,
        agent_type_str=request.agent_type,
        project_id=project.id,
        org_id=project.organization_id,
        user_id=current_user.id,
        session_key=session_key,
    )

    return {
        "message": "Build started in background",
        "session_key": session_key,
        "agent_type": request.agent_type,
        "task": request.task,
        "status": "BUILDING",
    }


@router.get("/{project_id}/build/{session_key}", response_model=dict)
async def get_build_result(
    project_id: str,
    session_key: str,
    current_user: User = Depends(get_current_active_user),
):
    """Poll the result of a background build."""
    result = _build_results.get(session_key)
    if not result:
        return {"status": "BUILDING", "message": "Build is still in progress..."}
    return result


@router.get("/sandboxes", response_model=list)
async def list_sandboxes(
    current_user: User = Depends(get_current_active_user),
):
    """List all active sandbox sessions."""
    return SandboxManager.list_sessions()


@router.get("/sandboxes/{session_id}", response_model=dict)
async def get_sandbox(
    session_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """Get details of a specific sandbox session."""
    session = SandboxManager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sandbox not found")
    return session.to_dict()


@router.get("/sandboxes/{session_id}/diff", response_model=dict)
async def get_sandbox_diff(
    session_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """Get the git diff of changes in a sandbox."""
    return SandboxManager.get_diff(session_id)


@router.get("/sandboxes/{session_id}/file", response_model=dict)
async def read_sandbox_file(
    session_id: str,
    path: str = ".",
    current_user: User = Depends(get_current_active_user),
):
    """Read a file from a sandbox workspace."""
    return SandboxManager.read_file(session_id, path)


@router.delete("/sandboxes/{session_id}", response_model=dict)
async def delete_sandbox(
    session_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """Clean up a sandbox workspace."""
    return SandboxManager.cleanup(session_id)
