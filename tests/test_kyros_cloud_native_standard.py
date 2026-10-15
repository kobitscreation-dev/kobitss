"""
Test Suite: Kyros Cloud-Native Standard Alignment Verification
Validates the 4 differentiated architectural pillars of Kobits against the Kyros Cloud-Native standard:
  1. Cloud SaaS-Native Execution (Zero host code execution, CloudMicroVMSandbox default)
  2. Containerized In-Sandbox Pre-Review Gate (_verify_sandbox_code containerized toolchains)
  3. Enterprise Cross-Session Episodic Memory & Knowledge Graph (MemoryService & context_builder)
  4. Cloud-Centric Remote Control Plane & Client (kobits_cli run, status, logs, preview, /goal, /boost)
"""

import argparse
import asyncio
import os
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from backend.core.config import settings
import backend.models
from backend.core.database import AsyncSessionLocal
from backend.models.project import Project, ProjectMemory, ProjectMemoryStatus, Activity, ActivityType, Task
from backend.models.mission import Mission, MissionStatus, WorkflowPhase
from backend.models.communication import AgentDecision, AgentLesson
from backend.models.agent import Agent, AgentType
from backend.services.cloud_sandbox import CloudMicroVMSandbox
from backend.services.environment_bootstrapper import EnvironmentBootstrapper, SENSITIVE_ENV_KEYS
from backend.services.mission_runtime import MissionRuntime
from backend.services.memory_service import MemoryService
from backend.services.context_builder import build_agent_context
import kobits_cli


# =========================================================================
# Pillar 1: Cloud SaaS-Native Execution (Zero Host Code Execution)
# =========================================================================

def test_cloud_native_default_driver_and_mode():
    """Verify cloud_microvm and cloud_native are the system defaults."""
    assert CloudMicroVMSandbox.is_cloud_native() is True
    driver = EnvironmentBootstrapper.detect_isolation_driver()
    assert driver == "cloud_microvm"


def test_cloud_microvm_sandbox_spawning_and_zero_host_leak():
    """Verify CloudMicroVMSandbox spawns hermetic remote MicroVMs with zero host leaks."""
    session_id = f"test-kyros-{uuid.uuid4().hex[:8]}"
    sb = CloudMicroVMSandbox.get_or_create(session_id=session_id)
    assert sb is not None
    assert sb.session_id == session_id

    # Verify write_file operates in the sandbox workspace
    write_res = sb.write_file("src/service.py", "def compute():\n    return 42\n")
    assert write_res is True

    # Verify run_command executes inside MicroVM
    cmd_res = sb.run_command("python3 src/service.py", workdir="/workspace")
    assert cmd_res.get("exit_code") == 0

    # Ensure sensitive host credentials are excluded from sandbox environments
    env_scrubbed = sb.env or {}
    for sensitive_key in SENSITIVE_ENV_KEYS:
        assert sensitive_key not in env_scrubbed

    # Clean up session
    closed = CloudMicroVMSandbox.close_session(session_id)
    assert closed is True


# =========================================================================
# Pillar 2: Containerized In-Sandbox Pre-Review Gate
# =========================================================================

def test_containerized_in_sandbox_pre_review_gate(tmp_path):
    """Verify _verify_sandbox_code executes containerized toolchains inside MicroVM."""
    # Create valid python, typescript, and go files
    py_file = tmp_path / "valid.py"
    py_file.write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")

    ts_file = tmp_path / "app.ts"
    ts_file.write_text("export function greet(name: string): string {\n    return 'Hello ' + name;\n}\n", encoding="utf-8")

    go_file = tmp_path / "main.go"
    go_file.write_text("package main\n\nfunc main() {}\n", encoding="utf-8")

    session_id = f"sb-gate-{uuid.uuid4().hex[:8]}"
    cloud_sb = CloudMicroVMSandbox.get_or_create(session_id=session_id)
    cloud_sb.ensure_synced(local_dir=str(tmp_path), remote_dir="/workspace")

    report = MissionRuntime._verify_sandbox_code(
        sandbox_dir=str(tmp_path),
        target_files=["valid.py", "app.ts", "main.go"],
    )

    assert report["status"] == "PASSED"
    assert "container_verification" in report
    container_report = report["container_verification"]
    assert container_report.get("container_isolated") is True
    assert container_report.get("status") == "PASSED"
    assert "python" in report["verified_by_language"]

    CloudMicroVMSandbox.close_session(session_id)


