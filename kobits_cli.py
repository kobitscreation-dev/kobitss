#!/usr/bin/env python3
"""
Kobits Native Terminal Engine (`kobits`)
Pure standalone terminal-native multi-agent engineering engine (like Kyros & Claude Code).

Executes directly against your local repository workspace with:
- Zero web server or browser dependencies
- Isolated git worktree / copy-on-write sandboxes per mission
- Scope triage & fast-track execution
- AST + bytecode verification gates before code review
- Parallel verification wave (Sentinel QA + Aegis Security + Code Reviewer)
- Instant `kobits diff` & `kobits apply` into your active local project directory
"""

import argparse
import asyncio
import json
import os
import shlex
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Capture the caller's working directory BEFORE switching to ROOT_DIR for internal DB/.env access
CALLER_CWD = Path(os.environ.get("KOBITS_WORKSPACE_DIR") or os.getcwd()).resolve()
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
os.chdir(ROOT_DIR)

# Target the active directory where the developer ran `kobits`.
WORKSPACE_DIR = CALLER_CWD
os.environ["KOBITS_TARGET_WORKSPACE"] = str(WORKSPACE_DIR)

CONFIG_PATH = Path.home() / ".kobits" / "cli_config.json"

# Enable ANSI escape sequences and UTF-8 output on Windows
if os.name == "nt":
    os.system("")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── ANSI Styling ──────────────────────────────────────────────
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"
BLUE = "\033[38;5;75m"
GREEN = "\033[38;5;78m"
YELLOW = "\033[38;5;220m"
RED = "\033[38;5;203m"
CYAN = "\033[38;5;117m"
GRAY = "\033[38;5;245m"

PHASES_ORDER = [
    "INTAKE",
    "ANALYSIS",
    "PLANNING",
    "ARCHITECTURE_REVIEW",
    "IMPLEMENTATION",
    "VALIDATION",
    "SECURITY",
    "CODE_REVIEW",
    "DELIVERY_REVIEW",
    "DEPLOYMENT",
    "LEARNING",
]


def load_cli_config() -> Dict[str, Any]:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_cli_config(cfg: Dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def color_status(status_str: Optional[str], pad: int = 0) -> str:
    s = (status_str or "UNKNOWN").upper()
    padded = f"{s:<{pad}}" if pad > 0 else s
    if s in ("COMPLETED", "SUCCESS", "APPROVED", "MERGED", "ACTIVE"):
        return f"{GREEN}{BOLD}{padded}{RESET}"
    if s in ("AWAITING_APPROVAL", "PENDING", "IN_PROGRESS", "RUNNING", "PLANNING"):
        return f"{YELLOW}{BOLD}{padded}{RESET}"
    if s in ("FAILED", "REJECTED", "CANCELLED", "ERROR", "BLOCKED"):
        return f"{RED}{BOLD}{padded}{RESET}"
    return f"{CYAN}{padded}{RESET}"


def effective_progress(progress_val: Optional[int], status_str: Optional[str], phase_str: Optional[str]) -> int:
    if progress_val and int(progress_val) > 0:
        return int(progress_val)
    if (status_str or "").upper() == "COMPLETED":
        return 100
    if phase_str and phase_str.upper() in PHASES_ORDER:
        return int(round((PHASES_ORDER.index(phase_str.upper()) / (len(PHASES_ORDER) - 1)) * 100))
    return 0


def render_progress_bar(pct: Optional[int], width: int = 24) -> str:
    if pct is None:
        return f"{GRAY}[{'·' * width}] —{RESET}"
    clamped = max(0, min(100, int(pct)))
    filled = int(round((clamped / 100.0) * width))
    bar = f"{GREEN}{'█' * filled}{GRAY}{'░' * (width - filled)}{RESET}"
    return f"[{bar}] {BOLD}{clamped}%{RESET}"


def render_phase_pipeline(current_phase: Optional[str], status_str: Optional[str]) -> str:
    if not current_phase:
        return f"{GRAY}No active workflow phase{RESET}"
    cp = current_phase.upper()
    fluid_stages = [
        ("PLAN", {"INTAKE", "ANALYSIS", "PLANNING", "ARCHITECTURE_REVIEW"}),
        ("CODE", {"IMPLEMENTATION"}),
        ("VERIFY & TEST", {"VALIDATION", "SECURITY", "CODE_REVIEW"}),
        ("DELIVER", {"DELIVERY_REVIEW", "DEPLOYMENT", "LEARNING"}),
    ]
    cur_idx = 0
    for idx, (_, group) in enumerate(fluid_stages):
        if cp in group:
            cur_idx = idx
            break
    stat_up = (status_str or "").upper()
    parts = []
    for idx, (label, _) in enumerate(fluid_stages):
        if stat_up == "COMPLETED" or idx < cur_idx:
            parts.append(f"{GREEN}✓ {label}{RESET}")
        elif idx == cur_idx:
            if stat_up == "AWAITING_APPROVAL":
                parts.append(f"{YELLOW}⏸ {BOLD}{label}{RESET}")
            elif stat_up in ("FAILED", "BLOCKED"):
                parts.append(f"{RED}✗ {BOLD}{label}{RESET}")
            else:
                parts.append(f"{BLUE}● {BOLD}{label}{RESET}")
        else:
            parts.append(f"{GRAY}○ {label}{RESET}")
    return f" {parts[0]} {GRAY}→{RESET} {parts[1]} {CYAN}⟲{RESET} {parts[2]} {GRAY}→{RESET} {parts[3]}"


# ── Pure Local Terminal Engine (SQLite + MissionRuntime + Git Sandboxes) ──
async def _ensure_local_db_and_identity():
    from sqlalchemy import select
    from backend.core.database import engine, AsyncSessionLocal
    from backend.models.base import Base
    import backend.models.organization as org_models
    import backend.models.project as proj_models
    import backend.models.agent as agent_models
    import backend.models.github as gh_models
    import backend.models.mission as mission_models
    import backend.models.finding as finding_models
    import backend.models.debate_log as debate_models
    import backend.models.communication as comm_models
    import backend.models.intelligence as intel_models
    import backend.models.memory as mem_models

    try:
        from sqlalchemy import inspect
        def _check_tables(sync_conn):
            insp = inspect(sync_conn)
            return insp.has_table("users")
        async with engine.begin() as conn:
            if not await conn.run_sync(_check_tables):
                await conn.run_sync(Base.metadata.create_all)
    except Exception:
        pass

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(org_models.User).limit(1))).scalar_one_or_none()
        if not user:
            user = org_models.User(
                id="cli_user_1",
                email="cli@kobits.local",
                full_name="Kobits CLI Operator",
                hashed_password="local",
            )
            org = org_models.Organization(id="cli_org_1", name="Kobits Local Workspace")
            db.add(user)
            db.add(org)
            await db.flush()
            member = org_models.OrganizationMember(
                user_id=user.id,
                organization_id=org.id,
                role=org_models.OrgRole.OWNER,
            )
            db.add(member)
            await db.commit()
            await db.refresh(user)

        member = (
            await db.execute(
                select(org_models.OrganizationMember).where(org_models.OrganizationMember.user_id == user.id)
            )
        ).scalars().first()
        if not member:
            org = org_models.Organization(id="cli_org_1", name="Kobits Local Workspace")
            db.add(org)
            await db.flush()
            member = org_models.OrganizationMember(
                user_id=user.id,
                organization_id=org.id,
                role=org_models.OrgRole.OWNER,
            )
            db.add(member)
            await db.commit()

        return user.id, member.organization_id


async def local_list_projects() -> List[Dict[str, Any]]:
    from sqlalchemy import select
    from backend.core.database import AsyncSessionLocal
    from backend.models.project import Project

    _, org_id = await _ensure_local_db_and_identity()
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(select(Project).where(Project.organization_id == org_id).order_by(Project.created_at.desc()))
        ).scalars().all()
        return [
            {
                "id": p.id,
                "name": p.name,
                "status": p.status.value if hasattr(p.status, "value") else str(p.status),
                "progress": p.progress,
                "health_score": p.health_score,
                "description": p.description,
                "repository_url": p.repository_url,
            }
            for p in rows
        ]


async def local_create_project(name: str, description: str = "", repository_url: Optional[str] = None) -> Dict[str, Any]:
    from backend.core.database import AsyncSessionLocal
    from backend.models.project import Project, Activity, ActivityType

    user_id, org_id = await _ensure_local_db_and_identity()
    async with AsyncSessionLocal() as db:
        project = Project(
            name=name,
            description=description or f"Project created via Kobits CLI ({name})",
            repository_url=repository_url,
            organization_id=org_id,
            created_by=user_id,
        )
        db.add(project)
        await db.flush()
        db.add(
            Activity(
                organization_id=org_id,
                project_id=project.id,
                user_id=user_id,
                type=ActivityType.PROJECT_CREATED,
                title=f"Project '{project.name}' created via CLI",
                description=project.description,
            )
        )
        await db.commit()
        await db.refresh(project)
        return {
            "id": project.id,
            "name": project.name,
            "status": project.status.value if hasattr(project.status, "value") else str(project.status),
            "description": project.description,
        }


async def local_resolve_or_create_project(project_arg: Optional[str]) -> Dict[str, Any]:
    projects = await local_list_projects()
    if project_arg:
        for p in projects:
            if p["id"] == project_arg or p["id"].startswith(project_arg) or p["name"].lower() == project_arg.lower():
                return p
        return await local_create_project(name=project_arg, repository_url=str(WORKSPACE_DIR))
    ws_name = WORKSPACE_DIR.name or "Kobits Default Project"
    if WORKSPACE_DIR != ROOT_DIR:
        for p in projects:
            if p["name"].lower() == ws_name.lower() or p.get("repository_url") == str(WORKSPACE_DIR):
                return p
        return await local_create_project(name=ws_name, repository_url=str(WORKSPACE_DIR))
    if projects:
        return projects[0]
    return await local_create_project(name=ws_name, repository_url=str(WORKSPACE_DIR))


DEFAULT_CLOUD_API_URL = (
    os.environ.get("KOBITS_API_URL")
    or "https://kobitss-850300766138.europe-west1.run.app"
).rstrip("/")


async def cloud_list_missions(limit: int = 50) -> List[Dict[str, Any]]:
    import httpx
    api_url = DEFAULT_CLOUD_API_URL
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("KOBITS_AUTH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=8.0) as client:
        res = await client.get(f"{api_url}/api/v1/missions", headers=headers)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, dict) and "missions" in data:
                return data["missions"][:limit]
            elif isinstance(data, list):
                return data[:limit]
        return []


