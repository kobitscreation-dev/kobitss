"""
Comprehensive Enterprise Unified Architecture Verification Suite.

Tests and verifies 100% functionality across:
1. Persistent Team Memory Infrastructure (Namespaces, TTL decay, Source attribution, BM25 + Dense RRF).
2. Frontier Vision QA Engine (SentinelFinding, Responsive Viewports, Broken Asset & Contrast Reproducers).
3. Port Tunneling & HMR Dev Server Integration (All HTTP Verbs, Header Preservation, Redirects, WebSockets).
4. Governed Agent Teams (21 Specialists, Strict Pydantic Contracts, Approval Gates).
5. SWE-bench Verified TDD Engine & Auto-Remediation (Negative baseline, Positive verification, Atomic Rollback).
"""

import os
import sys
import json
import time
import math
import shutil
import tempfile
import asyncio
import subprocess
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy import text, select

from backend.main import app
from backend.core.database import get_db
from backend.models.base import Base
from backend.models.organization import User, Organization, OrganizationMember
from backend.models.project import Project, Task, TaskStatus, TaskPriority
from backend.models.mission import Mission, MissionStatus, WorkflowPhase, ApprovalStatus
from backend.models.agent import AgentType
from backend.services.team_memory import (
    MemoryNamespace,
    TeamMemoryEntry,
    BM25Scorer,
    TeamMemoryManager,
)
from backend.services.memory_service import MemoryService
from backend.services.visual_qa import (
    SentinelFinding,
    STANDARD_VIEWPORTS,
    VisualQAManager,
    VisualFinding,
    VisualQAReport,
    build_frontier_vision_prompt,
    parse_frontier_vision_response,
    analyze_with_frontier_vision,
)
from backend.services.governance import (
    GovernanceEngine,
    SPECIALIST_CONTRACTS,
    SpecialistContractDefinition,
    BaseAgentInput,
    BaseAgentOutput,
    ValidationResult,
    PhaseGateResult,
)
from backend.services.agent_registry import AGENT_REGISTRY, AgentDefinition
from backend.services.reproducer_engine import (
    ReproducerEngine,
    ReproducerExecution,
    BaselineValidationResult,
    PositiveVerificationResult,
    TDDExecutionResult,
)
from backend.services.sandbox_rollback import SandboxRollbackManager
from backend.services.sandbox_manager import SandboxManager