def test_containerized_in_sandbox_pre_review_gate_catches_syntax_error(tmp_path):
    """Verify _verify_sandbox_code blocks broken syntax before review."""
    bad_py = tmp_path / "broken.py"
    bad_py.write_text("def broken_syntax(:\n    pass\n", encoding="utf-8")

    report = MissionRuntime._verify_sandbox_code(
        sandbox_dir=str(tmp_path),
        target_files=["broken.py"],
    )

    assert report["status"] == "FAILED"
    assert len(report["errors"]) > 0
    assert any("syntaxerror" in err.lower() or "syntax error" in err.lower() for err in report["errors"])


# =========================================================================
# Pillar 3: Enterprise Cross-Session Episodic Memory & Knowledge Graph
# =========================================================================

@pytest.mark.asyncio
async def test_episodic_memory_indexing_and_dynamic_query():
    """Verify MemoryService indexes mission outcomes and queries them with hybrid search."""
    project_id = f"proj-ep-{uuid.uuid4().hex[:8]}"
    mission_id = f"miss-ep-{uuid.uuid4().hex[:8]}"
    org_id = "test-org"

    from backend.core.database import engine
    from backend.models.base import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        proj = Project(id=project_id, name="Episodic Project", organization_id=org_id, created_by="user-ep-1")
        db.add(proj)
        await db.commit()

        # 1. Index mission outcome with decisions, bug fixes, and lessons
        res = await MemoryService.index_mission_outcome(
            db=db,
            mission_id=mission_id,
            project_id=project_id,
            organization_id=org_id,
            outcome="SUCCESS",
            decisions=[
                {
                    "decision": "Use E2B Firecracker MicroVMs for remote sandbox isolation",
                    "reason": "Eliminates host port conflicts and guarantees zero host contamination",
                }
            ],
            bug_fixes=[
                {
                    "defect": "Database lock contention during parallel agent rollout",
                    "fix": "Introduced SQLite WAL mode and exponential retry backoff",
                    "root_cause": "Synchronous single-writer locking",
                }
            ],
            lessons=[
                {
                    "cause": "Host port binding collision",
                    "lesson": "Always bind dev servers to ephemeral container port forwarders",
                }
            ],
            architectural_notes="Cloud-native microVM runtime architecture established.",
        )

        assert res["status"] == "INDEXED"
        assert res["indexed_count"] >= 4

        # 2. Query episodic memory for decisions
        matches = await MemoryService.query_episodic_memory(
            db=db,
            project_id=project_id,
            query="remote sandbox microvm isolation",
            limit=5,
        )
        assert len(matches) > 0
        categories = [m["category"] for m in matches]
        assert any("episodic" in c for c in categories)

        # 3. Format episodic prompt context
        ctx = await MemoryService.get_episodic_context(
            db=db,
            project_id=project_id,
            query="database lock contention and retry backoff",
        )
        assert "Cross-Session Episodic Knowledge Graph" in ctx
        assert "Database lock contention" in ctx or "SQLite WAL" in ctx

        # 4. Verify context_builder injects episodic memory into Section 8B
        mission = Mission(
            id=mission_id,
            project_id=project_id,
            organization_id=org_id,
            created_by="user-ep-1",
            title="Episodic Verification Mission",
            objective="Verify episodic context injection",
            status=MissionStatus.ACTIVE,
            phase=WorkflowPhase.PLANNING,
        )
        task = Task(
            id=f"task-{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            mission_id=mission_id,
            title="Analyze database contention architecture",
            description="Resolve database lock contention and retry backoff",
            created_by="user-ep-1",
        )
        agent = Agent(
            id=f"agent-{uuid.uuid4().hex[:8]}",
            name="Architect",
            type=AgentType.SOLUTION_ARCHITECT,
        )
        db.add_all([mission, task, agent])
        await db.commit()

        built_ctx = await build_agent_context(
            db=db,
            agent=agent,
            project=proj,
            mission=mission,
            task=task,
            organization_id=org_id,
        )
        assert "ENTERPRISE EPISODIC MEMORY (REMEDIATIONS & ARCHITECTURE)" in built_ctx
        assert "Database lock contention" in built_ctx or "SQLite WAL" in built_ctx


