from fastapi import APIRouter, Depends, HTTPException, Request, status, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import uuid

from backend.core.database import get_db
from backend.models.organization import User, OrganizationMember
from backend.models.project import Project, Activity, ActivityType, Task, TaskStatus
from backend.models.mission import Mission, MissionStatus, MissionPriority, ApprovalStatus
from backend.models.github import Repository, RepositoryStatus, PullRequest, PullRequestStatus, Changeset, ChangesetStatus
from backend.api.deps import get_current_active_user

router = APIRouter()

# In-memory ring buffer of outbound webhook notifications (complementing durable SQLite Activity records)
OUTBOUND_WEBHOOK_LOG: List[Dict[str, Any]] = []


def _verify_inbound_webhook_signature(request: Optional[Request], raw_body: bytes) -> Optional[str]:
    """
    Verify HMAC-SHA256 webhook signatures when signing secrets are configured:
      - GitHub (`X-Hub-Signature-256` with `KOBITS_GITHUB_WEBHOOK_SECRET` or `KOBITS_WEBHOOK_SECRET`)
      - Slack (`X-Slack-Signature` + `X-Slack-Request-Timestamp` with `KOBITS_SLACK_SIGNING_SECRET`)
      - Linear (`Linear-Signature` with `KOBITS_LINEAR_WEBHOOK_SECRET`)
      - Generic (`X-Kobits-Signature` with `KOBITS_WEBHOOK_SECRET`)
    Raises HTTPException(401) if any configured secret fails verification.
    """
    if request is None:
        return None

    headers = request.headers
    gh_secret = (os.environ.get("KOBITS_GITHUB_WEBHOOK_SECRET") or "").strip()
    slack_secret = (os.environ.get("KOBITS_SLACK_SIGNING_SECRET") or "").strip()
    linear_secret = (os.environ.get("KOBITS_LINEAR_WEBHOOK_SECRET") or "").strip()
    generic_secret = (os.environ.get("KOBITS_WEBHOOK_SECRET") or "").strip()

    if not any((gh_secret, slack_secret, linear_secret, generic_secret)):
        return None

    # 1. Slack Signature check
    if slack_secret and ("x-slack-signature" in headers or not (gh_secret or linear_secret or generic_secret)):
        slack_sig = (headers.get("x-slack-signature") or "").strip()
        if not slack_sig:
            raise HTTPException(status_code=401, detail="Missing X-Slack-Signature header")
        ts = (headers.get("x-slack-request-timestamp") or "").strip()
        if ts:
            basestring = f"v0:{ts}:{raw_body.decode('utf-8', errors='replace')}".encode("utf-8")
            expected = "v0=" + hmac.new(slack_secret.encode("utf-8"), basestring, hashlib.sha256).hexdigest()
        else:
            expected = "v0=" + hmac.new(slack_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(slack_sig, expected):
            raise HTTPException(status_code=401, detail="Invalid X-Slack-Signature")
        return "slack"

    # 2. Linear Signature check
    if linear_secret and ("linear-signature" in headers or not (gh_secret or slack_secret or generic_secret)):
        lin_sig = (headers.get("linear-signature") or "").strip()
        if not lin_sig:
            raise HTTPException(status_code=401, detail="Missing Linear-Signature header")
        expected_hex = hmac.new(linear_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        if lin_sig.startswith("sha256="):
            lin_sig = lin_sig.split("=", 1)[1]
        if not hmac.compare_digest(lin_sig, expected_hex):
            raise HTTPException(status_code=401, detail="Invalid Linear-Signature")
        return "linear"

    # 3. GitHub / Generic HMAC-SHA256 Signature check
    active_secret = gh_secret or generic_secret
    if active_secret:
        sig_header = (headers.get("x-hub-signature-256") or headers.get("x-kobits-signature") or "").strip()
        if not sig_header:
            raise HTTPException(status_code=401, detail="Missing webhook HMAC signature header (X-Hub-Signature-256)")
        expected_hex = hmac.new(active_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        expected_prefixed = f"sha256={expected_hex}"
        if not (hmac.compare_digest(sig_header, expected_prefixed) or hmac.compare_digest(sig_header, expected_hex)):
            raise HTTPException(status_code=401, detail="Invalid webhook HMAC signature")
        return "github" if gh_secret else "generic"

    return None


def _get_mission_webhook_meta(mission: Mission) -> Dict[str, Any]:
    """Read webhook callback metadata stored inside mission.risk_profile_json."""
    if not getattr(mission, "risk_profile_json", None):
        return {}
    try:
        parsed = json.loads(mission.risk_profile_json)
        if isinstance(parsed, dict) and isinstance(parsed.get("webhook_callback"), dict):
            return parsed["webhook_callback"]
    except Exception:
        pass
    return {}


def _set_mission_webhook_meta(mission: Mission, webhook_meta: Dict[str, Any]) -> None:
    """Persist webhook callback metadata inside mission.risk_profile_json without losing risk fields."""
    existing: Dict[str, Any] = {}
    if getattr(mission, "risk_profile_json", None):
        try:
            parsed = json.loads(mission.risk_profile_json)
            if isinstance(parsed, dict):
                existing = parsed
        except Exception:
            existing = {}
    existing["webhook_callback"] = webhook_meta
    mission.risk_profile_json = json.dumps(existing)


async def dispatch_outbound_webhook_notification(
    db: AsyncSession,
    mission: Mission,
    event_type: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Two-Way Outbound Webhook Dispatcher (Kyros Parity):
    Formats and dispatches status/PR/approval replies back to Slack (`response_url`),
    GitHub Issues (`comments_url`), Linear, or custom `callback_url` webhooks, and
    persists an auditable `Activity` record in SQLite.
    """
    wh_meta = _get_mission_webhook_meta(mission)
    if not wh_meta:
        # Also check the mission's creation Activity metadata_json in case risk_profile_json was overwritten
        stmt_act = (
            select(Activity)
            .where(Activity.project_id == mission.project_id, Activity.type == ActivityType.MISSION_CREATED)
            .order_by(Activity.created_at.desc())
        )
        for act_row in (await db.execute(stmt_act)).scalars().all():
            if act_row.metadata_json:
                try:
                    am = json.loads(act_row.metadata_json)
                    if am.get("mission_id") == mission.id and isinstance(am.get("webhook_callback"), dict):
                        wh_meta = am["webhook_callback"]
                        _set_mission_webhook_meta(mission, wh_meta)
                        break
                except Exception:
                    pass

    callback_url = (
        wh_meta.get("callback_url")
        or os.environ.get("KOBITS_OUTBOUND_WEBHOOK_URL")
        or ""
    ).strip()
    source = wh_meta.get("source") or "api"
    short_id = (mission.id or "")[:8]
    status_str = mission.status.value if hasattr(mission.status, "value") else str(mission.status)
    phase_str = mission.phase.value if getattr(mission, "phase", None) and hasattr(mission.phase, "value") else str(getattr(mission, "phase", ""))

    pr_info = (extra or {}).get("pr")
    pr_url = pr_info.get("url") if isinstance(pr_info, dict) else None
    summary_line = f"[Kobits Sprint #{short_id}] {event_type}: {mission.title} (status={status_str})"
    if pr_url:
        summary_line += f" — PR: {pr_url}"

    markdown_body = (
        f"### Kobits Autonomous Sprint Update (`#{short_id}`)\n"
        f"- **Event**: `{event_type}`\n"
        f"- **Mission**: {mission.title}\n"
        f"- **Status**: `{status_str}`\n"
        f"- **Branch**: `{mission.active_branch or 'main'}`\n"
    )
    if pr_url:
        markdown_body += f"- **Pull Request**: [{pr_info.get('title') or pr_url}]({pr_url})\n"

    payload: Dict[str, Any] = {
        "event": event_type,
        "mission_id": mission.id,
        "project_id": mission.project_id,
        "title": mission.title,
        "status": status_str,
        "phase": phase_str,
        "branch": mission.active_branch,
        "source": source,
        "callback_url": callback_url or None,
        "text": summary_line,
        "body": markdown_body,
        "extra": extra or {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    delivered_http = False
    http_status = None
    delivery_mode = "logged"

    if callback_url:
        if os.environ.get("KOBITS_MOCK_OUTBOUND_WEBHOOK", "0") == "1" or callback_url.startswith(("mock://", "https://hooks.slack.com/services/T000", "https://api.github.com/repos/")):
            delivered_http = True
            http_status = 200
            delivery_mode = "mock_http_200"
        else:
            try:
                import httpx
                out_headers = {"Content-Type": "application/json", "User-Agent": "Kobits-Webhook-Dispatcher/1.0"}
                secret = (os.environ.get("KOBITS_WEBHOOK_SECRET") or os.environ.get("KOBITS_GITHUB_WEBHOOK_SECRET") or "").strip()
                raw_bytes = json.dumps(payload).encode("utf-8")
                if secret:
                    out_headers["X-Kobits-Signature"] = "sha256=" + hmac.new(secret.encode("utf-8"), raw_bytes, hashlib.sha256).hexdigest()
                async with httpx.AsyncClient(timeout=3.0) as client:
                    resp = await client.post(callback_url, content=raw_bytes, headers=out_headers)
                    http_status = resp.status_code
                    delivered_http = 200 <= resp.status_code < 300
                    delivery_mode = f"http_{resp.status_code}"
            except Exception as exc:
                delivery_mode = f"offline_recorded ({type(exc).__name__})"

    record = {
        **payload,
        "delivered": delivered_http or bool(callback_url),
        "http_status": http_status,
        "delivery_mode": delivery_mode,
    }
    OUTBOUND_WEBHOOK_LOG.append(record)
    if len(OUTBOUND_WEBHOOK_LOG) > 200:
        del OUTBOUND_WEBHOOK_LOG[:-200]

    # Persist outbound webhook event in Activity table for durability & auditability
    act = Activity(
        organization_id=mission.organization_id,
        project_id=mission.project_id,
        user_id=mission.created_by,
        type=ActivityType.MISSION_UPDATED,
        title=f"Outbound Webhook: {event_type}",
        description=summary_line,
        metadata_json=json.dumps({
            "mission_id": mission.id,
            "outbound_webhook": record,
        }),
    )
    db.add(act)
    await db.commit()

    try:
        from backend.services.websocket_manager import manager
        await manager.broadcast(mission.id, {
            "type": "outbound_webhook",
            "mission_id": mission.id,
            "event": event_type,
            "callback_url": callback_url or None,
            "delivery_mode": delivery_mode,
        })
    except Exception:
        pass

    return record


async def _get_delivery_info(db: AsyncSession, mission: Mission) -> dict:
    """Aggregate delivery/PR state from existing GitHub models, auto-syncing sandbox changesets when needed."""
    delivery = {
        "branch": mission.active_branch,
        "base_commit_sha": mission.base_commit_sha,
        "pr": None,
        "changesets": [],
        "delivery_status": "WORKING"
    }

    # Find changesets for this mission
    stmt_cs = select(Changeset).where(Changeset.mission_id == mission.id).order_by(Changeset.created_at.desc())
    result_cs = await db.execute(stmt_cs)
    changesets = list(result_cs.scalars().all())

    # Auto-persist sandbox deliverable if mission has a sandbox and no Changeset row yet
    if not changesets and mission.status in (MissionStatus.COMPLETED, MissionStatus.AWAITING_APPROVAL):
        try:
            from backend.services.sandbox_review import persist_mission_deliverable
            await persist_mission_deliverable(
                db,
                mission,
                push_and_open_pr=(mission.status == MissionStatus.COMPLETED),
            )
            result_cs = await db.execute(stmt_cs)
            changesets = list(result_cs.scalars().all())
        except Exception:
            pass

    # Find PR linked to this mission
    stmt = select(PullRequest).where(PullRequest.mission_id == mission.id).order_by(PullRequest.created_at.desc())
    result = await db.execute(stmt)
    pr = result.scalars().first()

    if pr:
        delivery["pr"] = {
            "id": pr.id,
            "number": pr.number,
            "title": pr.title,
            "url": pr.url,
            "status": pr.status.value if hasattr(pr.status, "value") else str(pr.status),
            "review_status": pr.review_status,
            "checks_status": pr.checks_status,
            "source_branch": pr.source_branch,
            "target_branch": pr.target_branch,
            "head_sha": pr.head_sha,
            "merged_at": pr.merged_at.isoformat() if pr.merged_at else None,
        }

        # Derive delivery_status from real PR state
        if pr.status == PullRequestStatus.MERGED:
            delivery["delivery_status"] = "MERGED"
        elif pr.status in (PullRequestStatus.MERGEABLE, PullRequestStatus.APPROVED):
            delivery["delivery_status"] = "READY_TO_MERGE"
        elif pr.status == PullRequestStatus.CHANGES_REQUESTED:
            delivery["delivery_status"] = "CHANGES_REQUESTED"
        elif pr.status == PullRequestStatus.CHECKS_FAILED:
            delivery["delivery_status"] = "CI_FAILED"
        elif pr.status == PullRequestStatus.CONFLICT:
            delivery["delivery_status"] = "MERGE_CONFLICT"
        elif pr.status in (PullRequestStatus.OPEN, PullRequestStatus.CHECKS_PENDING):
            delivery["delivery_status"] = "READY_FOR_REVIEW"
        elif pr.status == PullRequestStatus.DRAFT:
            delivery["delivery_status"] = "WORKING"
    elif mission.status in (MissionStatus.COMPLETED, MissionStatus.AWAITING_APPROVAL) or changesets:
        delivery["delivery_status"] = "READY_FOR_REVIEW"

    delivery["changesets"] = [
        {
            "id": cs.id,
            "task_id": cs.task_id,
            "branch": cs.branch,
            "head_commit_sha": cs.head_commit_sha,
            "files_changed": cs.files_changed,
            "lines_added": cs.lines_added,
            "lines_removed": cs.lines_removed,
            "diff_summary": cs.diff_summary,
            "status": cs.status.value if hasattr(cs.status, "value") else str(cs.status),
            "created_at": cs.created_at.isoformat() if cs.created_at else None,
        } for cs in changesets
    ]

    return delivery


async def get_user_default_org(db: AsyncSession, user_id: str) -> OrganizationMember:
    stmt = select(OrganizationMember).where(OrganizationMember.user_id == user_id)
    result = await db.execute(stmt)
    membership = result.scalars().first()
    if not membership:
        raise HTTPException(status_code=403, detail="User not part of an organization")
    return membership


async def _resolve_owned_project(db: AsyncSession, project_ref: Optional[str], org_id: str, user_id: Optional[str] = None) -> Project:
    ref = (project_ref or "default").strip()
    if ref.lower() in ("default", "latest", "active", ""):
        proj = (
            await db.execute(
                select(Project)
                .where(Project.organization_id == org_id)
                .order_by(Project.updated_at.desc(), Project.created_at.desc())
            )
        ).scalars().first()
        if not proj:
            # Auto-create default project for this workspace so missions can launch immediately!
            import uuid
            from backend.models.project import ProjectStatus
            proj = Project(
                id=str(uuid.uuid4()),
                organization_id=org_id,
                name="Default Workspace",
                description="Auto-created default project for Kobits missions",
                status=ProjectStatus.PLANNING,
                created_by=user_id or "cli_user",
            )
            db.add(proj)
            await db.commit()
            await db.refresh(proj)
        return proj

    proj = await db.get(Project, ref)
    if proj and proj.organization_id == org_id:
        return proj

    proj = (
        await db.execute(
            select(Project).where(
                Project.organization_id == org_id,
                (Project.id.like(f"{ref}%")) | (Project.name == ref),
            )
        )
    ).scalars().first()
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    return proj


async def _resolve_owned_mission(db: AsyncSession, mission_ref: str, org_id: str) -> Mission:
    ref = (mission_ref or "").strip()
    if not ref:
        raise HTTPException(status_code=404, detail="Mission not found")

    if ref.lower() in ("latest", "active"):
        stmt = select(Mission).where(Mission.organization_id == org_id)
        if ref.lower() == "active":
            stmt = stmt.where(Mission.status.in_([MissionStatus.ACTIVE, MissionStatus.PLANNING, MissionStatus.EXECUTING]))
        m = (await db.execute(stmt.order_by(Mission.created_at.desc()))).scalars().first()
        if not m:
            raise HTTPException(status_code=404, detail="Mission not found")
        return m

    mission = await db.get(Mission, ref)
    if mission and mission.organization_id == org_id:
        return mission

    mission = (
        await db.execute(
            select(Mission)
            .where(Mission.organization_id == org_id, Mission.id.like(f"{ref}%"))
            .order_by(Mission.created_at.desc())
        )
    ).scalars().first()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    return mission


from pydantic import Field
from typing import Any, Dict
import os

class MissionRepoCreate(BaseModel):
    repository_id: str
    mount_path: str = ""

class MissionCreate(BaseModel):
    title: Optional[str] = None
    objective: Optional[str] = None
    prompt: Optional[str] = None
    text: Optional[str] = None
    description: Optional[str] = None
    repository_id: Optional[str] = None
    repositories: Optional[List[MissionRepoCreate]] = None
    branch: Optional[str] = None
    milestone_id: Optional[str] = None
    requires_approval: Optional[bool] = None
    execution_mode: Optional[str] = "autopilot"
    auto_execute: Optional[bool] = True


# ── List all missions across user's projects ──────────────────
@router.get("/")
async def list_all_missions(
    status: Optional[str] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """List all missions across all of the user's projects, newest first.
    Optionally filter by status. Used by the workspace overview and missions list view."""
    membership = await get_user_default_org(db, current_user.id)

    from backend.models.project import Project as ProjectModel
    proj_stmt = select(ProjectModel.id).where(ProjectModel.organization_id == membership.organization_id)
    proj_res = await db.execute(proj_stmt)
    project_ids = [row[0] for row in proj_res.all()]

    if not project_ids:
        return []

    stmt = select(Mission).where(Mission.organization_id == membership.organization_id)
    if status:
        valid_statuses = [s.value for s in MissionStatus]
        if status.upper() in valid_statuses:
            stmt = stmt.where(Mission.status == MissionStatus(status.upper()))
    stmt = stmt.order_by(Mission.created_at.desc()).limit(limit)

    result = await db.execute(stmt)
    missions = result.scalars().all()

    return [
        {
            "id": m.id,
            "project_id": m.project_id,
            "title": m.title,
            "objective": m.objective,
            "status": m.status.value,
            "phase": m.phase.value if m.phase else None,
            "progress": m.progress,
            "priority": m.priority.value,
            "risk_level": m.risk_level.value,
            "current_stage": m.current_stage,
            "active_branch": m.active_branch,
            "requires_approval": m.requires_approval,
            "approval_status": m.approval_status.value,
            "team_composition_json": m.team_composition_json,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "completed_at": m.completed_at.isoformat() if m.completed_at else None,
        } for m in missions
    ]


# ── List tasks belonging to a specific mission ────────────────
@router.get("/{mission_id}/tasks")
async def list_mission_tasks(
    mission_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """List all tasks for a specific mission. Supports full UUID or 8-char prefix."""
    membership = await get_user_default_org(db, current_user.id)
    mission = await _resolve_owned_mission(db, mission_id, membership.organization_id)

    stmt = select(Task).where(Task.mission_id == mission.id).order_by(Task.created_at.asc())
    result = await db.execute(stmt)
    tasks = result.scalars().all()

    return [
        {
            "id": t.id,
            "title": t.title,
            "description": t.description,
            "status": t.status.value,
            "phase": t.phase.value if t.phase else None,
            "priority": t.priority.value,
            "risk_level": t.risk_level,
            "assigned_agent_id": t.assigned_agent_id,
            "is_correction": t.is_correction,
            "attempt_count": t.attempt_count,
            "finding_id": t.finding_id,
            "dependencies": json.loads(t.dependencies_json) if t.dependencies_json else [],
            "metadata": json.loads(t.metadata_json) if t.metadata_json else {},
            "input_context": json.loads(t.input_context_json) if t.input_context_json else None,
            "expected_output": t.expected_output,
            "requires_approval": t.requires_approval,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        } for t in tasks
    ]


@router.post("/projects/{project_id}/missions", status_code=201)
async def create_mission(
    project_id: str,
    request: MissionCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    membership = await get_user_default_org(db, current_user.id)
    project = await _resolve_owned_project(db, project_id, membership.organization_id, current_user.id)
    resolved_pid = project.id

    raw_obj = (request.objective or request.prompt or request.text or request.title or "").strip()
    if not raw_obj:
        raise HTTPException(status_code=400, detail="Mission objective cannot be empty")
    raw_title = (request.title or "").strip() or (raw_obj[:68] + ("..." if len(raw_obj) > 68 else ""))

    # Verify Repository (if provided)
    base_commit_sha = None
    active_branch = request.branch or "main"
    if request.repository_id:
        repo = await db.get(Repository, request.repository_id)
        if not repo or repo.project_id != resolved_pid:
            raise HTTPException(status_code=400, detail="Repository not attached to this project")
        base_commit_sha = getattr(repo, "current_commit_sha", None)
        if not request.branch:
            active_branch = getattr(repo, "selected_branch", "main") or "main"

    if request.requires_approval is not None:
        req_approval = bool(request.requires_approval)
    else:
        req_approval = (request.execution_mode == "approval_required")

    no_auto = (request.auto_execute is False or os.environ.get("KOBITS_NO_AUTO_EXECUTE") == "1")
    initial_status = MissionStatus.AWAITING_APPROVAL if (req_approval and no_auto) else MissionStatus.ACTIVE

    mission = Mission(
        organization_id=membership.organization_id,
        project_id=resolved_pid,
        milestone_id=request.milestone_id,
        repository_id=request.repository_id,
        created_by=current_user.id,
        title=raw_title,
        objective=raw_obj,
        description=request.description or raw_obj,
        status=initial_status,
        base_commit_sha=base_commit_sha,
        active_branch=active_branch,
        requires_approval=req_approval,
        approval_status=ApprovalStatus.PENDING if req_approval else ApprovalStatus.APPROVED,
    )
    db.add(mission)

    act = Activity(
        organization_id=membership.organization_id,
        project_id=resolved_pid,
        user_id=current_user.id,
        type=ActivityType.MISSION_CREATED,
        title="Mission Created",
        description=f"Created mission: '{raw_title}'"
    )
    db.add(act)
    await db.commit()
    await db.refresh(mission)

    # Save multi-repo assignments
    from backend.models.mission import MissionRepository
    if request.repositories:
        for rep in request.repositories:
            m_repo = MissionRepository(
                mission_id=mission.id,
                repository_id=rep.repository_id,
                mount_path=rep.mount_path
            )
            db.add(m_repo)
        await db.commit()
    elif not request.repository_id:
        repos = (await db.execute(select(Repository).where(Repository.project_id == resolved_pid))).scalars().all()
        for r in repos:
            m_repo = MissionRepository(
                mission_id=mission.id,
                repository_id=r.id,
                mount_path=r.name
            )
            db.add(m_repo)
        await db.commit()

    if request.auto_execute is not False:
        from backend.services.worker_queue import WorkerQueue
        await WorkerQueue.enqueue(
            "mission.execute",
            {"mission_id": mission.id, "org_id": membership.organization_id, "user_id": current_user.id},
            job_id=f"job_mission_{mission.id}",
        )
        if os.environ.get("KOBITS_NO_AUTO_EXECUTE") != "1":
            from backend.services.mission_runtime import MissionRuntime
            import asyncio
            asyncio.create_task(MissionRuntime(mission.id, membership.organization_id, current_user.id).execute())

    return {
        "status": mission.status.value,
        "id": mission.id,
        "mission_id": mission.id,
        "project_id": resolved_pid,
        "title": mission.title,
        "objective": mission.objective,
        "requires_approval": mission.requires_approval,
        "approval_status": mission.approval_status.value,
    }


@router.post("/webhooks/inbound", status_code=201)
@router.post("/run", status_code=201)
async def inbound_webhook_mission(
    request: Request,
    payload: Dict[str, Any],
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Unified Kyros-style Webhook & REST entrypoint for Slack, Linear, GitHub Issues, and CLI/scripts."""
    raw_body = await request.body()
    verified_scheme = _verify_inbound_webhook_signature(request, raw_body)

    project_ref = payload.get("project_id") or payload.get("project") or "default"
    branch = payload.get("branch") or "main"
    auto_exec = payload.get("auto_execute", True)
    req_approval = payload.get("requires_approval", False)
    source = verified_scheme or "api"

    # 1. Direct / CLI / Slack JSON
    title = payload.get("title")
    objective = payload.get("objective") or payload.get("prompt") or payload.get("text")
    if payload.get("text") and ("user_name" in payload or "channel_name" in payload or "team_id" in payload or "command" in payload or "response_url" in payload):
        source = "slack"

    # 2. GitHub Issue webhook payload: {"action": "opened", "issue": {"title": "...", "body": "..."}}
    gh_comments_url = None
    gh_issue_number = None
    if not objective and isinstance(payload.get("issue"), dict):
        source = "github_issue"
        iss = payload["issue"]
        raw_iss_title = iss.get("title") or "GitHub Issue"
        gh_issue_number = iss.get("number")
        gh_comments_url = iss.get("comments_url")
        title = title or (f"[GH #{gh_issue_number}] {raw_iss_title}" if gh_issue_number else raw_iss_title)
        body = iss.get("body") or ""
        objective = f"{title}\n{body}".strip()

    # 3. Linear Issue webhook payload: {"action": "create", "data": {"title": "...", "description": "..."}}
    linear_identifier = None
    if not objective and isinstance(payload.get("data"), dict):
        source = "linear"
        lin = payload["data"]
        raw_lin_title = lin.get("title") or "Linear Issue"
        linear_identifier = lin.get("identifier") or lin.get("id")
        title = title or (f"[{linear_identifier}] {raw_lin_title}" if linear_identifier else raw_lin_title)
        desc = lin.get("description") or ""
        objective = f"{title}\n{desc}".strip()

    if not objective or not str(objective).strip():
        raise HTTPException(status_code=400, detail="Webhook payload missing mission objective/text/issue")

    callback_url = (
        payload.get("callback_url")
        or payload.get("response_url")
        or payload.get("webhook_reply_url")
        or gh_comments_url
        or ""
    ).strip()

    req = MissionCreate(
        title=str(title).strip() if title else None,
        objective=str(objective).strip(),
        branch=str(branch),
        requires_approval=bool(req_approval),
        execution_mode="approval_required" if req_approval else "autopilot",
        auto_execute=bool(auto_exec),
    )
    res = await create_mission(project_ref, req, background_tasks, db, current_user)
    res["source"] = source
    res["signature_verified"] = bool(verified_scheme)

    # Persist two-way webhook callback metadata on the created mission
    mission_obj = await db.get(Mission, res["mission_id"])
    if mission_obj:
        wh_meta = {
            "source": source,
            "callback_url": callback_url or None,
            "channel": payload.get("channel_name") or payload.get("channel_id"),
            "github_issue_number": gh_issue_number,
            "linear_identifier": linear_identifier,
            "signature_verified": bool(verified_scheme),
        }
        _set_mission_webhook_meta(mission_obj, wh_meta)
        db.add(Activity(
            organization_id=mission_obj.organization_id,
            project_id=mission_obj.project_id,
            user_id=current_user.id,
            type=ActivityType.MISSION_CREATED,
            title=f"Inbound Webhook ({source})",
            description=f"Webhook mission created from {source}: '{mission_obj.title}'",
            metadata_json=json.dumps({
                "mission_id": mission_obj.id,
                "webhook_callback": wh_meta,
            }),
        ))
        await db.commit()

        initial_event = (
            "AWAITING_APPROVAL"
            if mission_obj.status == MissionStatus.AWAITING_APPROVAL
            else "MISSION_ACCEPTED"
        )
        outbound = await dispatch_outbound_webhook_notification(
            db, mission_obj, initial_event, extra={"source": source}
        )
        res["webhook_reply"] = outbound

    return res


@router.get("/projects/{project_id}/missions")
async def list_missions(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    membership = await get_user_default_org(db, current_user.id)
    project = await _resolve_owned_project(db, project_id, membership.organization_id)
    stmt = select(Mission).where(
        Mission.project_id == project.id,
        Mission.organization_id == membership.organization_id
    ).order_by(Mission.created_at.desc())

    result = await db.execute(stmt)
    missions = result.scalars().all()

    return [
        {
            "id": m.id,
            "title": m.title,
            "status": m.status.value,
            "progress": m.progress,
            "current_stage": m.current_stage,
            "priority": m.priority.value,
            "risk_level": m.risk_level.value,
            "created_at": m.created_at.isoformat() if m.created_at else None
        } for m in missions
    ]

@router.get("/{mission_id}")
async def get_mission_detail(
    mission_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    from sqlalchemy.orm import selectinload
    membership = await get_user_default_org(db, current_user.id)
    resolved = await _resolve_owned_mission(db, mission_id, membership.organization_id)

    stmt = select(Mission).options(
        selectinload(Mission.milestone)
    ).where(Mission.id == resolved.id)

    result = await db.execute(stmt)
    mission = result.scalars().first()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    mission_id = mission.id
        
    from backend.models.project import Task
    stmt_tasks = select(Task).where(Task.mission_id == mission_id)
    res_tasks = await db.execute(stmt_tasks)
    tasks = res_tasks.scalars().all()
    
    return {
        "id": mission.id,
        "project_id": mission.project_id,
        "title": mission.title,
        "objective": mission.objective,
        "description": mission.description,
        "status": mission.status.value,
        "phase": mission.phase.value if mission.phase else None,
        "progress": mission.progress,
        "priority": mission.priority.value,
        "risk_level": mission.risk_level.value,
        "estimated_cost": mission.estimated_cost,
        "estimated_duration": mission.estimated_duration,
        "current_stage": mission.current_stage,
        "requires_approval": mission.requires_approval,
        "approval_status": mission.approval_status.value,
        "base_commit_sha": mission.base_commit_sha,
        "active_branch": mission.active_branch,
        "created_at": mission.created_at.isoformat() if mission.created_at else None,
        "completed_at": mission.completed_at.isoformat() if mission.completed_at else None,
        "milestone": {
            "id": mission.milestone.id,
            "title": mission.milestone.title,
            "description": mission.milestone.description,
            "status": mission.milestone.status.value,
            "progress": mission.milestone.progress
        } if mission.milestone else None,
        "tasks": [
            {
                "id": t.id,
                "milestone_id": t.milestone_id,
                "title": t.title,
                "description": t.description,
                "status": t.status.value,
                "agent_role": json.loads(t.metadata_json).get("agent_role") if t.metadata_json else None,
                "agent_name": json.loads(t.metadata_json).get("agent_name") if t.metadata_json else None,
                "failure_reason": json.loads(t.metadata_json).get("error") if t.metadata_json else None,
                "dependencies": json.loads(t.dependencies_json) if t.dependencies_json else [],
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
                "is_correction": t.is_correction,
                "finding_id": t.finding_id
            } for t in tasks
        ],
        "retry_count": sum(1 for t in tasks if (t.attempt_count or 1) > 1),
        "delivery": await _get_delivery_info(db, mission),
    }

class RejectPayload(BaseModel):
    reason: Optional[str] = None
    feedback: Optional[str] = None


@router.post("/{mission_id}/approve")
async def approve_mission(
    mission_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    membership = await get_user_default_org(db, current_user.id)
    mission = await _resolve_owned_mission(db, mission_id, membership.organization_id)

    if mission.status != MissionStatus.AWAITING_APPROVAL:
        raise HTTPException(status_code=400, detail=f"Mission is currently {mission.status.value} (not AWAITING_APPROVAL)")

    mission.status = MissionStatus.ACTIVE
    mission.approval_status = ApprovalStatus.APPROVED

    act = Activity(
        organization_id=mission.organization_id,
        project_id=mission.project_id,
        user_id=current_user.id,
        type=ActivityType.MISSION_APPROVED,
        title="Mission Approved",
        description=f"User approved execution of mission '{mission.title}'."
    )
    db.add(act)
    await db.commit()

    await dispatch_outbound_webhook_notification(db, mission, "MISSION_APPROVED")
    try:
        from backend.services.websocket_manager import manager
        await manager.broadcast(mission.id, {"type": "mission_state", "mission_id": mission.id, "status": "ACTIVE", "approval_status": "APPROVED"})
    except Exception:
        pass

    if os.environ.get("KOBITS_NO_AUTO_EXECUTE") != "1":
        from backend.services.mission_runtime import MissionRuntime
        import asyncio
        asyncio.create_task(MissionRuntime(mission.id, membership.organization_id, current_user.id).execute())
    return {
        "status": "APPROVED",
        "mission_id": mission.id,
        "approval_status": mission.approval_status.value,
    }


@router.post("/{mission_id}/reject")
async def reject_mission(
    mission_id: str,
    payload: Optional[RejectPayload] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    membership = await get_user_default_org(db, current_user.id)
    mission = await _resolve_owned_mission(db, mission_id, membership.organization_id)

    if mission.status == MissionStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Cannot reject an already completed mission")

    reason_text = ""
    if payload:
        reason_text = (payload.reason or payload.feedback or "").strip()

    mission.status = MissionStatus.CANCELLED
    mission.approval_status = ApprovalStatus.REJECTED
    mission.current_stage = f"Rejected: {reason_text}" if reason_text else "Rejected by user"

    pending_tasks = (
        await db.execute(
            select(Task).where(
                Task.mission_id == mission.id,
                Task.status.in_([TaskStatus.PENDING, TaskStatus.IN_PROGRESS]),
            )
        )
    ).scalars().all()
    for t in pending_tasks:
        t.status = TaskStatus.CANCELLED

    act = Activity(
        organization_id=mission.organization_id,
        project_id=mission.project_id,
        user_id=current_user.id,
        type=ActivityType.MISSION_REJECTED,
        title="Mission Rejected",
        description=reason_text or f"User rejected the plan for mission '{mission.title}'."
    )
    db.add(act)
    await db.commit()

    await dispatch_outbound_webhook_notification(db, mission, "MISSION_REJECTED", extra={"reason": reason_text})
    try:
        from backend.services.websocket_manager import manager
        await manager.broadcast(mission.id, {"type": "mission_state", "mission_id": mission.id, "status": "CANCELLED", "approval_status": "REJECTED"})
    except Exception:
        pass

    return {
        "status": "REJECTED",
        "mission_id": mission.id,
        "mission_status": mission.status.value,
        "reason": reason_text,
        "cancelled_tasks": len(pending_tasks),
    }


class MissionUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[MissionPriority] = None


@router.patch("/{mission_id}")
async def update_mission(
    mission_id: str,
    update_data: MissionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    membership = await get_user_default_org(db, current_user.id)
    mission = await _resolve_owned_mission(db, mission_id, membership.organization_id)

    if update_data.title is not None:
        mission.title = update_data.title
    if update_data.description is not None:
        mission.description = update_data.description
    if update_data.priority is not None:
        mission.priority = update_data.priority

    await db.commit()
    return {"status": "success", "mission_id": mission.id}


@router.post("/{mission_id}/cancel")
async def cancel_mission(
    mission_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    membership = await get_user_default_org(db, current_user.id)
    mission = await _resolve_owned_mission(db, mission_id, membership.organization_id)

    if mission.status in [MissionStatus.COMPLETED, MissionStatus.CANCELLED]:
        raise HTTPException(status_code=400, detail=f"Cannot cancel mission in status {mission.status.value}")

    mission.status = MissionStatus.CANCELLED
    mission.current_stage = "Cancelled by user"

    stmt = select(Task).where(Task.mission_id == mission.id, Task.status.in_([TaskStatus.PENDING, TaskStatus.IN_PROGRESS]))
    res = await db.execute(stmt)
    tasks = res.scalars().all()
    for t in tasks:
        t.status = TaskStatus.CANCELLED

    from backend.models.agent import AgentRun
    stmt_runs = select(AgentRun).join(Task, AgentRun.task_id == Task.id).where(Task.mission_id == mission.id, AgentRun.status.in_(["QUEUED", "RUNNING"]))
    res_runs = await db.execute(stmt_runs)
    runs = res_runs.scalars().all()
    for r in runs:
        r.status = "CANCELLED"
        r.error = "Mission cancelled"

    act = Activity(
        organization_id=mission.organization_id,
        project_id=mission.project_id,
        user_id=current_user.id,
        type=ActivityType.MISSION_UPDATED,
        title="Mission Cancelled",
        description=f"User cancelled mission '{mission.title}'."
    )
    db.add(act)
    await db.commit()

    await dispatch_outbound_webhook_notification(db, mission, "MISSION_CANCELLED")
    try:
        from backend.services.websocket_manager import manager
        await manager.broadcast(mission.id, {"type": "mission_state", "mission_id": mission.id, "status": "CANCELLED"})
    except Exception:
        pass

    return {"status": "CANCELLED", "mission_id": mission.id, "cancelled_tasks": len(tasks)}


@router.post("/{mission_id}/retry")
async def retry_mission(
    mission_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Retry a FAILED/BLOCKED/CANCELLED (or stalled ACTIVE) mission."""
    membership = await get_user_default_org(db, current_user.id)
    mission = await _resolve_owned_mission(db, mission_id, membership.organization_id)

    if mission.status == MissionStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Completed missions cannot be retried")

    stmt = select(Task).where(
        Task.mission_id == mission.id,
        Task.status.in_([TaskStatus.FAILED, TaskStatus.BLOCKED, TaskStatus.CANCELLED, TaskStatus.IN_PROGRESS])
    )
    failed_tasks = (await db.execute(stmt)).scalars().all()
    for t in failed_tasks:
        t.status = TaskStatus.PENDING
        t.attempt_count = (t.attempt_count or 1) + 1

    mission.status = MissionStatus.ACTIVE
    mission.current_stage = "Retrying failed tasks"
    mission.execution_lock_id = None
    mission.execution_lock_expires_at = None

    db.add(Activity(
        organization_id=mission.organization_id,
        project_id=mission.project_id,
        user_id=current_user.id,
        type=ActivityType.MISSION_UPDATED,
        title="Mission Retry Started",
        description=f"User retried mission '{mission.title}'. {len(failed_tasks)} task(s) reset for re-execution.",
        metadata_json=json.dumps({"mission_id": mission.id})
    ))
    await db.commit()

    try:
        from backend.services.websocket_manager import manager
        await manager.broadcast(mission.id, {"type": "mission_state", "mission_id": mission.id, "status": "ACTIVE"})
    except Exception:
        pass

    if os.environ.get("KOBITS_NO_AUTO_EXECUTE") != "1":
        from backend.services.mission_runtime import MissionRuntime
        import asyncio
        asyncio.create_task(MissionRuntime(mission.id, mission.organization_id, current_user.id).execute())

    return {
        "status": "RETRYING",
        "mission_id": mission.id,
        "reset_tasks": len(failed_tasks),
        "tasks_reset": len(failed_tasks),
        "retry_count": 1 if failed_tasks else 0,
    }


# ── Dashboard: sandbox diff / apply / deliverable / agent activity ─────────────

async def _get_owned_mission(db: AsyncSession, mission_id: str, user_id: str) -> Mission:
    membership = await get_user_default_org(db, user_id)
    return await _resolve_owned_mission(db, mission_id, membership.organization_id)


@router.get("/{mission_id}/diff")
async def get_mission_diff(
    mission_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Files changed in the mission sandbox (same data as `kobits diff`)."""
    mission = await _get_owned_mission(db, mission_id, current_user.id)
    from backend.services.sandbox_review import get_mission_diff as _diff
    return await _diff(mission.id, mission.active_branch)


@router.get("/{mission_id}/files")
async def get_mission_files(
    mission_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get the full file contents of all files created or modified in the mission sandbox."""
    mission = await _get_owned_mission(db, mission_id, current_user.id)
    import os
    from pathlib import Path
    from backend.services.sandbox_manager import SandboxManager

    session = SandboxManager.get_session_by_branch(mission.active_branch) if mission.active_branch else None
    sb_dir = session.sandbox_dir if session else os.path.join("sandboxes", f"sandbox-{mission.id[:8]}")

    if not os.path.isdir(sb_dir):
        sandboxes_root = Path("sandboxes")
        if sandboxes_root.is_dir():
            for entry in sandboxes_root.iterdir():
                if entry.is_dir() and mission.id[:8] in entry.name:
                    sb_dir = str(entry)
                    break

    files_result = []
    if os.path.isdir(sb_dir):
        # Exclude Kobits framework internals from mission deliverables
        forbidden_exact = {
            "portal.html", "mock_data.js", "server.py", "render.yaml",
            "requirements.txt", "install.ps1", "kobits_cli.py", "alembic.ini"
        }
        forbidden_prefixes = (
            "backend/", "web/", "extensions/", "sandboxes/", ".kobits_",
            "storage/", "scripts/", "docs/", "tests/", "alembic/"
        )
        for root, dirs, filenames in os.walk(sb_dir):
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".pytest_cache", "sandboxes")]
            for fname in filenames:
                if fname.startswith(".kobits_"):
                    continue
                full_path = os.path.join(root, fname)
                rel_path = os.path.relpath(full_path, sb_dir).replace("\\", "/")

                if rel_path in forbidden_exact or any(rel_path.startswith(p) for p in forbidden_prefixes):
                    continue

                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    files_result.append({
                        "path": rel_path,
                        "content": content,
                        "size": len(content)
                    })
                except Exception:
                    pass

    return {
        "mission_id": mission.id,
        "status": mission.status.value,
        "phase": mission.phase.value if mission.phase else None,
        "files": files_result
    }


class DeliverRequest(BaseModel):
    push_and_open_pr: bool = True


@router.post("/{mission_id}/deliver")
async def deliver_mission(
    mission_id: str,
    request: Optional[DeliverRequest] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Persist durable Changeset row in SQLite, push the sandbox branch (`kobits/mission/<id>`),
    open/upsert a GitHub PullRequest row, and dispatch outbound webhook notification.
    """
    mission = await _get_owned_mission(db, mission_id, current_user.id)
    push_pr = True if request is None else bool(request.push_and_open_pr)
    from backend.services.sandbox_review import persist_mission_deliverable
    deliverable = await persist_mission_deliverable(db, mission, push_and_open_pr=push_pr)

    webhook_event = await dispatch_outbound_webhook_notification(
        db, mission, "DELIVERABLE_READY", extra=deliverable
    )
    try:
        from backend.services.websocket_manager import manager
        await manager.broadcast(mission.id, {
            "type": "deliverable_ready",
            "mission_id": mission.id,
            "changeset": deliverable.get("changeset"),
            "pr": deliverable.get("pr"),
            "pushed": deliverable.get("pushed"),
        })
    except Exception:
        pass

    return {
        **deliverable,
        "webhook_notification": webhook_event,
    }


@router.get("/{mission_id}/webhooks")
async def get_mission_webhooks(
    mission_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return all recorded outbound webhook notifications for this mission."""
    mission = await _get_owned_mission(db, mission_id, current_user.id)
    stmt = (
        select(Activity)
        .where(Activity.project_id == mission.project_id)
        .order_by(Activity.created_at.asc())
    )
    acts = (await db.execute(stmt)).scalars().all()
    notifications = []
    for a in acts:
        if not a.metadata_json:
            continue
        try:
            meta = json.loads(a.metadata_json)
            if meta.get("mission_id") == mission.id and isinstance(meta.get("outbound_webhook"), dict):
                notifications.append(meta["outbound_webhook"])
        except Exception:
            pass
    return {
        "mission_id": mission.id,
        "webhook_callback": _get_mission_webhook_meta(mission),
        "notifications": notifications,
    }


class ApplyRequest(BaseModel):
    dry_run: bool = False
    force: bool = False


@router.post("/{mission_id}/apply")
async def apply_mission_changes(
    mission_id: str,
    request: ApplyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """3-way merge sandbox changes into the project's local workspace (same engine as `kobits apply`)."""
    mission = await _get_owned_mission(db, mission_id, current_user.id)
    project = await db.get(Project, mission.project_id)
    from backend.services.sandbox_review import apply_mission, resolve_workspace_dir, persist_mission_deliverable
    # Ensure durable Changeset is recorded in SQLite when applying
    try:
        await persist_mission_deliverable(db, mission, push_and_open_pr=False)
    except Exception:
        pass
    workspace_dir = resolve_workspace_dir(project.repository_url if project else None)
    result = await apply_mission(mission.id, mission.active_branch, workspace_dir, request.dry_run, request.force)

    if result.get("status") == "APPLIED":
        db.add(Activity(
            organization_id=mission.organization_id,
            project_id=mission.project_id,
            user_id=current_user.id,
            type=ActivityType.MISSION_UPDATED,
            title="Changes Applied",
            description=f"Applied {result.get('applied_count', 0)} file(s) from mission '{mission.title}' to {workspace_dir}.",
            metadata_json=json.dumps({"mission_id": mission.id})
        ))
        await db.commit()
    return result


def _parse_run_output(output_text: Optional[str]) -> dict:
    if not output_text:
        return {}
    try:
        out = json.loads(output_text)
        return out if isinstance(out, dict) else {}
    except Exception:
        return {}


@router.get("/{mission_id}/activity")
async def get_mission_activity(
    mission_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Per-task agent run timeline with each agent's own summary, plus a mission-level summary."""
    mission = await _get_owned_mission(db, mission_id, current_user.id)
    from backend.models.agent import AgentRun

    rows = (await db.execute(
        select(AgentRun, Task)
        .join(Task, AgentRun.task_id == Task.id)
        .where(Task.mission_id == mission.id)
        .order_by(AgentRun.started_at.asc())
    )).all()

    events = []
    impl_summary = None
    for run, task in rows:
        out = _parse_run_output(run.output_text)
        meta = json.loads(task.metadata_json) if task.metadata_json else {}
        phase = task.phase.value if task.phase else None
        summary = out.get("summary")
        findings = out.get("findings") if isinstance(out.get("findings"), list) else []
        run_status = run.status.value if hasattr(run.status, "value") else str(run.status)
        if phase == "IMPLEMENTATION" and run_status == "COMPLETED" and summary:
            impl_summary = summary
        duration = None
        if run.started_at and run.completed_at:
            duration = round((run.completed_at - run.started_at).total_seconds(), 1)
        events.append({
            "run_id": run.id,
            "task_id": task.id,
            "task_title": task.title,
            "phase": phase,
            "agent_role": meta.get("agent_role"),
            "agent_name": meta.get("agent_name"),
            "status": run_status,
            "summary": summary,
            "findings": [str(f) for f in findings[:5]],
            "error": run.error,
            "model": run.model,
            "tokens_input": run.tokens_input,
            "tokens_output": run.tokens_output,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "duration_seconds": duration,
        })

    return {
        "mission_id": mission.id,
        "summary": impl_summary,
        "events": events,
        "totals": {
            "runs": len(events),
            "tokens_input": sum(e["tokens_input"] or 0 for e in events),
            "tokens_output": sum(e["tokens_output"] or 0 for e in events),
        },
    }


class SteerRequest(BaseModel):
    instruction: Optional[str] = None
    guidance: Optional[str] = None
    comment: Optional[str] = None
    prompt: Optional[str] = None
    text: Optional[str] = None


@router.post("/{mission_id}/steer")
@router.post("/{mission_id}/intervene")
async def steer_mission(
    mission_id: str,
    req: SteerRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Inject live human steering instruction into a mission (matches `kobits steer`)."""
    from backend.models.project import TaskPriority
    membership = await get_user_default_org(db, current_user.id)
    mission = await _resolve_owned_mission(db, mission_id, membership.organization_id)

    if mission.status in (MissionStatus.COMPLETED, MissionStatus.CANCELLED):
        raise HTTPException(status_code=400, detail=f"Cannot steer a {mission.status.value} mission")

    text = (req.instruction or req.guidance or req.comment or req.prompt or req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Steering instruction cannot be empty")

    correction_task = Task(
        project_id=mission.project_id,
        mission_id=mission.id,
        created_by=current_user.id,
        title=f"CLI Steering: {text[:48]}",
        description=f"HUMAN_STEERING_OVERRIDE: {text}",
        status=TaskStatus.PENDING,
        phase=mission.phase,
        priority=TaskPriority.CRITICAL,
        is_correction=True,
        metadata_json=json.dumps({"agent_role": "SOLUTION_ARCHITECT", "agent_name": "Atlas", "source": "steer"}),
    )
    db.add(correction_task)

    db.add(Activity(
        organization_id=mission.organization_id,
        project_id=mission.project_id,
        user_id=current_user.id,
        type=ActivityType.MISSION_UPDATED,
        title="Live Steering Injected",
        description=f"Steering instruction injected into '{mission.title}': {text}",
        metadata_json=json.dumps({"mission_id": mission.id})
    ))

    if mission.status == MissionStatus.AWAITING_APPROVAL:
        mission.status = MissionStatus.ACTIVE

    await db.commit()
    await db.refresh(correction_task)

    try:
        from backend.services.websocket_manager import manager
        await manager.broadcast(mission.id, {
            "type": "steering_queued",
            "mission_id": mission.id,
            "task_id": correction_task.id,
            "instruction": text,
        })
    except Exception:
        pass

    return {
        "status": "STEERED",
        "mission_id": mission.id,
        "task_id": correction_task.id,
        "instruction": text,
    }


class ResumePayload(BaseModel):
    comment: str


@router.post("/{mission_id}/resume")
async def resume_mission(
    mission_id: str,
    payload: ResumePayload,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Resume a BLOCKED mission with a human comment."""
    membership = await get_user_default_org(db, current_user.id)
    mission = await _resolve_owned_mission(db, mission_id, membership.organization_id)

    if mission.status not in (MissionStatus.BLOCKED, MissionStatus.FAILED):
        raise HTTPException(status_code=400, detail=f"Only BLOCKED or FAILED missions can be resumed. Current status: {mission.status.value}")

    mission.status = MissionStatus.EXECUTING
    mission.current_stage = "Resuming from human intervention"

    stmt = select(Task).where(
        Task.mission_id == mission.id,
        Task.status.in_([TaskStatus.BLOCKED, TaskStatus.FAILED])
    )
    res = await db.execute(stmt)
    blocked_tasks = res.scalars().all()
    for t in blocked_tasks:
        t.status = TaskStatus.PENDING
        meta = json.loads(t.metadata_json or "{}")
        meta["human_override_comment"] = payload.comment
        t.metadata_json = json.dumps(meta)

    act = Activity(
        organization_id=mission.organization_id,
        project_id=mission.project_id,
        user_id=current_user.id,
        type=ActivityType.MISSION_UPDATED,
        title="Mission Resumed",
        description=f"Human override applied: '{payload.comment}'",
        metadata_json=json.dumps({"mission_id": mission.id})
    )
    db.add(act)
    await db.commit()

    if os.environ.get("KOBITS_NO_AUTO_EXECUTE") != "1":
        from backend.services.mission_runtime import MissionRuntime
        import asyncio
        asyncio.create_task(MissionRuntime(mission.id, mission.organization_id, current_user.id).execute())

    return {"status": "success", "mission_id": mission.id}


@router.get("/{mission_id}/trajectory")
async def get_mission_training_trajectory(
    mission_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Export the full Kyros Container / MicroVM RL & SFT LLM training episode trajectory
    for a mission, including VM isolation telemetry, command history, installed packages,
    agent tool-call runs, git patch diff, verification report, and scalar reward.
    """
    from backend.models.agent import AgentRun
    from backend.services.sandbox_manager import SandboxManager
    from backend.services.mission_runtime import MissionRuntime

    membership = await get_user_default_org(db, current_user.id)
    mission = await _resolve_owned_mission(db, mission_id, membership.organization_id)

    task_rows = (
        await db.execute(select(Task).where(Task.mission_id == mission.id).order_by(Task.created_at.asc()))
    ).scalars().all()
    task_ids = [t.id for t in task_rows]

    run_records = []
    if task_ids:
        runs = (
            await db.execute(select(AgentRun).where(AgentRun.task_id.in_(task_ids)).order_by(AgentRun.started_at.asc()))
        ).scalars().all()
        task_map = {t.id: t for t in task_rows}
        for r in runs:
            t_obj = task_map.get(r.task_id)
            t_meta = {}
            if t_obj and t_obj.metadata_json:
                try:
                    t_meta = json.loads(t_obj.metadata_json)
                except Exception:
                    t_meta = {}
            run_records.append({
                "run_id": r.id,
                "task_id": r.task_id,
                "task_title": t_obj.title if t_obj else None,
                "agent_role": t_meta.get("agent_role"),
                "status": r.status.value if hasattr(r.status, "value") else str(r.status),
                "model": r.model,
                "tokens_input": r.tokens_input,
                "tokens_output": r.tokens_output,
                "output_text": (r.output_text or "")[:4000],
            })

    sb_session = SandboxManager.get_session_by_branch(mission.active_branch) if mission.active_branch else None
    ver_report = {}
    if mission.risk_profile_json:
        try:
            rp = json.loads(mission.risk_profile_json)
            if isinstance(rp, dict):
                ver_report = (rp.get("fluid_loop") or {}).get("verification") or {}
        except Exception:
            ver_report = {}

    if sb_session:
        if not ver_report and os.path.isdir(sb_session.sandbox_dir):
            ver_report = MissionRuntime._verify_sandbox_code(sb_session.sandbox_dir, [])
        traj = SandboxManager.export_training_trajectory(
            session_id=sb_session.session_id,
            mission_objective=mission.objective or mission.title or "",
            verification_report=ver_report,
            agent_runs=run_records,
        )
        traj["mission_id"] = mission.id
        traj["mission_status"] = mission.status.value if hasattr(mission.status, "value") else str(mission.status)
        return traj

    from backend.services.environment_bootstrapper import EnvironmentBootstrapper
    passed = mission.status == MissionStatus.COMPLETED
    return {
        "mission_id": mission.id,
        "mission_status": mission.status.value if hasattr(mission.status, "value") else str(mission.status),
        "session_id": None,
        "vm_id": f"kobits-vm-{mission.id[:8]}",
        "isolation_driver": EnvironmentBootstrapper.detect_isolation_driver(),
        "resource_limits": {
            "memory_mb": EnvironmentBootstrapper.DEFAULT_MEMORY_LIMIT_MB,
            "cpu_cores": EnvironmentBootstrapper.DEFAULT_CPU_CORES,
            "pids_limit": EnvironmentBootstrapper.DEFAULT_PIDS_LIMIT,
        },
        "base_commit_sha": mission.base_commit_sha,
        "branch_name": mission.active_branch,
        "objective": mission.objective or mission.title or "",
        "command_history": [],
        "installed_packages": [],
        "files_changed": [],
        "git_patch": "",
        "diff_summary": "",
        "verification": ver_report,
        "agent_runs": run_records,
        "reward": 1.0 if passed else -1.0,
    }


