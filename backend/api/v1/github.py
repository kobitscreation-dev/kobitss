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
        after_sha = payload.get("after")
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
                description=f"Detected push to {branch}. New HEAD is {after_sha[:7]}."
            )
            db.add(act)
            await db.commit()
            background_tasks.add_task(sync_repository_background, repo.id, repo.organization_id)
            return {"status": "sync_started"}
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
        # Check if @kobits is mentioned in the PR line comment
        if "@kobits" not in comment_body.lower() and "@kobits-ai" not in comment_body.lower():
            return {"status": "ignored", "reason": "no @kobits mention in review comment"}

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

        # Get GitHub token
        conn = await db.get(GitHubConnection, repo.github_connection_id)
        gh_token = ""
        if conn and conn.metadata_json:
            try:
                gh_token = json.loads(conn.metadata_json).get("token", "")
            except Exception:
                pass

        # Clean prompt
        cleaned_prompt = re.sub(r"@kobits(-ai)?(\[bot\])?", "", comment_body, flags=re.IGNORECASE).strip()
        objective = (
            f"Address PR #{pr_number} review comment from @{sender_login} on `{file_path}`"
            f"{f' (line {line_num})' if line_num else ''}:\n\n"
            f"Instruction: {cleaned_prompt}\n\n"
            f"Diff Context:\n```\n{diff_hunk}\n```"
        )

        from backend.models.mission import WorkflowPhase
        creator_id = conn.user_id if conn else "system_automator"
        mission = Mission(
            project_id=repo.project_id,
            organization_id=repo.organization_id,
            created_by=creator_id,
            title=f"[PR #{pr_number} Review] Fix {os.path.basename(file_path or 'file')}:{line_num or 1}",
            objective=objective,
            description=f"Automated PR review fix dispatched from Pull Request #{pr_number} line comment by @{sender_login}.",
            status=MissionStatus.PLANNING,
            phase=WorkflowPhase.INTAKE,
            active_branch=pr_head_branch,  # Commits and pushes directly to the existing PR branch
            repository_id=repo.id,
        )

        db.add(mission)
        await db.commit()
        await db.refresh(mission)

        # Reply directly on the PR line comment thread
        if gh_token and pr_number and comment_id:
            from backend.services.github_service import GitHubService
            reply_text = (
                f"🤖 **Kobits PR Correction Dispatched** (Mission `{mission.id[:8]}`)\n\n"
                f"- **Target File**: `{file_path}` (line {line_num})\n"
                f"- **Branch**: `{pr_head_branch}`\n"
                f"- **Workflow**: Isolated AST Analysis → Compiler & Test Suite Check → Direct Push to PR\n\n"
                f"Kobits is analyzing the directive and will push a commit directly to this PR branch once tests pass."
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
                "https://kobits.space"
            )

        # Dispatch background mission
        from backend.services.mission_runtime import MissionRuntime
        background_tasks.add_task(MissionRuntime.run_mission_background, mission.id)

        return {
            "status": "pr_review_correction_dispatched",
            "mission_id": mission.id,
            "branch": pr_head_branch,
            "pr_number": pr_number,
        }

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

        # Trigger check: '@kobits' in text, 'kobits' label, or '[kobits]' prefix in title
        text_lower = (raw_text or "").lower()
        has_mention = "@kobits" in text_lower or "@kobits-ai" in text_lower or "@kobits[bot]" in text_lower
        has_label = any(lbl in ("kobits", "kobits-ai", "agent", "automated") for lbl in labels)
        has_title_tag = issue_title.lower().startswith(("[kobits]", "kobits:", "[agent]"))

        if not (has_mention or has_label or has_title_tag):
            return {"status": "ignored", "reason": "no @kobits mention or tag found"}

        # Extract prompt
        cleaned_prompt = re.sub(r"@kobits(-ai)?(\[bot\])?", "", raw_text or "", flags=re.IGNORECASE).strip()
        if not cleaned_prompt or len(cleaned_prompt) < 5:
            cleaned_prompt = issue_title

        repo_data = payload.get("repository", {})
        repo_full_name = repo_data.get("full_name")
        if not repo_full_name:
            return {"status": "ignored", "reason": "missing repository full_name"}

        stmt = select(Repository).where(Repository.full_name == repo_full_name)
        repo = (await db.execute(stmt)).scalars().first()
        if not repo:
            return {"status": "ignored", "reason": f"repo {repo_full_name} not registered in Kobits"}

        # Get GitHub token
        conn = await db.get(GitHubConnection, repo.github_connection_id)
        gh_token = ""
        if conn and conn.metadata_json:
            try:
                gh_token = json.loads(conn.metadata_json).get("token", "")
            except Exception:
                pass

        from backend.models.mission import WorkflowPhase
        issue_num = issue_data.get("number", "1")
        active_branch = f"kobits/issue-{issue_num}-{uuid.uuid4().hex[:6]}"
        creator_id = conn.user_id if conn else "system_automator"

        mission = Mission(
            project_id=repo.project_id,
            organization_id=repo.organization_id,
            created_by=creator_id,
            title=f"[Issue #{issue_num}] {issue_title[:100]}",
            objective=cleaned_prompt,
            description=f"Automated engineering mission dispatched from GitHub Issue #{issue_num} by @{sender_login}.\n\nRaw prompt:\n{raw_text}",
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