# =========================================================================
# Pillar 4: Cloud-Centric Remote Control Plane & Client (kobits_cli)
# =========================================================================

@pytest.mark.asyncio
async def test_kobits_cli_run_defaults_to_cloud(capsys):
    """Verify kobits run defaults to cloud execution unless --local is passed."""
    args = argparse.Namespace(
        objective=["Build", "Cloud-Native", "Webhook"],
        title="Cloud Webhook",
        branch="main",
        local=False,
    )

    with patch("kobits_cli.cloud_create_and_run_mission", AsyncMock(return_value=0)) as mock_cloud_run:
        code = await kobits_cli.cmd_run(args)
        assert code == 0
        mock_cloud_run.assert_awaited_once_with(
            objective="Build Cloud-Native Webhook",
            title="Cloud Webhook",
            branch="main",
        )


@pytest.mark.asyncio
async def test_kobits_cli_run_local_flag_routes_to_local():
    """Verify kobits run --local routes to local engine execution."""
    args = argparse.Namespace(
        objective=["Build", "Local", "Feature"],
        title="Local Feature",
        project="proj-1",
        branch="main",
        autopilot=True,
        no_wait=True,
        local=True,
        provider="mock",
    )

    with patch("kobits_cli.local_create_and_run_mission", AsyncMock(return_value={"id": "m-local"})) as mock_local_run:
        code = await kobits_cli.cmd_run(args)
        assert code == 0
        mock_local_run.assert_awaited_once()


@pytest.mark.asyncio
async def test_kobits_cli_status_cloud_and_local(capsys):
    """Verify kobits status displays cloud control center or local workspace."""
    # Test --local status
    args_local = argparse.Namespace(mission_id=None, local=True)
    with patch("kobits_cli.local_list_projects", AsyncMock(return_value=[])):
        with patch("kobits_cli.local_list_missions", AsyncMock(return_value=[])):
            code = await kobits_cli.cmd_status(args_local)
            assert code == 0
    out_local = capsys.readouterr().out
    assert "KOBITS TERMINAL ENGINEERING ENGINE" in out_local

    # Test cloud status fallback when remote is mocked
    mock_missions = [
        {"id": "cloud-mission-001", "status": "ACTIVE", "phase": "PLANNING", "progress": 25, "title": "Cloud MicroVM"}
    ]
    with patch("kobits_cli.cloud_status", AsyncMock(return_value=0)) as mock_cs:
        args_cloud = argparse.Namespace(mission_id=None, local=False)
        code = await kobits_cli.cmd_status(args_cloud)
        assert code == 0
        mock_cs.assert_awaited_once()


@pytest.mark.asyncio
async def test_kobits_cli_logs_command(capsys):
    """Verify kobits logs streams activities for a mission."""
    mission_id = f"log-miss-{uuid.uuid4().hex[:8]}"
    async with AsyncSessionLocal() as db:
        act = Activity(
            id=str(uuid.uuid4()),
            organization_id="default_org",
            type=ActivityType.TASK_STARTED,
            title="MicroVM sandbox initialized at /workspace",
            description=f"Mission {mission_id} MicroVM ready",
        )
        db.add(act)
        await db.commit()

    args = argparse.Namespace(mission_id=mission_id, local=True)
    code = await kobits_cli.cmd_logs(args)
    assert code == 0

    out = capsys.readouterr().out
    assert "KOBITS MISSION ACTIVITY & TERMINAL LOG STREAM" in out
    assert "MicroVM sandbox initialized at /workspace" in out


def test_kobits_cli_parser_includes_logs_and_cloud_native_flags():
    """Verify build_parser supports logs, run, status, preview, boost, goal."""
    parser = kobits_cli.build_parser()
    assert "logs" in kobits_cli.KNOWN_COMMANDS

    norm_logs = kobits_cli._normalize_cli_argv(["/logs", "m-1234"])
    assert norm_logs == ["logs", "m-1234"]

    parsed_logs = parser.parse_args(["logs", "m-1234"])
    assert parsed_logs.command == "logs"
    assert parsed_logs.mission_id == "m-1234"