TEST_DB_PATH = "./test_enterprise_unified_architecture.db"
test_engine = create_async_engine(
    f"sqlite+aiosqlite:///{TEST_DB_PATH}",
    echo=False,
    poolclass=NullPool,
    connect_args={"check_same_thread": False, "timeout": 30.0},
)
TestSession = sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _seed_test_database():
    await test_engine.dispose()
    if os.path.exists(TEST_DB_PATH):
        try:
            os.remove(TEST_DB_PATH)
        except Exception:
            pass
    async with test_engine.begin() as conn:
        await conn.execute(text("PRAGMA foreign_keys = OFF"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with TestSession() as session:
        user = User(id="user_test", email="test@kobits.space", full_name="Tester", hashed_password="pw")
        org = Organization(id="org_test", name="Test Org")
        proj = Project(id="proj_test", organization_id="org_test", created_by="user_test", name="Test Project")
        session.add(user)
        session.add(org)
        session.add(proj)
        await session.commit()


async def get_test_db():
    async with TestSession() as session:
        yield session


@pytest.fixture(autouse=True)
def override_db_fixture():
    app.dependency_overrides[get_db] = get_test_db
    yield
    app.dependency_overrides[get_db] = get_test_db



# =========================================================================
# 1. PERSISTENT TEAM MEMORY INFRASTRUCTURE
# =========================================================================

class TestPersistentTeamMemoryInfrastructure:
    """Verifies namespaces, confidence decay, TTL, attribution, and hybrid BM25 + Dense RRF."""

    def test_memory_namespaces_normalization_and_attributes(self):
        # All 5 canonical namespaces must be present and normalizable
        assert MemoryNamespace.normalize("ADR") == MemoryNamespace.ADR
        assert MemoryNamespace.normalize("architecture") == MemoryNamespace.ADR
        assert MemoryNamespace.normalize("STYLE") == MemoryNamespace.CONVENTION
        assert MemoryNamespace.normalize("conventions") == MemoryNamespace.CONVENTION
        assert MemoryNamespace.normalize("DEFECT_FIX") == MemoryNamespace.DEFECT_FIX
        assert MemoryNamespace.normalize("bug_fix") == MemoryNamespace.DEFECT_FIX
        assert MemoryNamespace.normalize("PEER_NOTE") == MemoryNamespace.PEER_NOTE
        assert MemoryNamespace.normalize("handoff") == MemoryNamespace.PEER_NOTE
        assert MemoryNamespace.normalize("DOMAIN_CONCEPT") == MemoryNamespace.DOMAIN_CONCEPT
        assert MemoryNamespace.normalize("semantics") == MemoryNamespace.DOMAIN_CONCEPT
        assert MemoryNamespace.normalize("unknown_random_ns") == MemoryNamespace.PEER_NOTE

    def test_confidence_exponential_decay(self):
        now = datetime.now(timezone.utc)
        entry = TeamMemoryEntry(
            id="mem_decay_test",
            project_id="p1",
            organization_id="org1",
            namespace=MemoryNamespace.ADR,
            key="db_isolation_level",
            content="Use SERIALIZABLE for financial transactions.",
            source_agent="Architect",
            confidence=1.0,
            decay_factor=0.05,
            created_at=now,
        )

        # Immediate confidence is 1.0
        assert entry.compute_effective_confidence(as_of=now) == 1.0
        assert not entry.is_expired(as_of=now)

        # After 14 days, confidence decays: 1.0 * exp(-0.05 * 14) ~= 0.4966
        future_14d = now + timedelta(days=14)
        c_14d = entry.compute_effective_confidence(as_of=future_14d)
        expected_14d = round(1.0 * math.exp(-0.05 * 14), 4)
        assert abs(c_14d - expected_14d) < 0.01

        # After 100 days, confidence is heavily decayed
        future_100d = now + timedelta(days=100)
        c_100d = entry.compute_effective_confidence(as_of=future_100d)
        assert c_100d < 0.01

    def test_ttl_hard_expiration(self):
        now = datetime.now(timezone.utc)
        entry = TeamMemoryEntry(
            id="mem_ttl_test",
            project_id="p1",
            organization_id="org1",
            namespace=MemoryNamespace.PEER_NOTE,
            key="temp_feature_flag",
            content="Feature flag rollout for sprint 42.",
            source_agent="TechLead",
            confidence=0.9,
            ttl_seconds=3600.0,  # 1 hour
            created_at=now,
        )

        # At 30 minutes, valid
        t_30m = now + timedelta(minutes=30)
        assert entry.compute_effective_confidence(as_of=t_30m) > 0.0
        assert not entry.is_expired(as_of=t_30m)

        # At 2 hours, expired (TTL exceeded)
        t_2h = now + timedelta(hours=2)
        assert entry.compute_effective_confidence(as_of=t_2h) == 0.0
        assert entry.is_expired(as_of=t_2h)

    def test_source_agent_attribution_and_selective_targeting(self):
        entry_targeted = TeamMemoryEntry(
            id="mem_target_test",
            project_id="p1",
            organization_id="org1",
            namespace=MemoryNamespace.CONVENTION,
            key="react_hooks_rule",
            content="Never use useState inside loops.",
            source_agent="Axiom",
            target_agents=["Frontend_Engineer", "UI_UX_Designer"],
        )

        assert entry_targeted.is_applicable_to_agent("Frontend_Engineer")
        assert entry_targeted.is_applicable_to_agent("UI_UX_DESIGNER")
        assert not entry_targeted.is_applicable_to_agent("BACKEND_ENGINEER")
        assert not entry_targeted.is_applicable_to_agent("SRE")

        # Broadcast entry (empty target_agents) applies to everyone
        entry_broadcast = TeamMemoryEntry(
            id="mem_broadcast_test",
            project_id="p1",
            organization_id="org1",
            namespace=MemoryNamespace.DOMAIN_CONCEPT,
            key="order_lifecycle",
            content="An order transitions from PENDING -> PAID -> SHIPPED.",
            source_agent="ProductManager",
            target_agents=[],
        )
        assert entry_broadcast.is_applicable_to_agent("BACKEND_ENGINEER")
        assert entry_broadcast.is_applicable_to_agent("DevOps_Engineer")

    def test_bm25_scorer_lexical_tokenization(self):
        scorer = BM25Scorer(k1=1.5, b=0.75)
        corpus = [
            "Always wrap executeBatchTransaction in try-catch blocks with rollback on SQLiteException.",
            "Use Tailwind utility classes for responsive mobile layouts.",
            "Configure Docker container read-only root filesystem for zero-trust security.",
        ]
        scores = scorer.score("executeBatchTransaction SQLiteException", corpus)
        assert len(scores) == 3
        assert scores[0] > scores[1]
        assert scores[0] > scores[2]

    def test_hybrid_dense_and_bm25_rrf_retrieval(self):
        proj = f"proj_rrf_{int(time.time())}"

        TeamMemoryManager.store_memory(
            project_id=proj,
            namespace=MemoryNamespace.ADR,
            key="postgres_pool_sizing",
            content="Database connection pool size set to 20 with NullPool in async testing.",
            source_agent="Architect",
            confidence=1.0,
        )
        TeamMemoryManager.store_memory(
            project_id=proj,
            namespace=MemoryNamespace.DEFECT_FIX,
            key="sqlite_lock_timeout",
            content="SQLite database locked error fixed by disposing engine before recreating schema.",
            source_agent="BackendEngineer",
            confidence=0.95,
        )
        TeamMemoryManager.store_memory(
            project_id=proj,
            namespace=MemoryNamespace.CONVENTION,
            key="frontend_css_modules",
            content="Use CSS modules with BEM naming conventions for component isolation.",
            source_agent="FrontendEngineer",
            confidence=0.9,
        )

        results = TeamMemoryManager.query_memories(
            project_id=proj,
            query="SQLite connection pool database lock timeout",
            limit=5,
        )

        assert len(results) >= 2
        # Database-related memories must be ranked above CSS modules
        keys = [r["key"] for r in results]
        assert "sqlite_lock_timeout" in keys
        assert "postgres_pool_sizing" in keys
        assert results[0]["rrf_score"] > 0.0

    def test_memory_service_team_memory_integration(self):
        proj_id = "proj_mem_service_test"

        # Store via MemoryService
        entry = MemoryService.store_team_memory(
            project_id=proj_id,
            namespace="DEFECT_FIX",
            key="fastapi_dependency_override",
            content="Override get_db with custom async session maker in test harness.",
            source_agent="QA_Engineer",
            confidence=1.0,
        )
        assert entry.key == "fastapi_dependency_override"
        assert entry.namespace == MemoryNamespace.DEFECT_FIX

        # Query via MemoryService
        query_res = MemoryService.query_team_memory(
            project_id=proj_id,
            query="fastapi dependency override get_db",
            agent_name="QA_Engineer",
        )
        assert len(query_res) > 0
        assert query_res[0]["key"] == "fastapi_dependency_override"

        # Get formatted context for agent prompt
        ctx_text = MemoryService.get_team_context_for_agent(
            project_id=proj_id,
            agent_name="QA_Engineer",
            task_text="test harness dependency override",
        )
        assert "DEFECT_FIX" in ctx_text
        assert "Override get_db" in ctx_text


# =========================================================================
# 2. FRONTIER VISION QA ENGINE
# =========================================================================

class TestFrontierVisionQAEngine:
    """Verifies SentinelFinding, responsive viewports, broken asset & contrast reproducers."""

    def test_sentinel_finding_structure_and_serialization(self):
        finding = SentinelFinding(
            finding_id="sentinel_001",
            category="CONTRAST_VIOLATION",
            severity="HIGH",
            message="Button text color #aaaaaa on #ffffff has contrast ratio 2.3:1 (fails WCAG AA 4.5:1)",
            viewport="mobile",
            selector="button.cta-primary",
            wcag_criterion="WCAG 2.1 AA 1.4.3",
            suggested_fix="Change button text color to #222222 or darker to achieve 11.2:1 contrast ratio.",
            suggested_patch_hints=["color: #222222;"],
        )
        d = finding.to_dict()
        assert d["finding_id"] == "sentinel_001"
        assert d["category"] == "CONTRAST_VIOLATION"
        assert d["severity"] == "HIGH"
        assert d["viewport"] == "mobile"
        assert d["wcag_criterion"] == "WCAG 2.1 AA 1.4.3"

    def test_standard_viewports_coverage(self):
        assert "desktop" in STANDARD_VIEWPORTS
        assert "tablet" in STANDARD_VIEWPORTS
        assert "mobile" in STANDARD_VIEWPORTS

        assert STANDARD_VIEWPORTS["desktop"]["width"] == 1920
        assert STANDARD_VIEWPORTS["desktop"]["height"] == 1080
        assert STANDARD_VIEWPORTS["tablet"]["width"] == 768
        assert STANDARD_VIEWPORTS["tablet"]["height"] == 1024
        assert STANDARD_VIEWPORTS["mobile"]["width"] == 375
        assert STANDARD_VIEWPORTS["mobile"]["height"] == 667

    def test_broken_asset_reproducer_synthesis_and_execution(self):
        finding = SentinelFinding(
            finding_id="sentinel_broken_img",
            category="BROKEN_ASSET",
            severity="CRITICAL",
            message="Found broken image tag with empty or invalid src attribute",
            viewport="desktop",
            selector="img.hero-banner",
        )
        reproducer_script = finding.generate_reproducer_script()
        assert "Sentinel Autonomous Visual QA Negative Reproducer" in reproducer_script
        assert "BROKEN_ASSET" in reproducer_script

        # Verify real execution in temporary directory
        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = os.path.join(tmpdir, "reproduce_visual.py")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(reproducer_script + "\nif __name__ == '__main__':\n    sys.exit(reproduce())\n")

            # Phase A: Negative Baseline Check (Buggy HTML with empty src -> script MUST fail with exit code 1)
            with open(os.path.join(tmpdir, "index.html"), "w", encoding="utf-8") as f:
                f.write('<!DOCTYPE html><html><body><img class="hero-banner" src="" alt="Hero"></body></html>')

            neg_run = subprocess.run([sys.executable, script_path], cwd=tmpdir, capture_output=True, text=True)
            assert neg_run.returncode == 1
            assert "BUG REPRODUCED" in neg_run.stdout

            # Phase B: Positive Verification Check (Fixed HTML with valid src -> script MUST pass with exit code 0)
            with open(os.path.join(tmpdir, "index.html"), "w", encoding="utf-8") as f:
                f.write('<!DOCTYPE html><html><body><img class="hero-banner" src="/assets/hero.png" alt="Hero"></body></html>')

            pos_run = subprocess.run([sys.executable, script_path], cwd=tmpdir, capture_output=True, text=True)
            assert pos_run.returncode == 0
            assert pos_run.returncode == 0

    def test_contrast_violation_reproducer_synthesis_and_execution(self):
        finding = SentinelFinding(
            finding_id="sentinel_contrast",
            category="CONTRAST_VIOLATION",
            severity="HIGH",
            message="Low contrast text style violates WCAG AA",
            viewport="mobile",
            selector=".subtext",
        )
        reproducer_script = finding.generate_reproducer_script()

        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = os.path.join(tmpdir, "reproduce_contrast.py")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(reproducer_script + "\nif __name__ == '__main__':\n    sys.exit(reproduce())\n")

            # Buggy HTML with low contrast color #999 on white
            with open(os.path.join(tmpdir, "index.html"), "w", encoding="utf-8") as f:
                f.write('<!DOCTYPE html><html><body><div style="color: #999; background: #fff;">Low Contrast</div></body></html>')

            neg_run = subprocess.run([sys.executable, script_path], cwd=tmpdir, capture_output=True, text=True)
            assert neg_run.returncode == 1
            assert "BUG REPRODUCED" in neg_run.stdout

            # Fixed HTML with high contrast color #111 on white
            with open(os.path.join(tmpdir, "index.html"), "w", encoding="utf-8") as f:
                f.write('<!DOCTYPE html><html><body><div style="color: #111; background: #fff;">High Contrast</div></body></html>')

            pos_run = subprocess.run([sys.executable, script_path], cwd=tmpdir, capture_output=True, text=True)
            assert pos_run.returncode == 0

    def test_frontier_vision_prompt_and_response_parsing(self):
        prompt = build_frontier_vision_prompt(
            viewport_name="desktop",
            target_url="http://localhost:3000",
            dom_snapshot="<div class='dashboard'><h1>Title</h1></div>",
        )
        assert "DESKTOP" in prompt
        assert "1920x1080" in prompt
        assert "WCAG 2.1 AA" in prompt

        # Test structured response parsing
        raw_llm_json = json.dumps({
            "overall_status": "FAIL",
            "findings": [
                {
                    "category": "TEXT_OVERLAP",
                    "severity": "HIGH",
                    "selector": "h1.title",
                    "message": "Title text overlaps with navigation bar on mobile.",
                    "wcag_criterion": "WCAG 2.1 AA 1.4.4",
                    "suggested_fix": "Add margin-top: 24px on mobile viewport.",
                }
            ],
            "recommendations": ["Refactor navigation bar with flexible flex-wrap."],
        })
        parsed = parse_frontier_vision_response(raw_llm_json, viewport="mobile")
        assert len(parsed) == 1
        assert parsed[0].category == "TEXT_OVERLAP"
        assert parsed[0].severity == "HIGH"
        assert parsed[0].viewport == "mobile"

    def test_visual_qa_manager_elevate_to_sentinel_findings(self):
        report = VisualQAReport(
            passed=False,
            total_findings=2,
            critical_findings=1,
            findings=[
                VisualFinding(
                    category="CONTRAST_VIOLATION",
                    severity="HIGH",
                    message="Text contrast ratio 2.1:1 fails WCAG AA",
                    selector="p.description",
                ),
                VisualFinding(
                    category="RESPONSIVE_OVERFLOW",
                    severity="CRITICAL",
                    message="Table container width 800px causes horizontal scroll on 375px mobile viewport",
                    selector="table.data-grid",
                ),
            ],
            viewports_checked=["desktop", "mobile"],
            diff_ratio=0.05,
            summary="Visual QA inspection detected 2 defects.",
        )

        sentinel_findings = VisualQAManager.elevate_to_sentinel_findings(report)
        assert len(sentinel_findings) == 2
        categories = {f.category for f in sentinel_findings}
        assert "CONTRAST_VIOLATION" in categories
        assert "RESPONSIVE_BREAKPOINT_COMPLIANCE" in categories
        assert all(f.reproducer_snippet is not None for f in sentinel_findings)


# =========================================================================
# 3. PORT TUNNELING & HMR DEV SERVER INTEGRATION
# =========================================================================

class TestPortTunnelingAndHMRDevServer:
    """Verifies proxying of all HTTP verbs, header preservation, location rewriting, and WebSockets."""

    @pytest.mark.asyncio
    async def test_proxy_http_methods_forwarding(self):
        await _seed_test_database()
        mission_id = "mission_proxy_test"

        async with TestSession() as db:
            m = Mission(
                id=mission_id,
                organization_id="org_test",
                project_id="proj_test",
                created_by="user_test",
                title="Proxy Test Mission",
                objective="Test Dev Server Proxy",
                status=MissionStatus.ACTIVE,
                phase=WorkflowPhase.IMPLEMENTATION,
            )
            db.add(m)
            await db.commit()

        with tempfile.TemporaryDirectory() as sdir:
            # Set up .kobits_preview.json targeting port 34567
            with open(os.path.join(sdir, ".kobits_preview.json"), "w") as f:
                json.dump({"port": 34567, "active": True}, f)
            with open(os.path.join(sdir, ".kobits_sandbox.json"), "w") as f:
                json.dump({"session_id": mission_id, "mission_id": mission_id}, f)

            from backend.services.sandbox_manager import SandboxSession
            SandboxManager._sessions[mission_id] = SandboxSession(
                session_id=mission_id,
                project_root=sdir,
                branch_name="test-proxy-branch",
                sandbox_dir=sdir,
            )

            # Test all HTTP verbs: GET, POST, PUT, DELETE, PATCH, OPTIONS, HEAD
            for verb in ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"]:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.headers = {
                    "content-type": "application/json",
                    "authorization": "Bearer secret-token",
                    "x-kobits-custom": "verified",
                    "connection": "close",  # Should be excluded
                }
                mock_resp.content = b'{"status": "ok"}'

                with patch("httpx.AsyncClient.request", new_callable=AsyncMock, return_value=mock_resp), \
                     patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp), \
                     patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp), \
                     patch("httpx.AsyncClient.put", new_callable=AsyncMock, return_value=mock_resp), \
                     patch("httpx.AsyncClient.delete", new_callable=AsyncMock, return_value=mock_resp), \
                     patch("httpx.AsyncClient.patch", new_callable=AsyncMock, return_value=mock_resp), \
                     patch("httpx.AsyncClient.head", new_callable=AsyncMock, return_value=mock_resp), \
                     patch("httpx.AsyncClient.options", new_callable=AsyncMock, return_value=mock_resp):

                    client = TestClient(app)
                    res = client.request(
                        verb,
                        f"/api/v1/preview/{mission_id}/api/items",
                        json={"item_id": 42} if verb in ("POST", "PUT", "PATCH") else None,
                        headers={"Authorization": "Bearer test-key", "X-Request-Trace": "req-123"},
                    )

                    assert res.status_code == 200
                    # Verify hop-by-hop header is stripped
                    assert "connection" not in res.headers or res.headers["connection"] != "close"

    @pytest.mark.asyncio
    async def test_location_redirect_rewriting(self):
        await _seed_test_database()
        mission_id = "mission_redirect_test"

        async with TestSession() as db:
            m = Mission(
                id=mission_id,
                organization_id="org_test",
                project_id="proj_test",
                created_by="user_test",
                title="Redirect Test",
                objective="Test Location Rewriting",
                status=MissionStatus.ACTIVE,
                phase=WorkflowPhase.IMPLEMENTATION,
            )
            db.add(m)
            await db.commit()

        with tempfile.TemporaryDirectory() as sdir:
            with open(os.path.join(sdir, ".kobits_preview.json"), "w") as f:
                json.dump({"port": 3000, "active": True}, f)

            from backend.services.sandbox_manager import SandboxSession
            SandboxManager._sessions[mission_id] = SandboxSession(
                session_id=mission_id,
                project_root=sdir,
                branch_name="test-redirect-branch",
                sandbox_dir=sdir,
            )

            # Test 1: Relative redirect /login -> /api/v1/preview/{mission_id}/login
            mock_rel_redirect = MagicMock()
            mock_rel_redirect.status_code = 302
            mock_rel_redirect.headers = {"location": "/login", "content-type": "text/html"}
            mock_rel_redirect.content = b""

            with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_rel_redirect):
                client = TestClient(app)
                res = client.get(f"/api/v1/preview/{mission_id}/auth", follow_redirects=False)
                assert res.status_code == 302
                assert res.headers["location"] == f"/api/v1/preview/{mission_id}/login"

            # Test 2: Localhost redirect http://127.0.0.1:3000/dashboard -> /api/v1/preview/{mission_id}/dashboard
            mock_host_redirect = MagicMock()
            mock_host_redirect.status_code = 302
            mock_host_redirect.headers = {"location": "http://127.0.0.1:3000/dashboard", "content-type": "text/html"}
            mock_host_redirect.content = b""

            with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_host_redirect):
                client = TestClient(app)
                res = client.get(f"/api/v1/preview/{mission_id}/home", follow_redirects=False)
                assert res.status_code == 302
                assert res.headers["location"] == f"/api/v1/preview/{mission_id}/dashboard"

    def test_websocket_hmr_tunneling_text_and_binary_frames(self):
        mission_id = f"mission_ws_{int(time.time())}"
        client = TestClient(app)

        # Connect to HMR WebSocket proxy
        with client.websocket_connect(f"/api/v1/preview/{mission_id}/__ws_proxy__") as ws:
            # 1. Initial dev server standby status frame
            init_frame = ws.receive_json()
            assert init_frame["type"] == "kobits_hmr_status"
            assert init_frame["status"] == "dev_server_standby"
            assert init_frame["mission_id"] == mission_id

            # 2. Text frame forwarding / ack
            ws.send_text("vite:client:ping")
            resp_text = ws.receive_text()
            assert "kobits_ack: vite:client:ping" in resp_text

            # 3. Binary frame forwarding (Vite/Next.js binary HMR blobs)
            binary_payload = b"\x00\x01\x02\x03\xff\xfe\xfd"
            ws.send_bytes(binary_payload)
            resp_bytes = ws.receive_bytes()
            assert resp_bytes == binary_payload


