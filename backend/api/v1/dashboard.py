"""
Dashboard aggregate endpoints for the Claude-style web UI.

Everything here is computed from the database at request time. When there is
no data, values are returned as null (never a fabricated 0).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_active_user
from backend.core.database import get_db
from backend.models.agent import AgentRun
from backend.models.mission import Mission, MissionStatus
from backend.models.organization import OrganizationMember, User
from backend.models.project import Task

router = APIRouter()

RUNNING_STATUSES = {MissionStatus.ACTIVE, MissionStatus.PLANNING, MissionStatus.EXECUTING, MissionStatus.READY}
ATTENTION_STATUSES = {MissionStatus.AWAITING_APPROVAL, MissionStatus.BLOCKED, MissionStatus.FAILED}


async def _org_id(db: AsyncSession, user_id: str) -> Optional[str]:
    member = (await db.execute(
        select(OrganizationMember).where(OrganizationMember.user_id == user_id)
    )).scalars().first()
    return member.organization_id if member else None


@router.get("/summary")
async def dashboard_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    org_id = await _org_id(db, current_user.id)
    if not org_id:
        return {"missions": None, "avg_duration_seconds": None, "tokens": None, "user": {"name": current_user.full_name}}

    rows = (await db.execute(
        select(Mission.status, Mission.created_at, Mission.completed_at).where(Mission.organization_id == org_id)
    )).all()

    by_status: Dict[str, int] = defaultdict(int)
    durations = []
    for status, created_at, completed_at in rows:
        by_status[status.value] += 1
        if status == MissionStatus.COMPLETED and created_at and completed_at:
            durations.append((completed_at - created_at).total_seconds())

    tokens_in, tokens_out = (await db.execute(
        select(func.sum(AgentRun.tokens_input), func.sum(AgentRun.tokens_output)).where(AgentRun.organization_id == org_id)
    )).one()

    return {
        "user": {"name": current_user.full_name, "email": current_user.email},
        "missions": {
            "total": len(rows),
            "completed": by_status.get("COMPLETED", 0),
            "running": sum(by_status.get(s.value, 0) for s in RUNNING_STATUSES),
            "needs_attention": sum(by_status.get(s.value, 0) for s in ATTENTION_STATUSES),
            "failed": by_status.get("FAILED", 0),
            "by_status": dict(by_status),
        } if rows else None,
        "avg_duration_seconds": round(sum(durations) / len(durations), 1) if durations else None,
        "tokens": {"input": tokens_in, "output": tokens_out} if tokens_in is not None else None,
    }


@router.get("/agents")
async def dashboard_agents(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Registry agents enriched with real run statistics (grouped by the task's agent_role)."""
    from backend.services.agent_registry import AGENT_REGISTRY

    org_id = await _org_id(db, current_user.id)
    stats: Dict[str, Dict[str, Any]] = {}
    if org_id:
        role_expr = func.json_extract(Task.metadata_json, "$.agent_role")
        q = (
            select(
                role_expr.label("role"),
                AgentRun.status,
                func.count(AgentRun.id),
                func.sum(AgentRun.tokens_input + AgentRun.tokens_output),
                func.max(AgentRun.started_at),
            )
            .join(Task, AgentRun.task_id == Task.id)
            .where(AgentRun.organization_id == org_id)
            .group_by(role_expr, AgentRun.status)
        )
        for role, status, count, tokens, last in (await db.execute(q)).all():
            if not role:
                continue
            s = stats.setdefault(role, {"runs": 0, "completed": 0, "failed": 0, "tokens": 0, "last_run_at": None})
            s["runs"] += count
            status_val = status.value if hasattr(status, "value") else str(status)
            if status_val == "COMPLETED":
                s["completed"] += count
            elif status_val == "FAILED":
                s["failed"] += count
            s["tokens"] += tokens or 0
            if last and (s["last_run_at"] is None or last > s["last_run_at"]):
                s["last_run_at"] = last

    agents = []
    for agent_def in AGENT_REGISTRY.values():
        role = agent_def.type.value
        s = stats.get(role)
        agents.append({
            "id": role,
            "name": agent_def.name,
            "type": role,
            "description": agent_def.description,
            "capabilities": list(agent_def.capabilities),
            "runs": s["runs"] if s else 0,
            "success_rate": round(100 * s["completed"] / s["runs"], 1) if s and s["runs"] else None,
            "failed": s["failed"] if s else 0,
            "tokens": s["tokens"] if s else None,
            "last_run_at": s["last_run_at"].isoformat() if s and s["last_run_at"] else None,
        })
    agents.sort(key=lambda a: a["runs"], reverse=True)
    return agents
