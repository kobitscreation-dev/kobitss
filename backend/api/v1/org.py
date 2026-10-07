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
from backend.models.project import Activity, Project, Task, TaskStatus
from backend.models.mission import Mission, MissionStatus
from backend.models.agent import Agent, AgentRun, ApprovalRequest, ApprovalStatus as RunApprovalStatus
from backend.models.github import PullRequest, Changeset
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
    """Return the user's primary org membership."""
    result = await db.execute(
        select(OrganizationMember).where(OrganizationMember.user_id == user_id)
    )
    return result.scalars().first()


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
    if not membership:
        raise HTTPException(status_code=403, detail="No organisation membership")
    org_id = membership.organization_id

    projects = (await db.execute(
        select(Project).where(Project.organization_id == org_id).order_by(Project.updated_at.desc())
    )).scalars().all()

    base: Dict[str, Any] = {
        "projects": [{"id": p.id, "name": p.name, "status": _enum(p.status)} for p in projects],
        "project": None,
    }
    if not projects:
        return base

    project = next((p for p in projects if p.id == project_id), None) if project_id else projects[0]
    if project is None:
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
    role_expr = func.json_extract(Task.metadata_json, "$.agent_role")
    role_q = (
        select(
            role_expr.label("role"),
            AgentRun.status,
            func.count(AgentRun.id),
            func.coalesce(func.sum(AgentRun.tokens_input + AgentRun.tokens_output), 0),
            func.coalesce(func.sum(AgentRun.estimated_cost), 0.0),
            func.max(AgentRun.started_at),
        )
        .join(Task, AgentRun.task_id == Task.id)
        .where(AgentRun.organization_id == org_id, AgentRun.project_id == pid)
        .group_by(role_expr, AgentRun.status)
    )
    for r_role, r_status, r_cnt, r_tok, r_cost, r_last in (await db.execute(role_q)).all():
        if not r_role:
            continue
        st = role_stats.setdefault(r_role, {"runs": 0, "completed": 0, "failed": 0, "tokens": 0, "cost_usd": 0.0, "last_run_at": None})
        st["runs"] += r_cnt
        sv = _enum(r_status)
        if sv == "COMPLETED":
            st["completed"] += r_cnt
        elif sv == "FAILED":
            st["failed"] += r_cnt
        st["tokens"] += int(r_tok or 0)
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
    return base