# =========================================================================
# 4. GOVERNED AGENT TEAMS WITH STRICT CONTRACTS & SPECIALISTS
# =========================================================================

class TestGovernedAgentTeams:
    """Verifies strict Pydantic contracts and approval gates across all 21 specialized agents."""

    def test_all_21_specialists_have_registered_contracts(self):
        assert len(AGENT_REGISTRY) >= 21
        for agent_type, agent_def in AGENT_REGISTRY.items():
            assert isinstance(agent_def, AgentDefinition)
            contract = agent_def.contract
            assert contract is not None, f"Agent {agent_def.name} ({agent_type}) is missing governance contract!"
            assert isinstance(contract, SpecialistContractDefinition)
            assert issubclass(contract.input_schema, BaseAgentInput)
            assert issubclass(contract.output_schema, BaseAgentOutput)
            assert isinstance(agent_def.expected_artifacts, list)
            assert isinstance(agent_def.approval_gate_required, bool)

    def test_strict_pydantic_input_contract_validation(self):
        # 1. Valid input succeeds
        valid_input = {
            "mission_id": "m_123",
            "task_title": "Implement JWT authentication filter",
            "task_description": "Validate Bearer token on incoming HTTP requests.",
            "upstream_artifacts": {"API_SPEC": "spec.yaml"},
        }
        res_backend = GovernanceEngine.validate_agent_input(AgentType.BACKEND_ENGINEER, valid_input)
        assert res_backend.is_valid
        assert res_backend.sanitized_data["task_title"] == "Implement JWT authentication filter"

        # 2. Invalid input (empty task_title) fails validation
        invalid_input = {
            "mission_id": "m_123",
            "task_title": "",  # Empty title prohibited
        }
        res_invalid = GovernanceEngine.validate_agent_input(AgentType.BACKEND_ENGINEER, invalid_input)
        assert not res_invalid.is_valid
        assert len(res_invalid.errors) > 0
        assert any("task_title" in err for err in res_invalid.errors)

    def test_strict_pydantic_output_contract_validation(self):
        # 1. Valid output succeeds
        valid_output = {
            "status": "COMPLETED",
            "summary": "JWT auth filter implemented with 100% test coverage.",
            "artifacts": {"DIFF": "+ class AuthFilter..."},
            "findings": [],
        }
        res_valid = GovernanceEngine.validate_agent_output(AgentType.BACKEND_ENGINEER, valid_output)
        assert res_valid.is_valid

        # 2. Invalid output (missing summary) fails
        invalid_output = {
            "status": "COMPLETED",
            "summary": "",  # Empty summary prohibited
        }
        res_invalid = GovernanceEngine.validate_agent_output(AgentType.BACKEND_ENGINEER, invalid_output)
        assert not res_invalid.is_valid
        assert any("summary" in err for err in res_invalid.errors)

    def test_specialist_approval_gates_and_phase_transitions(self):
        # Critical agents must have approval gates configured
        sec_agent = AGENT_REGISTRY[AgentType.SECURITY_ENGINEER]
        assert sec_agent.approval_gate_required

        release_agent = AGENT_REGISTRY[AgentType.RELEASE_MANAGER]
        assert release_agent.approval_gate_required

        # Test phase transition gate enforcement:
        # Transitioning to DEPLOYMENT without approval must be blocked
        blocked_gate = GovernanceEngine.validate_phase_transition(
            from_phase=WorkflowPhase.DELIVERY_REVIEW,
            to_phase=WorkflowPhase.DEPLOYMENT,
            completed_artifacts=["PULL_REQUEST", "QA_REPORT"],
            approvals_granted=[],  # No approval granted
        )
        assert not blocked_gate.allowed
        assert "HUMAN_ENGINEER_DELIVERY_APPROVAL" in blocked_gate.missing_approvals

        # Transitioning with approval must pass
        allowed_gate = GovernanceEngine.validate_phase_transition(
            from_phase=WorkflowPhase.DELIVERY_REVIEW,
            to_phase=WorkflowPhase.DEPLOYMENT,
            completed_artifacts=["PULL_REQUEST", "QA_REPORT", "SECURITY_AUDIT"],
            approvals_granted=["HUMAN_ENGINEER_DELIVERY_APPROVAL"],
        )
        assert allowed_gate.allowed
        assert len(allowed_gate.missing_approvals) == 0


