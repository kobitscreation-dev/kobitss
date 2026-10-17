from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import json
import uuid
import httpx
import re
import os

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
    from backend.services.auth_service import get_user_default_org as _get_default_org
    return await _get_default_org(db, user_id)

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

@router.post("/disconnect")
async def disconnect_github(
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
    if conn:
        conn.status = GitHubConnectionStatus.DISCONNECTED
        conn.metadata_json = None
        await db.commit()
    return {"status": "success", "message": "GitHub disconnected successfully"}

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

async def _verify_github_project_tenant(
    db: AsyncSession,
    project_id: str,
    membership: OrganizationMember,
    action: str = "github.project_access",
) -> Project:
    proj = await db.get(Project, project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    if proj.organization_id != membership.organization_id:
        from backend.services.soc2_audit_service import AuditEvent, SOC2AuditService
        await SOC2AuditService.log_rbac_event(
            db,
            event=AuditEvent.RBAC_ACCESS_DENIED,
            user_id=membership.user_id,
            organization_id=proj.organization_id,
            actor_role=membership.role.value if hasattr(membership.role, "value") else str(membership.role),
            resource_type="project",
            resource_id=project_id,
            status="DENIED",
            detail={
                "action": action,
                "reason": "cross_tenant_project_access_attempt",
                "severity": "HIGH",
                "target_org_id": proj.organization_id,
                "caller_org_id": membership.organization_id,
            },
        )
        await db.commit()
        raise HTTPException(
            status_code=403,
            detail="Cross-tenant access denied: Project belongs to another organization.",
        )
    return proj


def _sanitize_push_commits(payload: dict) -> List[Dict[str, str]]:
    """Sanitize and wrap external git commit messages in <user_instruction source='git_commit_message'> boundary tags."""
    from app.ai.sanitizer import PromptSanitizer
    raw_commits = list(payload.get("commits") or [])
    head_commit = payload.get("head_commit")
    if isinstance(head_commit, dict) and not raw_commits:
        raw_commits.append(head_commit)
    sanitized_commits: List[Dict[str, str]] = []
    for c in raw_commits[:20]:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id") or "")[:12]
        raw_msg = str(c.get("message") or "")
        wrapped_msg = PromptSanitizer.wrap_user_instruction(
            raw_msg,
            source="git_commit_message",
            strict=False,
        )
        c["message"] = wrapped_msg
        sanitized_commits.append({
            "id": cid,
            "sanitized_message": PromptSanitizer.sanitize(raw_msg, strict=False),
            "wrapped_instruction": wrapped_msg,
        })
    if isinstance(head_commit, dict) and "message" in head_commit:
        head_commit["message"] = PromptSanitizer.wrap_user_instruction(
            str(head_commit.get("message") or ""),
            source="git_commit_message",
            strict=False,
        )
    return sanitized_commits


@router.post("/projects/{project_id}/repositories")
async def attach_repository(
    project_id: str,
    request: AttachRepoRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Attaches a GitHub repository to a Kobits project."""
    membership = await get_user_default_org(db, current_user.id)
    project = await _verify_github_project_tenant(db, project_id, membership, action="github.attach_repository")
        
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
            
            # Clone it using shell=False argument vector (never shell=True)
            clone_args = [
                "git",
                "clone",
                "--branch",
                str(repo.selected_branch),
                "--single-branch",
                auth_clone_url,
                target_dir,
            ]
            proc = subprocess.Popen(
                clone_args,
                shell=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, stderr = proc.communicate(timeout=120)
            
            if proc.returncode != 0:
                repo.status = RepositoryStatus.ERROR
                await db.commit()
                return
                
            # Get latest SHA using shell=False argument vector
            sha_args = ["git", "-C", target_dir, "rev-parse", "HEAD"]
            proc = subprocess.Popen(sha_args, shell=False, stdout=subprocess.PIPE)
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
    proj = await db.get(Project, project_id)
    if proj and proj.organization_id != membership.organization_id:
        await _verify_github_project_tenant(db, project_id, membership, action="github.sync_repository")
    repo = await db.get(Repository, repository_id)
    if repo and repo.organization_id and repo.organization_id != membership.organization_id:
        from backend.services.soc2_audit_service import AuditEvent, SOC2AuditService
        await SOC2AuditService.log_rbac_event(
            db,
            event=AuditEvent.RBAC_ACCESS_DENIED,
            user_id=current_user.id,
            organization_id=repo.organization_id,
            actor_role=membership.role.value if hasattr(membership.role, "value") else str(membership.role),
            resource_type="repository",
            resource_id=repository_id,
            status="DENIED",
            detail={
                "action": "github.sync_repository",
                "reason": "cross_tenant_repository_sync_attempt",
                "severity": "HIGH",
                "target_org_id": repo.organization_id,
                "caller_org_id": membership.organization_id,
            },
        )
        await db.commit()
        raise HTTPException(
            status_code=403,
            detail="Cross-tenant access denied: Repository belongs to another organization.",
        )
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
    proj = await db.get(Project, project_id)
    if proj and proj.organization_id != membership.organization_id:
        await _verify_github_project_tenant(db, project_id, membership, action="github.get_project_repository")
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
    after_sha = payload.get("after") or ""
    sanitized_commits = _sanitize_push_commits(payload)
    
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
        description=f"Detected push to {branch}. New HEAD is {after_sha[:7]}.",
        metadata_json=json.dumps({"sanitized_commits": sanitized_commits}),
    )
    db.add(act)
    await db.commit()
    
    background_tasks.add_task(sync_repository_background, repo.id, repo.organization_id)
    return {"status": "sync_started", "sanitized_commits": sanitized_commits}

from backend.models.github import PullRequest, PullRequestStatus
from backend.models.mission import Mission, MissionStatus

@router.post("/webhooks")
@router.post("/webhooks/events")
async def github_events_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    body_bytes = await request.body()
    sig = request.headers.get("X-Hub-Signature-256", "")
    from backend.services.github_app import GitHubAppService
    if not GitHubAppService.verify_webhook_signature(body_bytes, sig):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    event_type = request.headers.get("X-GitHub-Event", "ping")
    try:
        payload = json.loads(body_bytes) if body_bytes else {}
    except Exception:
        payload = {}

    if event_type == "installation":
        action = payload.get("action")
        inst = payload.get("installation", {})
        inst_id = str(inst.get("id"))
        account = inst.get("account", {})
        gh_login = account.get("login")
        if gh_login:
            stmt = select(GitHubConnection).where(GitHubConnection.github_username == gh_login)
            conn = (await db.execute(stmt)).scalars().first()
            if conn:
                conn.installation_id = inst_id
                conn.status = GitHubConnectionStatus.CONNECTED if action != "deleted" else GitHubConnectionStatus.DISCONNECTED
                await db.commit()
        return {"status": "processed", "installation_id": inst_id, "action": action}

    if event_type == "push":
        repository = payload.get("repository", {})
        ref = payload.get("ref", "")
        after_sha = payload.get("after") or ""
        sanitized_commits = _sanitize_push_commits(payload)
        repo_full_name = repository.get("full_name")
        if not repo_full_name:
            return {"status": "ignored"}
        branch = ref.replace("refs/heads/", "")
        stmt = select(Repository).where(Repository.full_name == repo_full_name)
        result = await db.execute(stmt)
        repo = result.scalars().first()
        if repo and repo.selected_branch == branch:
            repo.current_commit_sha = after_sha
            repo.status = RepositoryStatus.SYNCING
            act = Activity(
                organization_id=repo.organization_id,
                project_id=repo.project_id,
                type=ActivityType.PROJECT_UPDATED,
                title="GitHub Webhook Received",
                description=f"Detected push to {branch}. New HEAD is {after_sha[:7]}.",
                metadata_json=json.dumps({"sanitized_commits": sanitized_commits}),
            )
            db.add(act)
            await db.commit()
            background_tasks.add_task(sync_repository_background, repo.id, repo.organization_id)
            return {"status": "sync_started", "sanitized_commits": sanitized_commits}
        return {"status": "ignored"}

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
                
                # Gate 11: Review Feedback to Findings (sanitized with <user_instruction> boundary tags)
                from app.ai.sanitizer import PromptSanitizer
                from backend.services.orchestrator_decision import OrchestratorDecision
                raw_review_body = review_data.get("body") or "Changes requested"
                safe_review_body = PromptSanitizer.wrap_user_instruction(
                    raw_review_body,
                    source="github_pr_review",
                    strict=False,
                )
                await OrchestratorDecision.handle_pr_feedback(
                    db=db,
                    mission_id=pr.mission_id,
                    project_id=pr.project_id,
                    title=f"PR Feedback: {PromptSanitizer.sanitize(raw_review_body[:200], strict=False)}",
                    description=safe_review_body,
                )
                
            await db.commit()
        return {"status": "processed"}
        
    elif event_type in ("check_run", "workflow_run"):
        action = payload.get("action")
        check_run = payload.get("check_run", {})
        workflow_run = payload.get("workflow_run", {})
        is_workflow = event_type == "workflow_run"

        head_sha = workflow_run.get("head_sha") if is_workflow else check_run.get("head_sha")
        check_name = workflow_run.get("name", "GitHub Actions Workflow") if is_workflow else check_run.get("name", "CI Check")
        conclusion = workflow_run.get("conclusion") if is_workflow else check_run.get("conclusion")
        status = workflow_run.get("status") if is_workflow else check_run.get("status")
        head_branch = (
            workflow_run.get("head_branch")
            if is_workflow
            else (check_run.get("check_suite", {}).get("head_branch") or "")
        )

        # Output logs and annotations
        output_data = check_run.get("output", {})
        output_title = output_data.get("title", "")
        output_summary = output_data.get("summary", "")
        output_text = output_data.get("text", "")
        annotations = output_data.get("annotations", [])

        # Find associated pull request
        pr = None
        if head_sha:
            stmt = select(PullRequest).where(PullRequest.head_sha == head_sha)
            pr = (await db.execute(stmt)).scalars().first()
        if not pr and head_branch:
            stmt = select(PullRequest).where(PullRequest.source_branch == head_branch)
            pr = (await db.execute(stmt)).scalars().first()

        # Find associated mission
        mission = None
        if pr and pr.mission_id:
            mission = await db.get(Mission, pr.mission_id)
        if not mission and head_branch:
            stmt = select(Mission).where(Mission.active_branch == head_branch)
            mission = (await db.execute(stmt)).scalars().first()
        if not mission and head_branch and ("kobits" in head_branch.lower()):
            stmt = select(Mission).where(Mission.active_branch.isnot(None))
            all_missions = (await db.execute(stmt)).scalars().all()
            for m in all_missions:
                if m.active_branch and (m.active_branch in head_branch or head_branch in m.active_branch):
                    mission = m
                    break

        is_kobits_branch = bool(
            (head_branch and ("kobits" in head_branch.lower() or head_branch.startswith("kobits/")))
            or pr
            or mission
        )

        if status == "completed":
            if conclusion == "success":
                if pr:
                    pr.checks_status = "success"
                    if pr.review_status == "approved":
                        pr.status = PullRequestStatus.MERGEABLE
                    await db.commit()
                return {"status": "processed", "conclusion": "success"}

            elif conclusion in ("failure", "timed_out", "action_required"):
                if pr:
                    pr.checks_status = "failure"
                    pr.status = PullRequestStatus.CHECKS_FAILED

                if not is_kobits_branch and not mission:
                    if pr:
                        await db.commit()
                    return {"status": "ignored", "reason": "not a Kobits branch and no tracked mission"}

                # Format failed step logs and annotations
                failure_lines = []
                if output_title:
                    failure_lines.append(f"**Check Title**: {output_title}")
                if output_summary:
                    failure_lines.append(f"**Summary**: {output_summary}")
                if output_text:
                    failure_lines.append(f"**Details**:\n```\n{output_text}\n```")
                if annotations:
                    failure_lines.append("**Failed Steps & Annotations:**")
                    for ann in annotations:
                        p = ann.get("path", "")
                        l = ann.get("start_line") or ann.get("line") or 1
                        msg = ann.get("message", "")
                        lvl = ann.get("annotation_level", "failure").upper()
                        failure_lines.append(f"- `[{lvl}]` `{p}:{l}`: {msg}")
                if is_workflow and workflow_run.get("html_url"):
                    failure_lines.append(f"**Workflow Run**: {workflow_run.get('html_url')}")

                failure_details = "\n\n".join(failure_lines) if failure_lines else (
                    f"GitHub Actions CI '{check_name}' failed on branch `{head_branch or (mission.active_branch if mission else 'unknown')}`."
                )

                if mission:
                    from backend.models.project import Task, TaskStatus, TaskPriority, Activity, ActivityType
                    from backend.models.mission import WorkflowPhase

                    # Re-activate mission so MissionRuntime can acquire the execution lock
                    mission.status = MissionStatus.ACTIVE
                    mission.phase = WorkflowPhase.IMPLEMENTATION

                    target_branch = head_branch or mission.active_branch or "main"
                    task_id = str(uuid.uuid4())
                    remediation_task = Task(
                        id=task_id,
                        project_id=mission.project_id,
                        mission_id=mission.id,
                        title=f"Remediate CI Failure: {check_name}",
                        description=(
                            f"GitHub Actions CI check `{check_name}` failed on branch `{target_branch}`.\n\n"
                            f"{failure_details}\n\n"
                            f"Remediation Directive: Inspect the failing step/annotations, correct the code in the sandbox, verify syntax and tests, and commit the fix to `{target_branch}`."
                        ),
                        status=TaskStatus.PENDING,
                        is_correction=True,
                        priority=TaskPriority.HIGH,
                        phase=WorkflowPhase.IMPLEMENTATION,
                        created_by=mission.created_by or "system_automator",
                        metadata_json=json.dumps({
                            "source": "ci_failure",
                            "agent_role": "BACKEND_ENGINEER",
                            "check_name": check_name,
                            "head_sha": head_sha,
                            "head_branch": target_branch,
                        }),
                    )
                    db.add(remediation_task)

                    activity = Activity(
                        organization_id=mission.organization_id,
                        project_id=mission.project_id,
                        type=ActivityType.TASK_CREATED,
                        title=f"CI Remediation Dispatched: {check_name}",
                        description=f"Auto-generated correction task {task_id[:8]} for CI failure on branch {target_branch}.",
                    )
                    db.add(activity)
                    await db.commit()
                    await db.refresh(remediation_task)

                    # Enqueue remediation via DurableQueue & Background Mission Runtime
                    try:
                        from backend.services.durable_queue import DurableQueue
                        await DurableQueue.enqueue(
                            task_type="EXECUTE_MISSION",
                            payload={
                                "mission_id": mission.id,
                                "remediation": True,
                                "task_id": remediation_task.id,
                                "check_name": check_name,
                            },
                            queue_name="missions",
                            priority=10,
                            job_id=f"ci_remediation_{mission.id}_{uuid.uuid4().hex[:6]}",
                        )
                    except Exception:
                        pass

                    from backend.services.mission_runtime import MissionRuntime
                    background_tasks.add_task(MissionRuntime.run_mission_background, mission.id)

                    # Post PR comment notification if credentials exist
                    repo_data = payload.get("repository", {})
                    repo_full_name = repo_data.get("full_name")
                    if repo_full_name:
                        repo_stmt = select(Repository).where(Repository.full_name == repo_full_name)
                        repo = (await db.execute(repo_stmt)).scalars().first()
                        if repo:
                            conn = await db.get(GitHubConnection, repo.github_connection_id)
                            gh_token = ""
                            if conn and conn.metadata_json:
                                try:
                                    gh_token = json.loads(conn.metadata_json).get("token", "")
                                except Exception:
                                    pass
                            pr_num = pr.github_pr_id if (pr and pr.github_pr_id) else None
                            if not pr_num and check_run.get("pull_requests"):
                                pr_num = check_run["pull_requests"][0].get("number")
                            if gh_token and pr_num:
                                from backend.services.github_service import GitHubService
                                comment_body = (
                                    f"🤖 **Kobits CI Remediation Dispatched** (Task `{remediation_task.id[:8]}` on Mission `{mission.id[:8]}`)\n\n"
                                    f"- **Failed Check**: `{check_name}`\n"
                                    f"- **Branch**: `{target_branch}`\n"
                                    f"- **Logs & Annotations**:\n{failure_details[:400]}...\n\n"
                                    f"Kobits is reproducing the failure in the isolated sandbox, generating the fix, running verification gates, and pushing directly to `{target_branch}`."
                                )
                                background_tasks.add_task(
                                    GitHubService.create_issue_comment,
                                    repo_full_name,
                                    int(pr_num),
                                    comment_body,
                                    gh_token,
                                )

                    return {
                        "status": "ci_remediation_dispatched",
                        "mission_id": mission.id,
                        "task_id": remediation_task.id,
                        "branch": target_branch,
                        "check_name": check_name,
                    }

        if pr:
            await db.commit()
        return {"status": "processed"}

    elif event_type == "pull_request_review_comment":
        action = payload.get("action")
        if action not in ("created", "edited"):
            return {"status": "ignored", "reason": f"action '{action}' not actionable"}

        comment_data = payload.get("comment", {})
        pr_data = payload.get("pull_request", {})
        repo_data = payload.get("repository", {})
        sender_data = payload.get("sender", {})
        sender_login = (sender_data.get("login") or "").lower()

        # Ignore bot senders to prevent infinite feedback loops
        if sender_login.endswith("[bot]") or "kobits" in sender_login:
            return {"status": "ignored", "reason": "ignoring bot sender to prevent loop"}

        comment_body = comment_data.get("body", "")
        file_path = comment_data.get("path")
        line_num = comment_data.get("line") or comment_data.get("original_line")
        diff_hunk = comment_data.get("diff_hunk", "")
        comment_id = comment_data.get("id")
        pr_number = pr_data.get("number")
        repo_full_name = repo_data.get("full_name")
        pr_head_branch = pr_data.get("head", {}).get("ref")

        if not repo_full_name or not pr_head_branch:
            return {"status": "ignored", "reason": "missing repository or PR branch context"}

        stmt = select(Repository).where(Repository.full_name == repo_full_name)
        repo = (await db.execute(stmt)).scalars().first()
        if not repo:
            return {"status": "ignored", "reason": f"repo {repo_full_name} not registered in Kobits"}

        # Check if @kobits is mentioned or if this PR was authored by Kobits
        has_mention = "@kobits" in comment_body.lower() or "@kobits-ai" in comment_body.lower() or "@kobits[bot]" in comment_body.lower()
        is_kobits_pr = pr_head_branch.startswith("kobits/") or "kobits" in pr_head_branch.lower()

        # Check tracked PullRequest table
        pr_stmt = select(PullRequest).where(
            (PullRequest.github_pr_id == str(pr_number)) | (PullRequest.source_branch == pr_head_branch)
        )
        tracked_pr = (await db.execute(pr_stmt)).scalars().first()
        if tracked_pr:
            is_kobits_pr = True

        if not (has_mention or is_kobits_pr):
            return {"status": "ignored", "reason": "neither @kobits mention nor Kobits PR branch"}

        # Get GitHub token
        conn = await db.get(GitHubConnection, repo.github_connection_id)
        gh_token = ""
        if conn and conn.metadata_json:
            try:
                gh_token = json.loads(conn.metadata_json).get("token", "")
            except Exception:
                pass

        # Clean & wrap untrusted PR review comment with <user_instruction> boundary tags
        from app.ai.sanitizer import PromptSanitizer
        cleaned_prompt = re.sub(r"@kobits(-ai)?(\[bot\])?", "", comment_body, flags=re.IGNORECASE).strip()
        if not cleaned_prompt:
            cleaned_prompt = comment_body
        delimited_prompt = PromptSanitizer.wrap_user_instruction(
            cleaned_prompt,
            source="github_pr_review_comment",
            strict=False,
        )

        objective = (
            f"Address PR #{pr_number} review comment from @{sender_login} on `{file_path}`"
            f"{f' (line {line_num})' if line_num else ''}:\n\n"
            f"Instruction: {delimited_prompt}\n\n"
            f"Diff Context:\n```\n{diff_hunk}\n```"
        )

        from backend.models.mission import WorkflowPhase
        from backend.models.project import Task, TaskStatus, TaskPriority, Activity, ActivityType

        # Check for existing mission on this PR / branch
        existing_mission = None
        if tracked_pr and tracked_pr.mission_id:
            existing_mission = await db.get(Mission, tracked_pr.mission_id)
        if not existing_mission:
            stmt = select(Mission).where(Mission.active_branch == pr_head_branch)
            existing_mission = (await db.execute(stmt)).scalars().first()

        if existing_mission:
            # Re-activate mission so MissionRuntime can acquire the execution lock
            existing_mission.status = MissionStatus.ACTIVE
            existing_mission.phase = WorkflowPhase.IMPLEMENTATION

            # Create sub-task on the existing mission
            task_id = str(uuid.uuid4())
            correction_task = Task(
                id=task_id,
                project_id=existing_mission.project_id,
                mission_id=existing_mission.id,
                title=f"[PR #{pr_number} Review] Fix {os.path.basename(file_path or 'file')}:{line_num or 1}",
                description=objective,
                status=TaskStatus.PENDING,
                is_correction=True,
                priority=TaskPriority.HIGH,
                phase=WorkflowPhase.IMPLEMENTATION,
                created_by=existing_mission.created_by or "system_automator",
                metadata_json=json.dumps({
                    "source": "pr_review_comment",
                    "agent_role": "BACKEND_ENGINEER",
                    "pr_number": pr_number,
                    "comment_id": comment_id,
                    "file_path": file_path,
                    "line": line_num,
                }),
            )
            db.add(correction_task)

            act = Activity(
                organization_id=existing_mission.organization_id,
                project_id=existing_mission.project_id,
                type=ActivityType.TASK_CREATED,
                title=f"PR #{pr_number} Review Comment Dispatched",
                description=f"Automated correction task {task_id[:8]} for @{sender_login} on {file_path}:{line_num or 1}.",
            )
            db.add(act)
            await db.commit()
            await db.refresh(correction_task)
            mission = existing_mission
            task_id = correction_task.id
        else:
            # Create a new Mission targeting the existing PR branch
            creator_id = conn.user_id if conn else "system_automator"
            mission = Mission(
                project_id=repo.project_id,
                organization_id=repo.organization_id,
                created_by=creator_id,
                title=f"[PR #{pr_number} Review] Fix {os.path.basename(file_path or 'file')}:{line_num or 1}",
                objective=objective,
                description=f"Automated PR review fix dispatched from Pull Request #{pr_number} line comment by @{sender_login}.",
                status=MissionStatus.ACTIVE,
                phase=WorkflowPhase.IMPLEMENTATION,
                active_branch=pr_head_branch,  # Commits and pushes directly to the existing PR branch
                repository_id=repo.id,
            )
            db.add(mission)
            await db.commit()
            await db.refresh(mission)
            task_id = None

        # Reply directly on the PR line comment thread
        if gh_token and pr_number and comment_id:
            from backend.services.github_service import GitHubService
            reply_text = (
                f"🤖 **Kobits PR Correction Dispatched** (Mission `{mission.id[:8]}`)\n\n"
                f"- **Target File**: `{file_path}` (line {line_num})\n"
                f"- **Branch**: `{pr_head_branch}`\n"
                f"- **Directive**: {cleaned_prompt}\n\n"
                f"Kobits is applying the correction in the sandbox, verifying with tests, and pushing a commit directly to this PR branch."
            )
            background_tasks.add_task(
                GitHubService.reply_to_pr_review_comment,
                repo_full_name,
                int(pr_number),
                int(comment_id),
                reply_text,
                gh_token,
            )

        # Post GitHub commit status check (pending)
        pr_head_sha = pr_data.get("head", {}).get("sha")
        if gh_token and pr_head_sha:
            from backend.services.github_service import GitHubService
            background_tasks.add_task(
                GitHubService.create_commit_status,
                repo_full_name,
                pr_head_sha,
                "pending",
                f"Kobits AI reviewing {os.path.basename(file_path or 'file')}:{line_num or 1}...",
                "Kobits AI / Verification Gate",
                gh_token,
                "https://kobits.space",
            )

        # Enqueue via DurableQueue
        try:
            from backend.services.durable_queue import DurableQueue
            await DurableQueue.enqueue(
                task_type="EXECUTE_MISSION",
                payload={"mission_id": mission.id, "remediation": True, "task_id": task_id},
                queue_name="missions",
                priority=10,
                job_id=f"pr_review_{mission.id}_{uuid.uuid4().hex[:6]}",
            )
        except Exception:
            pass

        # Dispatch background mission execution
        from backend.services.mission_runtime import MissionRuntime
        background_tasks.add_task(MissionRuntime.run_mission_background, mission.id)

        res_payload = {
            "status": "pr_review_correction_dispatched",
            "mission_id": mission.id,
            "branch": pr_head_branch,
            "pr_number": pr_number,
        }
        if task_id:
            res_payload["task_id"] = task_id
        return res_payload

    elif event_type in ("issues", "issue_comment"):
        action = payload.get("action")
        # Only process on issue opened/edited or new comment created
        if action not in ("opened", "created", "reopened", "labeled"):
            return {"status": "ignored", "reason": f"action '{action}' not actionable"}

        issue_data = payload.get("issue", {})
        comment_data = payload.get("comment", {})
        sender_data = payload.get("sender", {})
        sender_login = (sender_data.get("login") or "").lower()

        # Ignore bot triggers or loopbacks
        if sender_login.endswith("[bot]") or "kobits" in sender_login:
            return {"status": "ignored", "reason": "ignoring bot sender to prevent loop"}

        # Extract text content
        raw_text = comment_data.get("body") if event_type == "issue_comment" else (issue_data.get("body") or "")
        issue_title = issue_data.get("title", "")
        labels = [l.get("name", "").lower() for l in issue_data.get("labels", [])]
        is_pr_comment = bool(issue_data.get("pull_request"))
        issue_num = issue_data.get("number", "1")

        repo_data = payload.get("repository", {})
        repo_full_name = repo_data.get("full_name")
        if not repo_full_name:
            return {"status": "ignored", "reason": "missing repository full_name"}

        stmt = select(Repository).where(Repository.full_name == repo_full_name)
        repo = (await db.execute(stmt)).scalars().first()
        if not repo:
            return {"status": "ignored", "reason": f"repo {repo_full_name} not registered in Kobits"}

        # Check if @kobits is mentioned in text, 'kobits' label, or '[kobits]' prefix in title
        text_lower = (raw_text or "").lower()
        has_mention = "@kobits" in text_lower or "@kobits-ai" in text_lower or "@kobits[bot]" in text_lower
        has_label = any(lbl in ("kobits", "kobits-ai", "agent", "automated") for lbl in labels)
        has_title_tag = issue_title.lower().startswith(("[kobits]", "kobits:", "[agent]"))

        # If it's a comment on a PR, check if that PR belongs to Kobits
        is_kobits_pr = False
        tracked_pr = None
        if is_pr_comment:
            pr_stmt = select(PullRequest).where(PullRequest.github_pr_id == str(issue_num))
            tracked_pr = (await db.execute(pr_stmt)).scalars().first()
            if tracked_pr:
                is_kobits_pr = True

        if not (has_mention or has_label or has_title_tag or is_kobits_pr):
            return {"status": "ignored", "reason": "no @kobits mention or tag found"}

        # Get GitHub token
        conn = await db.get(GitHubConnection, repo.github_connection_id)
        gh_token = ""
        if conn and conn.metadata_json:
            try:
                gh_token = json.loads(conn.metadata_json).get("token", "")
            except Exception:
                pass

        # Extract & wrap untrusted issue/comment prompt in <user_instruction> boundary tags
        from app.ai.sanitizer import PromptSanitizer
        cleaned_prompt = re.sub(r"@kobits(-ai)?(\[bot\])?", "", raw_text or "", flags=re.IGNORECASE).strip()
        if not cleaned_prompt or len(cleaned_prompt) < 2:
            cleaned_prompt = issue_title
        delimited_prompt = PromptSanitizer.wrap_user_instruction(
            cleaned_prompt,
            source="github_issue_comment" if event_type == "issue_comment" else "github_issue",
            strict=False,
        )

        from backend.models.mission import WorkflowPhase
        from backend.models.project import Task, TaskStatus, TaskPriority, Activity, ActivityType

        # If comment is on an existing tracked Kobits PR, dispatch remediation sub-task on that mission!
        if is_pr_comment and tracked_pr and tracked_pr.mission_id:
            mission = await db.get(Mission, tracked_pr.mission_id)
            if mission:
                # Re-activate mission so MissionRuntime can acquire the execution lock
                mission.status = MissionStatus.ACTIVE
                mission.phase = WorkflowPhase.IMPLEMENTATION

                task_id = str(uuid.uuid4())
                correction_task = Task(
                    id=task_id,
                    project_id=mission.project_id,
                    mission_id=mission.id,
                    title=f"[PR #{issue_num} Comment] {PromptSanitizer.sanitize(cleaned_prompt[:80], strict=False)}",
                    description=(
                        f"Address feedback from @{sender_login} on PR #{issue_num}:\n\n"
                        f"{delimited_prompt}\n\n"
                        f"Implement changes, run verification gates, and push directly to `{mission.active_branch}`."
                    ),
                    status=TaskStatus.PENDING,
                    is_correction=True,
                    priority=TaskPriority.HIGH,
                    phase=WorkflowPhase.IMPLEMENTATION,
                    created_by=mission.created_by or "system_automator",
                    metadata_json=json.dumps({
                        "source": "pr_conversation_comment",
                        "agent_role": "BACKEND_ENGINEER",
                        "pr_number": issue_num,
                    }),
                )
                db.add(correction_task)

                act = Activity(
                    organization_id=mission.organization_id,
                    project_id=mission.project_id,
                    type=ActivityType.TASK_CREATED,
                    title=f"PR #{issue_num} Comment Feedback Received",
                    description=f"Auto-generated correction task {task_id[:8]} from @{sender_login} feedback.",
                )
                db.add(act)
                await db.commit()
                await db.refresh(correction_task)

                # Enqueue via DurableQueue & Background Runtime
                try:
                    from backend.services.durable_queue import DurableQueue
                    await DurableQueue.enqueue(
                        task_type="EXECUTE_MISSION",
                        payload={"mission_id": mission.id, "remediation": True, "task_id": correction_task.id},
                        queue_name="missions",
                        priority=10,
                        job_id=f"pr_comment_{mission.id}_{uuid.uuid4().hex[:6]}",
                    )
                except Exception:
                    pass

                from backend.services.mission_runtime import MissionRuntime
                background_tasks.add_task(MissionRuntime.run_mission_background, mission.id)

                if gh_token:
                    from backend.services.github_service import GitHubService
                    comment_body = (
                        f"🤖 **Kobits PR Correction Dispatched** (Task `{correction_task.id[:8]}` on Mission `{mission.id[:8]}`)\n\n"
                        f"- **Directive**: {cleaned_prompt}\n"
                        f"- **Branch**: `{mission.active_branch}`\n\n"
                        f"Kobits is executing this feedback in the sandbox and will push a commit directly to this PR."
                    )
                    background_tasks.add_task(
                        GitHubService.create_issue_comment,
                        repo_full_name,
                        int(issue_num),
                        comment_body,
                        gh_token,
                    )

                return {
                    "status": "pr_comment_correction_dispatched",
                    "mission_id": mission.id,
                    "task_id": correction_task.id,
                    "pr_number": issue_num,
                }

        # Otherwise, standard GitHub Issue intake
        active_branch = f"kobits/issue-{issue_num}-{uuid.uuid4().hex[:6]}"
        creator_id = conn.user_id if conn else "system_automator"
        safe_issue_title = PromptSanitizer.sanitize(issue_title[:100], strict=False)
        safe_raw_text = PromptSanitizer.wrap_user_instruction(raw_text or "", source="github_issue_body", strict=False)

        mission = Mission(
            project_id=repo.project_id,
            organization_id=repo.organization_id,
            created_by=creator_id,
            title=f"[Issue #{issue_num}] {safe_issue_title}",
            objective=delimited_prompt,
            description=f"Automated engineering mission dispatched from GitHub Issue #{issue_num} by @{sender_login}.\n\nRaw prompt:\n{safe_raw_text}",
            status=MissionStatus.PLANNING,
            phase=WorkflowPhase.INTAKE,
            active_branch=active_branch,
            repository_id=repo.id,
        )

        db.add(mission)
        await db.commit()
        await db.refresh(mission)

        # Notify via GitHub issue comment
        if gh_token:
            from backend.services.github_service import GitHubService
            comment_body = (
                f"### 🚀 Kobits Mission Dispatched\n\n"
                f"- **Mission ID**: `{mission.id[:8]}`\n"
                f"- **Working Branch**: `{active_branch}`\n"
                f"- **Pipeline**: Cleanroom Ephemeral Git Sandbox → Compiler & Multi-Language Test Gate → Pull Request Delivery\n"
                f"- **Live Console**: [https://kobits.space/#missions/{mission.id}](https://kobits.space/#missions/{mission.id})\n\n"
                f"Kobits autonomous engineering team is now executing this issue."
            )
            background_tasks.add_task(
                GitHubService.create_issue_comment,
                repo_full_name,
                int(issue_num),
                comment_body,
                gh_token,
            )

        # Enqueue via DurableQueue
        try:
            from backend.services.durable_queue import DurableQueue
            await DurableQueue.enqueue(
                task_type="EXECUTE_MISSION",
                payload={"mission_id": mission.id},
                queue_name="missions",
                priority=10,
                job_id=f"issue_{mission.id}_{uuid.uuid4().hex[:6]}",
            )
        except Exception:
            pass

        # Dispatch background mission execution
        from backend.services.mission_runtime import MissionRuntime
        background_tasks.add_task(MissionRuntime.run_mission_background, mission.id)

        return {
            "status": "mission_dispatched",
            "mission_id": mission.id,
            "branch": active_branch,
            "issue": issue_num,
        }

    return {"status": "ignored"}


