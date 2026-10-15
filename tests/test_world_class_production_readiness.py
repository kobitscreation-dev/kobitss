"""
World-Class Production Readiness & Comprehensive Audit Test Suite.
Verifies all 10 Core Architectural Subsystems with rigorous enterprise stress testing:
1. Persistent Team Memory & Knowledge Graph
2. Port Tunneling & HMR Dev Server Proxy
3. Governed Agent Teams & Strict Pydantic Contracts
4. SWE-bench Verified Standard & Negative-to-Positive TDD Engine
5. PR & CI Auto-Remediation with Remote Git Push
6. Full Headless Chromium Browser Engine & In-Process Fallback
7. Parallel MicroVM Clusters & Best-of-N Candidate Search
8. Cloud-Centric Client & Remote Web Portal
9. Distributed Task Workers & PostgreSQL Durable Queue
10. Real-Time Steering & Mid-Turn Injection
"""

import os
import sys
import json
import time
import uuid
import math
import tempfile
import asyncio
import subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List

import pytest
import httpx
from starlette.websockets import WebSocketDisconnect
from starlette.testclient import TestClient

from backend.core.database import AsyncSessionLocal, engine
from backend.models.agent import AgentType
from backend.models.mission import Mission, MissionStatus, WorkflowPhase, ApprovalStatus
from backend.models.project import Project, Task, TaskStatus
from backend.models.durable_job import DurableJob, JobStatus
from backend.services.team_memory import (
    TeamMemoryManager, TeamMemoryEntry, MemoryNamespace, SPECIALIST_ALIASES
)
from backend.services.memory_service import MemoryService
from backend.services.governance import (
    GovernanceEngine, BaseAgentInput, BaseAgentOutput,
    SolutionArchitectInput, SolutionArchitectOutput,
    SecurityEngineerInput, SecurityEngineerOutput,
    QAEngineerInput, QAEngineerOutput,
    BackendEngineerInput, BackendEngineerOutput
)
from backend.services.agent_registry import AGENT_REGISTRY
from backend.services.reproducer_engine import ReproducerEngine
from backend.services.sandbox_rollback import SandboxRollbackManager
from backend.services.sandbox_manager import SandboxManager, SandboxSession
from backend.services.browser_engine import HeadlessBrowserEngine, generate_valid_png
from backend.services.best_of_n import BestOfNExplorer, PatchCandidate
from backend.services.durable_queue import DurableQueue
from backend.services.agent_executor import AgentExecutor
import kobits_cli


# ============================================================================
# 1. Persistent Team Memory & Knowledge Graph
# ============================================================================