@pytest.mark.asyncio
async def test_containerized_pytest_gate_executes_in_microvm_zero_host_leak(tmp_path):
    """Verify Stage 3A pytest execution runs in CloudMicroVMSandbox and does not invoke host pytest."""
    py_test = tmp_path / "test_sample.py"
    py_test.write_text("def test_ok():\n    assert 1 + 1 == 2\n", encoding="utf-8")

    session_id = f"sb-pytest-{uuid.uuid4().hex[:8]}"
    cloud_sb = CloudMicroVMSandbox.get_or_create(session_id=session_id)
    cloud_sb.ensure_synced(local_dir=str(tmp_path), remote_dir="/workspace")

    with patch("subprocess.run") as mock_host_subproc:
        report = MissionRuntime._verify_sandbox_code(
            sandbox_dir=str(tmp_path),
            target_files=["test_sample.py"],
        )
        assert report["status"] == "PASSED"
        assert report["pytest_summary"] is not None
        assert report["pytest_summary"]["driver"] == "cloud_microvm"
        assert report["pytest_summary"]["passed"] is True
        mock_host_subproc.assert_not_called()

    CloudMicroVMSandbox.close_session(session_id)


@pytest.mark.asyncio
async def test_containerized_pytest_gate_catches_syntax_error_in_microvm(tmp_path):
    """Verify Stage 3A pytest execution in microVM detects syntax errors in test files."""
    broken_test = tmp_path / "test_broken.py"
    broken_test.write_text("def test_broken(:\n    assert True\n", encoding="utf-8")

    report = MissionRuntime._verify_sandbox_code(
        sandbox_dir=str(tmp_path),
        target_files=["test_broken.py"],
    )
    assert report["status"] == "FAILED"
    assert any("syntaxerror" in err.lower() or "syntax error" in err.lower() for err in report["errors"])


@pytest.mark.asyncio
async def test_kobits_cli_resolve_mission_id_cloud_first():
    """Verify _resolve_mission_id queries cloud API first when not in local mode."""
    cloud_missions = [
        {"id": "cloud-mission-uuid-1111", "status": "ACTIVE", "title": "Cloud 1"},
        {"id": "cloud-mission-uuid-2222", "status": "COMPLETED", "title": "Cloud 2"},
    ]
    with patch("kobits_cli.cloud_list_missions", AsyncMock(return_value=cloud_missions)):
        mid = await kobits_cli._resolve_mission_id("cloud-mission-uuid-2")
        assert mid == "cloud-mission-uuid-2222"

        mid_active = await kobits_cli._resolve_mission_id(None, prefer_status="ACTIVE")
        assert mid_active == "cloud-mission-uuid-1111"


@pytest.mark.asyncio
async def test_kobits_cli_goal_cloud_client(capsys):
    """Verify kobits goal inspects goals from Cloud Run API."""
    mock_cloud_detail = {
        "id": "cm-98765432-1111-2222-3333-444455556666",
        "title": "Cloud Mission Goal Verification",
        "status": "ACTIVE",
        "phase": "EXECUTION",
        "progress": 75,
        "active_branch": "main",
        "objective": "Align Kobits to Kyros Cloud-Native standard",
        "tasks": [
            {"title": "Remote MicroVM Fleet", "status": "COMPLETED", "agent_name": "DevOps"},
            {"title": "Container Review Gate", "status": "IN_PROGRESS", "agent_name": "Reviewer"},
        ],
    }
    with patch("kobits_cli._resolve_mission_id", AsyncMock(return_value=mock_cloud_detail["id"])):
        with patch("kobits_cli.cloud_get_mission_detail", AsyncMock(return_value=mock_cloud_detail)):
            args = argparse.Namespace(mission_id=mock_cloud_detail["id"], local=False)
            code = await kobits_cli.cmd_goal(args)
            assert code == 0

    out = capsys.readouterr().out
    assert "KOBITS MISSION STRATEGIC GOALS & ACCEPTANCE CRITERIA" in out
    assert "Cloud Mission Goal Verification" in out
    assert "Align Kobits to Kyros Cloud-Native standard" in out
    assert "Remote MicroVM Fleet" in out