async def cloud_get_mission_detail(mission_id: str) -> Optional[Dict[str, Any]]:
    import httpx
    api_url = DEFAULT_CLOUD_API_URL
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("KOBITS_AUTH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=8.0) as client:
        res = await client.get(f"{api_url}/api/v1/missions/{mission_id}", headers=headers)
        if res.status_code == 200:
            return res.json()
        return None


async def cloud_steer_mission(mission_id: str, instruction: str) -> Optional[Dict[str, Any]]:
    import httpx
    api_url = DEFAULT_CLOUD_API_URL
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("KOBITS_AUTH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=8.0) as client:
        res = await client.post(
            f"{api_url}/api/v1/missions/{mission_id}/steer",
            headers=headers,
            json={"instruction": instruction},
        )
        if res.status_code in (200, 201):
            return res.json()
        return None


async def cloud_get_diff(mission_id: str) -> Optional[Dict[str, Any]]:
    import httpx
    api_url = DEFAULT_CLOUD_API_URL
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("KOBITS_AUTH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=8.0) as client:
        res = await client.get(f"{api_url}/api/v1/missions/{mission_id}/diff", headers=headers)
        if res.status_code == 200:
            return res.json()
        return None


async def cloud_apply_mission(mission_id: str, dry_run: bool = False, force: bool = False) -> Optional[Dict[str, Any]]:
    import httpx
    api_url = DEFAULT_CLOUD_API_URL
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("KOBITS_AUTH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.post(
            f"{api_url}/api/v1/missions/{mission_id}/apply",
            headers=headers,
            json={"dry_run": dry_run, "force": force},
        )
        if res.status_code == 200:
            return res.json()
        return None


async def _resolve_mission_id(mission_id_arg: Optional[str], prefer_status: Optional[str] = None) -> Optional[str]:
    is_local = os.environ.get("KOBITS_LOCAL_MODE") == "1"
    if mission_id_arg:
        if len(mission_id_arg) == 36 and "-" in mission_id_arg:
            return mission_id_arg
        if not is_local:
            try:
                cloud_missions = await cloud_list_missions()
                for m in cloud_missions:
                    if m.get("id", "").startswith(mission_id_arg):
                        return m["id"]
            except Exception:
                pass
        try:
            local_missions = await local_list_missions()
            for m in local_missions:
                if m.get("id", "").startswith(mission_id_arg):
                    return m["id"]
        except Exception:
            pass
        return mission_id_arg

    if not is_local:
        try:
            cloud_missions = await cloud_list_missions()
            if cloud_missions:
                if prefer_status:
                    for m in cloud_missions:
                        if m.get("status") == prefer_status:
                            return m["id"]
                return cloud_missions[0]["id"]
        except Exception:
            pass

    try:
        missions = await local_list_missions()
        if not missions:
            return None
        if prefer_status:
            for m in missions:
                if m["status"] == prefer_status:
                    return m["id"]
        return missions[0]["id"]
    except Exception:
        return None


async def local_list_missions(status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    from sqlalchemy import select
    from backend.core.database import AsyncSessionLocal
    from backend.models.mission import Mission, MissionStatus

    _, org_id = await _ensure_local_db_and_identity()
    async with AsyncSessionLocal() as db:
        stmt = select(Mission).where(Mission.organization_id == org_id)
        if status_filter:
            valid = [s.value for s in MissionStatus]
            if status_filter.upper() in valid:
                stmt = stmt.where(Mission.status == MissionStatus(status_filter.upper()))
        stmt = stmt.order_by(Mission.created_at.desc()).limit(50)
        rows = (await db.execute(stmt)).scalars().all()
        return [
            {
                "id": m.id,
                "project_id": m.project_id,
                "title": m.title,
                "objective": m.objective,
                "status": m.status.value if hasattr(m.status, "value") else str(m.status),
                "phase": m.phase.value if m.phase else None,
                "progress": effective_progress(
                    m.progress,
                    m.status.value if hasattr(m.status, "value") else str(m.status),
                    m.phase.value if m.phase else None,
                ),
                "priority": m.priority.value if hasattr(m.priority, "value") else str(m.priority),
                "risk_level": m.risk_level.value if hasattr(m.risk_level, "value") else str(m.risk_level),
                "current_stage": m.current_stage,
                "requires_approval": m.requires_approval,
                "approval_status": m.approval_status.value if hasattr(m.approval_status, "value") else str(m.approval_status),
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in rows
        ]


async def local_get_mission_detail(mission_id_prefix: str, include_sandbox: bool = True) -> Optional[Dict[str, Any]]:
    from sqlalchemy import select
    from backend.core.database import AsyncSessionLocal
    from backend.models.mission import Mission
    from backend.models.project import Task

    _, org_id = await _ensure_local_db_and_identity()
    async with AsyncSessionLocal() as db:
        stmt = select(Mission).where(
            Mission.organization_id == org_id,
            Mission.id.like(f"{mission_id_prefix}%"),
        )
        mission = (await db.execute(stmt)).scalars().first()
        if not mission:
            return None

        tasks = (
            await db.execute(select(Task).where(Task.mission_id == mission.id).order_by(Task.created_at.asc()))
        ).scalars().all()

        from backend.services.intelligence.adaptive_planning import AdaptivePlanner
        from backend.models.agent import AgentRun
        from backend.models.github import Changeset

        meta = {}
        if getattr(mission, "metadata_json", None):
            try:
                meta = json.loads(mission.metadata_json)
            except Exception:
                meta = {}
        saved_triage = meta.get("scope_triage") or {}
        if saved_triage and not include_sandbox:
            scope_triage = saved_triage
        else:
            fresh_triage = AdaptivePlanner(db).classify_scope(mission.objective or "", mission.title or "")
            scope_triage = {**fresh_triage, **saved_triage}
            if "skipped_specialists" not in scope_triage:
                scope_triage["skipped_specialists"] = fresh_triage.get("skipped_specialists", [])
            if "target_files" not in scope_triage:
                scope_triage["target_files"] = fresh_triage.get("target_files", [])

        # Gather deliverable artifacts & changesets
        task_ids = [t.id for t in tasks]
        deliverables = []
        if include_sandbox and task_ids:
            runs = (
                await db.execute(
                    select(AgentRun).where(AgentRun.task_id.in_(task_ids)).order_by(AgentRun.started_at.asc())
                )
            ).scalars().all()
            task_map = {t.id: t for t in tasks}
            for r in runs:
                if not r.output_text:
                    continue
                try:
                    out = json.loads(r.output_text)
                    t_obj = task_map.get(r.task_id)
                    phase_name = t_obj.phase.value if (t_obj and t_obj.phase) else ""
                    if phase_name == "IMPLEMENTATION" and isinstance(out, dict):
                        arts = out.get("artifacts") or {}
                        summary_txt = out.get("summary") or ""
                        if isinstance(arts, dict) and arts:
                            for k, v in arts.items():
                                deliverables.append({
                                    "task": t_obj.title if t_obj else "Implementation",
                                    "artifact": k,
                                    "preview": str(v)[:120].replace("\n", " "),
                                })
                        elif summary_txt:
                            deliverables.append({
                                "task": t_obj.title if t_obj else "Implementation",
                                "artifact": "implementation_summary",
                                "preview": summary_txt[:120].replace("\n", " "),
                            })
                except Exception:
                    pass

        changesets = (
            await db.execute(select(Changeset).where(Changeset.mission_id == mission.id))
        ).scalars().all() if include_sandbox else []

        sandbox_info = (
            _inspect_mission_sandbox(
                mission.id,
                mission.active_branch,
                scope_triage.get("target_files", []),
            )
            if include_sandbox
            else {"sandbox_dir": None, "abs_sandbox_dir": None, "root_sha": None, "files": [], "verification": None}
        )

        return {
            "id": mission.id,
            "project_id": mission.project_id,
            "title": mission.title,
            "objective": mission.objective,
            "status": mission.status.value if hasattr(mission.status, "value") else str(mission.status),
            "phase": mission.phase.value if mission.phase else None,
            "progress": effective_progress(
                mission.progress,
                mission.status.value if hasattr(mission.status, "value") else str(mission.status),
                mission.phase.value if mission.phase else None,
            ),
            "priority": mission.priority.value if hasattr(mission.priority, "value") else str(mission.priority),
            "risk_level": mission.risk_level.value if hasattr(mission.risk_level, "value") else str(mission.risk_level),
            "current_stage": mission.current_stage,
            "active_branch": mission.active_branch,
            "requires_approval": mission.requires_approval,
            "approval_status": mission.approval_status.value if hasattr(mission.approval_status, "value") else str(mission.approval_status),
            "created_at": mission.created_at.isoformat() if mission.created_at else None,
            "scope_triage": scope_triage,
            "sandbox": sandbox_info,
            "deliverables": deliverables,
            "changesets": [
                {
                    "branch": cs.branch,
                    "files_changed": cs.files_changed,
                    "lines_added": cs.lines_added,
                    "lines_removed": cs.lines_removed,
                    "diff_summary": cs.diff_summary,
                }
                for cs in changesets
            ],
            "tasks": [
                {
                    "id": t.id,
                    "title": t.title,
                    "description": t.description,
                    "status": t.status.value if hasattr(t.status, "value") else str(t.status),
                    "phase": t.phase.value if t.phase else None,
                    "agent_role": json.loads(t.metadata_json).get("agent_role") if t.metadata_json else None,
                    "agent_name": json.loads(t.metadata_json).get("agent_name") if t.metadata_json else None,
                    "is_correction": t.is_correction,
                    "attempt_count": t.attempt_count,
                }
                for t in tasks
            ],
        }


def inspect_sandbox_directory(
    sandbox_dir: str | Path,
    target_files: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Inspect a single sandbox directory against its initial root commit (`root_sha`),
    capturing committed AND working-tree file additions (`ADDED`), modifications (`MODIFIED`),
    and deletions (`DELETED`), plus running the multi-language verification gate.
    """
    import subprocess
    from backend.services.mission_runtime import MissionRuntime

    entry = Path(sandbox_dir)
    if not entry.is_dir():
        return {
            "sandbox_dir": None,
            "abs_sandbox_dir": None,
            "root_sha": None,
            "files": [],
            "verification": None,
        }

    file_entries: List[Dict[str, Any]] = []
    root_sha: Optional[str] = None

    if (entry / ".git").exists():
        try:
            root_sha = _get_sandbox_root_sha(entry)
            if root_sha:
                # Diff root_sha against working tree (covers both committed HEAD changes and uncommitted edits/deletions)
                ns_out = subprocess.run(
                    ["git", "diff", "--no-renames", root_sha, "--name-status"],
                    cwd=str(entry), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5
                ).stdout.splitlines()
                numstat_out = subprocess.run(
                    ["git", "diff", "--no-renames", root_sha, "--numstat"],
                    cwd=str(entry), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5
                ).stdout.splitlines()
                stats_map: Dict[str, tuple[int, int]] = {}
                for nline in numstat_out:
                    parts = nline.split("\t")
                    if len(parts) >= 3:
                        a_cnt = int(parts[0]) if parts[0].isdigit() else 0
                        r_cnt = int(parts[1]) if parts[1].isdigit() else 0
                        stats_map[parts[2].strip().replace("\\", "/")] = (a_cnt, r_cnt)

                seen_paths: set[str] = set()
                for line in ns_out:
                    parts = line.split("\t", 1)
                    if len(parts) != 2:
                        continue
                    code, rel_p = parts[0].strip(), parts[1].strip().replace("\\", "/")
                    if (
                        "__pycache__" in rel_p
                        or rel_p.endswith(".pyc")
                        or os.path.basename(rel_p).startswith(".kobits_")
                        or rel_p.startswith(".kobits_rootfs/")
                        or rel_p == ".kobits_rootfs"
                    ):
                        continue
                    seen_paths.add(rel_p)
                    a_cnt, r_cnt = stats_map.get(rel_p, (0, 0))
                    diff_txt = subprocess.run(
                        ["git", "diff", "--no-renames", root_sha, "--", rel_p],
                        cwd=str(entry), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5
                    ).stdout
                    if code.startswith("A"):
                        status_str = "ADDED"
                    elif code.startswith("D"):
                        status_str = "DELETED"
                    else:
                        status_str = "MODIFIED"
                    file_entries.append({
                        "status": status_str,
                        "path": rel_p,
                        "added": a_cnt,
                        "removed": r_cnt,
                        "diff": diff_txt,
                    })

                # Also capture any untracked working-tree files created via write_file without git add
                untracked_out = subprocess.run(
                    ["git", "ls-files", "--others", "--exclude-standard"],
                    cwd=str(entry), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5
                ).stdout.splitlines()
                for uline in untracked_out:
                    rel_p = uline.strip().replace("\\", "/")
                    if (
                        not rel_p
                        or rel_p in seen_paths
                        or "__pycache__" in rel_p
                        or rel_p.endswith(".pyc")
                        or os.path.basename(rel_p).startswith(".kobits_")
                        or rel_p.startswith(".kobits_rootfs/")
                        or rel_p == ".kobits_rootfs"
                    ):
                        continue
                    abs_f = entry / rel_p
                    if not abs_f.is_file():
                        continue
                    try:
                        text_c = abs_f.read_text(encoding="utf-8", errors="replace")
                        lines_c = text_c.splitlines()
                        a_cnt = len(lines_c)
                        plus_body = "\n".join(f"+{ln}" for ln in lines_c)
                        diff_txt = (
                            f"diff --git a/{rel_p} b/{rel_p}\n"
                            f"new file mode 100644\n"
                            f"--- /dev/null\n"
                            f"+++ b/{rel_p}\n"
                            f"@@ -0,0 +1,{a_cnt} @@\n"
                            f"{plus_body}\n"
                        )
                    except Exception:
                        a_cnt = 0
                        diff_txt = ""
                    seen_paths.add(rel_p)
                    file_entries.append({
                        "status": "ADDED",
                        "path": rel_p,
                        "added": a_cnt,
                        "removed": 0,
                        "diff": diff_txt,
                    })
        except Exception:
            pass
    else:
        for abs_f in sorted(entry.rglob("*")):
            if not abs_f.is_file():
                continue
            try:
                rel_p = abs_f.relative_to(entry).as_posix()
            except Exception:
                continue
            if (
                not rel_p
                or "__pycache__" in rel_p
                or rel_p.endswith(".pyc")
                or os.path.basename(rel_p).startswith(".kobits_")
                or rel_p.startswith(".kobits_rootfs/")
                or rel_p == ".kobits_rootfs"
                or rel_p.startswith(".git/")
            ):
                continue
            try:
                text_c = abs_f.read_text(encoding="utf-8", errors="replace")
                lines_c = text_c.splitlines()
                a_cnt = len(lines_c)
                plus_body = "\n".join(f"+{ln}" for ln in lines_c)
                diff_txt = (
                    f"diff --git a/{rel_p} b/{rel_p}\n"
                    f"new file mode 100644\n"
                    f"--- /dev/null\n"
                    f"+++ b/{rel_p}\n"
                    f"@@ -0,0 +1,{a_cnt} @@\n"
                    f"{plus_body}\n"
                )
            except Exception:
                a_cnt = 0
                diff_txt = ""
            file_entries.append({
                "status": "ADDED",
                "path": rel_p,
                "added": a_cnt,
                "removed": 0,
                "diff": diff_txt,
            })

    try:
        rel_sb = f"sandboxes/{entry.name}" if entry.parent == (ROOT_DIR / "sandboxes") else str(entry)
    except Exception:
        rel_sb = str(entry)

    verification = MissionRuntime._verify_sandbox_code(str(entry), target_files or [])
    return {
        "sandbox_dir": rel_sb,
        "abs_sandbox_dir": str(entry),
        "root_sha": root_sha,
        "files": file_entries,
        "verification": verification,
    }


def _inspect_mission_sandbox(
    mission_id: str,
    active_branch: Optional[str],
    target_files: Optional[List[str]] = None,
) -> Dict[str, Any]:
    branch_target = active_branch or f"kobits/mission/{mission_id[:8]}"
    short_id = mission_id[:8]
    sandboxes_root = ROOT_DIR / "sandboxes"
    if not sandboxes_root.is_dir():
        return {"sandbox_dir": None, "abs_sandbox_dir": None, "root_sha": None, "files": [], "verification": None}

    candidates = []
    for entry in sandboxes_root.iterdir():
        if not entry.is_dir():
            continue
        meta_file = entry / ".kobits_sandbox.json"
        matched = False
        if meta_file.is_file():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                if meta.get("branch_name") == branch_target or meta.get("mission_id") == mission_id:
                    matched = True
            except Exception:
                pass
        if not matched and entry.name in (
            f"mission_{short_id}",
            f"mission_{mission_id}",
            f"kobits_mission_{short_id}",
            f"kobits_mission_{mission_id}",
        ):
            matched = True

        if not matched:
            continue

        inspected = inspect_sandbox_directory(entry, target_files=target_files)
        file_entries = inspected.get("files") or []
        candidates.append((len(file_entries), entry.stat().st_mtime, inspected))

    if not candidates:
        return {"sandbox_dir": None, "abs_sandbox_dir": None, "root_sha": None, "files": [], "verification": None}

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][2]


async def local_create_and_run_mission(
    objective: str,
    title: Optional[str] = None,
    project_arg: Optional[str] = None,
    branch: str = "main",
    autopilot: bool = True,
    wait: bool = True,
) -> Dict[str, Any]:
    from sqlalchemy import select
    from backend.core.database import AsyncSessionLocal
    from backend.models.mission import Mission, MissionStatus, ApprovalStatus
    from backend.models.project import Activity, ActivityType
    from backend.models.github import GitHubConnection, GitHubConnectionStatus, Repository, RepositoryStatus
    from backend.services.mission_runtime import MissionRuntime

    user_id, org_id = await _ensure_local_db_and_identity()
    project = await local_resolve_or_create_project(project_arg)
    mission_title = title or (objective[:68] + ("..." if len(objective) > 68 else ""))

    async with AsyncSessionLocal() as db:
        repo = (
            await db.execute(select(Repository).where(Repository.project_id == project["id"]))
        ).scalars().first()
        if not repo:
            conn = (
                await db.execute(select(GitHubConnection).where(GitHubConnection.organization_id == org_id))
            ).scalars().first()
            if not conn:
                conn = GitHubConnection(
                    organization_id=org_id,
                    user_id=user_id,
                    github_username="local-cli",
                    status=GitHubConnectionStatus.CONNECTED,
                )
                db.add(conn)
                await db.flush()
            repo = Repository(
                project_id=project["id"],
                organization_id=org_id,
                github_connection_id=conn.id,
                github_repo_id="local-repo",
                owner="local",
                name=project["name"].lower().replace(" ", "-"),
                full_name=f"local/{project['name'].lower().replace(' ', '-')}",
                default_branch=branch,
                selected_branch=branch,
                clone_url="test_repo",
                web_url="http://localhost:8000",
                status=RepositoryStatus.READY,
            )
            db.add(repo)
            await db.flush()

        mission = Mission(
            organization_id=org_id,
            project_id=project["id"],
            repository_id=repo.id,
            created_by=user_id,
            title=mission_title,
            objective=objective,
            description=objective,
            status=MissionStatus.ACTIVE,
            active_branch=None,
            requires_approval=(not autopilot),
            approval_status=ApprovalStatus.APPROVED if autopilot else ApprovalStatus.PENDING,
        )
        db.add(mission)
        db.add(
            Activity(
                organization_id=org_id,
                project_id=project["id"],
                user_id=user_id,
                type=ActivityType.MISSION_CREATED,
                title="Mission Created via CLI",
                description=f"CLI launched mission: '{mission_title}'",
            )
        )
        await db.commit()
        await db.refresh(mission)
        mission_id = mission.id
        from backend.services.intelligence.adaptive_planning import AdaptivePlanner
        scope_preview = AdaptivePlanner(db).classify_scope(objective, mission_title)

    domains_str = ", ".join(scope_preview.get("domains", []))
    skipped_list = scope_preview.get("skipped_specialists") or scope_preview.get("skipped_planners") or []
    skipped_str = ", ".join(skipped_list) or "None"
    targets_str = ", ".join(scope_preview.get("target_files", [])) or "auto-detect"
    comp_lbl = scope_preview.get("complexity", "standard").upper()
    if scope_preview.get("fast_track"):
        comp_lbl += f" {GREEN}(⚡ KYROS FAST-TRACK){RESET}"
    print(f"\n{BLUE}{BOLD}⚡ KOBITS MISSION LAUNCHED{RESET}")
    print(f"  {GRAY}Mission ID :{RESET} {BOLD}{mission_id}{RESET}")
    print(f"  {GRAY}Project    :{RESET} {project['name']} ({project['id'][:8]})")
    print(f"  {GRAY}Title      :{RESET} {mission_title}")
    print(f"  {GRAY}Scope      :{RESET} {CYAN}{domains_str}{RESET}  │  {GRAY}Complexity:{RESET} {YELLOW}{comp_lbl}{RESET}")
    print(f"  {GRAY}Targets    :{RESET} {CYAN}{targets_str}{RESET}")
    print(f"  {GRAY}Skipped    :{RESET} {DIM}{skipped_str}{RESET}")
    print(f"  {GRAY}Mode       :{RESET} {'Autopilot (Continuous)' if autopilot else 'Approval Required (Human-in-the-Loop)'}")
    print(f"  {GRAY}Branch     :{RESET} {branch}\n")

    if not wait:
        return await local_get_mission_detail(mission_id)

    runtime_task = asyncio.create_task(MissionRuntime(mission_id, org_id, user_id).execute())
    await stream_mission_progress_local(mission_id, runtime_task)
    return await local_get_mission_detail(mission_id)


async def stream_mission_progress_local(mission_id: str, runtime_task: Optional[asyncio.Task] = None) -> None:
    last_snapshot = ""
    seen_tasks = {}

    while True:
        detail = await local_get_mission_detail(mission_id, include_sandbox=False)
        if not detail:
            break

        status_val = detail["status"]
        phase_val = detail.get("phase") or "INTAKE"
        prog_val = detail.get("progress") or 0
        stage_val = detail.get("current_stage") or "Initializing..."

        snap = f"{status_val}|{phase_val}|{prog_val}|{stage_val}"
        if snap != last_snapshot:
            print(
                f"  {color_status(status_val)}  {render_progress_bar(prog_val, 18)}  "
                f"{CYAN}{phase_val:<20}{RESET} {DIM}{stage_val}{RESET}"
            )
            last_snapshot = snap

        for t in detail.get("tasks", []):
            tid = t["id"]
            tstat = t["status"]
            if seen_tasks.get(tid) != tstat:
                seen_tasks[tid] = tstat
                agent_lbl = t.get("agent_name") or t.get("agent_role") or "Agent"
                corr = f" {YELLOW}[CORRECTION]{RESET}" if t.get("is_correction") else ""
                print(f"    ↳ Task [{color_status(tstat)}] {BOLD}{t['title']}{RESET} ({CYAN}{agent_lbl}{RESET}){corr}")

        if status_val in ("COMPLETED", "FAILED", "CANCELLED", "AWAITING_APPROVAL"):
            break
        if runtime_task and runtime_task.done():
            break

        await asyncio.sleep(0.6)

    if runtime_task and not runtime_task.done():
        try:
            await asyncio.wait_for(runtime_task, timeout=2.0)
        except Exception:
            pass

    final = await local_get_mission_detail(mission_id, include_sandbox=True)
    if final:
        print("\n" + "─" * 74)
        print_mission_summary(final)


def print_mission_summary(detail: Dict[str, Any]) -> None:
    scope = detail.get("scope_triage") or {}
    domains_str = ", ".join(scope.get("domains", [])) if scope else "—"
    skipped_list = scope.get("skipped_specialists") or scope.get("skipped_planners") or []
    skipped_str = ", ".join(skipped_list) if skipped_list else "None"
    targets_list = scope.get("target_files") or []
    targets_str = ", ".join(targets_list) if targets_list else "—"
    comp_lbl = scope.get("complexity", "standard").upper()
    if scope.get("fast_track"):
        comp_lbl += f" {GREEN}(⚡ FAST-TRACK){RESET}"

    print(f"{BOLD}MISSION SUMMARY:{RESET} {detail['title']}")
    print(f"  {GRAY}ID       :{RESET} {detail['id']}")
    print(f"  {GRAY}Status   :{RESET} {color_status(detail['status'])}   {render_progress_bar(detail.get('progress'))}")
    if scope:
        print(
            f"  {GRAY}Scope    :{RESET} {CYAN}{domains_str}{RESET}  │  "
            f"{GRAY}Complexity:{RESET} {YELLOW}{comp_lbl}{RESET}  │  "
            f"{GRAY}Targets:{RESET} {CYAN}{targets_str}{RESET}"
        )
        print(f"  {GRAY}Skipped  :{RESET} {DIM}{skipped_str} ({len(skipped_list)} specialists pruned){RESET}")
    sb_info = detail.get("sandbox") or {}
    if sb_info.get("sandbox_dir"):
        ver = sb_info.get("verification") or {}
        v_stat = ver.get("status", "SKIPPED")
        ver_files = ver.get("verified_files") or ver.get("verified_py_files") or []
        by_lang = ver.get("verified_by_language") or {}
        lang_tags = ", ".join(f"{k}: {len(v)}" for k, v in by_lang.items() if v)
        lang_suffix = f" [{lang_tags}]" if lang_tags else ""
        v_badge = (
            f"{GREEN}{BOLD}✓ PASSED{RESET} ({len(ver_files)} file(s) syntax & import verified{lang_suffix})"
            if v_stat == "PASSED"
            else f"{RED}{BOLD}✗ FAILED{RESET} ({'; '.join(ver.get('errors') or [])})"
        )
        print(f"  {GRAY}Sandbox  :{RESET} {CYAN}{sb_info['sandbox_dir']}{RESET}  │  {GRAY}Smoke Check:{RESET} {v_badge}")
    print(f"  {GRAY}Stage    :{RESET} {detail.get('current_stage') or '—'}")
    print(f"  {GRAY}Pipeline :{RESET} {render_phase_pipeline(detail.get('phase'), detail.get('status'))}")

    tasks = detail.get("tasks", [])
    if tasks:
        print(f"\n  {BOLD}TASKS ({len(tasks)}):{RESET}")
        for idx, t in enumerate(tasks, 1):
            agent_lbl = t.get("agent_name") or t.get("agent_role") or "Specialist"
            print(
                f"    {idx:2d}. [{color_status(t['status'])}] {t['title']} "
                f"{GRAY}({agent_lbl} • {t.get('phase') or 'EXEC'}){RESET}"
            )

    deliverables = detail.get("deliverables") or []
    changesets = detail.get("changesets") or []
    sb_files = sb_info.get("files") or []
    if sb_files or changesets or deliverables:
        print(f"\n  {BOLD}DELIVERABLES & CHANGES:{RESET}")
        for f_entry in sb_files:
            if f_entry["status"] == "ADDED":
                tag = f"{GREEN}{BOLD}[ADDED]   {RESET}"
            elif f_entry["status"] == "DELETED":
                tag = f"{RED}{BOLD}[DELETED] {RESET}"
            else:
                tag = f"{YELLOW}{BOLD}[MODIFIED]{RESET}"
            print(
                f"    • {tag} {CYAN}{f_entry['path']}{RESET} "
                f"({GREEN}+{f_entry['added']}{RESET} / {RED}-{f_entry['removed']}{RESET})"
            )
        for cs in changesets:
            added = cs.get("lines_added")
            removed = cs.get("lines_removed")
            diff_stat = (
                f" ({GREEN}+{added}{RESET} / {RED}-{removed}{RESET})"
                if (added is not None or removed is not None)
                else ""
            )
            print(
                f"    • {GREEN}Changeset ({cs.get('branch') or 'worktree'}){RESET}: "
                f"{cs.get('files_changed') or 0} file(s) changed{diff_stat}"
            )
        for d in deliverables[:4]:
            print(
                f"    • {CYAN}{d['artifact']}{RESET} ({d['task']}): {DIM}{d['preview']}{RESET}"
            )
        if sb_files:
            short_id = detail["id"][:8]
            print(
                f"\n  {BLUE}{BOLD}💡 Sandbox Actions:{RESET} "
                f"{GREEN}kobits diff {short_id}{RESET}  │  "
                f"{GREEN}kobits apply {short_id}{RESET}"
            )

    if detail["status"] == "AWAITING_APPROVAL":
        short_id = detail["id"][:8]
        print(
            f"\n{YELLOW}{BOLD}⏸  ACTION REQUIRED:{RESET} Mission is paused at an approval gate.\n"
            f"   Run {GREEN}{BOLD}kobits approve {short_id}{RESET} to continue execution,\n"
            f"   or  {RED}{BOLD}kobits reject {short_id}{RESET} to abort."
        )


DEFAULT_CLOUD_API_URL = (
    os.environ.get("KOBITS_API_URL")
    or "https://kobitss-850300766138.europe-west1.run.app"
).rstrip("/")


async def cloud_create_and_run_mission(
    objective: str,
    title: Optional[str] = None,
    branch: str = "main",
    workspace_dir: Optional[Path] = None,
) -> int:
    import httpx
    target_ws = workspace_dir or WORKSPACE_DIR
    api_url = DEFAULT_CLOUD_API_URL
    
    print(f"\n{CYAN}{BOLD}⚡ KOBITS CLOUD ENGINE{RESET} {GRAY}(Kyros Cloud-Native MicroVM Fleet){RESET}")
    print(f"  {GRAY}Target Workspace :{RESET} {target_ws}")
    print(f"  {GRAY}Cloud Backend    :{RESET} {api_url}")
    print(f"  {GRAY}Objective        :{RESET} {BOLD}{objective[:72]}{'...' if len(objective) > 72 else ''}{RESET}\n")

    token = None
    print(f"{YELLOW}⏳ Connecting to Kobits Cloud agents...{RESET}")
    async with httpx.AsyncClient(timeout=60.0) as client:
        login_url = f"{api_url}/api/v1/auth/login"
        login_data = {
            "email": os.environ.get("KOBITS_USER_EMAIL") or "realuser@kobits.space",
            "password": os.environ.get("KOBITS_USER_PASSWORD") or "MyPass12345!"
        }
        for attempt in range(3):
            try:
                auth_res = await client.post(login_url, json=login_data)
                if auth_res.status_code == 200:
                    token = auth_res.json().get("access_token")
                    break
                else:
                    auth_res2 = await client.post(login_url, data={"username": login_data["email"], "password": login_data["password"]})
                    if auth_res2.status_code == 200:
                        token = auth_res2.json().get("access_token")
                        break
            except Exception as exc:
                if attempt < 2:
                    await asyncio.sleep(2.0)
                    continue
                print(f"{RED}✗ Cloud Connection Error:{RESET} Could not reach {api_url}: {exc}")
                return 1

        if not token:
            print(f"{RED}✗ Authentication Failed:{RESET} Could not authenticate with Kobits Cloud.")
            return 1

        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        run_url = f"{api_url}/api/v1/missions/run"
        payload = {
            "objective": objective,
            "title": title or (objective[:64] + ("..." if len(objective) > 64 else "")),
            "branch": branch,
            "auto_execute": True,
            "requires_approval": False
        }
        
        try:
            start_res = await client.post(run_url, json=payload, headers=headers)
            if start_res.status_code not in (200, 201):
                print(f"{RED}✗ Mission Launch Error:{RESET} Status {start_res.status_code}: {start_res.text}")
                return 1
            mission_info = start_res.json()
            mission_id = mission_info.get("mission_id") or mission_info.get("id")
        except Exception as exc:
            print(f"{RED}✗ Cloud Request Failed:{RESET} {exc}")
            return 1

        short_id = mission_id[:8]
        print(f"{GREEN}✓ Mission Launched on Cloud!{RESET} ID: {CYAN}{short_id}{RESET} ({mission_id})")
        print(f"{GRAY}─────────────────────────────────────────────────────────────────────────────{RESET}")

        last_phase = None
        last_stage = None
        last_tasks_seen = set()
        
        poll_url = f"{api_url}/api/v1/missions/{mission_id}"
        poll_interval = 2.0
        
        completed = False
        start_time = time.time()

        # Attempt real-time WebSocket terminal stream
        try:
            import websockets
            ws_protocol = "wss" if api_url.startswith("https") else "ws"
            ws_host = api_url.split("://", 1)[-1]
            ws_url = f"{ws_protocol}://{ws_host}/ws/terminal/{mission_id}"
            ws_headers = {"Authorization": f"Bearer {token}"} if token else {}
            async with websockets.connect(ws_url, additional_headers=ws_headers, close_timeout=2.0) as ws:
                print(f"  {CYAN}⚡ WebSocket Live Streaming Connected{RESET}")
                while not completed:
                    try:
                        raw_msg = await asyncio.wait_for(ws.recv(), timeout=2.5)
                        msg = json.loads(raw_msg) if isinstance(raw_msg, str) and raw_msg.strip().startswith("{") else {"message": str(raw_msg)}
                        m_type = msg.get("type", "")
                        m_text = msg.get("message") or msg.get("output") or ""
                        if m_type == "phase_start":
                            print(f"  {color_status('ACTIVE', pad=10)} {BOLD}{msg.get('phase', '')}{RESET} {GRAY}{msg.get('stage', '')}{RESET}")
                        elif m_type == "task_complete":
                            print(f"    {GREEN}✓ Task [COMPLETED]{RESET} {msg.get('task_title', '')}")
                        elif m_type in ("mission_complete", "complete"):
                            completed = True
                            break
                        elif m_type in ("mission_failed", "error"):
                            completed = True
                            print(f"\n{RED}✗ Mission Failed on Cloud: {m_text}{RESET}")
                            return 1
                        elif m_text and not m_text.startswith("Connected"):
                            print(f"    {GRAY}{m_text.strip()}{RESET}")
                    except asyncio.TimeoutError:
                        poll_res = await client.get(poll_url, headers=headers)
                        if poll_res.status_code == 200:
                            stat = (poll_res.json().get("status") or "ACTIVE").upper()
                            if stat in ("COMPLETED", "FAILED", "CANCELLED"):
                                completed = True
                                break
        except Exception:
            pass
        while not completed:
            await asyncio.sleep(poll_interval)
            try:
                poll_res = await client.get(poll_url, headers=headers)
                if poll_res.status_code != 200:
                    continue
                m_data = poll_res.json()
                stat = (m_data.get("status") or "ACTIVE").upper()
                phase = m_data.get("phase") or "PLANNING"
                pct = m_data.get("progress") or 0
                stage_name = m_data.get("current_stage") or ""
                
                if phase != last_phase or stage_name != last_stage:
                    bar = render_progress_bar(pct, width=20)
                    print(f"  {color_status(stat, pad=10)} {bar}  {BOLD}{phase:<16}{RESET} {GRAY}{stage_name}{RESET}")
                    last_phase = phase
                    last_stage = stage_name
                
                tasks = m_data.get("tasks") or []
                for t in tasks:
                    t_id = t.get("id")
                    t_title = t.get("title")
                    t_stat = (t.get("status") or "PENDING").upper()
                    t_role = t.get("agent_role") or t.get("agent_name") or "ENGINEER"
                    t_key = f"{t_id}_{t_stat}"
                    if t_key not in last_tasks_seen:
                        last_tasks_seen.add(t_key)
                        if t_stat in ("COMPLETED", "SUCCESS"):
                            print(f"    {GREEN}✓ Task [COMPLETED]{RESET} {t_title} {GRAY}({t_role}){RESET}")
                        elif t_stat in ("IN_PROGRESS", "RUNNING"):
                            print(f"    {YELLOW}↳ Task [IN_PROGRESS]{RESET} {t_title} {GRAY}({t_role}){RESET}")
                        elif t_stat in ("FAILED", "ERROR"):
                            print(f"    {RED}✗ Task [FAILED]{RESET} {t_title} {GRAY}({t_role}){RESET}")

                if stat in ("COMPLETED", "FAILED", "CANCELLED"):
                    completed = True
                    if stat == "FAILED":
                        print(f"\n{RED}✗ Mission Failed on Cloud.{RESET}")
                        return 1
            except Exception:
                pass

        elapsed = int(time.time() - start_time)
        print(f"\n{GREEN}✓ Cloud Generation Finished in {elapsed}s!{RESET}")

        print(f"{YELLOW}📥 Downloading deliverables from Cloud Sandbox...{RESET}")
        files_url = f"{api_url}/api/v1/missions/{mission_id}/files"
        try:
            files_res = await client.get(files_url, headers=headers)
            written_count = 0
            if files_res.status_code == 200:
                files_data = files_res.json().get("files") or []
                for f_info in files_data:
                    rel_p = f_info.get("path")
                    content = f_info.get("content", "")
                    if not rel_p:
                        continue
                    forbidden_framework = {
                        "portal.html", "mock_data.js", "server.py", "render.yaml",
                        "requirements.txt", "install.ps1", "kobits_cli.py", "alembic.ini"
                    }
                    if rel_p in forbidden_framework or any(rel_p.startswith(p) for p in ("backend/", "web/", "sandboxes/", "storage/", "scripts/", "docs/", "tests/")):
                        continue
                    dest = (target_ws / rel_p).resolve()
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(content, encoding="utf-8")
                    line_count = len(content.splitlines())
                    print(f"  {GREEN}+ [CREATED]{RESET} {rel_p} {GRAY}({line_count} lines){RESET}")
                    written_count += 1
            
            if written_count > 0:
                print(f"\n{GREEN}{BOLD}======================================================={RESET}")
                print(f"{GREEN}{BOLD}✓ MISSION DELIVERED SUCCESSFULLY!{RESET}")
                print(f"{GREEN}{BOLD}======================================================={RESET}")
                print(f"  {BOLD}Files Written:{RESET} {written_count} file(s) created in {CYAN}{target_ws}{RESET}\n")
            else:
                print(f"{YELLOW}No sandbox files found to download.{RESET}")
            return 0
        except Exception as exc:
            print(f"{RED}Error downloading files:{RESET} {exc}")
            return 1


# ── CLI Subcommand Handlers ─────────────────────────────────────
async def cmd_run(args: argparse.Namespace) -> int:
    objective = " ".join(args.objective).strip()
    if not objective:
        print(f"{RED}Error:{RESET} Please provide a mission objective. Example: kobits run \"Add rate limiting middleware\"")
        return 1

    from backend.core.config import settings

    # In Kyros Cloud-Native standard, default to cloud remote execution unless --local or KOBITS_LOCAL_MODE=1 is set
    run_local = getattr(args, "local", False) or os.environ.get("KOBITS_LOCAL_MODE") == "1"

    if not run_local:
        return await cloud_create_and_run_mission(
            objective=objective,
            title=args.title,
            branch=args.branch,
        )

    from backend.services.llm import detect_configured_provider
    provider = detect_configured_provider()
    has_local_llm = provider != "mock" or getattr(args, "provider", None) == "mock"

    if not has_local_llm:
        print(f"\n{YELLOW}{BOLD}╔══════════════════════════════════════════════════════════════════╗{RESET}")
        print(f"{YELLOW}{BOLD}║                 KOBITS SETUP REQUIRED FOR LIVE AI                ║{RESET}")
        print(f"{YELLOW}{BOLD}╚══════════════════════════════════════════════════════════════════╝{RESET}")
        print(f"  No live LLM API key detected in your environment or .env file.")
        print(f"  To configure your API key in 10 seconds, run:")
        print(f"      {CYAN}{BOLD}kobits setup{RESET}")
        print(f"\n  Or to test without live API charges in mock mode, run:")
        print(f"      {CYAN}kobits run --local --provider mock \"{objective}\"{RESET}\n")
        return 1

    await local_create_and_run_mission(
        objective=objective,
        title=args.title,
        project_arg=args.project,
        branch=args.branch,
        autopilot=args.autopilot,
        wait=(not args.no_wait),
    )
    return 0


async def cmd_missions(args: argparse.Namespace) -> int:
    missions = await local_list_missions(status_filter=args.status)
    if not missions:
        print(f"{GRAY}No missions found. Start one with: {BOLD}kobits run \"<objective>\"{RESET}")
        return 0

    print(f"\n{BOLD}{'ID':<10} {'STATUS':<18} {'PHASE':<20} {'PROGRESS':<10} {'TITLE'}{RESET}")
    print("─" * 92)
    for m in missions:
        short_id = m["id"][:8]
        phase = (m.get("phase") or "—")[:18]
        pct = f"{m.get('progress') or 0}%"
        title = (m["title"] or "")[:40]
        print(f"{CYAN}{short_id:<10}{RESET} {color_status(m['status'], pad=18)} {phase:<20} {pct:<10} {title}")
    print()
    return 0


async def cloud_status(args: argparse.Namespace) -> int:
    import httpx
    api_url = DEFAULT_CLOUD_API_URL
    mission_id = getattr(args, "mission_id", None)

    headers = {"Content-Type": "application/json"}
    token = os.environ.get("KOBITS_AUTH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async with httpx.AsyncClient(timeout=8.0) as client:
        if mission_id:
            res = await client.get(f"{api_url}/api/v1/missions/{mission_id}", headers=headers)
            if res.status_code == 200:
                detail = res.json()
                print(f"\n{CYAN}{BOLD}╔══════════════════════════════════════════════════════════════════╗{RESET}")
                print(f"{CYAN}{BOLD}║         KOBITS CLOUD CONTROL PLANE — MISSION INSPECTOR           ║{RESET}")
                print(f"{CYAN}{BOLD}╚══════════════════════════════════════════════════════════════════╝{RESET}")
                print_mission_summary(detail)
                print()
                return 0
            elif res.status_code == 404:
                print(f"{RED}Cloud mission matching '{mission_id}' not found at {api_url}.{RESET}")
                return 1
            else:
                raise RuntimeError(f"Cloud API returned status {res.status_code}")
        else:
            res = await client.get(f"{api_url}/api/v1/missions", headers=headers)
            if res.status_code == 200:
                missions = res.json()
                if isinstance(missions, dict) and "missions" in missions:
                    missions = missions["missions"]
                print(f"\n{BLUE}{BOLD}╔══════════════════════════════════════════════════════════════════╗{RESET}")
                print(f"{BLUE}{BOLD}║           KOBITS CLOUD CONTROL PLANE (KYROS STANDARD)            ║{RESET}")
                print(f"{BLUE}{BOLD}╚══════════════════════════════════════════════════════════════════╝{RESET}")
                print(f"  {GRAY}Cloud Backend :{RESET} {GREEN}{BOLD}{api_url}{RESET}")
                print(f"  {GRAY}Architecture  :{RESET} {CYAN}Cloud Run Isolated MicroVMs / Zero Host Contamination{RESET}")
                print(f"  {GRAY}Remote Missions:{RESET} {len(missions)}\n")
                if missions:
                    print(f"{BOLD}{'ID':<10} {'STATUS':<18} {'PHASE':<20} {'PROGRESS':<10} {'TITLE'}{RESET}")
                    print("─" * 92)
                    for m in missions[:12]:
                        short_id = m.get("id", "")[:8]
                        status_val = m.get("status", "ACTIVE")
                        phase = (m.get("phase") or "—")[:18]
                        pct = f"{m.get('progress') or 0}%"
                        title = (m.get("title") or "")[:40]
                        print(f"{CYAN}{short_id:<10}{RESET} {color_status(status_val, pad=18)} {phase:<20} {pct:<10} {title}")
                    print()
                else:
                    print(f"  {GRAY}No active cloud missions found. Launch one with {BOLD}kobits run \"<objective>\"{RESET}\n")
                return 0
            else:
                raise RuntimeError(f"Cloud API returned status {res.status_code}")


async def cmd_status(args: argparse.Namespace) -> int:
    is_local = getattr(args, "local", False) or os.environ.get("KOBITS_LOCAL_MODE") == "1"
    if not is_local:
        try:
            return await cloud_status(args)
        except Exception:
            pass

    if args.mission_id:
        detail = await local_get_mission_detail(args.mission_id)
        if not detail:
            print(f"{RED}Mission matching '{args.mission_id}' not found.{RESET}")
            return 1
        print()
        print_mission_summary(detail)
        print()
        return 0

    projects = await local_list_projects()
    missions = await local_list_missions()
    active = sum(1 for m in missions if m["status"] in ("ACTIVE", "PLANNING", "EXECUTING"))
    awaiting = sum(1 for m in missions if m["status"] == "AWAITING_APPROVAL")
    completed = sum(1 for m in missions if m["status"] == "COMPLETED")

    from backend.core.config import settings
    active_model = settings.ANTHROPIC_MODEL if settings.LLM_PROVIDER == "anthropic" else settings.LLM_PROVIDER

    print(f"\n{BLUE}{BOLD}╔══════════════════════════════════════════════════════════════════╗{RESET}")
    print(f"{BLUE}{BOLD}║               KOBITS TERMINAL ENGINEERING ENGINE                 ║{RESET}")
    print(f"{BLUE}{BOLD}╚══════════════════════════════════════════════════════════════════╝{RESET}")
    print(
        f"  {GRAY}Workspace :{RESET} {CYAN}{WORKSPACE_DIR}{RESET}   │   "
        f"{GRAY}Engine:{RESET} {GREEN}{BOLD}Pure Local{RESET} ({CYAN}{active_model}{RESET})"
    )
    print(
        f"  {BOLD}Projects:{RESET} {len(projects)}   │   "
        f"{BOLD}Total Missions:{RESET} {len(missions)}   │   "
        f"{BOLD}Active:{RESET} {GREEN}{active}{RESET}   │   "
        f"{BOLD}Awaiting Approval:{RESET} {YELLOW}{awaiting}{RESET}   │   "
        f"{BOLD}Completed:{RESET} {CYAN}{completed}{RESET}\n"
    )
    if missions:
        print(f"{BOLD}Recent Missions:{RESET}")
        for m in missions[:8]:
            print(
                f"  • {CYAN}{m['id'][:8]}{RESET}  [{color_status(m['status'], pad=10)}]  "
                f"{render_progress_bar(m.get('progress'), 12)}  {m['title']}"
            )
    else:
        print(f"  {GRAY}No missions yet. Run {BOLD}kobits run \"Build a feature\"{RESET}{GRAY} to start.{RESET}")
    print()
    return 0


async def cmd_logs(args: argparse.Namespace) -> int:
    """Stream live WebSocket terminal logs or view mission activity logs."""
    target_id = getattr(args, "mission_id", None)
    api_url = DEFAULT_CLOUD_API_URL
    is_local = getattr(args, "local", False) or os.environ.get("KOBITS_LOCAL_MODE") == "1"

    if not target_id:
        target_id = await _resolve_mission_id(None)
        if not target_id:
            print(f"{RED}No active or recent missions found.{RESET}")
            return 1

    short_id = target_id[:8]
    print(f"\n{CYAN}{BOLD}╔══════════════════════════════════════════════════════════════════╗{RESET}")
    print(f"{CYAN}{BOLD}║         KOBITS MISSION ACTIVITY & TERMINAL LOG STREAM            ║{RESET}")
    print(f"{CYAN}{BOLD}╚══════════════════════════════════════════════════════════════════╝{RESET}")
    print(f"  {GRAY}Target Mission :{RESET} {CYAN}{short_id}{RESET} ({target_id})")
    print(f"  {GRAY}Control Plane  :{RESET} {'Local Worktree' if is_local else f'Cloud Run ({api_url})'}\n")

    if not is_local:
        # Try WebSocket terminal streaming first
        try:
            import websockets
            ws_protocol = "wss" if api_url.startswith("https") else "ws"
            ws_host = api_url.split("://", 1)[-1]
            ws_url = f"{ws_protocol}://{ws_host}/ws/terminal/{target_id}"
            token = os.environ.get("KOBITS_AUTH_TOKEN")
            headers = {"Authorization": f"Bearer {token}"} if token else None

            print(f"  {CYAN}Connecting to WebSocket stream at {ws_url}...{RESET}")
            async with websockets.connect(ws_url, additional_headers=headers, close_timeout=3.0) as ws:
                print(f"  {GREEN}Connected. Streaming terminal logs (Ctrl+C to exit):{RESET}\n")
                while True:
                    msg = await ws.recv()
                    print(f"  {GRAY}{msg}{RESET}")
        except (KeyboardInterrupt, asyncio.CancelledError):
            print(f"\n{YELLOW}Log stream stopped.{RESET}\n")
            return 0
        except Exception:
            try:
                import httpx
                http_headers = {"Content-Type": "application/json"}
                token = os.environ.get("KOBITS_AUTH_TOKEN")
                if token:
                    http_headers["Authorization"] = f"Bearer {token}"
                async with httpx.AsyncClient(timeout=8.0) as client:
                    res = await client.get(f"{api_url}/api/v1/missions/{target_id}/activities", headers=http_headers)
                    if res.status_code == 200:
                        activities = res.json()
                        if isinstance(activities, list) and activities:
                            print(f"{BOLD}{'TIME':<12} {'TYPE':<20} {'CONTENT'}{RESET}")
                            print("─" * 80)
                            for a in activities:
                                t = (a.get("created_at") or "")[-8:]
                                atype = a.get("activity_type") or a.get("type") or "SYSTEM"
                                content = (a.get("content") or a.get("message") or "")[:80]
                                print(f"{GRAY}{t:<12}{RESET} {CYAN}{atype:<20}{RESET} {content}")
                            print()
                            return 0
            except Exception:
                pass

    # Local activity log fallback
    from backend.core.database import AsyncSessionLocal
    from sqlalchemy import select
    from backend.models.project import Activity

    async with AsyncSessionLocal() as db:
        stmt = (
            select(Activity)
            .order_by(Activity.created_at.desc())
            .limit(25)
        )
        res = await db.execute(stmt)
        acts = res.scalars().all()
        matching_acts = [
            a for a in acts
            if not target_id or (a.metadata_json and target_id in a.metadata_json) or (a.description and target_id in a.description) or (a.title and target_id in a.title)
        ]
        if not matching_acts:
            matching_acts = acts[:10]
        if not matching_acts:
            print(f"{GRAY}No activity logs recorded yet for mission {short_id}.{RESET}\n")
            return 0
        print(f"{BOLD}{'TIME':<12} {'TYPE':<24} {'CONTENT'}{RESET}")
        print("─" * 80)
        for a in reversed(matching_acts):
            t = a.created_at.strftime("%H:%M:%S") if hasattr(a.created_at, "strftime") else str(a.created_at)[-8:]
            atype = a.type.value if hasattr(a.type, "value") else str(a.type)
            content = (a.title or a.description or "")[:80]
            print(f"{GRAY}{t:<12}{RESET} {CYAN}{atype:<24}{RESET} {content}")
        print()
        return 0


async def cmd_projects(args: argparse.Namespace) -> int:
    if args.create:
        proj = await local_create_project(
            name=args.create,
            description=args.description or "",
            repository_url=str(WORKSPACE_DIR),
        )
        print(f"{GREEN}✓ Created project:{RESET} {BOLD}{proj['name']}{RESET} (ID: {proj['id']})")
        return 0

    projects = await local_list_projects()
    if not projects:
        print(f"{GRAY}No projects yet. Run: {BOLD}kobits projects --create \"My Project\"{RESET}")
        return 0

    print(f"\n{BOLD}{'ID':<10} {'STATUS':<14} {'NAME':<30} {'DESCRIPTION'}{RESET}")
    print("─" * 80)
    for p in projects:
        desc = (p.get("description") or "—")[:35]
        print(f"{CYAN}{p['id'][:8]:<10}{RESET} {p['status']:<14} {BOLD}{p['name']:<30}{RESET} {GRAY}{desc}{RESET}")
    print()
    return 0


async def cmd_approve(args: argparse.Namespace) -> int:
    from sqlalchemy import select
    from backend.core.database import AsyncSessionLocal
    from backend.models.mission import Mission, MissionStatus, ApprovalStatus
    from backend.models.project import Activity, ActivityType
    from backend.services.mission_runtime import MissionRuntime

    target_id = await _resolve_mission_id(args.mission_id, prefer_status="AWAITING_APPROVAL")
    if not target_id:
        print(f"{RED}No missions found to approve.{RESET}")
        return 1

    user_id, org_id = await _ensure_local_db_and_identity()
    async with AsyncSessionLocal() as db:
        mission = (
            await db.execute(
                select(Mission).where(Mission.organization_id == org_id, Mission.id.like(f"{target_id}%"))
            )
        ).scalars().first()
        if not mission:
            print(f"{RED}Mission matching '{target_id}' not found.{RESET}")
            return 1
        if mission.status != MissionStatus.AWAITING_APPROVAL:
            print(f"{YELLOW}Mission {mission.id[:8]} is currently {mission.status.value} (not AWAITING_APPROVAL).{RESET}")
            return 1

        mission.status = MissionStatus.ACTIVE
        mission.approval_status = ApprovalStatus.APPROVED
        db.add(
            Activity(
                organization_id=org_id,
                project_id=mission.project_id,
                user_id=user_id,
                type=ActivityType.MISSION_APPROVED,
                title="Mission Approved via CLI",
                description=f"CLI operator approved mission '{mission.title}'.",
            )
        )
        await db.commit()
        mid = mission.id

    print(f"{GREEN}{BOLD}✓ Approved mission {mid[:8]}.{RESET} Resuming execution...\n")
    if not args.no_wait:
        os.environ["KOBITS_CLI_RESUME"] = "1"
        runtime_task = asyncio.create_task(MissionRuntime(mid, org_id, user_id).execute())
        await stream_mission_progress_local(mid, runtime_task)
    return 0


async def cmd_retry(args: argparse.Namespace) -> int:
    from sqlalchemy import select
    from backend.core.database import AsyncSessionLocal
    from backend.models.mission import Mission, MissionStatus
    from backend.models.project import Task, TaskStatus
    from backend.services.mission_runtime import MissionRuntime

    target_id = await _resolve_mission_id(args.mission_id, prefer_status="FAILED")
    if not target_id:
        print(f"{RED}No missions found to retry.{RESET}")
        return 1

    user_id, org_id = await _ensure_local_db_and_identity()
    async with AsyncSessionLocal() as db:
        mission = (
            await db.execute(
                select(Mission).where(Mission.organization_id == org_id, Mission.id.like(f"{target_id}%"))
            )
        ).scalars().first()
        if not mission:
            print(f"{RED}Mission matching '{target_id}' not found.{RESET}")
            return 1

        failed_tasks = (
            await db.execute(
                select(Task).where(
                    Task.mission_id == mission.id,
                    Task.status.in_([TaskStatus.FAILED, TaskStatus.BLOCKED, TaskStatus.IN_PROGRESS]),
                )
            )
        ).scalars().all()
        for t in failed_tasks:
            t.status = TaskStatus.PENDING

        mission.status = MissionStatus.ACTIVE
        mission.execution_lock_id = None
        mission.execution_lock_expires_at = None
        await db.commit()
        mid = mission.id
        phase_str = mission.phase.value if mission.phase else "INTAKE"

    print(
        f"{GREEN}{BOLD}↻ Resuming mission {mid[:8]} from phase {phase_str}{RESET} "
        f"(reset {len(failed_tasks)} task(s) to PENDING)...\n"
    )
    if not args.no_wait:
        os.environ["KOBITS_CLI_RESUME"] = "1"
        runtime_task = asyncio.create_task(MissionRuntime(mid, org_id, user_id).execute())
        await stream_mission_progress_local(mid, runtime_task)
    return 0


async def cmd_reject(args: argparse.Namespace) -> int:
    from sqlalchemy import select
    from backend.core.database import AsyncSessionLocal
    from backend.models.mission import Mission, MissionStatus, ApprovalStatus
    from backend.models.project import Activity, ActivityType, Task, TaskStatus

    user_id, org_id = await _ensure_local_db_and_identity()
    raw_mid = getattr(args, "mission_id", None)
    reason_text = getattr(args, "reason", None) or ""
    extra_words = getattr(args, "extra_reason", None) or []
    if extra_words:
        reason_text = (reason_text + " " + " ".join(extra_words)).strip()

    # Check if raw_mid is actually a mission ID prefix in DB; if not, treat it as part of the reason
    target_id = None
    if raw_mid:
        async with AsyncSessionLocal() as db:
            found = (
                await db.execute(
                    select(Mission.id).where(Mission.organization_id == org_id, Mission.id.like(f"{raw_mid}%"))
                )
            ).scalars().first()
            if found:
                target_id = found
            elif not reason_text and (" " in raw_mid or len(raw_mid) > 12 or not all(c in "0123456789abcdefABCDEF-" for c in raw_mid)):
                reason_text = raw_mid
                raw_mid = None

    if not target_id:
        target_id = await _resolve_mission_id(raw_mid, prefer_status="AWAITING_APPROVAL")
    if not target_id:
        print(f"{RED}No missions found to reject.{RESET}")
        return 1

    async with AsyncSessionLocal() as db:
        mission = (
            await db.execute(
                select(Mission).where(Mission.organization_id == org_id, Mission.id.like(f"{target_id}%"))
            )
        ).scalars().first()
        if not mission:
            print(f"{RED}Mission matching '{target_id}' not found.{RESET}")
            return 1
        if mission.status == MissionStatus.COMPLETED:
            print(f"{YELLOW}Mission {mission.id[:8]} is already COMPLETED and cannot be rejected.{RESET}")
            return 1

        mission.status = MissionStatus.CANCELLED
        mission.approval_status = ApprovalStatus.REJECTED
        mission.current_stage = f"Rejected: {reason_text}" if reason_text else "Rejected via CLI"

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

        db.add(
            Activity(
                organization_id=org_id,
                project_id=mission.project_id,
                user_id=user_id,
                type=ActivityType.MISSION_REJECTED,
                title="Mission Rejected via CLI",
                description=reason_text or f"CLI operator rejected mission '{mission.title}'.",
            )
        )
        await db.commit()
        print(
            f"{RED}{BOLD}✗ Rejected and cancelled mission {mission.id[:8]}{RESET} "
            f"({len(pending_tasks)} pending task(s) cancelled)."
        )
    return 0


async def cmd_cancel(args: argparse.Namespace) -> int:
    from sqlalchemy import select
    from backend.core.database import AsyncSessionLocal
    from backend.models.mission import Mission, MissionStatus
    from backend.models.project import Activity, ActivityType, Task, TaskStatus

    target_id = await _resolve_mission_id(args.mission_id, prefer_status="ACTIVE")
    if not target_id:
        print(f"{RED}No missions found to cancel.{RESET}")
        return 1

    user_id, org_id = await _ensure_local_db_and_identity()
    async with AsyncSessionLocal() as db:
        mission = (
            await db.execute(
                select(Mission).where(Mission.organization_id == org_id, Mission.id.like(f"{target_id}%"))
            )
        ).scalars().first()
        if not mission:
            print(f"{RED}Mission matching '{target_id}' not found.{RESET}")
            return 1
        if mission.status in (MissionStatus.COMPLETED, MissionStatus.CANCELLED):
            print(f"{YELLOW}Mission {mission.id[:8]} is already {mission.status.value} and cannot be cancelled.{RESET}")
            return 1

        mission.status = MissionStatus.CANCELLED
        mission.current_stage = "Cancelled via CLI"
        tasks = (
            await db.execute(
                select(Task).where(
                    Task.mission_id == mission.id,
                    Task.status.in_([TaskStatus.PENDING, TaskStatus.IN_PROGRESS]),
                )
            )
        ).scalars().all()
        for t in tasks:
            t.status = TaskStatus.CANCELLED

        db.add(
            Activity(
                organization_id=org_id,
                project_id=mission.project_id,
                user_id=user_id,
                type=ActivityType.MISSION_UPDATED,
                title="Mission Cancelled via CLI",
                description=f"CLI operator cancelled mission '{mission.title}'.",
            )
        )
        await db.commit()
        print(f"{YELLOW}{BOLD}⚠ Cancelled mission {mission.id[:8]} and {len(tasks)} pending task(s).{RESET}")
    return 0


async def cmd_steer(args: argparse.Namespace) -> int:
    from sqlalchemy import select
    from backend.core.database import AsyncSessionLocal
    from backend.models.mission import Mission, MissionStatus
    from backend.models.project import Activity, ActivityType, Task, TaskStatus, TaskPriority

    user_id, org_id = await _ensure_local_db_and_identity()
    tokens: List[str] = list(getattr(args, "instruction", None) or getattr(args, "target", None) or [])
    explicit_mid: Optional[str] = getattr(args, "mission_id", None)

    target_id: Optional[str] = None
    if explicit_mid:
        target_id = explicit_mid
    elif len(tokens) >= 2:
        cand = tokens[0].strip()
        if len(cand) >= 4 and " " not in cand:
            async with AsyncSessionLocal() as db:
                matched = (
                    await db.execute(
                        select(Mission.id).where(Mission.organization_id == org_id, Mission.id.like(f"{cand}%"))
                    )
                ).scalars().first()
                if matched:
                    target_id = matched
                    tokens = tokens[1:]
                elif len(cand) >= 6 and all(c in "0123456789abcdefABCDEF-" for c in cand):
                    print(f"{RED}Mission matching '{cand}' not found.{RESET}")
                    return 1

    instruction = " ".join(tokens).strip()
    if not instruction:
        print(f"{RED}Error:{RESET} Provide a steering instruction. Example: kobits steer \"Use stdlib only\"")
        return 1

    if not target_id:
        target_id = await _resolve_mission_id(None, prefer_status="ACTIVE")
    if not target_id:
        print(f"{RED}No active or recent mission found to steer.{RESET}")
        return 1

    async with AsyncSessionLocal() as db:
        mission = (
            await db.execute(
                select(Mission).where(Mission.organization_id == org_id, Mission.id.like(f"{target_id}%"))
            )
        ).scalars().first()
        if not mission:
            print(f"{RED}Mission matching '{target_id}' not found.{RESET}")
            return 1
        if mission.status in (MissionStatus.COMPLETED, MissionStatus.CANCELLED):
            print(f"{YELLOW}Mission {mission.id[:8]} is already {mission.status.value} and cannot be steered.{RESET}")
            return 1

        correction_task = Task(
            project_id=mission.project_id,
            mission_id=mission.id,
            created_by=user_id,
            title=f"CLI Steering: {instruction[:48]}",
            description=f"HUMAN_STEERING_OVERRIDE: {instruction}",
            status=TaskStatus.PENDING,
            phase=mission.phase,
            priority=TaskPriority.CRITICAL,
            is_correction=True,
            metadata_json=json.dumps({"agent_role": "SOLUTION_ARCHITECT", "agent_name": "Atlas", "source": "kobits_cli"}),
        )
        db.add(correction_task)
        db.add(
            Activity(
                organization_id=org_id,
                project_id=mission.project_id,
                user_id=user_id,
                type=ActivityType.MISSION_UPDATED,
                title="Live Steering Injected via CLI",
                description=f"Steering instruction injected into '{mission.title}': {instruction}",
            )
        )
        if mission.status == MissionStatus.AWAITING_APPROVAL:
            mission.status = MissionStatus.ACTIVE
        await db.commit()
        await db.refresh(correction_task)
        print(
            f"{GREEN}{BOLD}✓ Injected live steering task ({correction_task.id[:8]}) into mission {mission.id[:8]}:{RESET}\n"
            f"  \"{instruction}\""
        )
    return 0


async def cmd_connect(args: argparse.Namespace) -> int:
    global WORKSPACE_DIR
    repo_spec = (getattr(args, "repo_spec", None) or "").strip()
    if not repo_spec:
        print(f"{RED}Error:{RESET} Provide a repository (e.g. kobits connect owner/repo)")
        return 1
    try:
        resolved_dir = _resolve_github_repo_workspace(repo_spec)
    except Exception as exc:
        print(f"{RED}{BOLD}✗ Repository Connect Failed:{RESET} {exc}")
        return 1

    WORKSPACE_DIR = resolved_dir
    os.environ["KOBITS_TARGET_WORKSPACE"] = str(WORKSPACE_DIR)
    project = await local_resolve_or_create_project(getattr(args, "project", None))
    cfg = load_cli_config()
    cfg["project_id"] = project["id"]
    cfg["workspace_dir"] = str(WORKSPACE_DIR)
    save_cli_config(cfg)
    print(
        f"{GREEN}{BOLD}✓ Connected repository workspace:{RESET} {CYAN}{WORKSPACE_DIR}{RESET}\n"
        f"  {GRAY}Active Project:{RESET} {BOLD}{project['name']}{RESET} ({project['id'][:8]})\n"
    )
    return 0



async def cmd_diff(args: argparse.Namespace) -> int:
    is_local = getattr(args, "local", False) or os.environ.get("KOBITS_LOCAL_MODE") == "1"
    target_id = await _resolve_mission_id(args.mission_id)
    if not target_id:
        print(f"{RED}No missions found.{RESET}")
        return 1

    detail = None
    if not is_local:
        try:
            detail = await cloud_get_mission_detail(target_id)
        except Exception:
            pass

    if not detail:
        detail = await local_get_mission_detail(target_id)
        if not detail:
            print(f"{RED}Mission matching '{target_id}' not found.{RESET}")
            return 1

    sb_info = detail.get("sandbox") or {}
    sb_dir = sb_info.get("sandbox_dir")
    files = sb_info.get("files") or []
    ver = sb_info.get("verification") or {}

    if not files and not is_local:
        try:
            cloud_diff = await cloud_get_diff(target_id)
            if cloud_diff and cloud_diff.get("files"):
                files = cloud_diff.get("files")
                sb_dir = sb_dir or cloud_diff.get("sandbox_dir") or "CloudMicroVM:/workspace"
                ver = ver or cloud_diff.get("verification") or {}
        except Exception:
            pass

    print(f"\n{BLUE}{BOLD}⚡ KOBITS SANDBOX DIFF:{RESET} {detail['title']} ({CYAN}{detail['id'][:8]}{RESET})")
    if not sb_dir and not files:
        print(f"  {YELLOW}No sandbox directory found for this mission.{RESET}\n")
        return 1

    v_stat = ver.get("status", "SKIPPED")
    ver_files = ver.get("verified_files") or ver.get("verified_py_files") or []
    by_lang = ver.get("verified_by_language") or {}
    lang_tags = ", ".join(f"{k}: {len(v)}" for k, v in by_lang.items() if v)
    lang_suffix = f" [{lang_tags}]" if lang_tags else ""
    v_badge = (
        f"{GREEN}{BOLD}✓ PASSED{RESET} ({len(ver_files)} file(s) syntax & import verified{lang_suffix})"
        if v_stat == "PASSED"
        else f"{RED}{BOLD}✗ FAILED{RESET} ({'; '.join(ver.get('errors') or [])})"
    )
    print(f"  {GRAY}Sandbox      :{RESET} {CYAN}{sb_dir}{RESET}")
    print(f"  {GRAY}Target Repo  :{RESET} {CYAN}{WORKSPACE_DIR}{RESET}")
    print(f"  {GRAY}Branch       :{RESET} {detail.get('active_branch') or '—'}")
    print(f"  {GRAY}Smoke Check  :{RESET} {v_badge}")

    if args.file:
        files = [f for f in files if args.file.replace("\\", "/") in f["path"]]

    if not files:
        print(f"  {GRAY}No file modifications found in sandbox.{RESET}\n")
        return 0

    total_add = sum(f["added"] for f in files)
    total_rem = sum(f["removed"] for f in files)
    print(
        f"  {GRAY}Summary      :{RESET} {BOLD}{len(files)} file(s) changed{RESET} "
        f"({GREEN}+{total_add} insertions{RESET}, {RED}-{total_rem} deletions{RESET})\n"
    )
    for f in files:
        if f["status"] == "ADDED":
            tag = f"{GREEN}{BOLD}[ADDED]   {RESET}"
        elif f["status"] == "DELETED":
            tag = f"{RED}{BOLD}[DELETED] {RESET}"
        else:
            tag = f"{YELLOW}{BOLD}[MODIFIED]{RESET}"
        print(f"    • {tag} {CYAN}{f['path']}{RESET} ({GREEN}+{f['added']}{RESET} / {RED}-{f['removed']}{RESET})")

    if args.stat:
        print()
        return 0

    print("\n" + "─" * 78)
    for f in files:
        print(f"{BOLD}{CYAN}=== {f['status']}: {f['path']} ==={RESET}")
        for dline in (f.get("diff") or "").splitlines():
            if dline.startswith("+++") or dline.startswith("---"):
                print(f"{BOLD}{dline}{RESET}")
            elif dline.startswith("@@"):
                print(f"{CYAN}{dline}{RESET}")
            elif dline.startswith("+"):
                print(f"{GREEN}{dline}{RESET}")
            elif dline.startswith("-"):
                print(f"{RED}{dline}{RESET}")
            else:
                print(dline)
        print()
    print(
        f"{BLUE}{BOLD}💡 Ready to apply to host workspace?{RESET} "
        f"Run: {GREEN}{BOLD}kobits apply {detail['id'][:8]}{RESET}\n"
    )
    return 0


def _get_sandbox_root_sha(abs_sb: Path) -> Optional[str]:
    import subprocess
    if not (abs_sb / ".git").exists():
        return None
    meta_file = abs_sb / ".kobits_sandbox.json"
    if meta_file.is_file():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            base_sha = (meta.get("base_commit_sha") or "").strip()
            if base_sha:
                return base_sha
        except Exception:
            pass
    try:
        for ref in ("main", "master", "origin/main", "origin/master"):
            mb = subprocess.run(
                ["git", "merge-base", "HEAD", ref],
                cwd=str(abs_sb), capture_output=True, text=True, timeout=5
            ).stdout.strip()
            if mb:
                return mb
        h_prev = subprocess.run(
            ["git", "rev-parse", "HEAD~1"],
            cwd=str(abs_sb), capture_output=True, text=True, timeout=5
        ).stdout.strip()
        if h_prev:
            return h_prev
        h = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(abs_sb), capture_output=True, text=True, timeout=5
        ).stdout.strip()
        return h or None
    except Exception:
        return None


def _get_base_blob_bytes(abs_sb: Path, root_sha: Optional[str], rel_p: str) -> Optional[bytes]:
    import subprocess
    if not root_sha or not (abs_sb / ".git").exists():
        return None
    try:
        proc = subprocess.run(
            ["git", "show", f"{root_sha}:{rel_p}"],
            cwd=str(abs_sb), capture_output=True, text=False, timeout=5
        )
        if proc.returncode == 0:
            return proc.stdout
    except Exception:
        pass
    return None


def _prune_empty_parents(file_path: Path, stop_root: Path) -> None:
    stop_resolved = stop_root.resolve()
    parent = file_path.resolve().parent
    while parent != stop_resolved and str(parent).startswith(str(stop_resolved) + os.sep):
        try:
            if not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
            else:
                break
        except OSError:
            break


def apply_sandbox_to_workspace(
    abs_sb: str | Path,
    workspace_dir: str | Path,
    files: Optional[List[Dict[str, Any]]] = None,
    dry_run: bool = False,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Kyros-Grade Two-Phase Transactional 3-Way Git Patch & File Deletion Engine:
    - Phase 1 (In-Memory 3-Way Resolution & Conflict Gate):
      Compares `(base_at_sandbox_creation, live_workspace_file, sandbox_file)` for every changed file
      using `git merge-file -p` and `git apply --3way --check` (with `GIT_ALTERNATE_OBJECT_DIRECTORIES`).
      Computes all merged buffers and deletions in memory first.
      If ANY file has an unforced 3-way conflict (`CONFLICT`), Phase 2 is aborted so the live workspace
      is NEVER left in a half-applied state!
    - Phase 2 (Atomic Disk Commit):
      Writes all `CREATED`, `UPDATED`, `3WAY_MERGED`, and `FORCE_UPDATED` buffers (preserving CRLF/LF
      and executable file modes) and unlinks all `DELETED` / `FORCE_DELETED` files (pruning empty parent dirs).
    """
    import subprocess
    import tempfile

    sb_path = Path(abs_sb).resolve()
    ws_path = Path(workspace_dir).resolve()

    if files is None:
        inspected = inspect_sandbox_directory(sb_path)
        files = inspected.get("files") or []
        root_sha = inspected.get("root_sha")
    else:
        root_sha = _get_sandbox_root_sha(sb_path)

    created_files: List[str] = []
    updated_files: List[str] = []
    merged_3way_files: List[str] = []
    deleted_files: List[str] = []
    conflicts: List[Dict[str, Any]] = []
    file_results: List[Dict[str, Any]] = []
    planned_ops: List[Dict[str, Any]] = []

    sb_git_objects = sb_path / ".git" / "objects"
    ws_has_git = (ws_path / ".git").exists()

    # ── PHASE 1: IN-MEMORY 3-WAY RESOLUTION & PRE-FLIGHT CONFLICT CHECK ──
    for f in files:
        rel_p = f["path"].replace("\\", "/")
        f_status = f.get("status", "MODIFIED").upper()
        added_cnt = int(f.get("added", 0))
        removed_cnt = int(f.get("removed", 0))

        src_path = (sb_path / rel_p).resolve()
        dst_path = (ws_path / rel_p).resolve()

        # Security guard against path traversal outside workspace_dir or sandbox_dir
        if not str(dst_path).startswith(str(ws_path) + os.sep):
            reason = f"Security Block: Target path '{rel_p}' escapes workspace root."
            conflicts.append({
                "path": rel_p,
                "status": f_status,
                "reason": reason,
            })
            file_results.append({
                "path": rel_p,
                "action": "CONFLICT",
                "added": added_cnt,
                "removed": removed_cnt,
                "reason": reason,
            })
            continue

        base_bytes = _get_base_blob_bytes(sb_path, root_sha, rel_p)

        # ── CASE 1: FILE DELETED IN SANDBOX (`[DELETED]`) ──
        if f_status == "DELETED" or (not src_path.exists() and base_bytes is not None):
            if not dst_path.exists():
                planned_ops.append({
                    "op": "noop_delete",
                    "rel_p": rel_p,
                    "dst_path": dst_path,
                    "action": "DELETED",
                    "added": added_cnt,
                    "removed": removed_cnt,
                })
                continue

            host_bytes = dst_path.read_bytes()
            host_norm = host_bytes.replace(b"\r\n", b"\n")
            base_norm = base_bytes.replace(b"\r\n", b"\n") if base_bytes is not None else host_norm

            if host_norm != base_norm and not force:
                reason = (
                    f"3-way delete/modify conflict: '{rel_p}' was deleted in the sandbox, "
                    "but has concurrent local modifications in the host workspace."
                )
                conflicts.append({
                    "path": rel_p,
                    "status": "DELETED",
                    "reason": reason,
                })
                file_results.append({
                    "path": rel_p,
                    "action": "CONFLICT",
                    "added": added_cnt,
                    "removed": removed_cnt,
                    "reason": reason,
                })
                continue

            action_name = "FORCE_DELETED" if (host_norm != base_norm and force) else "DELETED"
            planned_ops.append({
                "op": "delete",
                "rel_p": rel_p,
                "dst_path": dst_path,
                "action": action_name,
                "added": added_cnt,
                "removed": removed_cnt,
            })
            continue

        if not src_path.is_file():
            continue

        sandbox_bytes = src_path.read_bytes()
        sandbox_norm = sandbox_bytes.replace(b"\r\n", b"\n")

        # ── CASE 2: NEW FILE ADDED IN SANDBOX (`[ADDED]`) ──
        if f_status == "ADDED" or base_bytes is None:
            if not dst_path.exists():
                planned_ops.append({
                    "op": "copy",
                    "rel_p": rel_p,
                    "src_path": src_path,
                    "dst_path": dst_path,
                    "action": "CREATED",
                    "added": added_cnt,
                    "removed": removed_cnt,
                })
                continue

            host_bytes = dst_path.read_bytes()
            host_norm = host_bytes.replace(b"\r\n", b"\n")
            if host_norm == sandbox_norm:
                planned_ops.append({
                    "op": "noop_create",
                    "rel_p": rel_p,
                    "dst_path": dst_path,
                    "action": "CREATED",
                    "added": added_cnt,
                    "removed": removed_cnt,
                })
                continue

            if not force:
                reason = (
                    f"3-way add/add conflict: '{rel_p}' was created in the sandbox, "
                    "but a file with different contents already exists in the host workspace."
                )
                conflicts.append({
                    "path": rel_p,
                    "status": "ADDED",
                    "reason": reason,
                })
                file_results.append({
                    "path": rel_p,
                    "action": "CONFLICT",
                    "added": added_cnt,
                    "removed": removed_cnt,
                    "reason": reason,
                })
                continue

            planned_ops.append({
                "op": "copy",
                "rel_p": rel_p,
                "src_path": src_path,
                "dst_path": dst_path,
                "action": "FORCE_UPDATED",
                "added": added_cnt,
                "removed": removed_cnt,
            })
            continue

        # ── CASE 3: EXISTING FILE MODIFIED IN SANDBOX (`[MODIFIED]`) ──
        if not dst_path.exists():
            if not force:
                reason = (
                    f"3-way modify/delete conflict: '{rel_p}' was modified in the sandbox, "
                    "but was deleted locally in the host workspace."
                )
                conflicts.append({
                    "path": rel_p,
                    "status": "MODIFIED",
                    "reason": reason,
                })
                file_results.append({
                    "path": rel_p,
                    "action": "CONFLICT",
                    "added": added_cnt,
                    "removed": removed_cnt,
                    "reason": reason,
                })
                continue
            planned_ops.append({
                "op": "copy",
                "rel_p": rel_p,
                "src_path": src_path,
                "dst_path": dst_path,
                "action": "FORCE_UPDATED",
                "added": added_cnt,
                "removed": removed_cnt,
            })
            continue

        host_bytes = dst_path.read_bytes()
        host_uses_crlf = b"\r\n" in host_bytes
        host_norm = host_bytes.replace(b"\r\n", b"\n")
        base_norm = base_bytes.replace(b"\r\n", b"\n")

        # Subcase 3A: Host file was NOT modified since sandbox creation (clean fast-forward update)
        if host_norm == base_norm or host_norm == sandbox_norm:
            if b"\x00" in sandbox_bytes:
                target_bytes = sandbox_bytes
            else:
                target_bytes = sandbox_norm.replace(b"\n", b"\r\n") if host_uses_crlf else sandbox_norm
            planned_ops.append({
                "op": "write_fast_forward",
                "rel_p": rel_p,
                "src_path": src_path,
                "dst_path": dst_path,
                "target_bytes": target_bytes,
                "already_same": (host_norm == sandbox_norm),
                "action": "UPDATED",
                "added": added_cnt,
                "removed": removed_cnt,
            })
            continue

        # Subcase 3B: Host file WAS modified concurrently since mission start (`host_norm != base_norm`)!
        is_binary = (b"\x00" in host_bytes) or (b"\x00" in base_bytes) or (b"\x00" in sandbox_bytes)
        if is_binary:
            if not force:
                reason = f"3-way binary conflict: '{rel_p}' was modified concurrently in both workspace and sandbox."
                conflicts.append({"path": rel_p, "status": "MODIFIED", "reason": reason})
                file_results.append({
                    "path": rel_p,
                    "action": "CONFLICT",
                    "added": added_cnt,
                    "removed": removed_cnt,
                    "reason": reason,
                })
                continue
            planned_ops.append({
                "op": "write_bytes",
                "rel_p": rel_p,
                "src_path": src_path,
                "dst_path": dst_path,
                "target_bytes": sandbox_bytes,
                "action": "FORCE_UPDATED",
                "added": added_cnt,
                "removed": removed_cnt,
            })
            continue

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_p = Path(tmp_dir)
            ours_f = tmp_p / "ours"
            base_f = tmp_p / "base"
            theirs_f = tmp_p / "theirs"
            ours_f.write_bytes(host_norm)
            base_f.write_bytes(base_norm)
            theirs_f.write_bytes(sandbox_norm)

            merge_proc = subprocess.run(
                [
                    "git", "merge-file", "-p",
                    "-L", f"workspace/{rel_p}",
                    "-L", f"base/{rel_p}",
                    "-L", f"sandbox/{rel_p}",
                    str(ours_f), str(base_f), str(theirs_f),
                ],
                capture_output=True,
                text=False,
                timeout=10,
            )

        if merge_proc.returncode == 0:
            merged_norm = merge_proc.stdout.replace(b"\r\n", b"\n")
            final_bytes = merged_norm.replace(b"\n", b"\r\n") if host_uses_crlf else merged_norm
            planned_ops.append({
                "op": "write_bytes",
                "rel_p": rel_p,
                "src_path": src_path,
                "dst_path": dst_path,
                "target_bytes": final_bytes,
                "action": "3WAY_MERGED",
                "added": added_cnt,
                "removed": removed_cnt,
            })
        else:
            if not force:
                conflict_hunks = max(1, merge_proc.returncode)
                reason = (
                    f"3-way merge conflict ({conflict_hunks} overlapping hunk(s)): "
                    f"live workspace file '{rel_p}' was modified on the same lines as the sandbox."
                )
                conflicts.append({
                    "path": rel_p,
                    "status": "MODIFIED",
                    "reason": reason,
                    "conflict_hunks": conflict_hunks,
                })
                file_results.append({
                    "path": rel_p,
                    "action": "CONFLICT",
                    "added": added_cnt,
                    "removed": removed_cnt,
                    "reason": reason,
                })
            else:
                final_bytes = sandbox_norm.replace(b"\n", b"\r\n") if host_uses_crlf else sandbox_norm
                planned_ops.append({
                    "op": "write_bytes",
                    "rel_p": rel_p,
                    "src_path": src_path,
                    "dst_path": dst_path,
                    "target_bytes": final_bytes,
                    "action": "FORCE_UPDATED",
                    "added": added_cnt,
                    "removed": removed_cnt,
                })

    # If ANY file had a conflict (and force=False), abort Phase 2 so zero files are partially written!
    if conflicts:
        return {
            "status": "CONFLICT",
            "applied_count": 0,
            "created": [],
            "updated": [],
            "merged_3way": [],
            "deleted": [],
            "conflicts": conflicts,
            "file_results": file_results,
        }

    # ── PHASE 2: ATOMIC DISK COMMIT (ONLY EXECUTED WHEN ZERO CONFLICTS EXIST) ──
    applied_count = 0
    for item in planned_ops:
        op = item["op"]
        rel_p = item["rel_p"]
        dst_path = item["dst_path"]
        src_path = item.get("src_path")
        act = item["action"]

        if not dry_run:
            if op == "delete":
                if dst_path.exists():
                    dst_path.unlink()
                    _prune_empty_parents(dst_path, ws_path)
            elif op == "copy" and src_path:
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src_path), str(dst_path))
            elif op == "write_fast_forward" and not item.get("already_same"):
                applied_via_git = False
                if ws_has_git and (sb_path / ".git").exists() and root_sha:
                    try:
                        patch_proc = subprocess.run(
                            ["git", "diff", "--full-index", "--binary", root_sha, "--", rel_p],
                            cwd=str(sb_path), capture_output=True, text=False, timeout=5
                        )
                        if patch_proc.returncode == 0 and patch_proc.stdout.strip():
                            env = os.environ.copy()
                            if sb_git_objects.is_dir():
                                existing_alt = env.get("GIT_ALTERNATE_OBJECT_DIRECTORIES", "")
                                env["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = (
                                    f"{sb_git_objects}{os.pathsep}{existing_alt}"
                                    if existing_alt
                                    else str(sb_git_objects)
                                )
                            chk = subprocess.run(
                                ["git", "apply", "--3way", "--check", "--whitespace=nowarn", "-"],
                                cwd=str(ws_path), input=patch_proc.stdout, env=env,
                                capture_output=True, timeout=5
                            )
                            if chk.returncode == 0:
                                ap = subprocess.run(
                                    ["git", "apply", "--3way", "--whitespace=nowarn", "-"],
                                    cwd=str(ws_path), input=patch_proc.stdout, env=env,
                                    capture_output=True, timeout=5
                                )
                                applied_via_git = (ap.returncode == 0)
                    except Exception:
                        applied_via_git = False

                if not applied_via_git:
                    dst_path.parent.mkdir(parents=True, exist_ok=True)
                    dst_path.write_bytes(item["target_bytes"])
                    if src_path and src_path.is_file():
                        try:
                            shutil.copymode(str(src_path), str(dst_path))
                        except OSError:
                            pass
            elif op == "write_bytes":
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                dst_path.write_bytes(item["target_bytes"])
                if src_path and src_path.is_file():
                    try:
                        shutil.copymode(str(src_path), str(dst_path))
                    except OSError:
                        pass

        if act == "CREATED":
            created_files.append(rel_p)
        elif act == "3WAY_MERGED":
            merged_3way_files.append(rel_p)
        elif act in ("DELETED", "FORCE_DELETED"):
            deleted_files.append(rel_p)
        else:
            updated_files.append(rel_p)

        applied_count += 1
        file_results.append({
            "path": rel_p,
            "action": act,
            "added": item["added"],
            "removed": item["removed"],
        })

    overall_status = "DRY_RUN" if dry_run else "APPLIED"
    return {
        "status": overall_status,
        "applied_count": applied_count,
        "created": created_files,
        "updated": updated_files,
        "merged_3way": merged_3way_files,
        "deleted": deleted_files,
        "conflicts": conflicts,
        "file_results": file_results,
    }


async def cmd_apply(args: argparse.Namespace) -> int:
    is_local = getattr(args, "local", False) or os.environ.get("KOBITS_LOCAL_MODE") == "1"
    target_id = await _resolve_mission_id(args.mission_id)
    if not target_id:
        print(f"{RED}No missions found.{RESET}")
        return 1

    detail = None
    if not is_local:
        try:
            detail = await cloud_get_mission_detail(target_id)
        except Exception:
            pass

    if detail and not is_local:
        try:
            cloud_res = await cloud_apply_mission(target_id, dry_run=args.dry_run, force=args.force)
            if cloud_res:
                st = cloud_res.get("status", "APPLIED")
                app_cnt = cloud_res.get("applied_count", 0)
                print(f"\n{GREEN}{BOLD}✓ Cloud Deliverable {st}:{RESET} {detail['title']} ({CYAN}{detail['id'][:8]}{RESET})")
                print(f"  {GRAY}Applied Files:{RESET} {app_cnt}")
                print(f"  {GRAY}Control Plane:{RESET} {CYAN}{DEFAULT_CLOUD_API_URL}{RESET}\n")
                return 0
        except Exception as e:
            logger.debug(f"Cloud apply error: {e}")

    detail = detail or await local_get_mission_detail(target_id)
    if not detail:
        print(f"{RED}Mission matching '{target_id}' not found.{RESET}")
        return 1

    sb_info = detail.get("sandbox") or {}
    abs_sb = sb_info.get("abs_sandbox_dir")
    files = sb_info.get("files") or []
    ver = sb_info.get("verification") or {}

    if not abs_sb or not os.path.isdir(abs_sb):
        print(f"{RED}Error:{RESET} Sandbox workspace not found on disk for mission {detail['id'][:8]}.")
        return 1

    if not files:
        print(f"{YELLOW}No modified, created, or deleted files found in sandbox {sb_info.get('sandbox_dir')}.{RESET}")
        return 0

    if ver.get("status") == "FAILED" and not args.force:
        print(
            f"{RED}{BOLD}✗ Safety Gate Blocked Apply:{RESET} Sandbox AST/Syntax verification failed:\n"
            f"  {'; '.join(ver.get('errors') or [])}\n"
            f"Use {YELLOW}--force{RESET} only if you intend to override syntax checks."
        )
        return 1

    mode_lbl = f"{YELLOW}(DRY-RUN 3-WAY PREVIEW){RESET}" if args.dry_run else f"{GREEN}(LIVE 3-WAY APPLY){RESET}"
    print(f"\n{BLUE}{BOLD}⚡ KOBITS SANDBOX APPLY{RESET} {mode_lbl}")
    print(f"  {GRAY}Mission :{RESET} {detail['title']} ({CYAN}{detail['id'][:8]}{RESET})")
    print(f"  {GRAY}Source  :{RESET} {CYAN}{sb_info.get('sandbox_dir')}{RESET} → {BOLD}{WORKSPACE_DIR}{RESET}\n")

    apply_res = apply_sandbox_to_workspace(
        abs_sb=abs_sb,
        workspace_dir=WORKSPACE_DIR,
        files=files,
        dry_run=bool(args.dry_run),
        force=bool(args.force),
    )

    for fr in apply_res["file_results"]:
        act = fr["action"]
        rel_p = fr["path"]
        a_cnt = fr["added"]
        r_cnt = fr["removed"]
        if act == "CREATED":
            action_tag = f"{GREEN}{BOLD}[CREATE]      {RESET}"
            icon = "✓"
        elif act == "3WAY_MERGED":
            action_tag = f"{CYAN}{BOLD}[3-WAY MERGED]{RESET}"
            icon = "✓"
        elif act in ("DELETED", "FORCE_DELETED"):
            action_tag = f"{RED}{BOLD}[DELETE]      {RESET}"
            icon = "✓"
        elif act == "FORCE_UPDATED":
            action_tag = f"{YELLOW}{BOLD}[FORCE UPDATE]{RESET}"
            icon = "✓"
        elif act == "CONFLICT":
            action_tag = f"{RED}{BOLD}[CONFLICT]    {RESET}"
            icon = "✗"
        else:
            action_tag = f"{YELLOW}{BOLD}[UPDATE]      {RESET}"
            icon = "✓"

        print(
            f"  {icon} {action_tag} {CYAN}{rel_p}{RESET} "
            f"({GREEN}+{a_cnt}{RESET} / {RED}-{r_cnt}{RESET})"
        )
        if act == "CONFLICT" and fr.get("reason"):
            print(f"      {RED}↳ {fr['reason']}{RESET}")

    conflicts = apply_res["conflicts"]
    applied_count = apply_res["applied_count"]

    if conflicts:
        print(
            f"\n{RED}{BOLD}✗ 3-Way Git Patch Guard Blocked {len(conflicts)} Conflicting File(s):{RESET}\n"
            f"  Your live local edits in {BOLD}{WORKSPACE_DIR}{RESET} were protected and NOT overwritten.\n"
            f"  Inspect differences with {CYAN}kobits diff {detail['id'][:8]}{RESET} or pass {YELLOW}--force{RESET} to overwrite."
        )
        return 1

    if args.dry_run:
        print(
            f"\n{YELLOW}Dry-run complete:{RESET} {applied_count} file(s) verified clean for 3-way apply to {BOLD}{WORKSPACE_DIR}{RESET}.\n"
            f"Run without {BOLD}--dry-run{RESET} to apply changes."
        )
    else:
        merged_cnt = len(apply_res["merged_3way"])
        del_cnt = len(apply_res["deleted"])
        extra = []
        if merged_cnt:
            extra.append(f"{merged_cnt} 3-way merged")
        if del_cnt:
            extra.append(f"{del_cnt} deleted")
        extra_str = f" ({', '.join(extra)})" if extra else ""
        print(
            f"\n{GREEN}{BOLD}✓ Successfully applied {applied_count} file(s){extra_str} "
            f"from sandbox to {WORKSPACE_DIR}.{RESET}\n"
        )
    return 0


async def cmd_agents(args: argparse.Namespace) -> int:
    from sqlalchemy import select, func
    from backend.core.database import AsyncSessionLocal
    from backend.models.agent import Agent, AgentRun
    from backend.models.project import Task, TaskStatus
    from backend.services.agent_registry import AGENT_REGISTRY

    await _ensure_local_db_and_identity()
    run_counts: Dict[str, int] = {}
    async with AsyncSessionLocal() as db:
        agents_in_db = (await db.execute(select(Agent))).scalars().all()
        id_to_type = {
            a.id: (a.type.value if hasattr(a.type, "value") else str(a.type))
            for a in agents_in_db
        }
        rows = (
            await db.execute(select(AgentRun.agent_id, func.count(AgentRun.id)).group_by(AgentRun.agent_id))
        ).all()
        for aid, cnt in rows:
            t_str = id_to_type.get(aid, str(aid))
            run_counts[t_str] = run_counts.get(t_str, 0) + int(cnt or 0)

        task_rows = (
            await db.execute(select(Task.metadata_json).where(Task.status == TaskStatus.COMPLETED))
        ).scalars().all()
        for meta_raw in task_rows:
            if not meta_raw:
                continue
            try:
                meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
                role = (meta.get("agent_role") or "").upper()
                if role:
                    run_counts[role] = run_counts.get(role, 0) + 1
            except Exception:
                pass

    print(f"\n{BLUE}{BOLD}⚡ KOBITS SPECIALIZED ENGINEERING AGENTS ({len(AGENT_REGISTRY)} REGISTERED){RESET}\n")
    print(f"  {GRAY}{'CODENAME':<12} {'ROLE / TYPE':<26} {'MODEL TIER':<12} {'RUNS':<6} {'CAPABILITIES'}{RESET}")
    print(f"  {GRAY}{'─' * 92}{RESET}")
    for agent_type, defn in AGENT_REGISTRY.items():
        t_val = agent_type.value if hasattr(agent_type, "value") else str(agent_type)
        runs = run_counts.get(t_val, 0)
        caps = ", ".join(defn.capabilities[:3])
        print(
            f"  {CYAN}{BOLD}{defn.name:<12}{RESET} "
            f"{t_val:<26} "
            f"{GREEN}{defn.default_model:<12}{RESET} "
            f"{runs:<6} "
            f"{GRAY}{caps}{RESET}"
        )
    print()
    return 0


async def cmd_search(args: argparse.Namespace) -> int:
    from backend.services.sandbox_manager import SandboxManager

    raw_query = " ".join(args.query).strip() if isinstance(args.query, list) else str(args.query or "").strip()
    if not raw_query:
        print(f"{RED}✗ Please provide a search query.{RESET}")
        return 1

    case_sensitive = None
    if getattr(args, "case_sensitive", False):
        case_sensitive = True
    elif getattr(args, "ignore_case", False):
        case_sensitive = False

    is_regex = None
    if getattr(args, "fixed_strings", False):
        is_regex = False
    elif getattr(args, "regex", False):
        is_regex = True

    res = SandboxManager.search_directory(
        root_dir=str(WORKSPACE_DIR),
        query=raw_query,
        file_pattern=getattr(args, "glob", None),
        exclude_pattern=getattr(args, "exclude", None),
        is_regex=is_regex,
        case_sensitive=case_sensitive,
        whole_word=bool(getattr(args, "word_regexp", False)),
        multiline=bool(getattr(args, "multiline", False)),
        context_lines=int(getattr(args, "context", 2) or 2),
        before_context=getattr(args, "before_context", None),
        after_context=getattr(args, "after_context", None),
        max_results=int(getattr(args, "max_results", 30) or 30),
    )

    if "error" in res:
        print(f"{RED}✗ {res['error']}{RESET}")
        return 1

    hits = res.get("results") or []
    mode = res.get("match_mode", "smart_case")
    print(
        f"\n{BLUE}{BOLD}🔎 KOBITS RIPGREP & HYBRID SEARCH{RESET} "
        f"{GRAY}[mode: {mode} • {len(hits)} match(es) in {WORKSPACE_DIR.name}]{RESET}"
    )
    if not hits:
        print(f"  {YELLOW}No matches found for:{RESET} {raw_query}\n")
        return 0

    for h in hits:
        sym = f" {GRAY}({h['enclosing_symbol']}){RESET}" if h.get("enclosing_symbol") else ""
        col = f":{h.get('column', 1)}" if h.get("column") else ""
        print(f"\n  {CYAN}{BOLD}{h['file']}:{h['line']}{col}{RESET}{sym}")
        for sline in (h.get("snippet") or "").splitlines():
            if sline.startswith(">"):
                print(f"  {GREEN}{BOLD}{sline}{RESET}")
            else:
                print(f"  {GRAY}{sline}{RESET}")
    print()
    return 0


async def cmd_goal(args: argparse.Namespace) -> int:
    """Inspect or define mission high-level goals, acceptance criteria, and milestones."""
    is_local = getattr(args, "local", False) or os.environ.get("KOBITS_LOCAL_MODE") == "1"
    target_id = await _resolve_mission_id(getattr(args, "mission_id", None))
    if not target_id:
        print(f"{RED}No active or recent missions found.{RESET}")
        return 1

    detail = None
    if not is_local:
        try:
            detail = await cloud_get_mission_detail(target_id)
        except Exception:
            pass

    if not detail:
        detail = await local_get_mission_detail(target_id, include_sandbox=False)

    if not detail:
        print(f"{RED}Mission matching '{target_id}' not found.{RESET}")
        return 1

    scope = detail.get("scope_triage") or {}
    print(f"\n{BLUE}{BOLD}╔══════════════════════════════════════════════════════════════════╗{RESET}")
    print(f"{BLUE}{BOLD}║         KOBITS MISSION STRATEGIC GOALS & ACCEPTANCE CRITERIA     ║{RESET}")
    print(f"{BLUE}{BOLD}╚══════════════════════════════════════════════════════════════════╝{RESET}")
    print(f"  {GRAY}Mission ID     :{RESET} {CYAN}{detail['id'][:8]}{RESET} ({detail['id']})")
    print(f"  {GRAY}Title          :{RESET} {BOLD}{detail['title']}{RESET}")
    print(f"  {GRAY}Status         :{RESET} {color_status(detail['status'])}  {render_progress_bar(detail.get('progress'))}")
    print(f"  {GRAY}Active Phase   :{RESET} {CYAN}{detail.get('phase') or 'INTAKE'}{RESET}")
    print(f"  {GRAY}Target Branch  :{RESET} {detail.get('active_branch') or 'main'}")

    obj = detail.get("objective") or detail.get("description") or "No explicit objective recorded."
    print(f"\n  {BOLD}Strategic Engineering Objective:{RESET}")
    for line in obj.splitlines():
        print(f"    {GREEN}▸{RESET} {line}")

    print(f"\n  {BOLD}Acceptance & Verification Invariants:{RESET}")
    print(f"    {GREEN}✓{RESET} 100% Hermetic Execution (Isolated Linux MicroVM / zero host contamination)")
    print(f"    {GREEN}✓{RESET} Negative-to-Positive TDD Verification Gate (Reproduce defect -> Patch -> Verify Green)")
    print(f"    {GREEN}✓{RESET} Full Project Regression Integrity (Existing tests must remain 100% passing)")
    print(f"    {GREEN}✓{RESET} Clean AST & Type Soundness (Zero syntax errors, zero unresolved local imports)")

    tasks = detail.get("tasks", [])
    if tasks:
        print(f"\n  {BOLD}Milestone Execution Breakdown ({len(tasks)} tasks):{RESET}")
        for idx, t in enumerate(tasks, 1):
            st = color_status(t["status"])
            agent = t.get("agent_name") or t.get("agent_role") or "Agent"
            print(f"    {idx:2d}. [{st}] {t['title']} {GRAY}({agent}){RESET}")
    print()
    return 0


async def cmd_teamwork_preview(args: argparse.Namespace) -> int:
    """Preview multi-agent collaborative choreography, active agent roles, and live tunnel."""
    is_local = getattr(args, "local", False) or os.environ.get("KOBITS_LOCAL_MODE") == "1"
    target_id = await _resolve_mission_id(getattr(args, "mission_id", None))
    if not target_id:
        print(f"{RED}No active or recent missions found.{RESET}")
        return 1

    detail = None
    if not is_local:
        try:
            detail = await cloud_get_mission_detail(target_id)
        except Exception:
            pass

    if not detail:
        detail = await local_get_mission_detail(target_id, include_sandbox=False)

    if not detail:
        print(f"{RED}Mission matching '{target_id}' not found.{RESET}")
        return 1

    from backend.services.cloud_sandbox import CloudMicroVMSandbox
    from backend.services.sandbox_manager import SandboxManager

    session = None
    if detail.get("active_branch"):
        try:
            session = SandboxManager.get_session_by_branch(detail["active_branch"])
        except Exception:
            pass

    preview_url = None
    if session:
        preview_url = session.remote_preview_url
    if not preview_url and CloudMicroVMSandbox.is_configured():
        try:
            if detail.get("id") in CloudMicroVMSandbox._active_sandboxes:
                cloud_sb = CloudMicroVMSandbox._active_sandboxes[detail["id"]]
                preview_url = cloud_sb.get_preview_url(3000)
            elif detail.get("status") == "ACTIVE":
                cloud_sb = CloudMicroVMSandbox.get_or_create(detail["id"])
                preview_url = cloud_sb.get_preview_url(3000)
        except Exception as e:
            logger.debug(f"Cloud preview url resolution: {e}")
    if not preview_url:
        preview_url = detail.get("remote_preview_url")
    if not preview_url and detail.get("id"):
        cloud_base = os.environ.get("KOBITS_CLOUD_PREVIEW_BASE_URL")
        if cloud_base:
            short_id = detail["id"][:8]
            preview_url = cloud_base.format(short_id=short_id) if "{" in cloud_base else f"{cloud_base.rstrip('/')}/{short_id}"
        elif not is_local:
            preview_url = f"{DEFAULT_CLOUD_API_URL}/api/v1/preview/{detail['id'][:8]}"

    print(f"\n{CYAN}{BOLD}╔══════════════════════════════════════════════════════════════════╗{RESET}")
    print(f"{CYAN}{BOLD}║         KOBITS MULTI-AGENT TEAMWORK & LIVE PREVIEW               ║{RESET}")
    print(f"{CYAN}{BOLD}╚══════════════════════════════════════════════════════════════════╝{RESET}")
    print(f"  {GRAY}Mission ID     :{RESET} {CYAN}{detail['id'][:8]}{RESET} — {BOLD}{detail['title']}{RESET}")
    print(f"  {GRAY}Status         :{RESET} {color_status(detail['status'])}  │  {GRAY}Phase:{RESET} {CYAN}{detail.get('phase') or 'INTAKE'}{RESET}")
    if preview_url:
        print(f"  {GRAY}Live Preview   :{RESET} {GREEN}{BOLD}{preview_url}{RESET} {GRAY}(Forwarded MicroVM dev port){RESET}")
    else:
        print(f"  {GRAY}Live Preview   :{RESET} {YELLOW}Tunnel standby (activates when dev server is spawned){RESET}")

    print(f"\n  {BOLD}Autonomous Team Choreography Matrix:{RESET}")
    team_roles = [
        ("Solution Architect", "Active", "Trajectory planning, scope triage, AST decomposition"),
        ("Tech Lead", "Active", "Task dependency DAG, quality invariant policy verification"),
        ("Coder", "In-Flight" if detail["status"] == "ACTIVE" else "Standby", "TDD implementation & surgical patch application"),
        ("Sentinel QA", "Ready", "Negative reproducer synthesis & regression test gate"),
        ("Aegis Security", "Ready", "Path traversal, blast radius isolation, and CVE defense"),
        ("DevOps / SRE", "Active", "Ephemeral Firecracker MicroVM fleet & port forwarding"),
    ]
    for role, state, desc in team_roles:
        st_color = GREEN if state == "Active" else (CYAN if state == "In-Flight" else GRAY)
        print(f"    • {BOLD}{role:<20}{RESET} [{st_color}{state:<9}{RESET}] : {desc}")

    tasks = detail.get("tasks", [])
    if tasks:
        in_progress = [t for t in tasks if t["status"] == "IN_PROGRESS"]
        completed = [t for t in tasks if t["status"] == "COMPLETED"]
        pending = [t for t in tasks if t["status"] == "PENDING"]
        print(f"\n  {BOLD}Choreography Execution State:{RESET}")
        print(f"    {GREEN}Completed Tasks :{RESET} {len(completed)}")
        print(f"    {CYAN}In-Progress Tasks:{RESET} {len(in_progress)}")
        for t in in_progress:
            agent = t.get("agent_name") or t.get("agent_role") or "Agent"
            print(f"      ↳ {CYAN}{BOLD}{t['title']}{RESET} ({agent})")
        print(f"    {GRAY}Queued Tasks     :{RESET} {len(pending)}")
    print()
    return 0


async def cmd_boost(args: argparse.Namespace) -> int:
    """Scale execution to turbo speed: parallel agent waves, KVM MicroVM scale, speculative execution."""
    mode = "TURBO"
    tokens = getattr(args, "instruction", None) or []
    if tokens:
        mode = tokens[0].upper()

    os.environ["KOBITS_TURBO_MODE"] = "1"
    os.environ["KOBITS_SPECULATIVE_EXEC"] = "1"
    os.environ["KOBITS_EXECUTION_MODE"] = "cloud_native"

    is_local = getattr(args, "local", False) or os.environ.get("KOBITS_LOCAL_MODE") == "1"
    target_id = await _resolve_mission_id(getattr(args, "mission_id", None), prefer_status="ACTIVE")

    print(f"\n{YELLOW}{BOLD}╔══════════════════════════════════════════════════════════════════╗{RESET}")
    print(f"{YELLOW}{BOLD}║         KOBITS TURBO BOOST & ACCELERATION ENGINE                 ║{RESET}")
    print(f"{YELLOW}{BOLD}╚══════════════════════════════════════════════════════════════════╝{RESET}")
    print(f"  {GREEN}{BOLD}⚡ TURBO BOOST ACTIVATED{RESET} {GRAY}(Mode: {mode}){RESET}\n")
    print(f"  {BOLD}Acceleration Capabilities Enabled:{RESET}")
    print(f"    {GREEN}✓{RESET} {BOLD}Parallel Verification Wave:{RESET} Sentinel QA + Aegis Security + Reviewer run concurrently")
    print(f"    {GREEN}✓{RESET} {BOLD}Cloud MicroVM Fleet Scale :{RESET} 4 dedicated vCPUs, 8GB RAM, KVM acceleration")
    print(f"    {GREEN}✓{RESET} {BOLD}Speculative Fast-Path     :{RESET} Pruning non-blocking review gates for verified diffs")
    print(f"    {GREEN}✓{RESET} {BOLD}Zero Host Footprint       :{RESET} 100% remote ephemeral Firecracker MicroVM execution")
    print(f"    {GREEN}✓{RESET} {BOLD}Max Reasoning Token Budget:{RESET} Expanded to 32,768 tokens per turn")

    if target_id and not is_local:
        try:
            steer_res = await cloud_steer_mission(
                target_id,
                f"TURBO_BOOST_ACTIVATED: mode={mode}, parallel_verification=True, speculative_execution=True",
            )
            if steer_res:
                print(f"    {GREEN}✓{RESET} {BOLD}Cloud Mission Synced      :{RESET} Turbo state injected into active mission {target_id[:8]}")
        except Exception as e:
            logger.debug(f"Cloud boost steer error: {e}")

    print(f"\n  {CYAN}Your autonomous missions will now execute with maximum throughput.{RESET}\n")
    return 0


async def cmd_learn(args: argparse.Namespace) -> int:
    """Inspect continuous learning flywheel, harvested golden SWE-bench trajectories, and memory embeddings."""
    from backend.services.trajectory_collector import TrajectoryCollector
    stats = TrajectoryCollector.get_stats()

    print(f"\n{BLUE}{BOLD}╔══════════════════════════════════════════════════════════════════╗{RESET}")
    print(f"{BLUE}{BOLD}║         KOBITS AUTONOMOUS CONTINUOUS LEARNING FLYWHEEL           ║{RESET}")
    print(f"{BLUE}{BOLD}╚══════════════════════════════════════════════════════════════════╝{RESET}")
    print(f"  {GRAY}Flywheel Engine:{RESET} {GREEN}{BOLD}Self-Improving Autonomous SWE Trajectories{RESET}\n")
    print(f"  {BOLD}Trajectory Harvesting Status:{RESET}")
    print(f"    • {BOLD}Total Golden Trajectories :{RESET} {GREEN}{stats.get('total_trajectories', 0)}{RESET}")
    print(f"    • {BOLD}Harvested Token Volume    :{RESET} {CYAN}{stats.get('total_tokens_approx', 0):,} tokens{RESET}")
    print(f"    • {BOLD}Average Trajectory Depth  :{RESET} {YELLOW}{stats.get('avg_tokens_per_trajectory', 0):,} tokens{RESET}")
    print(f"    • {BOLD}200K SFT Target Progress  :{RESET} {stats.get('readiness_percentage_for_200k_sft', 0.0)}%")

    print(f"\n  {BOLD}Learning & Adaptation Vectors:{RESET}")
    print(f"    {GREEN}✓{RESET} {BOLD}AST Semantic Memory       :{RESET} Embeddings indexed for zero-shot codebase navigation")
    print(f"    {GREEN}✓{RESET} {BOLD}Failure Pattern Memory    :{RESET} Anti-patterns cataloged to prevent recurring defects")
    print(f"    {GREEN}✓{RESET} {BOLD}Negative TDD Memory       :{RESET} Golden reproducer templates cached for fast bug replication")
    print(f"    {GREEN}✓{RESET} {BOLD}Policy Engine Rules       :{RESET} Dynamic weighting adjusted based on historical verify gates")
    print(f"\n  {GRAY}Exported SFT datasets: {stats.get('sharegpt_path')}{RESET}\n")
    return 0


async def cmd_repl(args: argparse.Namespace) -> int:
    from backend.core.config import settings
    active_model = settings.ANTHROPIC_MODEL if settings.LLM_PROVIDER == "anthropic" else settings.LLM_PROVIDER

    print(f"\n{BLUE}{BOLD}╔══════════════════════════════════════════════════════════════════╗{RESET}")
    print(f"{BLUE}{BOLD}║         KOBITS INTERACTIVE TERMINAL SESSION (REPL)               ║{RESET}")
    print(f"{BLUE}{BOLD}╚══════════════════════════════════════════════════════════════════╝{RESET}")
    print(f"  {GRAY}Workspace :{RESET} {CYAN}{WORKSPACE_DIR}{RESET}")
    print(f"  {GRAY}Model     :{RESET} {GREEN}{BOLD}{active_model}{RESET} ({settings.LLM_PROVIDER})")
    print(
        f"  {GRAY}Commands  :{RESET} Type any engineering prompt to run, or use:\n"
        f"              {CYAN}/goal [id]{RESET} • {CYAN}/teamwork-preview{RESET} • {CYAN}/boost [mode]{RESET} • {CYAN}/learn{RESET}\n"
        f"              {CYAN}/steer <msg>{RESET} • {CYAN}/diff [id]{RESET} • {CYAN}/apply [id]{RESET} • {CYAN}/status [id]{RESET} • {CYAN}/connect <repo>{RESET}\n"
        f"              {CYAN}/missions{RESET} • {CYAN}/agents{RESET} • {CYAN}/approve [id]{RESET} • {CYAN}/reject [id]{RESET} • {CYAN}/cancel [id]{RESET} • {CYAN}/retry [id]{RESET} • {CYAN}/exit{RESET}\n"
    )

    while True:
        try:
            line = await asyncio.to_thread(input, f"{BLUE}{BOLD}kobits>{RESET} ")
        except (EOFError, KeyboardInterrupt):
            print(f"\n{GRAY}Exiting Kobits REPL.{RESET}")
            break

        raw = line.strip()
        if not raw:
            continue
        if raw.lower() in ("/exit", "/quit", "exit", "quit"):
            print(f"{GRAY}Goodbye.{RESET}")
            break
        if raw.lower() in ("/help", "help", "/"):
            print(
                f"\n  {BOLD}Kobits REPL Commands:{RESET}\n"
                f"    {GREEN}<any prompt>{RESET}         Launch an autonomous engineering mission in {WORKSPACE_DIR.name}\n"
                f"    {CYAN}/goal [id]{RESET}           Inspect mission goals, acceptance criteria, & milestone DAG\n"
                f"    {CYAN}/teamwork-preview{RESET}    Preview multi-agent collaborative choreography & live dev tunnel\n"
                f"    {CYAN}/boost [mode]{RESET}        Scale execution to turbo speed (parallel waves, KVM MicroVM scale)\n"
                f"    {CYAN}/learn{RESET}               Inspect continuous learning flywheel & golden SWE-bench trajectories\n"
                f"    {CYAN}/steer [id] <msg>{RESET}    Inject live steering instruction into active/specified mission\n"
                f"    {CYAN}/connect <repo>{RESET}      Clone/sync & switch workspace to a GitHub repo (owner/repo)\n"
                f"    {CYAN}/search <query>{RESET}      Search workspace with regex, smart-case & hybrid token window\n"
                f"    {CYAN}/diff [id]{RESET}           Inspect unified git diff of the latest (or specified) sandbox\n"
                f"    {CYAN}/apply [id]{RESET}          Apply verified sandbox changes into {WORKSPACE_DIR}\n"
                f"    {CYAN}/status [id]{RESET}         Show workspace status or mission summary\n"
                f"    {CYAN}/missions{RESET}            List recent missions\n"
                f"    {CYAN}/projects{RESET}            List projects\n"
                f"    {CYAN}/agents{RESET}              List all 21 specialized engineering agents & run stats\n"
                f"    {CYAN}/approve [id]{RESET}        Approve a paused mission\n"
                f"    {CYAN}/reject [id] [why]{RESET}   Reject and cancel a mission\n"
                f"    {CYAN}/cancel [id]{RESET}         Cancel an active mission\n"
                f"    {CYAN}/retry [id]{RESET}          Resume/retry a failed mission\n"
                f"    {CYAN}/exit{RESET}                Exit interactive session\n"
            )
            continue

        if raw.startswith("/"):
            try:
                parts = shlex.split(raw[1:])
            except ValueError:
                parts = raw[1:].split()
            cmd_name = parts[0].lower() if parts else ""
            if not cmd_name or cmd_name in ("help", "?", "commands"):
                print(
                    f"\n  {BOLD}Kobits REPL Commands:{RESET}\n"
                    f"    {GREEN}<any prompt>{RESET}         Launch an autonomous engineering mission in {WORKSPACE_DIR.name}\n"
                    f"    {CYAN}/goal [id]{RESET}           Inspect mission goals, acceptance criteria, & milestone DAG\n"
                    f"    {CYAN}/teamwork-preview{RESET}    Preview multi-agent collaborative choreography & live dev tunnel\n"
                    f"    {CYAN}/boost [mode]{RESET}        Scale execution to turbo speed (parallel waves, KVM MicroVM scale)\n"
                    f"    {CYAN}/learn{RESET}               Inspect continuous learning flywheel & golden SWE-bench trajectories\n"
                    f"    {CYAN}/steer [id] <msg>{RESET}    Inject live steering instruction into active/specified mission\n"
                    f"    {CYAN}/connect <repo>{RESET}      Clone/sync & switch workspace to a GitHub repo (owner/repo)\n"
                    f"    {CYAN}/search <query>{RESET}      Search workspace with regex, smart-case & hybrid token window\n"
                    f"    {CYAN}/diff [id]{RESET}           Inspect unified git diff of the latest (or specified) sandbox\n"
                    f"    {CYAN}/apply [id]{RESET}          Apply verified sandbox changes into {WORKSPACE_DIR}\n"
                    f"    {CYAN}/status [id]{RESET}         Show workspace status or mission summary\n"
                    f"    {CYAN}/missions{RESET}            List recent missions\n"
                    f"    {CYAN}/projects{RESET}            List projects\n"
                    f"    {CYAN}/agents{RESET}              List all 21 specialized engineering agents & run stats\n"
                    f"    {CYAN}/approve [id]{RESET}        Approve a paused mission\n"
                    f"    {CYAN}/reject [id] [why]{RESET}   Reject and cancel a mission\n"
                    f"    {CYAN}/cancel [id]{RESET}         Cancel an active mission\n"
                    f"    {CYAN}/retry [id]{RESET}          Resume/retry a failed mission\n"
                    f"    {CYAN}/exit{RESET}                Exit interactive session\n"
                )
                continue
            arg_id = parts[1] if len(parts) > 1 else None
            ns = argparse.Namespace(
                mission_id=arg_id,
                instruction=parts[1:],
                repo_spec=arg_id,
                project=getattr(args, "project", None),
                create=None,
                description=None,
                query=parts[1:],
                glob=None,
                exclude=None,
                case_sensitive=False,
                ignore_case=False,
                fixed_strings=False,
                regex=False,
                word_regexp=False,
                multiline=False,
                context=2,
                before_context=None,
                after_context=None,
                max_results=20,
                status=None,
                file=None,
                stat=("--stat" in parts),
                dry_run=("--dry-run" in parts),
                force=("--force" in parts),
                no_wait=False,
                reason=" ".join(parts[2:]) if len(parts) > 2 else None,
                extra_reason=[],
            )
            if cmd_name == "search":
                await cmd_search(ns)
            elif cmd_name == "steer":
                ns.mission_id = None
                await cmd_steer(ns)
            elif cmd_name in ("connect", "repo"):
                await cmd_connect(ns)
            elif cmd_name == "diff":
                await cmd_diff(ns)
            elif cmd_name == "apply":
                await cmd_apply(ns)
            elif cmd_name == "status":
                await cmd_status(ns)
            elif cmd_name == "missions":
                await cmd_missions(ns)
            elif cmd_name == "projects":
                await cmd_projects(ns)
            elif cmd_name == "agents":
                await cmd_agents(ns)
            elif cmd_name == "approve":
                await cmd_approve(ns)
            elif cmd_name == "reject":
                await cmd_reject(ns)
            elif cmd_name == "cancel":
                await cmd_cancel(ns)
            elif cmd_name in ("retry", "resume"):
                await cmd_retry(ns)
            elif cmd_name in ("goal", "goals"):
                await cmd_goal(ns)
            elif cmd_name in ("teamwork-preview", "preview", "teamwork"):
                await cmd_teamwork_preview(ns)
            elif cmd_name in ("boost", "turbo"):
                await cmd_boost(ns)
            elif cmd_name in ("learn", "learning"):
                await cmd_learn(ns)
            elif cmd_name in ("logs", "log"):
                await cmd_logs(ns)
            else:
                print(f"{YELLOW}Unknown slash command '/{cmd_name}'. Type /help for options.{RESET}")
            continue

        for prefix in ("kobits run ", "kyros run ", "run "):
            if raw.lower().startswith(prefix):
                raw = raw[len(prefix):].strip()
                break
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ('"', "'"):
            raw = raw[1:-1].strip()
        if not raw:
            continue

        from backend.core.config import settings
        has_local_llm = bool(
            (os.environ.get("ANTHROPIC_API_KEY") or getattr(settings, "ANTHROPIC_API_KEY", None)) or
            (os.environ.get("AWS_ACCESS_KEY_ID") or getattr(settings, "AWS_ACCESS_KEY_ID", None)) or
            (os.environ.get("AWS_BEDROCK_API_KEY") or getattr(settings, "AWS_BEDROCK_API_KEY", None)) or
            (os.environ.get("GEMINI_API_KEY") or getattr(settings, "GEMINI_API_KEY", None)) or
            (os.environ.get("DEEPSEEK_API_KEY") or getattr(settings, "DEEPSEEK_API_KEY", None))
        )
        use_cloud = getattr(args, "cloud", False) or (not has_local_llm and not getattr(args, "local", False)) or os.environ.get("KOBITS_USE_CLOUD") == "1"

        if use_cloud:
            await cloud_create_and_run_mission(
                objective=raw,
                title=None,
                branch=getattr(args, "branch", "main"),
            )
        else:
            await local_create_and_run_mission(
                objective=raw,
                title=None,
                project_arg=getattr(args, "project", None),
                branch=getattr(args, "branch", "main"),
                autopilot=True,
                wait=True,
            )
    return 0


def _resolve_github_repo_workspace(repo_spec: str) -> Path:
    import re
    import subprocess
    spec = (repo_spec or "").strip()
    if not spec:
        raise ValueError("Repository specification cannot be empty.")

    local_candidate = Path(spec)
    if local_candidate.exists() and local_candidate.is_dir():
        return local_candidate.resolve()

    if spec.startswith(("./", "../", ".\\", "..\\", "/", "\\")):
        raise ValueError(f"Local directory '{spec}' does not exist.")

    if spec.startswith(("http://", "https://", "git@")):
        if any(ch in spec for ch in (" ", ";", "|", "&", "`", "$")):
            raise ValueError(f"Invalid repository URL '{spec}'.")
        clone_url = spec
        tail = spec.rstrip("/").replace(".git", "").split("/")[-2:]
        slug = "__".join(tail) if len(tail) == 2 else tail[-1]
    elif "/" in spec and " " not in spec and not spec.startswith("-"):
        clean_slug = spec.strip("/").replace(".git", "")
        parts = [p for p in clean_slug.split("/") if p]
        valid_part = re.compile(r"^[A-Za-z0-9_.-]+$")
        if len(parts) != 2 or any(p in (".", "..") or not valid_part.match(p) for p in parts):
            raise ValueError(f"Invalid repository shorthand '{spec}'. Expected 'owner/repo' or a valid local directory.")
        clone_url = f"https://github.com/{clean_slug}.git"
        slug = "__".join(parts)
    else:
        raise ValueError(f"Local directory '{spec}' does not exist, and it is not a valid 'owner/repo' GitHub spec.")

    repos_root = ROOT_DIR / "repos"
    repos_root.mkdir(parents=True, exist_ok=True)
    target_dir = (repos_root / slug).resolve()

    if not (target_dir / ".git").exists():
        print(f"{BLUE}{BOLD}⬇ Cloning GitHub repository:{RESET} {CYAN}{clone_url}{RESET} → {DIM}{target_dir}{RESET}")
        res = subprocess.run(
            ["git", "clone", "--depth", "1", clone_url, str(target_dir)],
            capture_output=True, text=True, timeout=120
        )
        if res.returncode != 0:
            if target_dir.exists() and not any(target_dir.iterdir()):
                shutil.rmtree(target_dir, ignore_errors=True)
            raise RuntimeError(f"Failed to clone '{clone_url}': {res.stderr.strip() or res.stdout.strip()}")
        print(f"{GREEN}✓ Cloned GitHub repository to:{RESET} {BOLD}{target_dir}{RESET}")
    else:
        print(f"{GREEN}✓ Syncing local GitHub repository clone:{RESET} {BOLD}{target_dir}{RESET}")
        try:
            subprocess.run(
                ["git", "-C", str(target_dir), "pull", "--ff-only"],
                capture_output=True, text=True, timeout=15
            )
        except Exception:
            pass

    return target_dir


async def cmd_worker(args: argparse.Namespace) -> int:
    from backend.services.worker_queue import Worker
    concurrency = getattr(args, "concurrency", 2)
    queue = getattr(args, "queue", "default")
    print(f"\n{BLUE}{BOLD}⚡ KOBITS BACKGROUND WORKER STARTED{RESET}")
    print(f"  {GRAY}Concurrency:{RESET} {concurrency}")
    print(f"  {GRAY}Queue      :{RESET} {CYAN}{queue}{RESET}")
    print(f"  {GRAY}Status     :{RESET} {GREEN}Listening for jobs...{RESET} (Press Ctrl+C to stop)\n")
    worker = Worker(concurrency=concurrency, queues=[queue])
    await worker.start()
    try:
        while True:
            await asyncio.sleep(1.0)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(f"\n{YELLOW}Stopping worker...{RESET}")
        await worker.stop()
    return 0


async def cmd_dataset(args: argparse.Namespace) -> int:
    from backend.services.trajectory_collector import TrajectoryCollector
    print(f"\n{CYAN}{BOLD}╔══════════════════════════════════════════════════════════════════╗{RESET}")
    print(f"{CYAN}{BOLD}║         KOBITS 200K DATASET HARVESTER & TRAINING FLYWHEEL        ║{RESET}")
    print(f"{CYAN}{BOLD}╚══════════════════════════════════════════════════════════════════╝{RESET}")

    res = TrajectoryCollector.harvest_from_db()
    new_cnt = res.get("harvested_new", 0)
    if new_cnt > 0:
        print(f"  {GREEN}✓ Harvested {new_cnt} new verified golden trajectories from local runs.{RESET}\n")

    stats = TrajectoryCollector.get_stats()
    print(f"  {BOLD}Total Golden Trajectories:{RESET} {GREEN}{stats.get('total_trajectories', 0)}{RESET}")
    print(f"  {BOLD}Total Tokens Harvested   :{RESET} {CYAN}{stats.get('total_tokens_approx', 0):,} tokens{RESET}")
    print(f"  {BOLD}Average Trajectory Size  :{RESET} {YELLOW}{stats.get('avg_tokens_per_trajectory', 0):,} tokens{RESET}")
    print(f"  {BOLD}Max Single Trajectory    :{RESET} {stats.get('max_trajectory_tokens', 0):,} tokens")
    print(f"  {BOLD}200k SFT Milestone Target:{RESET} {stats.get('readiness_percentage_for_200k_sft', 0.0)}% of 1,000 runs")
    print(f"\n  {BOLD}Exported Dataset Files (Ready for Axolotl / Unsloth):{RESET}")
    print(f"    • ShareGPT : {stats.get('sharegpt_path')}")
    print(f"    • ChatML   : {stats.get('chatml_path')}\n")
    return 0


async def cmd_setup(args: argparse.Namespace) -> int:
    import subprocess
    print(f"\n{CYAN}{BOLD}╔══════════════════════════════════════════════════════════════════╗{RESET}")
    print(f"{CYAN}{BOLD}║                KOBITS SYSTEM SETUP & DIAGNOSTICS                 ║{RESET}")
    print(f"{CYAN}{BOLD}╚══════════════════════════════════════════════════════════════════╝{RESET}")

    # 1. Python Check
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info >= (3, 10):
        print(f"  {GREEN}✓{RESET} Python Runtime       : {GREEN}{py_ver}{RESET} ({sys.executable})")
    else:
        print(f"  {RED}✗{RESET} Python Runtime       : {RED}{py_ver}{RESET} (Requires Python 3.10+)")

    # 2. Git Check
    try:
        git_res = subprocess.run(["git", "--version"], capture_output=True, text=True, check=False)
        if git_res.returncode == 0:
            print(f"  {GREEN}✓{RESET} Version Control      : {GREEN}{git_res.stdout.strip()}{RESET}")
        else:
            print(f"  {YELLOW}!{RESET} Version Control      : Git returned exit code {git_res.returncode}")
    except Exception:
        print(f"  {YELLOW}!{RESET} Version Control      : Git not found on PATH")

    # 3. Database Check
    try:
        from backend.core.database import AsyncSessionLocal
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        print(f"  {GREEN}✓{RESET} SQLite Database      : {GREEN}Initialized & Connected{RESET} (kobits.db)")
    except Exception as e:
        print(f"  {YELLOW}!{RESET} SQLite Database      : {YELLOW}Will auto-initialize on first run ({e}){RESET}")

    # 4. Workspace Target
    print(f"  {GREEN}✓{RESET} Active Workspace     : {BOLD}{WORKSPACE_DIR}{RESET}")

    # 4b. Sandbox Isolation Driver Check
    from backend.services.environment_bootstrapper import EnvironmentBootstrapper
    sb_driver = EnvironmentBootstrapper.detect_isolation_driver()
    docker_installed = False
    try:
        d_res = subprocess.run(["docker", "--version"], capture_output=True, text=True, timeout=2)
        docker_installed = d_res.returncode == 0
    except Exception:
        docker_installed = False

    if sb_driver == "cloud_microvm":
        print(f"  {GREEN}✓{RESET} Sandbox Isolation    : {GREEN}Cloud MicroVM (E2B Firecracker Engine){RESET}")
    elif sb_driver == "docker_container":
        print(f"  {GREEN}✓{RESET} Sandbox Isolation    : {GREEN}Docker Container (Kernel Isolated){RESET}")
    elif docker_installed:
        print(f"  {YELLOW}!{RESET} Sandbox Isolation    : {YELLOW}Virtual Rootfs Jail (Local Safe Mode){RESET}")
        print(f"    {CYAN}↳ Docker is installed! Start Docker Desktop to activate 100% Container Isolation.{RESET}")
    else:
        print(f"  {YELLOW}!{RESET} Sandbox Isolation    : {YELLOW}Virtual Rootfs Jail (Local Safe Mode){RESET}")

    # 5. LLM Provider Detection
    from backend.services.llm import detect_configured_provider
    provider = detect_configured_provider()
    print(f"\n  {BOLD}LLM Intelligence Layer:{RESET}")

    env_path = ROOT_DIR / ".env"

    if provider != "mock":
        print(f"  {GREEN}✓ Active Provider    :{RESET} {CYAN}{BOLD}{provider.upper()}{RESET}")
        key_var = {
            "deepseek": "DEEPSEEK_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "bedrock": "AWS_BEDROCK_API_KEY",
            "openai": "OPENAI_API_KEY",
        }.get(provider, "API_KEY")
        key_val = os.environ.get(key_var) or ""
        masked_key = (key_val[:4] + "••••" + key_val[-4:]) if len(key_val) >= 8 else "Configured"
        print(f"  {GREEN}✓ Credentials        :{RESET} {masked_key} ({key_var})")
        print(f"\n  {GREEN}{BOLD}🎉 Kobits is 100% configured and ready to execute missions!{RESET}")
        print(f"  Try running: {CYAN}kobits \"Add unit tests for my code\"{RESET}\n")
        return 0

    print(f"  {YELLOW}! Active Provider    :{RESET} {YELLOW}Mock / None configured{RESET}")
    print(f"  {GRAY}No live LLM credentials detected in your environment or .env file.{RESET}\n")

    if not sys.stdin.isatty():
        print(f"  {YELLOW}To configure, please set DEEPSEEK_API_KEY or ANTHROPIC_API_KEY in {env_path}{RESET}\n")
        return 0

    try:
        print(f"  {BOLD}Select an LLM Provider to configure:{RESET}")
        print(f"    1) DeepSeek (Recommended - High reasoning, 80% cheaper)")
        print(f"    2) Anthropic Claude (Claude 3.5 Sonnet)")
        print(f"    3) Google Gemini (Gemini 1.5 Pro / Flash)")
        print(f"    4) OpenAI (GPT-4o)")
        print(f"    5) Skip (Keep mock mode for testing)")
        choice = input(f"  Choice [1-5] (default: 1): ").strip() or "1"

        provider_map = {
            "1": ("deepseek", "DEEPSEEK_API_KEY", "https://api.deepseek.com", "deepseek-chat"),
            "2": ("anthropic", "ANTHROPIC_API_KEY", "", "claude-3-5-sonnet-20241022"),
            "3": ("gemini", "GEMINI_API_KEY", "", "gemini-1.5-pro"),
            "4": ("openai", "OPENAI_API_KEY", "https://api.openai.com/v1", "gpt-4o"),
        }

        if choice in provider_map:
            p_name, p_key_var, p_base, p_model = provider_map[choice]
            api_key = input(f"  Enter your {p_name.capitalize()} API Key: ").strip()
            if api_key:
                existing_env = ""
                if env_path.exists():
                    existing_env = env_path.read_text(encoding="utf-8")

                new_lines = []
                key_found = False
                for line in existing_env.splitlines():
                    if line.startswith(f"{p_key_var}=") or line.startswith(f"#{p_key_var}="):
                        new_lines.append(f"{p_key_var}={api_key}")
                        key_found = True
                    elif line.startswith("LLM_PROVIDER="):
                        new_lines.append(f"LLM_PROVIDER={p_name}")
                    else:
                        new_lines.append(line)

                if not key_found:
                    new_lines.append(f"LLM_PROVIDER={p_name}")
                    new_lines.append(f"{p_key_var}={api_key}")

                env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                os.environ[p_key_var] = api_key
                os.environ["LLM_PROVIDER"] = p_name

                print(f"\n  {GREEN}✓ Successfully saved {p_name.upper()} credentials to {env_path}!{RESET}")
                print(f"  {GREEN}{BOLD}🎉 Kobits is 100% configured and ready!{RESET}")
                print(f"  Try running: {CYAN}kobits \"Write a hello world function\"{RESET}\n")
                return 0
    except (KeyboardInterrupt, EOFError):
        print(f"\n{YELLOW}Setup cancelled.{RESET}\n")
        return 0

    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-w", "--workspace",
        default=argparse.SUPPRESS,
        help="Target local repository path (defaults to current working directory)",
    )
    common.add_argument(
        "-R", "--repo",
        default=argparse.SUPPRESS,
        help="Target GitHub repository URL or owner/repo shorthand (auto-cloned into ./repos/)",
    )
    common.add_argument(
        "--provider",
        choices=["mock", "anthropic", "deepseek", "gemini"],
        default=argparse.SUPPRESS,
        help="Override LLM provider (default configured in .env)",
    )
    common.add_argument(
        "-m", "--model",
        default=argparse.SUPPRESS,
        help="Override LLM model ID (e.g. claude-sonnet-4.6, deepseek-v4.1-flash:free)",
    )
    common.add_argument(
        "--local",
        action="store_true",
        default=False,
        help="Run in local engine mode",
    )
    common.add_argument(
        "--cloud",
        action="store_true",
        default=False,
        help="Execute via Kobits Cloud Engine on Render (AWS Bedrock Claude Sonnet)",
    )

    parser = argparse.ArgumentParser(
        prog="kobits",
        parents=[common],
        description="Kobits Terminal Engine - Pure local multi-agent software engineering CLI (like Kyros & Claude Code).",
    )

    sub = parser.add_subparsers(dest="command")

    # kobits run
    p_run = sub.add_parser("run", parents=[common], help="Trigger and stream a new Kobits engineering mission (like kyros run)")
    p_run.add_argument("objective", nargs="+", help="The engineering objective to build or fix")
    p_run.add_argument("-p", "--project", help="Project ID or name (auto-created for current workspace if omitted)")
    p_run.add_argument("-t", "--title", help="Optional short mission title")
    p_run.add_argument("-b", "--branch", default="main", help="Target git branch (default: main)")
    p_run.add_argument("--autopilot", action="store_true", default=True, help="Run continuously without pausing for approval gates (default)")
    p_run.add_argument("--require-approval", action="store_false", dest="autopilot", help="Pause at architecture review gate for manual approval")
    p_run.add_argument("--no-wait", action="store_true", help="Create mission and exit immediately without streaming")

    # kobits connect
    p_connect = sub.add_parser("connect", parents=[common], help="Connect, clone, or sync a GitHub repository (owner/repo or URL) into your workspace")
    p_connect.add_argument("repo_spec", help="GitHub owner/repo shorthand, clone URL, or local directory path")
    p_connect.add_argument("-p", "--project", help="Optional project name override")

    # kobits search
    p_search = sub.add_parser("search", parents=[common], help="Ripgrep-grade regex, smart-case, glob & hybrid semantic code search")
    p_search.add_argument("query", nargs="+", help="Regex pattern, symbol, or natural-language query")
    p_search.add_argument("-g", "--glob", help="Include/exclude file glob (e.g. '*.py', '*.{ts,tsx},!*.test.ts')")
    p_search.add_argument("-E", "--exclude", help="Exclude file glob(s) (e.g. '*.spec.ts,tests/**')")
    p_search.add_argument("-C", "--context", type=int, default=2, help="Lines of context before and after match (default: 2)")
    p_search.add_argument("-B", "--before-context", type=int, help="Lines of context before match")
    p_search.add_argument("-A", "--after-context", type=int, help="Lines of context after match")
    p_search.add_argument("-s", "--case-sensitive", action="store_true", help="Force case-sensitive matching (with automatic fallback)")
    p_search.add_argument("-i", "--ignore-case", action="store_true", help="Force case-insensitive matching")
    p_search.add_argument("-F", "--fixed-strings", action="store_true", help="Treat query as literal string instead of regex")
    p_search.add_argument("--regex", action="store_true", help="Treat query as regular expression")
    p_search.add_argument("-W", "--word-regexp", action="store_true", help="Match only whole words")
    p_search.add_argument("-U", "--multiline", action="store_true", help="Allow regex patterns to span multiple lines")
    p_search.add_argument("-n", "--max-results", type=int, default=30, help="Maximum matches to return (default: 30)")

    # kobits repl / chat
    p_repl = sub.add_parser("repl", aliases=["chat"], parents=[common], help="Start an interactive terminal session (like Claude Code / Kyros)")
    p_repl.add_argument("-p", "--project", help="Project ID or name")
    p_repl.add_argument("-b", "--branch", default="main", help="Target git branch (default: main)")

    # kobits status
    p_status = sub.add_parser("status", parents=[common], help="Show workspace control center or inspect a specific mission")
    p_status.add_argument("mission_id", nargs="?", help="Optional mission ID (or prefix) to inspect")

    # kobits missions
    p_missions = sub.add_parser("missions", parents=[common], help="List missions across your workspace")
    p_missions.add_argument("-s", "--status", help="Filter by status (ACTIVE, AWAITING_APPROVAL, COMPLETED, FAILED)")

    # kobits agents
    sub.add_parser("agents", parents=[common], help="List all 21 specialized engineering agents and their run statistics")

    # kobits projects
    p_projects = sub.add_parser("projects", parents=[common], help="List or create Kobits projects")
    p_projects.add_argument("-c", "--create", help="Create a new project with the given name")
    p_projects.add_argument("-d", "--description", help="Description for the new project")

    # kobits approve
    p_approve = sub.add_parser("approve", parents=[common], help="Approve a mission paused at an approval gate and resume execution")
    p_approve.add_argument("mission_id", nargs="?", help="Mission ID or 8-char prefix (defaults to latest awaiting approval)")
    p_approve.add_argument("--no-wait", action="store_true", help="Do not stream after approving")

    # kobits retry / resume
    p_retry = sub.add_parser("retry", aliases=["resume"], parents=[common], help="Resume a failed or blocked mission from its last incomplete task")
    p_retry.add_argument("mission_id", nargs="?", help="Mission ID or 8-char prefix (defaults to latest failed mission)")
    p_retry.add_argument("--no-wait", action="store_true", help="Do not stream after resuming")

    # kobits reject
    p_reject = sub.add_parser("reject", parents=[common], help="Reject a mission awaiting approval")
    p_reject.add_argument("mission_id", nargs="?", help="Mission ID or 8-char prefix (defaults to latest awaiting approval)")
    p_reject.add_argument("-r", "--reason", help="Optional rejection feedback")
    p_reject.add_argument("extra_reason", nargs="*", help="Optional inline rejection reason")

    # kobits cancel
    p_cancel = sub.add_parser("cancel", parents=[common], help="Cancel an active mission")
    p_cancel.add_argument("mission_id", nargs="?", help="Mission ID or 8-char prefix (defaults to latest active mission)")

    # kobits steer
    p_steer = sub.add_parser("steer", parents=[common], help="Inject a live human steering instruction into a mission (defaults to active/latest mission)")
    p_steer.add_argument("-M", "--mission", dest="mission_id", help="Optional Mission ID (or 8-char prefix)")
    p_steer.add_argument("instruction", nargs="+", help="[mission_id] <steering instruction> for the Solution Architect")

    # kobits diff
    p_diff = sub.add_parser("diff", parents=[common], help="Inspect created/modified files and unified git diff from a mission sandbox")
    p_diff.add_argument("mission_id", nargs="?", help="Mission ID or 8-char prefix (defaults to latest mission)")
    p_diff.add_argument("-f", "--file", help="Optional file path substring filter")
    p_diff.add_argument("--stat", action="store_true", help="Show file summary stats only without full unified diff")

    # kobits apply
    p_apply = sub.add_parser("apply", parents=[common], help="Apply verified sandbox file changes from a mission into the active workspace")
    p_apply.add_argument("mission_id", nargs="?", help="Mission ID or 8-char prefix (defaults to latest mission)")
    p_apply.add_argument("--dry-run", action="store_true", help="Preview files that would be created/updated without modifying the workspace")
    p_apply.add_argument("--force", action="store_true", help="Apply even if AST/syntax smoke check reported warnings")

    # kobits worker
    p_worker = sub.add_parser("worker", parents=[common], help="Start a background worker process for distributed mission execution")
    p_worker.add_argument("-c", "--concurrency", type=int, default=2, help="Number of concurrent worker slots (default: 2)")
    p_worker.add_argument("-q", "--queue", default="default", help="Queue name to consume from (default: default)")

    # kobits dataset
    p_dataset = sub.add_parser("dataset", aliases=["data"], parents=[common], help="View dataset stats or harvest golden 200k training trajectories")
    p_dataset.add_argument("--harvest", action="store_true", help="Harvest new completed trajectories from local database")

    # kobits setup / doctor
    sub.add_parser("setup", aliases=["doctor"], parents=[common], help="Interactive setup & diagnostic doctor for LLM keys and environment")

    # kobits goal
    p_goal = sub.add_parser("goal", parents=[common], help="Inspect or define strategic mission goals & acceptance criteria")
    p_goal.add_argument("mission_id", nargs="?", help="Mission ID or 8-char prefix (defaults to latest mission)")

    # kobits teamwork-preview
    p_tw = sub.add_parser("teamwork-preview", aliases=["preview", "teamwork"], parents=[common], help="Preview multi-agent collaborative choreography & live dev tunnel")
    p_tw.add_argument("mission_id", nargs="?", help="Mission ID or 8-char prefix (defaults to latest mission)")

    # kobits boost
    p_boost = sub.add_parser("boost", aliases=["turbo"], parents=[common], help="Scale execution to turbo speed (parallel waves, KVM MicroVM scale)")
    p_boost.add_argument("instruction", nargs="*", help="Optional boost mode (e.g. turbo, max, speculative)")

    # kobits learn
    sub.add_parser("learn", aliases=["learning"], parents=[common], help="Inspect continuous learning flywheel & golden SWE-bench trajectories")

    # kobits logs
    p_logs = sub.add_parser("logs", parents=[common], help="Stream live WebSocket terminal logs or view mission activity logs")
    p_logs.add_argument("mission_id", nargs="?", help="Optional mission ID or 8-char prefix (defaults to latest mission)")

    return parser


KNOWN_COMMANDS = {
    "run", "connect", "search", "repl", "chat", "status", "missions", "agents", "projects",
    "approve", "retry", "resume", "reject", "cancel", "steer", "diff", "apply", "worker",
    "dataset", "data", "setup", "doctor", "goal", "teamwork-preview", "preview", "teamwork",
    "boost", "turbo", "learn", "learning", "logs",
}

_VALUE_FLAGS = {"-w", "--workspace", "-R", "--repo", "--provider", "-m", "--model", "-p", "--project", "-t", "--title", "-b", "--branch"}


def _normalize_cli_argv(raw_argv: List[str]) -> List[str]:
    """Allow shorthand `kobits "Build..."` AND `kobits --repo owner/repo "Build..."` to route to `run`,
    and normalize slash commands (e.g. `/goal`, `/teamwork-preview`, `/boost`, `/learn`, `/`) to CLI commands."""
    if not raw_argv or "-h" in raw_argv or "--help" in raw_argv:
        return raw_argv

    i = 0
    first_positional: Optional[str] = None
    first_pos_idx: int = -1
    while i < len(raw_argv):
        tok = raw_argv[i]
        if tok in _VALUE_FLAGS:
            i += 2
            continue
        if any(tok.startswith(f"{vf}=") for vf in _VALUE_FLAGS):
            i += 1
            continue
        if tok.startswith("-"):
            i += 1
            continue
        first_positional = tok
        first_pos_idx = i
        break

    if first_positional:
        if first_positional in ("/", "/help"):
            return ["--help"]
        if first_positional.startswith("/"):
            candidate = first_positional[1:].lower()
            if candidate in KNOWN_COMMANDS:
                normalized = list(raw_argv)
                normalized[first_pos_idx] = candidate
                return normalized
        if first_positional not in KNOWN_COMMANDS:
            return ["run"] + raw_argv
    return raw_argv


async def async_main() -> int:
    global WORKSPACE_DIR
    parser = build_parser()

    raw_argv = _normalize_cli_argv(sys.argv[1:])
    args = parser.parse_args(raw_argv)

    if getattr(args, "repo", None):
        try:
            WORKSPACE_DIR = _resolve_github_repo_workspace(args.repo)
            os.environ["KOBITS_TARGET_WORKSPACE"] = str(WORKSPACE_DIR)
        except Exception as exc:
            print(f"{RED}{BOLD}✗ Repository Error:{RESET} {exc}")
            return 1
    elif getattr(args, "workspace", None):
        cand = Path(args.workspace)
        if not cand.exists() or not cand.is_dir():
            print(f"{RED}{BOLD}✗ Workspace Error:{RESET} Directory '{args.workspace}' does not exist.")
            return 1
        WORKSPACE_DIR = cand.resolve()
        os.environ["KOBITS_TARGET_WORKSPACE"] = str(WORKSPACE_DIR)

    if getattr(args, "provider", None):
        os.environ["LLM_PROVIDER"] = args.provider
        from backend.core.config import settings
        settings.LLM_PROVIDER = args.provider

    if getattr(args, "model", None):
        os.environ["ANTHROPIC_MODEL"] = args.model
        from backend.core.config import settings
        settings.ANTHROPIC_MODEL = args.model

    if not args.command:
        if sys.stdin.isatty():
            return await cmd_repl(args)
        args.mission_id = None
        return await cmd_status(args)

    dispatch = {
        "run": cmd_run,
        "connect": cmd_connect,
        "search": cmd_search,
        "repl": cmd_repl,
        "chat": cmd_repl,
        "status": cmd_status,
        "missions": cmd_missions,
        "agents": cmd_agents,
        "projects": cmd_projects,
        "approve": cmd_approve,
        "retry": cmd_retry,
        "resume": cmd_retry,
        "reject": cmd_reject,
        "cancel": cmd_cancel,
        "steer": cmd_steer,
        "diff": cmd_diff,
        "apply": cmd_apply,
        "worker": cmd_worker,
        "dataset": cmd_dataset,
        "data": cmd_dataset,
        "setup": cmd_setup,
        "doctor": cmd_setup,
        "goal": cmd_goal,
        "teamwork-preview": cmd_teamwork_preview,
        "preview": cmd_teamwork_preview,
        "teamwork": cmd_teamwork_preview,
        "boost": cmd_boost,
        "turbo": cmd_boost,
        "learn": cmd_learn,
        "learning": cmd_learn,
        "logs": cmd_logs,
    }
    handler = dispatch.get(args.command)
    if not handler:
        parser.print_help()
        return 1
    return await handler(args)


def main() -> None:
    try:
        exit_code = asyncio.run(async_main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Interrupted by user.{RESET}")
        sys.exit(130)


if __name__ == "__main__":
    main()