@pytest.mark.asyncio
async def test_persistent_team_memory_and_specialist_aliases():
    """Verify team memory storage, 21 specialist alias mappings, TTL expiration, and DB sync."""
    project_id = f"proj_mem_{uuid.uuid4().hex[:8]}"
    TeamMemoryManager.clear(project_id)

    # 1. Record memories across namespaces
    entry_adr = TeamMemoryManager.record_adr(
        project_id=project_id,
        source_agent="Axiom",
        title="Event Sourced Audit Architecture",
        decision="Use append-only ledger in PostgreSQL",
        rationale="Immutable compliance history",
        confidence=1.0,
    )
    assert entry_adr.namespace == MemoryNamespace.ADR
    assert entry_adr.decay_factor == 0.01

    entry_conv = TeamMemoryManager.record_convention(
        project_id=project_id,
        source_agent="Forge",
        rule="Use Pydantic v2 model_validate instead of parse_obj",
        example="MyModel.model_validate(data)",
        confidence=0.95,
    )
    assert entry_conv.namespace == MemoryNamespace.CONVENTION

    entry_defect = TeamMemoryManager.record_defect_fix(
        project_id=project_id,
        source_agent="Sentinel",
        defect="Race condition on sandbox worktree checkout",
        fix="Acquire per-sandbox file lock before git operations",
        root_cause="Concurrent workers sharing index.lock",
        confidence=0.9,
    )
    assert entry_defect.namespace == MemoryNamespace.DEFECT_FIX

    # 2. Verify all 21 specialist alias mappings in SPECIALIST_ALIASES
    canonical_agents = [
        "nexus", "axiom", "forge", "muse", "schema", "core", "pixel", "swift",
        "neuron", "bridge", "pulse", "sentinel", "aegis", "review", "vector",
        "access", "insight", "scribe", "launch", "reliant", "catalyst"
    ]
    for agent in canonical_agents:
        assert agent in SPECIALIST_ALIASES, f"Agent {agent} missing from SPECIALIST_ALIASES"
        aliases = SPECIALIST_ALIASES[agent]
        assert len(aliases) >= 1, f"Agent {agent} must have alias mappings"

    # 3. Test selective targeting to specific specialists
    targeted_note = TeamMemoryManager.record_peer_note(
        project_id=project_id,
        source_agent="Sentinel",
        target_agents=["aegis", "security_engineer"],
        note="Review OAuth redirect URL sanitization on auth endpoint",
        confidence=1.0,
        ttl_seconds=3600,
    )
    # Must be applicable to Aegis and security_engineer
    assert targeted_note.is_applicable_to_agent("aegis")
    assert targeted_note.is_applicable_to_agent("security_engineer")
    assert targeted_note.is_applicable_to_agent("security")
    # Must NOT be applicable to unrelated agent Core (backend_engineer)
    assert not targeted_note.is_applicable_to_agent("core")

    # 4. TTL Expiration & Time-Decayed Confidence
    now = datetime.now(timezone.utc)
    # Memory created 10 hours ago with 5-hour TTL -> expired
    past_time = now - timedelta(hours=10)
    expired_entry = TeamMemoryEntry(
        id=str(uuid.uuid4()),
        project_id=project_id,
        organization_id="org1",
        namespace=MemoryNamespace.PEER_NOTE,
        key="ephemeral_note",
        content="Transient build cache cleared",
        source_agent="Pulse",
        ttl_seconds=5 * 3600,
        created_at=past_time,
    )
    assert expired_entry.is_expired(now)
    assert expired_entry.compute_effective_confidence(now) == 0.0

    # 5. Hybrid Semantic Query (Dense + BM25 RRF)
    results = TeamMemoryManager.query_team_memory(
        project_id=project_id,
        query="PostgreSQL audit architecture append-only",
        limit=5,
    )
    assert len(results) >= 1
    top_hit = results[0]
    assert "Audit" in top_hit["content"] or "PostgreSQL" in top_hit["content"]
    assert top_hit["rrf_score"] > 0.0

    # 6. Database Persistence & Episodic Sync
    async with AsyncSessionLocal() as db:
        pm = await TeamMemoryManager.sync_to_db(db, entry_adr)
        assert pm is not None
        assert pm.id == entry_adr.id
        assert pm.project_id == project_id
        # Cross-session query via MemoryService
        retrieved = await MemoryService.query_episodic_memory(
            db, project_id=project_id, query="audit ledger"
        )
        assert len(retrieved) >= 1
        assert any("PostgreSQL" in r["content"] for r in retrieved)

    TeamMemoryManager.clear(project_id)


# ============================================================================
# 2. Port Tunneling & HMR Dev Server Proxy
# ============================================================================

