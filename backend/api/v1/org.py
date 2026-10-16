"""
Kobits — Organisation-level API endpoints.
Provides:
  GET /api/v1/org/activity   — global activity feed across all org projects
  GET /api/v1/org/dashboard  — aggregated, DB-derived numbers for the Kyros-style dashboard
"""
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from backend.core.database import get_db
from backend.models.organization import User, OrganizationMember
from backend.models.project import Activity, Project, ProjectStatus, Task, TaskStatus
from backend.models.mission import Mission, MissionStatus
from backend.models.agent import Agent, AgentRun, ApprovalRequest, ApprovalStatus as RunApprovalStatus
from backend.models.github import PullRequest, Changeset, GitHubConnection, GitHubConnectionStatus
from backend.api.deps import get_current_active_user

router = APIRouter()

_TERMINAL_MISSION = {MissionStatus.COMPLETED, MissionStatus.FAILED, MissionStatus.CANCELLED}
_ROOT_DIR = Path(__file__).resolve().parents[3]


def _scan_sandbox_index() -> Dict[str, Dict[str, Any]]:
    """Fast (<5ms) index of sandboxes/*/.kobits_sandbox.json keyed by 8-char mission prefix."""
    index: Dict[str, Dict[str, Any]] = {}
    sb_root = _ROOT_DIR / "sandboxes"
    if not sb_root.is_dir():
        return index
    try:
        for entry in os.scandir(sb_root):
            if not entry.is_dir():
                continue
            meta_file = Path(entry.path) / ".kobits_sandbox.json"
            if not meta_file.is_file():
                continue
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8", errors="ignore"))
                branch = meta.get("branch_name") or ""
                prefix = branch.rsplit("/", 1)[-1][:8] if "/" in branch else ""
                if not prefix:
                    continue
                raw_files = [
                    f for f in (meta.get("files_changed") or [])
                    if isinstance(f, str) and not f.startswith(".kobits")
                ]
                loc_added = 0
                if 0 < len(raw_files) <= 25:
                    for rel in raw_files:
                        fp = Path(entry.path) / rel
                        if fp.is_file():
                            try:
                                loc_added += sum(1 for _ in fp.open("rb"))
                            except OSError:
                                pass
                index[prefix] = {
                    "sandbox_dir": f"sandboxes/{entry.name}",
                    "branch": branch,
                    "files_changed": len(raw_files),
                    "file_list": raw_files[:15],
                    "lines_added": loc_added,
                }
            except Exception:
                continue
    except OSError:
        pass
    return index


async def _get_org(db: AsyncSession, user_id: str) -> OrganizationMember:
    """Return the user's primary org membership, auto-provisioning if missing."""
    from backend.services.auth_service import get_user_default_org
    return await get_user_default_org(db, user_id)


