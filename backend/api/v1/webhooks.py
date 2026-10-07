from fastapi import APIRouter, Depends, Request, HTTPException, BackgroundTasks, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.core.database import get_db
from backend.models.project import Project
from backend.models.mission import Mission, MissionStatus, WorkflowPhase
import uuid
import json
from backend.services.mission_runtime import MissionRuntime
import asyncio

router = APIRouter()

@router.post("/github")
async def github_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Webhook listener for GitHub.
    Listens for issues.opened, issue_comment.created.
    If @kobits is tagged or the issue is assigned to kobits, it creates a Mission.
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
        title = issue.get("title", "GitHub Issue")
        body = issue.get("body", "")
        
        # Check if @kobits is in body or if assigned to kobits
        body_lower = (body or "").lower()
        assignees = [a.get("login", "").lower() for a in issue.get("assignees", [])]
        
        if "@kobits" in body_lower or "kobits" in assignees:
            # Find a matching project
            stmt = select(Project).where(Project.repository_url == repo_url)
            result = await db.execute(stmt)
            project = result.scalars().first()
            
            if not project:
                # If no project matches exactly by URL, just grab the default dev project for now
                stmt = select(Project).limit(1)
                result = await db.execute(stmt)
                project = result.scalars().first()
                if not project:
                    return {"status": "ignored", "reason": "No project found"}
            
            # Create a Mission for this ticket!
            m = Mission(
                id=str(uuid.uuid4()),
                organization_id=project.organization_id,
                project_id=project.id,
                repository_id=project.repository_id,
                created_by=project.created_by, # Fallback to project owner
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
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
        
    issue = payload.get("issue", {})
    fields = issue.get("fields", {})
    
    title = fields.get("summary", "Jira Issue")
    body = fields.get("description", "")
    
    # Check assignees or mentions here... (Simplified for prototype)
    
    stmt = select(Project).limit(1)
    result = await db.execute(stmt)
    project = result.scalars().first()
    
    if project:
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

    return {"status": "ignored"}


@router.post("/linear")
async def linear_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Webhook listener for Linear.
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
        
        stmt = select(Project).limit(1)
        result = await db.execute(stmt)
        project = result.scalars().first()
        
        if project:
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
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
        
    # 1. Slack URL Verification Challenge (Mandatory for real Slack apps)
    if payload.get("type") == "url_verification":
        return Response(content=payload.get("challenge"), media_type="text/plain")
        
    # 2. Handle actual Slack events
    if payload.get("type") == "event_callback":
        event = payload.get("event", {})
        
        # We only care about mentions of the bot
        if event.get("type") == "app_mention":
            text = event.get("text", "")
            channel = event.get("channel")
            
            # Clean up the bot mention from the text (e.g., "<@U12345> fix the bug" -> "fix the bug")
            import re
            cleaned_text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()
            
            if not cleaned_text:
                return {"status": "ignored", "reason": "empty prompt"}
            
            # Find a project to attach this to (In production, map Slack Channel ID to Project ID)
            stmt = select(Project).limit(1)
            result = await db.execute(stmt)
            project = result.scalars().first()
            
            if project:
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
                
                # Trigger the mission runtime asynchronously
                runtime = MissionRuntime(m.id)
                asyncio.create_task(runtime.execute())
                
                return {"status": "success", "mission_id": m.id}

    return {"status": "ignored"}
