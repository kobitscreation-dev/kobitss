from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import json
import uuid
import httpx

from backend.core.database import get_db
from backend.models.organization import User, OrganizationMember
from backend.models.project import Project, Activity, ActivityType
from backend.models.github import (
    GitHubConnection, GitHubConnectionStatus, Repository, 
    RepositoryStatus, RepositoryBranch, RepositorySnapshot
)
from backend.api.deps import get_current_active_user
from backend.services.sandbox_manager import SandboxManager

router = APIRouter(tags=["github"])

async def get_user_default_org(db: AsyncSession, user_id: str) -> OrganizationMember:
    stmt = select(OrganizationMember).where(OrganizationMember.user_id == user_id)
    result = await db.execute(stmt)
    membership = result.scalars().first()
    if not membership:
        raise HTTPException(status_code=403, detail="User not part of an organization")
    return membership

class ConnectRequest(BaseModel):
    token: str
    username: str

@router.post("/connect")
async def connect_github(
    request: ConnectRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Simulates OAuth callback by accepting a Personal Access Token (PAT) for real GitHub API calls."""
    membership = await get_user_default_org(db, current_user.id)
    
    # Check if exists
    stmt = select(GitHubConnection).where(
        GitHubConnection.organization_id == membership.organization_id,
        GitHubConnection.user_id == current_user.id
    )
    result = await db.execute(stmt)
    conn = result.scalars().first()
    
    if not conn:
        conn = GitHubConnection(
            organization_id=membership.organization_id,
            user_id=current_user.id,
            github_username=request.username,
            status=GitHubConnectionStatus.CONNECTED,
            metadata_json=json.dumps({"token": request.token}) # In production this would be encrypted
        )
        db.add(conn)
    else:
        conn.github_username = request.username
        conn.status = GitHubConnectionStatus.CONNECTED
        conn.metadata_json = json.dumps({"token": request.token})
        
    await db.commit()
    return {"status": "success", "message": "GitHub connected successfully"}

@router.get("/status")
async def get_github_connection_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    membership = await get_user_default_org(db, current_user.id)
    stmt = select(GitHubConnection).where(
        GitHubConnection.organization_id == membership.organization_id,
        GitHubConnection.user_id == current_user.id
    )
    result = await db.execute(stmt)
    conn = result.scalars().first()
    
    if conn and conn.status == GitHubConnectionStatus.CONNECTED:
        return {"connected": True, "username": conn.github_username}
    return {"connected": False}

@router.get("/repositories")
async def list_github_repositories(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Fetch user's repositories from real GitHub API."""
    membership = await get_user_default_org(db, current_user.id)
    
    stmt = select(GitHubConnection).where(
        GitHubConnection.organization_id == membership.organization_id,
        GitHubConnection.user_id == current_user.id,
        GitHubConnection.status == GitHubConnectionStatus.CONNECTED
    )
    result = await db.execute(stmt)
    conn = result.scalars().first()
    
    if not conn or not conn.metadata_json:
        raise HTTPException(status_code=400, detail="GitHub not connected")
        
    meta = json.loads(conn.metadata_json)
    token = meta.get("token")
    
    # Real GitHub API Call
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.github.com/user/repos?per_page=100&sort=updated",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github.v3+json"
            }
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail="Failed to fetch repositories from GitHub")
        
        repos = resp.json()
        
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "full_name": r["full_name"],
            "private": r["private"],
            "html_url": r["html_url"],
            "default_branch": r["default_branch"],
            "updated_at": r["updated_at"]
        } for r in repos
    ]

class AttachRepoRequest(BaseModel):
    github_repo_id: str
    full_name: str
    name: str
    owner: str
    private: bool
    default_branch: str
    clone_url: str
    html_url: str
    project_id: str