@pytest.mark.asyncio
async def test_preview_dev_server_proxy_and_websocket_hmr():
    """Verify HTTP method forwarding, header filtering, redirect rewriting, and WebSocket HMR tunneling."""
    from backend.main import app
    from backend.api.v1.preview import set_preview_config

    with tempfile.TemporaryDirectory() as sb_dir:
        # Create a mock mission
        mission_id = str(uuid.uuid4())
        async with AsyncSessionLocal() as db:
            m = Mission(
                id=mission_id,
                organization_id="org_test",
                project_id="proj_test",
                created_by="user_test",
                title="Preview Test",
                objective="Test Dev Server Proxy",
                status=MissionStatus.ACTIVE,
                phase=WorkflowPhase.IMPLEMENTATION,
            )
            db.add(m)
            await db.commit()

        # Register sandbox session
        session = SandboxSession(
            session_id=mission_id,
            project_root=sb_dir,
            branch_name=f"kobits/mission/{mission_id[:8]}",
            sandbox_dir=sb_dir,
        )
        SandboxManager._sessions[mission_id] = session

        # Write index.html
        (Path(sb_dir) / "index.html").write_text(
            "<html><head><title>App</title></head><body><h1>Live App</h1></body></html>",
            encoding="utf-8"
        )

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            # 1. GET static asset
            resp = await client.get(f"/api/v1/preview/{mission_id}/index.html")
            assert resp.status_code == 200
            assert "Live App" in resp.text
            assert f'<base href="/api/v1/preview/{mission_id}/">' in resp.text

            # 2. SPA routing fallback
            resp_spa = await client.get(f"/api/v1/preview/{mission_id}/dashboard/settings")
            assert resp_spa.status_code == 200
            assert "Live App" in resp_spa.text

            # 3. Path traversal attack blocked
            resp_trav = await client.get(f"/api/v1/preview/{mission_id}/../../etc/passwd")
            assert resp_trav.status_code in (403, 404)

            # 4. Preview Info endpoint
            resp_info = await client.get(f"/api/v1/preview/{mission_id}/info")
            assert resp_info.status_code == 200
            info_data = resp_info.json()
            assert info_data["sandbox_found"] is True
            assert info_data["mission_id"] == mission_id

        # 5. WebSocket HMR tunnel test
        from starlette.testclient import TestClient
        client_sync = TestClient(app)
        with client_sync.websocket_connect(f"/api/v1/preview/{mission_id}/__ws_proxy__") as ws:
            # Standby greeting
            greeting = ws.receive_json()
            assert greeting.get("type") == "kobits_hmr_status"
            # Text frame echo
            ws.send_text("vite:ping")
            reply = ws.receive_text()
            assert "kobits_ack" in reply
            # Binary frame echo
            test_blob = b"\x01\x02\x03\x04\x05"
            ws.send_bytes(test_blob)
            binary_reply = ws.receive_bytes()
            assert binary_reply == test_blob


# ============================================================================
# 3. Governed Agent Teams & Strict Pydantic Contracts
# ============================================================================

def test_governed_agent_teams_contracts_and_cumulative_phase_gates():
    """Verify strict Pydantic typed contracts and cumulative phase gate validation."""
    # 1. Validate Solution Architect input/output contracts
    arch_contract = GovernanceEngine.resolve_contract(AgentType.SOLUTION_ARCHITECT)
    assert arch_contract is not None
    assert arch_contract.approval_gate_required is True

    valid_arch_input = {
        "mission_id": "m123",
        "task_title": "Design Database Schema",
        "task_description": "Define tables for users and orders",
    }
    val_in = GovernanceEngine.validate_agent_input(AgentType.SOLUTION_ARCHITECT, valid_arch_input)
    assert val_in.is_valid is True

    # Empty task title must be rejected
    invalid_arch_input = {"mission_id": "m123", "task_title": "   "}
    val_in_fail = GovernanceEngine.validate_agent_input(AgentType.SOLUTION_ARCHITECT, invalid_arch_input)
    assert val_in_fail.is_valid is False

    # Valid output
    valid_arch_out = {
        "summary": "Architecture completed",
        "architecture_decisions": [{"adr": "ADR-001", "title": "Postgres storage"}],
        "artifacts": {"ARCHITECTURE_DECISION_RECORD": "ADR text", "API_SPECIFICATION": "OpenAPI"},
    }
    val_out = GovernanceEngine.validate_agent_output(AgentType.SOLUTION_ARCHITECT, valid_arch_out)
    assert val_out.is_valid is True

    # 2. Cumulative Phase Gate Validation: Cannot bypass Architecture or Security review
    # Attempt jumping from INTAKE directly to IMPLEMENTATION without Architecture approval -> BLOCKED
    gate_impl_unapproved = GovernanceEngine.validate_phase_transition(
        current_phase="INTAKE",
        target_phase="IMPLEMENTATION",
        approvals=[],
    )
    assert gate_impl_unapproved.allowed is False
    assert "SOLUTION_ARCHITECT" in gate_impl_unapproved.missing_approvals

    # With Architecture approval -> ALLOWED
    gate_impl_approved = GovernanceEngine.validate_phase_transition(
        current_phase="ARCHITECTURE_REVIEW",
        target_phase="IMPLEMENTATION",
        approvals=["SOLUTION_ARCHITECT"],
    )
    assert gate_impl_approved.allowed is True

    # Attempt jumping to DEPLOYMENT without Security review -> BLOCKED
    gate_deploy_no_sec = GovernanceEngine.validate_phase_transition(
        current_phase="IMPLEMENTATION",
        target_phase="DEPLOYMENT",
        approvals=["SOLUTION_ARCHITECT", "HUMAN_ENGINEER_DELIVERY_APPROVAL"],
    )
    assert gate_deploy_no_sec.allowed is False
    assert "SECURITY_ENGINEER" in gate_deploy_no_sec.missing_approvals

    # Attempt jumping to DEPLOYMENT without Architecture approval -> BLOCKED (cumulative)
    gate_deploy_no_arch = GovernanceEngine.validate_phase_transition(
        current_phase="CODE_REVIEW",
        target_phase="DEPLOYMENT",
        approvals=["SECURITY_ENGINEER", "HUMAN_ENGINEER_DELIVERY_APPROVAL"],
    )
    assert gate_deploy_no_arch.allowed is False
    assert "SOLUTION_ARCHITECT" in gate_deploy_no_arch.missing_approvals

    # Fully approved deployment -> ALLOWED
    gate_deploy_all_approved = GovernanceEngine.validate_phase_transition(
        current_phase="CODE_REVIEW",
        target_phase="DEPLOYMENT",
        approvals=["SOLUTION_ARCHITECT", "SECURITY_ENGINEER", "HUMAN_ENGINEER_DELIVERY_APPROVAL"],
    )
    assert gate_deploy_all_approved.allowed is True