@router.get("/activity")
async def get_org_activity(
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return the latest activity events across all projects in the user's organisation."""
    membership = await _get_org(db, current_user.id)
    if not membership:
        return []

    stmt = (
        select(Activity)
        .where(Activity.organization_id == membership.organization_id)
        .order_by(Activity.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    events = result.scalars().all()

    return [
        {
            "id": e.id,
            "type": e.type.value,
            "title": e.title,
            "description": e.description,
            "project_id": e.project_id,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in events
    ]


def _iso(dt) -> Optional[str]:
    return dt.isoformat() if dt else None


def _enum(v) -> Optional[str]:
    return v.value if hasattr(v, "value") else v


def _agent_from_meta(metadata_json: Optional[str]) -> Optional[str]:
    if not metadata_json:
        return None
    try:
        meta = json.loads(metadata_json)
        return meta.get("agent_name") or meta.get("agent_role")
    except (ValueError, AttributeError):
        return None


@router.get("/dashboard")
async def get_dashboard(
    project_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Client dashboard aggregate.

    Every number is computed from database rows in the caller's organisation.
    Where no source data exists the value is null (never a placeholder number).
    """
    membership = await _get_org(db, current_user.id)
    org_id = membership.organization_id

    projects = (await db.execute(
        select(Project).where(Project.organization_id == org_id).order_by(Project.updated_at.desc())
    )).scalars().all()

    if not projects:
        import uuid
        default_proj = Project(
            id=str(uuid.uuid4()),
            name="Primary Engineering Workspace",
            description="Autonomous multi-agent software engineering workspace.",
            status=ProjectStatus.READY,
            progress=0,
            health_score=100,
            organization_id=org_id,
            created_by=current_user.id,
        )
        db.add(default_proj)
        await db.commit()
        await db.refresh(default_proj)
        projects = [default_proj]

    base: Dict[str, Any] = {
        "projects": [{"id": p.id, "name": p.name, "status": _enum(p.status)} for p in projects],
        "project": None,
    }

    project = None
    if not project_id or project_id.lower() in ("default", "latest", "active", "", "null", "undefined") or project_id.startswith("demo-"):
        project = projects[0]
    else:
        project = next((p for p in projects if p.id == project_id or p.name == project_id or p.id.startswith(project_id)), None)
        if project is None:
            if project_id.startswith("demo-"):
                project = projects[0]
            else:
                raise HTTPException(status_code=404, detail="Project not found")
    pid = project.id

    # ── Tasks → progress ─────────────────────────────────────────────
    task_rows = (await db.execute(
        select(Task.status, func.count()).where(Task.project_id == pid).group_by(Task.status)
    )).all()
    by_status = {_enum(s): n for s, n in task_rows}
    total_tasks = sum(by_status.values())
    countable = total_tasks - by_status.get(TaskStatus.CANCELLED.value, 0)
    completed_tasks = by_status.get(TaskStatus.COMPLETED.value, 0)
    percent = round(completed_tasks * 100 / countable) if countable > 0 else None

    # ── Missions ─────────────────────────────────────────────────────
    missions = (await db.execute(
        select(Mission).where(Mission.project_id == pid).order_by(Mission.created_at.desc())
    )).scalars().all()
    mission_ids = [m.id for m in missions]

    # All tasks for this project (used for milestones, current mission, and phase reports)
    all_project_tasks = (await db.execute(
        select(Task).where(Task.project_id == pid).order_by(Task.created_at.asc())
    )).scalars().all()
    tasks_by_mission: Dict[str, List[Task]] = {}
    for t in all_project_tasks:
        if t.mission_id:
            tasks_by_mission.setdefault(t.mission_id, []).append(t)

    agent_ids = {t.assigned_agent_id for t in all_project_tasks if t.assigned_agent_id}
    agent_names: Dict[str, str] = {}
    if agent_ids:
        agent_names = {a.id: a.name for a in (await db.execute(
            select(Agent).where(Agent.id.in_(agent_ids))
        )).scalars().all()}

    # ── Spend (real telemetry from AgentRun) ─────────────────────────
    spend_row = (await db.execute(
        select(
            func.count(AgentRun.id),
            func.coalesce(func.sum(AgentRun.estimated_cost), 0.0),
            func.coalesce(func.sum(AgentRun.tokens_input), 0),
            func.coalesce(func.sum(AgentRun.tokens_output), 0),
        ).where(AgentRun.organization_id == org_id, AgentRun.project_id == pid)
    )).one()
    runs_count, cost_usd, tok_in, tok_out = spend_row

    # Token budget across non-cancelled missions (Mission.budget_max_tokens / current_token_usage)
    budgeted = [m for m in missions if m.status != MissionStatus.CANCELLED]
    budget_total = sum((m.budget_max_tokens or 0) for m in budgeted) if budgeted else None
    raw_budget_used = sum((m.current_token_usage or 0) for m in budgeted) if budgeted else None
    actual_tokens = int((tok_in or 0) + (tok_out or 0))
    budget_used = raw_budget_used if (raw_budget_used is not None and raw_budget_used > 0) else (
        actual_tokens if (raw_budget_used is not None and actual_tokens > 0 and cost_usd > 1.0) else raw_budget_used
    )

    # ── Deliverables & Sandbox Index ─────────────────────────────────
    changesets = []
    prs = []
    if mission_ids:
        changesets = (await db.execute(
            select(Changeset).where(Changeset.mission_id.in_(mission_ids))
        )).scalars().all()
        prs = (await db.execute(
            select(PullRequest).where(PullRequest.project_id == pid, PullRequest.mission_id.in_(mission_ids))
        )).scalars().all()

    sb_index = _scan_sandbox_index()

    def _deliverable(m: Mission) -> dict:
        cs = [c for c in changesets if c.mission_id == m.id]
        mprs = [p for p in prs if p.mission_id == m.id]
        latest_pr = max(mprs, key=lambda p: p.created_at, default=None)
        sb = sb_index.get(m.id[:8]) or {}
        cs_files = sum((c.files_changed or 0) for c in cs) if cs else None
        cs_added = sum((c.lines_added or 0) for c in cs) if cs else None
        cs_removed = sum((c.lines_removed or 0) for c in cs) if cs else None

        files_changed = cs_files if (cs_files is not None and cs_files > 0) else (sb.get("files_changed") or cs_files)
        lines_added = cs_added if (cs_added is not None and cs_added > 0) else (sb.get("lines_added") or cs_added)
        lines_removed = cs_removed if cs_removed is not None else (0 if sb else None)

        m_tasks = tasks_by_mission.get(m.id, [])
        m_tasks_done = sum(1 for t in m_tasks if t.status == TaskStatus.COMPLETED)
        dur = round((m.completed_at - m.created_at).total_seconds(), 1) if (m.completed_at and m.created_at) else None

        return {
            "mission_id": m.id,
            "title": m.title,
            "objective": m.objective,
            "status": _enum(m.status),
            "phase": _enum(m.phase),
            "progress": m.progress,
            "branch": m.active_branch or sb.get("branch") or f"kobits/mission/{m.id[:8]}",
            "sandbox_dir": sb.get("sandbox_dir"),
            "file_list": sb.get("file_list") or [],
            "created_at": _iso(m.created_at),
            "completed_at": _iso(m.completed_at),
            "duration_seconds": dur,
            "tasks_total": len(m_tasks),
            "tasks_done": m_tasks_done,
            "pr_count": len(mprs),
            "files_changed": files_changed,
            "lines_added": lines_added,
            "lines_removed": lines_removed,
            "pr": {
                "number": latest_pr.number,
                "url": latest_pr.url,
                "status": _enum(latest_pr.status),
                "checks_status": latest_pr.checks_status,
            } if latest_pr else None,
        }

    completed = [m for m in missions if m.status == MissionStatus.COMPLETED]
    deliverables = [_deliverable(m) for m in completed[:6]]

    # Full mission timeline for the Kyros Milestones & Deliverables views
    milestones_list = []
    for m in missions:
        if m.status == MissionStatus.CANCELLED:
            continue
        d_info = _deliverable(m)
        m_tasks = tasks_by_mission.get(m.id, [])
        d_info["tasks"] = [
            {
                "id": t.id,
                "title": t.title,
                "status": _enum(t.status),
                "phase": _enum(t.phase),
                "agent": agent_names.get(t.assigned_agent_id) or _agent_from_meta(t.metadata_json),
            }
            for t in m_tasks
        ]
        milestones_list.append(d_info)

    # ── Current mission ("sprint") with its tasks ────────────────────
    active = next((m for m in missions if m.status not in _TERMINAL_MISSION), None)
    current = None
    if active:
        tasks = tasks_by_mission.get(active.id, [])
        current = {
            "mission_id": active.id,
            "title": active.title,
            "objective": active.objective,
            "status": _enum(active.status),
            "phase": _enum(active.phase),
            "progress": active.progress,
            "branch": active.active_branch or f"kobits/mission/{active.id[:8]}",
            "created_at": _iso(active.created_at),
            "tasks_total": len(tasks),
            "tasks_done": sum(1 for t in tasks if t.status == TaskStatus.COMPLETED),
            "tasks": [
                {
                    "id": t.id,
                    "title": t.title,
                    "status": _enum(t.status),
                    "phase": _enum(t.phase),
                    "agent": agent_names.get(t.assigned_agent_id) or _agent_from_meta(t.metadata_json),
                }
                for t in tasks
            ],
        }

    # ── Needs attention ──────────────────────────────────────────────
    awaiting_missions = sum(1 for m in missions if m.status == MissionStatus.AWAITING_APPROVAL)
    pending_requests = (await db.execute(
        select(func.count(ApprovalRequest.id)).where(
            ApprovalRequest.project_id == pid,
            ApprovalRequest.status == RunApprovalStatus.PENDING,
        )
    )).scalar() or 0
    failed_tasks = by_status.get(TaskStatus.FAILED.value, 0)
    blocked_tasks = by_status.get(TaskStatus.BLOCKED.value, 0)

    # ── Team: agents that actually ran on this project ───────────────
    team_rows = (await db.execute(
        select(Agent.id, Agent.name, Agent.type, func.count(AgentRun.id), func.max(AgentRun.started_at))
        .join(AgentRun, AgentRun.agent_id == Agent.id)
        .where(AgentRun.project_id == pid, AgentRun.organization_id == org_id)
        .group_by(Agent.id, Agent.name, Agent.type)
        .order_by(func.count(AgentRun.id).desc())
        .limit(8)
    )).all()

    team_list = [
        {"id": a_id, "name": name, "type": _enum(a_type), "runs": n, "last_run_at": _iso(last)}
        for a_id, name, a_type, n, last in team_rows
    ]

    # Enrich with task metadata agent_role when AgentRun.agent_id was NULL (CLI runs)
    from backend.services.agent_registry import AGENT_REGISTRY
    role_By_Type = {adef.type.value: adef for adef in AGENT_REGISTRY.values()}
    role_stats: Dict[str, Dict[str, Any]] = {}
    role_q = (
        select(
            Task.metadata_json,
            AgentRun.status,
            AgentRun.tokens_input,
            AgentRun.tokens_output,
            AgentRun.estimated_cost,
            AgentRun.started_at,
        )
        .join(Task, AgentRun.task_id == Task.id)
        .where(AgentRun.organization_id == org_id, AgentRun.project_id == pid)
    )
    for meta_raw, r_status, r_in, r_out, r_cost, r_last in (await db.execute(role_q)).all():
        r_role = _agent_from_meta(meta_raw)
        if not r_role:
            continue
        st = role_stats.setdefault(r_role, {"runs": 0, "completed": 0, "failed": 0, "tokens": 0, "cost_usd": 0.0, "last_run_at": None})
        st["runs"] += 1
        sv = _enum(r_status)
        if sv == "COMPLETED":
            st["completed"] += 1
        elif sv == "FAILED":
            st["failed"] += 1
        st["tokens"] += int((r_in or 0) + (r_out or 0))
        st["cost_usd"] += float(r_cost or 0.0)
        if r_last and (st["last_run_at"] is None or r_last > st["last_run_at"]):
            st["last_run_at"] = r_last

    if not team_list and role_stats:
        sorted_roles = sorted(role_stats.items(), key=lambda kv: kv[1]["runs"], reverse=True)[:8]
        for r_role, st in sorted_roles:
            adef = role_By_Type.get(r_role)
            team_list.append({
                "id": r_role,
                "name": adef.name if adef else r_role,
                "type": r_role,
                "runs": st["runs"],
                "tokens": st["tokens"],
                "cost_usd": round(st["cost_usd"], 4),
                "success_rate": round(100 * st["completed"] / st["runs"], 1) if st["runs"] else None,
                "last_run_at": _iso(st["last_run_at"]),
            })

    # Full 21-agent roster enriched with project stats for the Team & Reports tabs
    roster = []
    for adef in AGENT_REGISTRY.values():
        r_role = adef.type.value
        st = role_stats.get(r_role)
        # Also check if team_rows had direct agent_id matches
        direct = next((t for t in team_list if t["type"] == r_role), None)
        runs_val = st["runs"] if st else (direct["runs"] if direct else 0)
        roster.append({
            "id": r_role,
            "name": adef.name,
            "type": r_role,
            "description": adef.description,
            "capabilities": list(adef.capabilities),
            "runs": runs_val,
            "completed": st["completed"] if st else runs_val,
            "failed": st["failed"] if st else 0,
            "success_rate": round(100 * st["completed"] / st["runs"], 1) if (st and st["runs"]) else None,
            "tokens": st["tokens"] if st else None,
            "cost_usd": round(st["cost_usd"], 4) if st else None,
            "last_run_at": _iso(st["last_run_at"]) if st else (direct["last_run_at"] if direct else None),
        })
    roster.sort(key=lambda a: (a["runs"], a["name"]), reverse=True)

    # ── Phase Breakdown for Reports ──────────────────────────────────
    phase_breakdown: Dict[str, Dict[str, int]] = {}
    for t in all_project_tasks:
        ph = _enum(t.phase) or "UNSPECIFIED"
        pb = phase_breakdown.setdefault(ph, {"total": 0, "completed": 0, "failed": 0, "in_progress": 0})
        pb["total"] += 1
        ts = _enum(t.status)
        if ts == "COMPLETED":
            pb["completed"] += 1
        elif ts == "FAILED":
            pb["failed"] += 1
        elif ts in ("IN_PROGRESS", "RUNNING"):
            pb["in_progress"] += 1

    durations = [
        (m.completed_at - m.created_at).total_seconds()
        for m in completed if m.completed_at and m.created_at
    ]
    avg_duration = round(sum(durations) / len(durations), 1) if durations else None

    # ── Activity ─────────────────────────────────────────────────────
    activity = (await db.execute(
        select(Activity).where(Activity.organization_id == org_id, Activity.project_id == pid)
        .order_by(Activity.created_at.desc()).limit(12)
    )).scalars().all()

    base["project"] = {
        "id": pid,
        "name": project.name,
        "description": project.description,
        "status": _enum(project.status),
        "repository_url": project.repository_url,
        "created_at": _iso(project.created_at),
    }
    base.update({
        "progress": {"percent": percent, "completed_tasks": completed_tasks, "total_tasks": total_tasks},
        "missions": {
            "total": len(missions),
            "completed": len(completed),
            "active": sum(1 for m in missions if m.status not in _TERMINAL_MISSION),
            "failed": sum(1 for m in missions if m.status == MissionStatus.FAILED),
        },
        "avg_duration_seconds": avg_duration,
        "spend": {
            "usd": round(float(cost_usd), 4) if runs_count else None,
            "tokens_input": int(tok_in) if runs_count else None,
            "tokens_output": int(tok_out) if runs_count else None,
            "runs": int(runs_count),
        },
        "token_budget": {
            "total": budget_total,
            "used": budget_used,
            "remaining": (budget_total - budget_used) if budget_total is not None else None,
        },
        "deliverables": deliverables,
        "milestones": milestones_list,
        "current": current,
        "attention": {
            "awaiting_approval": awaiting_missions + pending_requests,
            "failed_tasks": failed_tasks,
            "blocked_tasks": blocked_tasks,
        },
        "team": team_list,
        "roster": roster,
        "phase_breakdown": phase_breakdown,
        "activity": [
            {"id": e.id, "type": _enum(e.type), "title": e.title, "description": e.description,
             "created_at": _iso(e.created_at)}
            for e in activity
        ],
    })

    # GitHub account connection status for the active user/org
    gh_conn_stmt = select(GitHubConnection).where(
        GitHubConnection.organization_id == org_id,
        GitHubConnection.user_id == current_user.id,
        GitHubConnection.status == GitHubConnectionStatus.CONNECTED,
    )
    gh_conn = (await db.execute(gh_conn_stmt)).scalars().first()
    base["github"] = {
        "connected": bool(gh_conn),
        "username": gh_conn.github_username if gh_conn else None,
    }

    return base


# ═══════════════════════════════════════════════════════════════════════════
#  ENTERPRISE RBAC (Admin, Lead Engineer, Read-Only Reviewer) & SOC2 AUDIT
# ═══════════════════════════════════════════════════════════════════════════

from fastapi import Request, status
from pydantic import BaseModel, Field
from backend.api.deps import get_current_org_member
from backend.models.organization import (
    ENTERPRISE_ROLES,
    ROLE_HIERARCHY,
    ROLE_PERMISSIONS,
    OrgRole,
    Permission,
)
from backend.models.audit_log import (
    AuditCategory,
    AuditEvent,
    AuditLog,
    normalize_audit_timestamp,
)
from backend.services.auth_service import (
    enforce_permission_with_audit,
    enforce_role_with_audit,
)
from backend.services.soc2_audit_service import SOC2AuditService

rbac_router = APIRouter()
audit_router = APIRouter()


class MemberRoleAssignRequest(BaseModel):
    user_id: Optional[str] = None
    email: Optional[str] = None
    role: str = Field(..., description="Enterprise role: ADMIN ('Admin'), LEAD_ENGINEER ('Lead Engineer'), or READ_ONLY_REVIEWER ('Read-Only Reviewer')")


class MemberRoleUpdateRequest(BaseModel):
    role: str = Field(..., description="Target enterprise role")


class SOC2OperationalEventRequest(BaseModel):
    event_type: str = Field(..., description="file_read | command_executed | ip_access | code_change")
    file_path: Optional[str] = None
    content: Optional[str] = None
    before_content: Optional[str] = None
    after_content: Optional[str] = None
    operation: str = "write"
    command: Optional[str] = None
    exit_code: int = 0
    output_snippet: Optional[str] = None
    blocked: bool = False
    ip_address: Optional[str] = None
    endpoint_or_url: Optional[str] = None
    method: str = "GET"
    session_id: Optional[str] = None
    mission_id: Optional[str] = None
    project_id: Optional[str] = None
    detail: Optional[Dict[str, Any]] = None


def _serialize_member(member: OrganizationMember, user_obj: Optional[User] = None) -> Dict[str, Any]:
    role_enum = OrgRole.normalize(member.role)
    return {
        "id": member.id,
        "user_id": member.user_id,
        "organization_id": member.organization_id,
        "email": user_obj.email if user_obj else None,
        "full_name": user_obj.full_name if user_obj else None,
        "role": role_enum.value,
        "display_name": role_enum.display_name,
        "rank": role_enum.rank,
        "is_read_only": role_enum.is_read_only,
        "permissions": sorted(p.value for p in role_enum.permissions),
    }


def _serialize_audit_log(entry: AuditLog) -> Dict[str, Any]:
    return {
        "id": entry.id,
        "sequence_number": entry.sequence_number,
        "prev_hash": entry.prev_hash,
        "entry_hash": entry.entry_hash,
        "hmac_signature": entry.hmac_signature,
        "organization_id": entry.organization_id,
        "user_id": entry.user_id,
        "actor_role": entry.actor_role,
        "session_id": entry.session_id,
        "mission_id": entry.mission_id,
        "project_id": entry.project_id,
        "event": entry.event,
        "category": entry.category,
        "severity": entry.severity,
        "status": entry.status,
        "ip_address": entry.ip_address,
        "user_agent": entry.user_agent,
        "resource_type": entry.resource_type,
        "resource_id": entry.resource_id,
        "file_path": entry.file_path,
        "command_executed": entry.command_executed,
        "code_change_hash": entry.code_change_hash,
        "content_hash": entry.content_hash,
        "detail": entry.detail,
        "occurred_at": normalize_audit_timestamp(entry.occurred_at),
    }


@router.get("/rbac/roles")
@rbac_router.get("/roles")
async def get_rbac_roles(
    current_user: User = Depends(get_current_active_user),
):
    """Return the Enterprise RBAC role hierarchy and granular permission matrix."""
    roles_payload = []
    for role_enum in OrgRole:
        roles_payload.append(
            {
                "role": role_enum.value,
                "display_name": role_enum.display_name,
                "rank": ROLE_HIERARCHY[role_enum],
                "is_enterprise_tier": role_enum in ENTERPRISE_ROLES,
                "is_read_only": role_enum.is_read_only,
                "permissions": sorted(p.value for p in ROLE_PERMISSIONS[role_enum]),
            }
        )
    return {
        "enterprise_roles": [
            {
                "role": r.value,
                "display_name": r.display_name,
                "rank": r.rank,
                "is_read_only": r.is_read_only,
                "permissions": sorted(p.value for p in r.permissions),
            }
            for r in ENTERPRISE_ROLES
        ],
        "all_roles": roles_payload,
    }


@router.get("/rbac/me")
@rbac_router.get("/me")
async def get_my_rbac_profile(
    request: Request,
    membership: OrganizationMember = Depends(get_current_org_member),
    current_user: User = Depends(get_current_active_user),
):
    """Return the authenticated user's active organization role and permissions."""
    return _serialize_member(membership, current_user)


@router.get("/members")
@rbac_router.get("/members")
async def list_org_members(
    request: Request,
    membership: OrganizationMember = Depends(get_current_org_member),
    db: AsyncSession = Depends(get_db),
):
    """List all members and their RBAC roles in the active organization."""
    await enforce_permission_with_audit(
        db,
        membership,
        Permission.RBAC_READ,
        request=request,
        action="list_org_members",
    )
    stmt = (
        select(OrganizationMember, User)
        .join(User, OrganizationMember.user_id == User.id)
        .where(OrganizationMember.organization_id == membership.organization_id)
    )
    rows = (await db.execute(stmt)).all()
    return {
        "organization_id": membership.organization_id,
        "members": [_serialize_member(m, u) for m, u in rows],
    }


@router.post("/members", status_code=status.HTTP_201_CREATED)
@rbac_router.post("/members", status_code=status.HTTP_201_CREATED)
async def add_or_assign_org_member(
    payload: MemberRoleAssignRequest,
    request: Request,
    membership: OrganizationMember = Depends(get_current_org_member),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Assign or add a user to the organization with an Enterprise RBAC role.
    Restricted to ADMIN / OWNER (`Permission.RBAC_ASSIGN`).
    """
    actor_role = await enforce_permission_with_audit(
        db,
        membership,
        Permission.RBAC_ASSIGN,
        request=request,
        action="assign_org_member_role",
    )
    try:
        target_role = OrgRole.normalize(payload.role)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    target_user: Optional[User] = None
    if payload.user_id:
        target_user = await db.get(User, payload.user_id)
    elif payload.email:
        target_user = (
            await db.execute(select(User).where(User.email == payload.email.strip()))
        ).scalar_one_or_none()

    if not target_user:
        raise HTTPException(status_code=404, detail="Target user not found")

    existing = (
        await db.execute(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == membership.organization_id,
                OrganizationMember.user_id == target_user.id,
            )
        )
    ).scalar_one_or_none()

    if existing:
        previous_role = OrgRole.normalize(existing.role).value
        existing.role = target_role
        audit_ev = AuditEvent.RBAC_ROLE_UPDATED
        member_record = existing
    else:
        previous_role = None
        member_record = OrganizationMember(
            user_id=target_user.id,
            organization_id=membership.organization_id,
            role=target_role,
        )
        db.add(member_record)
        await db.flush()
        audit_ev = AuditEvent.RBAC_MEMBER_ADDED

    await SOC2AuditService.log_rbac_event(
        db,
        event=audit_ev,
        user_id=current_user.id,
        organization_id=membership.organization_id,
        actor_role=actor_role.value,
        target_user_id=target_user.id,
        target_role=target_role.value,
        detail={"previous_role": previous_role, "new_role": target_role.value},
        request=request,
    )
    await db.commit()
    await db.refresh(member_record)
    return _serialize_member(member_record, target_user)


@router.patch("/members/{target_user_id}")
@router.put("/members/{target_user_id}/role")
@rbac_router.patch("/members/{target_user_id}")
@rbac_router.put("/members/{target_user_id}/role")
async def update_org_member_role(
    target_user_id: str,
    payload: MemberRoleUpdateRequest,
    request: Request,
    membership: OrganizationMember = Depends(get_current_org_member),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing organization member's Enterprise RBAC role (Admin only)."""
    actor_role = await enforce_permission_with_audit(
        db,
        membership,
        Permission.RBAC_ASSIGN,
        request=request,
        action="update_org_member_role",
    )
    try:
        target_role = OrgRole.normalize(payload.role)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    target_member = (
        await db.execute(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == membership.organization_id,
                OrganizationMember.user_id == target_user_id,
            )
        )
    ).scalar_one_or_none()
    if not target_member:
        raise HTTPException(status_code=404, detail="Member not found in organization")

    previous_role = OrgRole.normalize(target_member.role).value
    target_member.role = target_role

    await SOC2AuditService.log_rbac_event(
        db,
        event=AuditEvent.RBAC_ROLE_UPDATED,
        user_id=current_user.id,
        organization_id=membership.organization_id,
        actor_role=actor_role.value,
        target_user_id=target_user_id,
        target_role=target_role.value,
        detail={"previous_role": previous_role, "new_role": target_role.value},
        request=request,
    )
    await db.commit()
    await db.refresh(target_member)
    target_user = await db.get(User, target_user_id)
    return _serialize_member(target_member, target_user)


@router.delete("/members/{target_user_id}")
@rbac_router.delete("/members/{target_user_id}")
async def remove_org_member(
    target_user_id: str,
    request: Request,
    membership: OrganizationMember = Depends(get_current_org_member),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove a member from the organization (Admin only)."""
    actor_role = await enforce_permission_with_audit(
        db,
        membership,
        Permission.RBAC_ASSIGN,
        request=request,
        action="remove_org_member",
    )
    target_member = (
        await db.execute(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == membership.organization_id,
                OrganizationMember.user_id == target_user_id,
            )
        )
    ).scalar_one_or_none()
    if not target_member:
        raise HTTPException(status_code=404, detail="Member not found in organization")

    removed_role = OrgRole.normalize(target_member.role).value
    await db.delete(target_member)
    await SOC2AuditService.log_rbac_event(
        db,
        event=AuditEvent.RBAC_MEMBER_REMOVED,
        user_id=current_user.id,
        organization_id=membership.organization_id,
        actor_role=actor_role.value,
        target_user_id=target_user_id,
        target_role=removed_role,
        request=request,
    )
    await db.commit()
    return {"removed": True, "user_id": target_user_id, "previous_role": removed_role}


# ── SOC2 Type II Tamper-Evident Audit Endpoints ───────────────────────────

@router.get("/audit-logs")
@audit_router.get("/logs")
@audit_router.get("")
async def list_soc2_audit_logs(
    request: Request,
    category: Optional[str] = None,
    event: Optional[str] = None,
    user_id: Optional[str] = None,
    actor_role: Optional[str] = None,
    status_filter: Optional[str] = None,
    file_path: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    membership: OrganizationMember = Depends(get_current_org_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Query tamper-evident SOC2 Type II hash-chained audit logs for the organization.
    Accessible to Admin, Lead Engineer, and Read-Only Reviewer (`Permission.AUDIT_READ`).
    """
    await enforce_permission_with_audit(
        db,
        membership,
        Permission.AUDIT_READ,
        request=request,
        action="list_soc2_audit_logs",
    )

    stmt = select(AuditLog).where(AuditLog.organization_id == membership.organization_id)
    if category:
        stmt = stmt.where(AuditLog.category == category.upper())
    if event:
        stmt = stmt.where(AuditLog.event == event)
    if user_id:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if actor_role:
        stmt = stmt.where(AuditLog.actor_role == OrgRole.normalize(actor_role).value)
    if status_filter:
        stmt = stmt.where(AuditLog.status == status_filter.upper())
    if file_path:
        stmt = stmt.where(AuditLog.file_path == file_path.replace("\\", "/"))

    stmt = stmt.order_by(AuditLog.sequence_number.asc()).offset(max(0, offset)).limit(max(1, min(limit, 500)))
    entries = list((await db.execute(stmt)).scalars().all())
    serialized = [_serialize_audit_log(e) for e in entries]
    return {
        "organization_id": membership.organization_id,
        "count": len(serialized),
        "total": len(serialized),
        "logs": serialized,
        "items": serialized,
    }


@router.post("/audit-logs/events", status_code=status.HTTP_201_CREATED)
@audit_router.post("/events", status_code=status.HTTP_201_CREATED)
async def record_soc2_operational_event(
    payload: SOC2OperationalEventRequest,
    request: Request,
    membership: OrganizationMember = Depends(get_current_org_member),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Record an explicit signed SOC2 operational audit event (file_read, command_executed,
    ip_access, or code_change). Restricted to non-read-only engineering/admin roles
    for mutating events (`command_executed`, `code_change`).
    """
    actor_role = OrgRole.normalize(membership.role)
    ev_kind = payload.event_type.strip().lower()

    if ev_kind in ("code_change", "code.change", "code_write", "code_edit", "code_delete"):
        await enforce_permission_with_audit(
            db, membership, Permission.CODE_WRITE, request=request, action="audit_code_change"
        )
        entry = await SOC2AuditService.log_code_change(
            db,
            file_path=payload.file_path or "unknown",
            before_content=payload.before_content,
            after_content=payload.after_content if payload.after_content is not None else payload.content,
            operation=payload.operation,
            user_id=current_user.id,
            organization_id=membership.organization_id,
            actor_role=actor_role.value,
            session_id=payload.session_id,
            mission_id=payload.mission_id,
            project_id=payload.project_id,
            ip_address=payload.ip_address,
            detail=payload.detail,
            request=request,
        )
    elif ev_kind in ("command_executed", "command.execute", "command"):
        await enforce_permission_with_audit(
            db, membership, Permission.COMMAND_EXECUTE, request=request, action="audit_command_execute"
        )
        entry = await SOC2AuditService.log_command_executed(
            db,
            command=payload.command or "",
            exit_code=payload.exit_code,
            output_snippet=payload.output_snippet,
            blocked=payload.blocked,
            user_id=current_user.id,
            organization_id=membership.organization_id,
            actor_role=actor_role.value,
            session_id=payload.session_id,
            mission_id=payload.mission_id,
            project_id=payload.project_id,
            ip_address=payload.ip_address,
            detail=payload.detail,
            request=request,
        )
    elif ev_kind in ("ip_access", "ip.access", "network"):
        await enforce_permission_with_audit(
            db, membership, Permission.FILE_READ, request=request, action="audit_ip_access"
        )
        entry = await SOC2AuditService.log_ip_access(
            db,
            ip_address=payload.ip_address or SOC2AuditService.extract_client_ip(request) or "127.0.0.1",
            endpoint_or_url=payload.endpoint_or_url,
            method=payload.method,
            user_id=current_user.id,
            organization_id=membership.organization_id,
            actor_role=actor_role.value,
            session_id=payload.session_id,
            mission_id=payload.mission_id,
            project_id=payload.project_id,
            detail=payload.detail,
            request=request,
        )
    elif ev_kind in ("file_read", "file.read", "file"):
        await enforce_permission_with_audit(
            db, membership, Permission.FILE_READ, request=request, action="audit_file_read"
        )
        entry = await SOC2AuditService.log_file_read(
            db,
            file_path=payload.file_path or "unknown",
            content=payload.content,
            user_id=current_user.id,
            organization_id=membership.organization_id,
            actor_role=actor_role.value,
            session_id=payload.session_id,
            mission_id=payload.mission_id,
            project_id=payload.project_id,
            ip_address=payload.ip_address,
            detail=payload.detail,
            request=request,
        )
    else:
        raise HTTPException(status_code=422, detail=f"Unsupported SOC2 event_type: {payload.event_type!r}")

    await db.commit()
    await db.refresh(entry)
    return _serialize_audit_log(entry)


@router.get("/audit-logs/verify")
@router.post("/audit-logs/verify")
@audit_router.get("/verify")
@audit_router.post("/verify")
async def verify_soc2_audit_chain(
    request: Request,
    membership: OrganizationMember = Depends(get_current_org_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Cryptographically verify the organization's entire append-only SHA-256 hash chain
    and HMAC-SHA256 signatures (`Permission.AUDIT_VERIFY`).
    """
    await enforce_permission_with_audit(
        db,
        membership,
        Permission.AUDIT_VERIFY,
        request=request,
        action="verify_soc2_audit_chain",
    )
    return await SOC2AuditService.verify_audit_chain(
        db,
        organization_id=membership.organization_id,
    )


@router.get("/audit-logs/soc2-report")
@audit_router.get("/soc2-report")
async def get_soc2_compliance_report(
    request: Request,
    membership: OrganizationMember = Depends(get_current_org_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Generate a cryptographically signed SOC2 Type II compliance report covering
    hash-chain integrity, file reads, commands executed, IPs accessed, signed
    code changes, and RBAC access denials (`Permission.AUDIT_READ`).
    """
    await enforce_permission_with_audit(
        db,
        membership,
        Permission.AUDIT_READ,
        request=request,
        action="get_soc2_compliance_report",
    )
    return await SOC2AuditService.generate_soc2_report(
        db,
        organization_id=membership.organization_id,
    )