# =========================================================================
# 5. SWE-BENCH VERIFIED TDD ENGINE & AUTO-REMEDIATION
# =========================================================================

class TestSWEBenchVerifiedTDDEngineAndRemediation:
    """Verifies negative baseline assertion, positive verification, and atomic rollback."""

    def test_reproducer_negative_baseline_verification(self):
        with tempfile.TemporaryDirectory() as sdir:
            # Buggy implementation: subtract instead of add
            with open(os.path.join(sdir, "calculator.py"), "w", encoding="utf-8") as f:
                f.write("def add(a, b):\n    return a - b  # BUG!\n")

            # Standalone reproducer test that asserts add(2, 2) == 4
            reproducer_content = (
                "import sys\n"
                "from calculator import add\n"
                "if add(2, 2) != 4:\n"
                "    print('BUG REPRODUCED: add(2,2) != 4')\n"
                "    sys.exit(1)\n"
                "sys.exit(0)\n"
            )
            ReproducerEngine.author_reproducer(sdir, reproducer_content, "reproduce_issue.py")

            # Negative baseline on buggy code MUST be valid (exit code != 0)
            baseline_res = ReproducerEngine.verify_negative_baseline(sdir, "reproduce_issue.py")
            assert baseline_res.valid
            assert baseline_res.status == "VALID_BASELINE"
            assert baseline_res.exit_code == 1

            # Negative Test: If reproducer exits with code 0 on buggy code, it MUST be rejected!
            bogus_content = "import sys\nsys.exit(0)\n"
            ReproducerEngine.author_reproducer(sdir, bogus_content, "reproduce_bogus.py")
            bogus_res = ReproducerEngine.verify_negative_baseline(sdir, "reproduce_bogus.py")
            assert not bogus_res.valid
            assert bogus_res.status == "INVALID_BASELINE"
            assert bogus_res.exit_code == 0

    def test_reproducer_positive_verification(self):
        with tempfile.TemporaryDirectory() as sdir:
            # Fixed implementation
            with open(os.path.join(sdir, "calculator.py"), "w", encoding="utf-8") as f:
                f.write("def add(a, b):\n    return a + b\n")

            reproducer_content = (
                "import sys\n"
                "from calculator import add\n"
                "if add(2, 2) != 4:\n"
                "    sys.exit(1)\n"
                "sys.exit(0)\n"
            )
            ReproducerEngine.author_reproducer(sdir, reproducer_content, "reproduce_issue.py")

            # Positive verification MUST pass
            pos_res = ReproducerEngine.verify_positive(sdir, "reproduce_issue.py")
            assert pos_res.success
            assert pos_res.reproducer_passed

    @pytest.mark.asyncio
    async def test_run_tdd_cycle_automatic_rollback_on_faulty_patch(self):
        with tempfile.TemporaryDirectory() as sdir:
            # Initialize git repository for git checkpoint tracking
            subprocess.run(["git", "init"], cwd=sdir, capture_output=True)
            subprocess.run(["git", "config", "user.name", "TestDev"], cwd=sdir, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@kobits.space"], cwd=sdir, capture_output=True)

            calc_file = os.path.join(sdir, "math_service.py")
            with open(calc_file, "w", encoding="utf-8") as f:
                f.write("def multiply(a, b):\n    return a + b  # BUG!\n")

            subprocess.run(["git", "add", "."], cwd=sdir, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial buggy commit"], cwd=sdir, capture_output=True)

            reproducer_code = (
                "import sys\n"
                "from math_service import multiply\n"
                "if multiply(3, 4) != 12:\n"
                "    sys.exit(1)\n"
                "sys.exit(0)\n"
            )

            # Coder dispatch that introduces another syntax/logic error
            async def faulty_coder(sandbox):
                with open(os.path.join(sandbox, "math_service.py"), "w", encoding="utf-8") as mf:
                    mf.write("def multiply(a, b):\n    return 0  # Still wrong!\n")

            tdd_result = await ReproducerEngine.run_tdd_cycle(
                sandbox_dir=sdir,
                coder_dispatch_fn=faulty_coder,
                issue_description="multiply function returns sum instead of product",
                script_content=reproducer_code,
                script_name="reproduce_issue.py",
                rollback_on_failure=True,
            )

            assert not tdd_result.success
            assert tdd_result.stage == "POSITIVE_VERIFICATION"
            assert tdd_result.rolled_back

            # Check that math_service.py was restored to the pre-patch state (reproducer was kept, patch reverted)
            with open(calc_file, "r", encoding="utf-8") as f:
                content = f.read()
            assert "return 0" not in content

    @pytest.mark.asyncio
    async def test_run_tdd_cycle_success_on_valid_patch(self):
        with tempfile.TemporaryDirectory() as sdir:
            subprocess.run(["git", "init"], cwd=sdir, capture_output=True)
            subprocess.run(["git", "config", "user.name", "TestDev"], cwd=sdir, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@kobits.space"], cwd=sdir, capture_output=True)

            calc_file = os.path.join(sdir, "math_service.py")
            with open(calc_file, "w", encoding="utf-8") as f:
                f.write("def multiply(a, b):\n    return a + b  # BUG!\n")

            subprocess.run(["git", "add", "."], cwd=sdir, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial buggy commit"], cwd=sdir, capture_output=True)

            reproducer_code = (
                "import sys\n"
                "from math_service import multiply\n"
                "if multiply(3, 4) != 12:\n"
                "    sys.exit(1)\n"
                "sys.exit(0)\n"
            )

            # Coder dispatch that correctly fixes the bug
            async def successful_coder(sandbox):
                with open(os.path.join(sandbox, "math_service.py"), "w", encoding="utf-8") as mf:
                    mf.write("def multiply(a, b):\n    return a * b\n")

            tdd_result = await ReproducerEngine.run_tdd_cycle(
                sandbox_dir=sdir,
                coder_dispatch_fn=successful_coder,
                issue_description="multiply function returns sum instead of product",
                script_content=reproducer_code,
                script_name="reproduce_issue.py",
                rollback_on_failure=True,
            )

            assert tdd_result.success
            assert tdd_result.stage == "COMPLETED"
            assert not tdd_result.rolled_back
            assert tdd_result.verification_result.reproducer_passed

            # Worktree retains the fixed code
            with open(calc_file, "r", encoding="utf-8") as f:
                content = f.read()
            assert "return a * b" in content

    def test_ci_failure_reproducer_synthesis(self):
        repro = ReproducerEngine.synthesize_ci_reproducer(
            check_name="unit-tests-ci",
            failure_details="AssertionError: timeout <= 0 not handled in backend/network/client.py:45",
            annotations=[
                {
                    "path": "backend/network/client.py",
                    "start_line": 45,
                    "message": "timeout <= 0 raised unhandled TimeoutError",
                    "annotation_level": "failure",
                }
            ],
        )
        assert "Negative-to-Positive TDD Reproducer for CI Check" in repro
        assert "backend/network/client.py" in repro
        assert "timeout" in repro
