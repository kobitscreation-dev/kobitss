import asyncio
import json
import logging
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database import get_db
from backend.models.github import Repository
from backend.models.mission import Mission, MissionStatus, WorkflowPhase
from backend.models.project import Project
from backend.services.mission_runtime import MissionRuntime

logger = logging.getLogger("kobits.security.webhooks")

router = APIRouter()


@router.post("/github")
async def github_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Webhook listener for GitHub.
    Listens for issues.opened, issue_comment.created.
    If @kobits is tagged or the issue is assigned to kobits, it creates a Mission.
    Multi-tenant isolation: Requires exact repository mapping, otherwise returns 404.
    """
    event = request.headers.get("x-github-event")
    
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if event == "issues" and payload.get("action") == "opened":
        issue = payload.get("issue", {})
        repo = payload.get("repository", {})
        
        repo_url = repo.get("html_url")
        clone_url = repo.get("clone_url")
        full_name = repo.get("full_name")
        title = issue.get("title", "GitHub Issue")
        body = issue.get("body", "")
        
        # Check if @kobits is in body or if assigned to kobits
        body_lower = (body or "").lower()
        assignees = [a.get("login", "").lower() for a in issue.get("assignees", [])]
        
        if "@kobits" in body_lower or "kobits" in assignees:
            project = None
            if repo_url:
                stmt = select(Project).where(Project.repository_url == repo_url)
                result = await db.execute(stmt)
                project = result.scalars().first()

            if not project and (full_name or clone_url or repo_url):
                r_stmt = select(Repository).where(
                    (Repository.full_name == full_name) |
                    (Repository.clone_url == clone_url) |
                    (Repository.web_url == repo_url)
                )
                r_res = await db.execute(r_stmt)
                repo_rec = r_res.scalars().first()
                if repo_rec and repo_rec.project_id:
                    project = await db.get(Project, repo_rec.project_id)
            
            if not project:
                logger.warning(
                    f"Security Audit: GitHub webhook rejected. Unmapped repository: '{repo_url or full_name}'"
                )
                raise HTTPException(status_code=404, detail="Project not found for repository")
            
            # Create a Mission for this ticket!
            m = Mission(
                id=str(uuid.uuid4()),
                organization_id=project.organization_id,
                project_id=project.id,
                repository_id=project.repository_id,
                created_by=project.created_by,  # Project owner
                title=f"[GitHub] {title}",
                objective=body or title,
                status=MissionStatus.ACTIVE,
                phase=WorkflowPhase.INTAKE
            )
            db.add(m)
            await db.commit()
            
            # Trigger the mission runtime asynchronously
            runtime = MissionRuntime(m.id)
            asyncio.create_task(runtime.execute())
            
            return {"status": "success", "mission_id": m.id, "message": "Mission started from GitHub issue"}
            
    return {"status": "ignored"}


@router.post("/jira")
async def jira_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Webhook listener for Jira.
    Multi-tenant isolation: Requires project mapping by key or id, otherwise returns 404.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
        
    issue = payload.get("issue", {})
    fields = issue.get("fields", {})
    
    title = fields.get("summary", "Jira Issue")
    body = fields.get("description", "")
    
    jira_proj = fields.get("project", {})
    jira_proj_key = jira_proj.get("key") or jira_proj.get("id") or payload.get("project_key")
    
    project = None
    if jira_proj_key:
        stmt = select(Project).where(
            (Project.name == jira_proj_key) | (Project.id == jira_proj_key)
        )
        result = await db.execute(stmt)
        project = result.scalars().first()
    
    if not project:
        logger.warning(
            f"Security Audit: Jira webhook rejected. Unmapped project key: '{jira_proj_key}'"
        )
        raise HTTPException(status_code=404, detail="Project not found for Jira issue")
    
    m = Mission(
        id=str(uuid.uuid4()),
        organization_id=project.organization_id,
        project_id=project.id,
        repository_id=project.repository_id,
        created_by=project.created_by,
        title=f"[Jira] {title}",
        objective=body or title,
        status=MissionStatus.ACTIVE,
        phase=WorkflowPhase.INTAKE
    )
    db.add(m)
    await db.commit()
    
    runtime = MissionRuntime(m.id)
    asyncio.create_task(runtime.execute())
    
    return {"status": "success", "mission_id": m.id}


@router.post("/linear")
async def linear_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Webhook listener for Linear.
    Multi-tenant isolation: Requires project mapping by team/project id, otherwise returns 404.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
        
    action = payload.get("action")
    if action == "create" and payload.get("type") == "Issue":
        data = payload.get("data", {})
        title = data.get("title", "Linear Issue")
        body = data.get("description", "")
        
        linear_team = data.get("teamId") or data.get("projectId") or (
            data.get("team", {}).get("key") if isinstance(data.get("team"), dict) else None
        )
        
        project = None
        if linear_team:
            stmt = select(Project).where(
                (Project.name == linear_team) | (Project.id == linear_team)
            )
            result = await db.execute(stmt)
            project = result.scalars().first()
        
        if not project:
            logger.warning(
                f"Security Audit: Linear webhook rejected. Unmapped team/project: '{linear_team}'"
            )
            raise HTTPException(status_code=404, detail="Project not found for Linear issue")
        
        m = Mission(
            id=str(uuid.uuid4()),
            organization_id=project.organization_id,
            project_id=project.id,
            repository_id=project.repository_id,
            created_by=project.created_by,
            title=f"[Linear] {title}",
            objective=body or title,
            status=MissionStatus.ACTIVE,
            phase=WorkflowPhase.INTAKE
        )
        db.add(m)
        await db.commit()
        
        runtime = MissionRuntime(m.id)
        asyncio.create_task(runtime.execute())
        
        return {"status": "success", "mission_id": m.id}

    return {"status": "ignored"}


@router.post("/slack")
async def slack_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Webhook listener for Slack Events API.
    Handles URL verification and app_mention events.
    Multi-tenant isolation: Requires channel mapping to Project, otherwise returns 404.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
        
    # 1. Slack URL Verification Challenge
    if payload.get("type") == "url_verification":
        return Response(content=payload.get("challenge"), media_type="text/plain")
        
    # 2. Handle actual Slack events
    if payload.get("type") == "event_callback":
        event = payload.get("event", {})
        
        if event.get("type") == "app_mention":
            text = event.get("text", "")
            channel = event.get("channel")
            
            cleaned_text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()
            if not cleaned_text:
                return {"status": "ignored", "reason": "empty prompt"}
            
            project = None
            if channel:
                stmt = select(Project).where(
                    (Project.id == channel) | (Project.name == channel)
                )
                result = await db.execute(stmt)
                project = result.scalars().first()
            
            if not project:
                logger.warning(
                    f"Security Audit: Slack webhook rejected. Unmapped channel: '{channel}'"
                )
                raise HTTPException(status_code=404, detail=f"Project not mapped for Slack channel {channel}")
            
            m = Mission(
                id=str(uuid.uuid4()),
                organization_id=project.organization_id,
                project_id=project.id,
                repository_id=project.repository_id,
                created_by=project.created_by,
                title=f"[Slack Request] {cleaned_text[:30]}...",
                objective=cleaned_text,
                status=MissionStatus.ACTIVE,
                phase=WorkflowPhase.INTAKE
            )
            db.add(m)
            await db.commit()
            
            runtime = MissionRuntime(m.id)
            asyncio.create_task(runtime.execute())
            
            return {"status": "success", "mission_id": m.id}

    return {"status": "ignored"}