@pytest.mark.asyncio
async def test_kobits_cli_teamwork_preview_cloud_client(capsys):
    """Verify kobits preview fetches remote preview URL from Cloud Run API."""
    mock_cloud_detail = {
        "id": "cm-prev-1111-2222-3333-444455556666",
        "title": "Cloud Preview Mission",
        "status": "ACTIVE",
        "phase": "EXECUTION",
        "remote_preview_url": "https://3000-vm-cloud.e2b.dev",
        "tasks": [],
    }
    with patch("kobits_cli._resolve_mission_id", AsyncMock(return_value=mock_cloud_detail["id"])):
        with patch("kobits_cli.cloud_get_mission_detail", AsyncMock(return_value=mock_cloud_detail)):
            args = argparse.Namespace(mission_id=mock_cloud_detail["id"], local=False)
            code = await kobits_cli.cmd_teamwork_preview(args)
            assert code == 0

    out = capsys.readouterr().out
    assert "KOBITS MULTI-AGENT TEAMWORK & LIVE PREVIEW" in out
    assert "https://3000-vm-cloud.e2b.dev" in out


@pytest.mark.asyncio
async def test_kobits_cli_boost_cloud_steering(capsys):
    """Verify kobits boost injects turbo mode into active cloud mission."""
    with patch("kobits_cli._resolve_mission_id", AsyncMock(return_value="cm-boost-1234")):
        with patch("kobits_cli.cloud_steer_mission", AsyncMock(return_value={"status": "STEERED"})) as mock_steer:
            args = argparse.Namespace(instruction=["HYPER_SCALE"], mission_id="cm-boost-1234", local=False)
            code = await kobits_cli.cmd_boost(args)
            assert code == 0
            mock_steer.assert_awaited_once()

    out = capsys.readouterr().out
    assert "KOBITS TURBO BOOST & ACCELERATION ENGINE" in out
    assert "Mode: HYPER_SCALE" in out
    assert "Turbo state injected into active mission" in out


@pytest.mark.asyncio
async def test_kobits_cli_diff_and_apply_cloud_client(capsys):
    """Verify kobits diff and apply interact with Cloud Run API."""
    mock_detail = {
        "id": "cm-diff-1234-5678-9012-345678901234",
        "title": "Cloud Diff Verification",
        "status": "AWAITING_APPROVAL",
    }
    mock_diff = {
        "sandbox_dir": "CloudMicroVM:/workspace",
        "files": [
            {
                "path": "src/core.py",
                "status": "MODIFIED",
                "added": 5,
                "removed": 1,
                "diff": "--- a/src/core.py\n+++ b/src/core.py\n@@ -1,1 +1,5 @@\n+cloud_native = True\n",
            }
        ],
        "verification": {"status": "PASSED"},
    }
    with patch("kobits_cli._resolve_mission_id", AsyncMock(return_value=mock_detail["id"])):
        with patch("kobits_cli.cloud_get_mission_detail", AsyncMock(return_value=mock_detail)):
            with patch("kobits_cli.cloud_get_diff", AsyncMock(return_value=mock_diff)):
                args_diff = argparse.Namespace(mission_id=mock_detail["id"], file=None, stat=False, local=False)
                code_diff = await kobits_cli.cmd_diff(args_diff)
                assert code_diff == 0

    out_diff = capsys.readouterr().out
    assert "KOBITS SANDBOX DIFF" in out_diff
    assert "src/core.py" in out_diff

    # Test cloud apply
    with patch("kobits_cli._resolve_mission_id", AsyncMock(return_value=mock_detail["id"])):
        with patch("kobits_cli.cloud_get_mission_detail", AsyncMock(return_value=mock_detail)):
            with patch("kobits_cli.cloud_apply_mission", AsyncMock(return_value={"status": "APPLIED", "applied_count": 1})):
                args_apply = argparse.Namespace(mission_id=mock_detail["id"], dry_run=False, force=False, local=False)
                code_apply = await kobits_cli.cmd_apply(args_apply)
                assert code_apply == 0

    out_apply = capsys.readouterr().out
    assert "Cloud Deliverable APPLIED" in out_apply