@router.post("/projects/{project_id}/repositories")
async def attach_repository(
    project_id: str,
    request: AttachRepoRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Attaches a GitHub repository to a Kobits project."""
    membership = await get_user_default_org(db, current_user.id)
    
    # Get project
    project = await db.execute(select(Project).where(Project.id == project_id, Project.organization_id == membership.organization_id))
    project = project.scalars().first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
        
    # Get connection
    conn = await db.execute(select(GitHubConnection).where(GitHubConnection.user_id == current_user.id))
    conn = conn.scalars().first()
    if not conn:
        raise HTTPException(status_code=400, detail="GitHub not connected")
        
    # Check if already attached
    existing = await db.execute(select(Repository).where(Repository.project_id == project_id))
    existing = existing.scalars().first()
    if existing:
        raise HTTPException(status_code=400, detail="Project already has a repository attached")
        
    repo = Repository(
        project_id=project_id,
        organization_id=membership.organization_id,
        github_connection_id=conn.id,
        github_repo_id=str(request.github_repo_id),
        owner=request.owner,
        name=request.name,
        full_name=request.full_name,
        default_branch=request.default_branch,
        selected_branch=request.default_branch,
        clone_url=request.clone_url,
        web_url=request.html_url,
        private=request.private,
        status=RepositoryStatus.READY
    )
    db.add(repo)
    
    # Add activity
    act = Activity(
        organization_id=membership.organization_id,
        project_id=project_id,
        user_id=current_user.id,
        type=ActivityType.PROJECT_UPDATED,
        title="Repository Attached",
        description=f"Attached GitHub repository {request.full_name} to project."
    )
    db.add(act)
    await db.commit()
    
    return {"status": "success", "repository_id": repo.id}

async def sync_repository_background(repository_id: str, org_id: str):
    from backend.core.database import AsyncSessionLocal
    import subprocess
    import os
    
    async with AsyncSessionLocal() as db:
        repo = await db.get(Repository, repository_id)
        if not repo:
            return
            
        repo.status = RepositoryStatus.SYNCING
        await db.commit()
        
        conn = await db.get(GitHubConnection, repo.github_connection_id)
        meta = json.loads(conn.metadata_json)
        token = meta.get("token")
        
        # Clone repository to Sandbox root just for tracking
        try:
            # Reconstruct clone url with token
            auth_clone_url = repo.clone_url.replace("https://", f"https://x-access-token:{token}@")
            target_dir = os.path.join(SandboxManager.SANDBOX_ROOT, f"{repo.project_id}_master")
            
            # Wipe old dir if exists
            if os.path.exists(target_dir):
                import shutil
                shutil.rmtree(target_dir, ignore_errors=True)
                
            os.makedirs(target_dir, exist_ok=True)
            
            # Clone it
            clone_cmd = f"git clone --branch {repo.selected_branch} --single-branch {auth_clone_url} {target_dir}"
            proc = subprocess.Popen(clone_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = proc.communicate(timeout=120)
            
            if proc.returncode != 0:
                repo.status = RepositoryStatus.ERROR
                await db.commit()
                return
                
            # Get latest SHA
            sha_cmd = f"git -C {target_dir} rev-parse HEAD"
            proc = subprocess.Popen(sha_cmd, shell=True, stdout=subprocess.PIPE)
            stdout, _ = proc.communicate()
            head_sha = stdout.decode().strip()
            
            repo.current_commit_sha = head_sha
            repo.last_synced_at = datetime.now(timezone.utc)
            repo.status = RepositoryStatus.READY
            
            # Create snapshot
            snap = RepositorySnapshot(
                repository_id=repo.id,
                branch=repo.selected_branch,
                commit_sha=head_sha,
                status="SYNCED",
                indexed_at=datetime.now(timezone.utc)
            )
            db.add(snap)
            
            # Activity
            act = Activity(
                organization_id=org_id,
                project_id=repo.project_id,
                user_id=conn.user_id,
                type=ActivityType.PROJECT_UPDATED,
                title="Repository Synchronized",
                description=f"Successfully cloned {repo.full_name} and synced to {head_sha[:7]}."
            )
            db.add(act)
            await db.commit()
            
            # Trigger Indexer here if we had the code indexing pipeline exposed 
            # (assuming it runs asynchronously on project_id)
            # await RepositoryIndexer.index_project(db, repo.project_id, target_dir)
            
        except Exception as e:
            repo.status = RepositoryStatus.ERROR
            await db.commit()

@router.post("/projects/{project_id}/repositories/{repository_id}/sync")
async def sync_repository(
    project_id: str,
    repository_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    membership = await get_user_default_org(db, current_user.id)
    repo = await db.get(Repository, repository_id)
    if not repo or repo.project_id != project_id:
        raise HTTPException(status_code=404, detail="Repository not found")
        
    background_tasks.add_task(sync_repository_background, repository_id, membership.organization_id)
    return {"status": "Sync triggered"}

@router.get("/projects/{project_id}/repositories")
async def get_project_repository(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    membership = await get_user_default_org(db, current_user.id)
    stmt = select(Repository).where(Repository.project_id == project_id, Repository.organization_id == membership.organization_id)
    result = await db.execute(stmt)
    repo = result.scalars().first()
    
    if not repo:
        return None
        
    return {
        "id": repo.id,
        "full_name": repo.full_name,
        "selected_branch": repo.selected_branch,
        "status": repo.status.value,
        "current_commit_sha": repo.current_commit_sha,
        "last_synced_at": repo.last_synced_at.isoformat() if repo.last_synced_at else None
    }

from fastapi import Request
import hmac
import hashlib

@router.post("/webhooks/push")
async def github_push_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    # In production, validate signature with webhook secret
    # signature = request.headers.get("X-Hub-Signature-256")
    payload = await request.json()
    
    repository = payload.get("repository", {})
    ref = payload.get("ref", "")
    after_sha = payload.get("after")
    
    repo_full_name = repository.get("full_name")
    if not repo_full_name:
        return {"status": "ignored"}
        
    branch = ref.replace("refs/heads/", "")
    
    # Find matching repository in our db
    stmt = select(Repository).where(Repository.full_name == repo_full_name)
    result = await db.execute(stmt)
    repo = result.scalars().first()
    
    if not repo or repo.selected_branch != branch:
        return {"status": "ignored"}
        
    repo.current_commit_sha = after_sha
    repo.status = RepositoryStatus.SYNCING
    
    act = Activity(
        organization_id=repo.organization_id,
        project_id=repo.project_id,
        type=ActivityType.PROJECT_UPDATED,
        title="GitHub Webhook Received",
        description=f"Detected push to {branch}. New HEAD is {after_sha[:7]}."
    )
    db.add(act)
    await db.commit()
    
    background_tasks.add_task(sync_repository_background, repo.id, repo.organization_id)
    return {"status": "sync_started"}

from backend.models.github import PullRequest, PullRequestStatus
from backend.models.mission import Mission, MissionStatus

@router.post("/webhooks/events")
async def github_events_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    event_type = request.headers.get("X-GitHub-Event", "ping")
    payload = await request.json()
    
    if event_type == "pull_request":
        action = payload.get("action")
        pr_data = payload.get("pull_request", {})
        repo_data = payload.get("repository", {})
        
        pr_id = str(pr_data.get("id"))
        
        stmt = select(PullRequest).where(PullRequest.github_pr_id == pr_id)
        pr = (await db.execute(stmt)).scalars().first()
        if not pr:
            return {"status": "ignored", "reason": "PR not tracked"}
            
        if action == "closed":
            if pr_data.get("merged"):
                pr.status = PullRequestStatus.MERGED
                merge_commit_sha = pr_data.get("merge_commit_sha")
                # Gate 18: Post-merge synchronization
                from backend.services.post_merge_sync import PostMergeSyncService
                await PostMergeSyncService.run(db=db, mission_id=pr.mission_id, merge_commit_sha=merge_commit_sha)
            else:
                pr.status = PullRequestStatus.CLOSED
        elif action in ["opened", "reopened", "synchronize"]:
            pr.head_sha = pr_data.get("head", {}).get("sha", pr.head_sha)
            
            if pr_data.get("mergeable") is False:
                pr.status = PullRequestStatus.CONFLICT
                from backend.services.orchestrator_decision import OrchestratorDecision
                await OrchestratorDecision.handle_merge_conflict(
                    db=db,
                    mission_id=pr.mission_id,
                    project_id=pr.project_id,
                    pr_id=pr.id,
                    base_ref=pr_data.get("base", {}).get("ref"),
                    head_ref=pr_data.get("head", {}).get("ref")
                )
            else:
                pr.status = PullRequestStatus.OPEN
            
        await db.commit()
        return {"status": "processed", "action": action}
        
    elif event_type == "pull_request_review":
        action = payload.get("action")
        pr_data = payload.get("pull_request", {})
        review_data = payload.get("review", {})
        
        pr_id = str(pr_data.get("id"))
        stmt = select(PullRequest).where(PullRequest.github_pr_id == pr_id)
        pr = (await db.execute(stmt)).scalars().first()
        if not pr:
            return {"status": "ignored"}
            
        if action == "submitted":
            state = review_data.get("state", "").lower()
            if state == "approved":
                pr.review_status = "approved"
                pr.status = PullRequestStatus.APPROVED
            elif state == "changes_requested":
                pr.review_status = "changes_requested"
                pr.status = PullRequestStatus.CHANGES_REQUESTED
                
                # Gate 11: Review Feedback to Findings
                from backend.services.orchestrator_decision import OrchestratorDecision
                await OrchestratorDecision.handle_pr_feedback(
                    db=db,
                    mission_id=pr.mission_id,
                    project_id=pr.project_id,
                    title=f"PR Feedback: {review_data.get('body', 'Changes requested')}",
                    description=review_data.get("body", "Changes requested")
                )
                
            await db.commit()
        return {"status": "processed"}
        
    elif event_type == "check_run":
        action = payload.get("action")
        check_run = payload.get("check_run", {})
        head_sha = check_run.get("head_sha")
        
        stmt = select(PullRequest).where(PullRequest.head_sha == head_sha)
        pr = (await db.execute(stmt)).scalars().first()
        if not pr:
            return {"status": "ignored"}
            
        if action == "completed":
            conclusion = check_run.get("conclusion")
            if conclusion == "success":
                pr.checks_status = "success"
                # If everything is green, mark MERGEABLE
                if pr.review_status == "approved":
                    pr.status = PullRequestStatus.MERGEABLE
            else:
                pr.checks_status = "failure"
                pr.status = PullRequestStatus.CHECKS_FAILED
                # Gate 13: CI Failure to Findings
                
        await db.commit()
        return {"status": "processed"}

    return {"status": "ignored"}