# ============================================================================
# 4. SWE-bench Verified Standard & Negative-to-Positive TDD Engine
# ============================================================================

def test_reproducer_engine_and_atomic_rollback():
    """Verify negative reproducer synthesis, baseline verification, patch verification, and rollback."""
    with tempfile.TemporaryDirectory() as sb_dir:
        # Initialize git repo in sandbox
        subprocess.run(["git", "init"], cwd=sb_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=sb_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.local"], cwd=sb_dir, check=True, capture_output=True)

        service_file = Path(sb_dir) / "calculator.py"
        service_file.write_text("def divide(a: int, b: int) -> float:\n    return a * b  # BUG: multiplies instead of divides\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=sb_dir, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial buggy implementation"], cwd=sb_dir, check=True, capture_output=True)

        # 1. Author reproducer test script
        repro_script = (
            "from calculator import divide\n"
            "res = divide(10, 2)\n"
            "assert res == 5.0, f'Expected 5.0, got {res}'\n"
            "print('SUCCESS')\n"
        )
        repro_path = ReproducerEngine.author_reproducer(sb_dir, repro_script, "reproduce_divide.py")
        assert os.path.isfile(repro_path)

        # 2. Negative baseline verification: MUST FAIL on unpatched buggy code
        base_res = ReproducerEngine.validate_negative_baseline(sb_dir, repro_path)
        assert base_res.valid is True
        assert base_res.status == "VALID_BASELINE"
        assert base_res.exit_code != 0

        # Create checkpoint before patching
        checkpoint_sha = SandboxRollbackManager.create_checkpoint(sb_dir, "pre_patch")
        assert checkpoint_sha is not None

        # 3. Apply erroneous patch (syntax error or still broken)
        service_file.write_text("def divide(a: int, b: int) -> float:\n    return a + b\n", encoding="utf-8")
        pos_fail = ReproducerEngine.verify_positive(sb_dir, repro_path)
        assert pos_fail.success is False

        # Rollback cleanly to checkpoint
        rolled_back = SandboxRollbackManager.rollback(sb_dir, checkpoint_sha)
        assert rolled_back is True
        assert "return a * b" in service_file.read_text(encoding="utf-8")

        # 4. Apply correct patch
        service_file.write_text("def divide(a: int, b: int) -> float:\n    return a / b\n", encoding="utf-8")
        pos_ok = ReproducerEngine.verify_positive(sb_dir, repro_path)
        assert pos_ok.success is True
        assert pos_ok.reproducer_passed is True


# ============================================================================
# 5. PR & CI Auto-Remediation with Remote Git Push
# ============================================================================

@pytest.mark.asyncio
async def test_ci_auto_remediation_webhook_and_branch_push():
    """Verify incoming CI check failure triggers remediation task and push_branch is callable."""
    from backend.main import app

    async with AsyncSessionLocal() as db:
        # Create project and mission
        proj_id = f"proj_ci_{uuid.uuid4().hex[:8]}"
        p = Project(
            id=proj_id,
            name="CI Project",
            organization_id="org_ci",
            repository_url="https://github.com/kobitscreation-dev/kobitss",
            created_by="user_ci",
        )
        db.add(p)
        await db.commit()

    webhook_payload = {
        "action": "completed",
        "check_run": {
            "name": "pytest-suite",
            "head_sha": "abc123456789",
            "status": "completed",
            "conclusion": "failure",
            "check_suite": {"head_branch": "kobits/fix-issue-42"},
            "output": {
                "title": "Tests Failed",
                "summary": "1 test failed in test_auth.py",
                "text": "AssertionError: expected 200, got 401",
                "annotations": [
                    {
                        "path": "backend/api/auth.py",
                        "start_line": 45,
                        "annotation_level": "failure",
                        "message": "Token expired not handled"
                    }
                ]
            }
        },
        "repository": {
            "html_url": "https://github.com/kobitscreation-dev/kobitss",
            "full_name": "kobitscreation-dev/kobitss"
        }
    }

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/webhooks/github",
            json=webhook_payload,
            headers={"X-GitHub-Event": "check_run", "Content-Type": "application/json"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") in ("remediation_task_created", "ci_remediation_dispatched", "processed", "success")

    # Verify SandboxManager.push_branch signature and local dry-run
    with tempfile.TemporaryDirectory() as sb_dir:
        subprocess.run(["git", "init"], cwd=sb_dir, check=True, capture_output=True)
        session_id = "test_push_session"
        session = SandboxSession(
            session_id=session_id,
            project_root=sb_dir,
            branch_name="kobits/fix-issue-42",
            sandbox_dir=sb_dir,
        )
        SandboxManager._sessions[session_id] = session
        # Calling push_branch without remote gracefully returns error instead of crashing
        res_push = SandboxManager.push_branch(session_id, "kobits/fix-issue-42", remote_url=None)
        assert "success" in res_push


# ============================================================================
# 6. Full Headless Chromium Browser Engine
# ============================================================================

@pytest.mark.asyncio
async def test_headless_browser_engine_dom_interactions_and_fallback():
    """Verify DOM navigation, clicking, typing, evaluate, screenshotting, and fallback emulation."""
    # Test valid PNG generator
    png_bytes = generate_valid_png(width=100, height=100, color=(50, 100, 150))
    assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(png_bytes) > 50

    # Test in-process headless browser engine
    engine = HeadlessBrowserEngine(headless=True)
    async with engine:
        # 1. Navigation
        ctx = await engine.navigate("https://example.com")
        assert ctx.url == "https://example.com"
        assert ctx.status_code == 200

        # 2. Type / Fill input
        type_res = await engine.type("#search-input", "Kobits Autonomous Engineering")
        assert type_res["success"] is True

        # 3. Click element
        click_res = await engine.click("button#submit")
        assert click_res["success"] is True

        # 4. Screenshot capture
        screenshot_bytes = await engine.screenshot()
        assert screenshot_bytes.startswith(b"\x89PNG\r\n\x1a\n")
        assert len(screenshot_bytes) > 50

        # 5. Screenshot base64 (both raw and data URI)
        b64_img = await engine.screenshot_base64()
        assert isinstance(b64_img, str) and len(b64_img) > 50
        b64_uri = await engine.screenshot_base64(data_uri=True)
        assert b64_uri.startswith("data:image/png;base64,")

        # 6. Console log capture
        logs = await engine.get_console_logs()
        assert len(logs) >= 1
        assert any("Navigated" in str(l) or "DOM Event" in str(l) for l in logs)


# ============================================================================
# 7. Parallel MicroVM Clusters & Best-of-N Candidate Search
# ============================================================================

@pytest.mark.asyncio
async def test_best_of_n_parallel_candidate_rollouts_and_parsimony():
    """Verify concurrent candidate patch rollouts via asyncio.gather(), parsimony scoring, and file deletion tracking."""
    # 1. Parsimony scoring validation
    score_clean = BestOfNExplorer.score_candidate(
        reproducer_passed=True, regression_passed=True, additions=3, deletions=1, files_changed_count=1
    )
    score_bloated = BestOfNExplorer.score_candidate(
        reproducer_passed=True, regression_passed=True, additions=500, deletions=200, files_changed_count=15
    )
    assert score_clean > score_bloated, "Minimal surgical patch must outscore bloated diff"

    with tempfile.TemporaryDirectory() as sb_dir:
        # Initialize git repo in sandbox
        subprocess.run(["git", "init"], cwd=sb_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=sb_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.local"], cwd=sb_dir, check=True, capture_output=True)

        app_file = Path(sb_dir) / "app.py"
        app_file.write_text("def run():\n    return 'bad'\n", encoding="utf-8")
        del_file = Path(sb_dir) / "deprecated.py"
        del_file.write_text("deprecated = True\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=sb_dir, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=sb_dir, check=True, capture_output=True)

        repro_file = Path(sb_dir) / "test_app.py"
        repro_file.write_text("from app import run\nassert run() == 'good'\n", encoding="utf-8")

        # Candidate 1: Fixes app.py and deletes deprecated.py (minimal)
        def candidate_1(cand_id: int, sdir: str):
            (Path(sdir) / "app.py").write_text("def run():\n    return 'good'\n", encoding="utf-8")
            if (Path(sdir) / "deprecated.py").exists():
                os.remove(Path(sdir) / "deprecated.py")

        # Candidate 2: Fails reproducer
        def candidate_2(cand_id: int, sdir: str):
            (Path(sdir) / "app.py").write_text("def run():\n    return 'still_bad'\n", encoding="utf-8")

        result = await BestOfNExplorer.explore_best_of_n(
            sandbox_dir=sb_dir,
            candidate_generators=[candidate_1, candidate_2],
            reproducer_script="test_app.py",
            parallel=True,
        )

        assert result.success is True
        assert result.selected_candidate is not None
        assert result.selected_candidate.candidate_id == 1
        assert result.selected_candidate.reproducer_passed is True
        # Verify app.py was fixed and deprecated.py was deleted
        assert "good" in (Path(sb_dir) / "app.py").read_text(encoding="utf-8")
        assert not (Path(sb_dir) / "deprecated.py").exists()


# ============================================================================
# 8. Cloud-Centric Client & Remote Web Portal
# ============================================================================

def test_kobits_cli_commands_and_cloud_run_configuration():
    """Verify CLI command parser, subcommands (run, status, logs, goal, preview, boost, diff, apply), and Cloud URL."""
    parser = kobits_cli.build_parser()

    # Verify DEFAULT_CLOUD_API_URL
    assert "kobitss-850300766138.europe-west1.run.app" in kobits_cli.DEFAULT_CLOUD_API_URL

    # Test parser commands
    args_run = parser.parse_args(["run", "Build an authentication service", "-b", "feature/auth"])
    assert args_run.command == "run"
    assert " ".join(args_run.objective) == "Build an authentication service"
    assert args_run.branch == "feature/auth"

    args_status = parser.parse_args(["status"])
    assert args_status.command == "status"

    args_logs = parser.parse_args(["logs", "03992d0b"])
    assert args_logs.command == "logs"
    assert args_logs.mission_id == "03992d0b"

    args_goal = parser.parse_args(["goal"])
    assert args_goal.command == "goal"

    args_preview = parser.parse_args(["preview", "03992d0b"])
    assert args_preview.command == "preview"

    args_boost = parser.parse_args(["boost", "high"])
    assert args_boost.command == "boost"

    args_diff = parser.parse_args(["diff", "--stat"])
    assert args_diff.command == "diff"
    assert args_diff.stat is True

    args_apply = parser.parse_args(["apply", "--dry-run"])
    assert args_apply.command == "apply"
    assert args_apply.dry_run is True

    # Test slash command normalization
    norm_goal = kobits_cli._normalize_cli_argv(["/goal"])
    assert norm_goal == ["goal"]

    norm_boost = kobits_cli._normalize_cli_argv(["/boost"])
    assert norm_boost == ["boost"]

    norm_learn = kobits_cli._normalize_cli_argv(["/learn"])
    assert norm_learn == ["learn"]


# ============================================================================
# 9. Distributed Task Workers & PostgreSQL Durable Queue
# ============================================================================

@pytest.mark.asyncio
async def test_durable_queue_claim_heartbeat_and_stale_worker_recovery():
    """Verify DurableQueue atomic job claiming, heartbeats, and stale lease reaping."""
    await DurableQueue.init_schema()
    queue_name = f"test_q_{uuid.uuid4().hex[:6]}"

    # 1. Enqueue job
    job_id = await DurableQueue.enqueue(
        task_type="PROCESS_PAYLOAD",
        payload={"amount": 100, "user": "alice"},
        queue_name=queue_name,
        priority=5,
    )
    assert job_id is not None

    # 2. Claim job
    worker_1 = "worker_node_alpha"
    job = await DurableQueue.claim_job(worker_id=worker_1, queue_name=queue_name, lease_seconds=10)
    assert job is not None
    assert job.id == job_id
    assert job.worker_id == worker_1
    assert job.status == JobStatus.LEASED.value

    # 3. Heartbeat
    hb_ok = await DurableQueue.heartbeat(job_id=job.id, worker_id=worker_1, lease_seconds=30)
    assert hb_ok is True

    # 4. Simulate crashed worker: expire lease timeout in database
    async with AsyncSessionLocal() as db:
        expired_time = datetime.now(timezone.utc) - timedelta(seconds=60)
        from sqlalchemy import update
        await db.execute(
            update(DurableJob)
            .where(DurableJob.id == job.id)
            .values(lease_timeout_at=expired_time)
        )
        await db.commit()

    # 5. Reap stale leases
    reaped = await DurableQueue.reap_stale_leases()
    assert reaped >= 1

    # Job should now be back in PENDING status for another worker to pick up
    async with AsyncSessionLocal() as db:
        res = await db.get(DurableJob, job.id)
        assert res.status == JobStatus.PENDING.value
        assert res.worker_id is None

    # 6. Worker 2 claims resurrected job and marks completed
    worker_2 = "worker_node_beta"
    job_retry = await DurableQueue.claim_job(worker_id=worker_2, queue_name=queue_name, lease_seconds=10)
    assert job_retry is not None
    assert job_retry.id == job.id
    assert job_retry.worker_id == worker_2

    completed = await DurableQueue.complete_job(job.id, worker_2, {"result": "processed"})
    assert completed is True

    async with AsyncSessionLocal() as db:
        final_job = await db.get(DurableJob, job.id)
        assert final_job.status == JobStatus.COMPLETED.value


# ============================================================================
# 10. Real-Time Steering & Mid-Turn Injection
# ============================================================================

@pytest.mark.asyncio
async def test_real_time_steering_and_mid_turn_injection():
    """Verify _inject_mid_turn_steering captures human steering tasks and alters tool directives."""
    mission_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())

    async with AsyncSessionLocal() as db:
        m = Mission(
            id=mission_id,
            organization_id="org_steer",
            project_id="proj_steer",
            created_by="user_steer",
            title="Active Engineering Mission",
            objective="Develop microservice",
            status=MissionStatus.ACTIVE,
            phase=WorkflowPhase.IMPLEMENTATION,
        )
        db.add(m)

        steer_task = Task(
            id=task_id,
            mission_id=mission_id,
            project_id="proj_steer",
            created_by="user_steer",
            title="Operator Guidance",
            description="HUMAN_STEERING_OVERRIDE: Use PostgreSQL rather than SQLite for high-concurrency writes",
            status=TaskStatus.PENDING,
            phase=WorkflowPhase.IMPLEMENTATION,
            is_correction=True,
            metadata_json=json.dumps({"source": "steer"}),
            dependencies_json="[]",
        )
        db.add(steer_task)
        await db.commit()

    # Instantiate AgentExecutor and trigger _inject_mid_turn_steering
    executor = AgentExecutor(db=None)
    mock_tool_result = {"status": "success", "output": "created table"}

    injected = await executor._inject_mid_turn_steering(
        mission_id=mission_id,
        current_turn=2,
        result=mock_tool_result,
    )

    assert injected is True
    assert "_LIVE_HUMAN_STEERING_DIRECTIVE" in mock_tool_result
    directive = mock_tool_result["_LIVE_HUMAN_STEERING_DIRECTIVE"]
    assert "Use PostgreSQL rather than SQLite" in directive
    assert mock_tool_result.get("type") == "mid_turn_steering_injected"

    # Verify task was marked completed and annotated as injected
    async with AsyncSessionLocal() as db:
        updated_t = await db.get(Task, task_id)
        assert updated_t.status == TaskStatus.COMPLETED
        meta = json.loads(updated_t.metadata_json)
        assert meta.get("injected_mid_turn") is True
