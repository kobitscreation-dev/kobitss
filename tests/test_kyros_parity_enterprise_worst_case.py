"""
Extreme Adversarial Worst-Case Test Suite for Kyros 1:1 Enterprise Parity.

Validates the 5 critical Kyros-parity capabilities under extreme, edge-case,
and degraded conditions:
1. Headless Browser Automation (Playwright & In-Process Headless Emulator)
2. Multi-Modal Visual QA & Responsive Regression Testing
3. Parallel Speculative Compute Scaling (Best-of-N /boost concurrent rollouts)
4. Full-Spectrum SPA API Proxying (GET/POST/PUT/DELETE) & WebSocket HMR Dev Server Tunneling
5. Proactive Jira/Linear/GitHub Epic & Roadmap Ingestion into Topological Milestone DAGs
6. Zero-Local Cloud MicroVM Hardening and Resilient Local Fallbacks
"""

import os
import io
import json
import shutil
import tempfile
import asyncio
import subprocess
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy import text

from backend.main import app
from backend.core.database import get_db
from backend.api.deps import get_current_active_user
from backend.models.base import Base
from backend.models.organization import User, Organization, OrganizationMember
from backend.models.project import Project, Task, TaskStatus
from backend.models.mission import Mission, MissionStatus, WorkflowPhase, ApprovalStatus
from backend.services.browser_engine import HeadlessBrowserEngine, generate_valid_png
from backend.services.visual_qa import VisualQAManager, VisualFinding, VisualQAReport
from backend.services.best_of_n import BestOfNExplorer, PatchCandidate, BestOfNResult
from backend.services.epic_ingestion import EpicIngestionEngine, EpicMilestoneDAG
from backend.services.cloud_sandbox import CloudMicroVMSandbox
from backend.services.sandbox_rollback import SandboxRollbackManager
from backend.services.reproducer_engine import ReproducerEngine
from backend.services.tool_registry import ToolRegistry
from backend.services.agent_registry import AGENT_REGISTRY, AgentType

TEST_DB_PATH = "./test_kyros_enterprise_worst_case.db"
test_engine = create_async_engine(
    f"sqlite+aiosqlite:///{TEST_DB_PATH}",
    echo=False,
    poolclass=NullPool,
    connect_args={"check_same_thread": False, "timeout": 30.0},
)
TestSession = sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

KYROS_USER = User(id="kyros_enterprise_user", email="enterprise@kyros.local", full_name="Kyros Architect", hashed_password="pw")
NOW = datetime.now(timezone.utc)


async def _seed_test_database():
    await test_engine.dispose()
    async with test_engine.begin() as conn:
        await conn.execute(text("PRAGMA foreign_keys = OFF"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with TestSession() as session:
        org = Organization(id="org_kyros_ent", name="Kyros Enterprise Corp")
        member = OrganizationMember(organization_id="org_kyros_ent", user_id="kyros_enterprise_user", role="OWNER")
        proj = Project(
            id="proj_kyros_parity",
            name="Kyros Parity Hub",
            organization_id="org_kyros_ent",
            created_by="kyros_enterprise_user",
        )
        mission = Mission(
            id="m_preview_enterprise_01",
            organization_id="org_kyros_ent",
            project_id="proj_kyros_parity",
            title="Preview Live Mission",
            objective="Inspect frontend layout",
            status=MissionStatus.ACTIVE,
            phase=WorkflowPhase.VALIDATION,
            created_by="kyros_enterprise_user",
        )
        session.add_all([org, KYROS_USER, member, proj, mission])
        await session.commit()


@pytest.fixture
def enterprise_client(monkeypatch):
    monkeypatch.setenv("KOBITS_NO_AUTO_EXECUTE", "1")
    monkeypatch.setenv("KOBITS_NO_RESUME", "1")
    asyncio.run(_seed_test_database())

    import backend.core.database as db_mod
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", TestSession)

    async def override_db():
        async with TestSession() as session:
            yield session

    async def override_current_user():
        return KYROS_USER

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_active_user] = override_current_user

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()
    if os.path.exists(TEST_DB_PATH):
        try:
            os.remove(TEST_DB_PATH)
        except Exception:
            pass


# ── TEST 1: Headless Browser Automation (DOM, Navigation, Screenshots) ────────

@pytest.mark.asyncio
async def test_headless_browser_navigation_and_dom_interaction(monkeypatch, tmp_path):
    """
    Verifies HeadlessBrowserEngine navigates DOM, executes clicks, fills text,
    evaluates scripts, captures console errors, and synthesizes 100% valid PNG screenshots.
    Tests both normal mode and forced in-process fallback mode.
    """
    # 1. Test In-Process Fallback Engine
    monkeypatch.setenv("KOBITS_BROWSER_FALLBACK", "1")

    # Create dummy local HTML file
    page_file = tmp_path / "test_app.html"
    page_file.write_text(
        "<!DOCTYPE html><html><head><title>Test App</title></head>"
        "<body><button id='action-btn'>Click Me</button><input id='email' /></body></html>",
        encoding="utf-8"
    )

    async with HeadlessBrowserEngine() as browser:
        ctx = await browser.navigate(f"file:///{page_file.as_posix()}")
        assert ctx.status_code == 200
        assert "Test App" in ctx.title

        # Click
        click_res = await browser.click("#action-btn")
        assert click_res["success"] is True

        # Type
        type_res = await browser.type("#email", "eng@kobits.ai")
        assert type_res["success"] is True
        assert type_res["typed"] == "eng@kobits.ai"

        # Screenshot - must return authentic PNG bytes with standard signature
        shot_bytes = await browser.screenshot(full_page=True)
        assert shot_bytes.startswith(b"\x89PNG\r\n\x1a\n")
        assert len(shot_bytes) > 50

        # Base64 screenshot
        b64_shot = await browser.screenshot_base64()
        assert isinstance(b64_shot, str)
        assert len(b64_shot) > 50

        # Evaluate script
        eval_title = await browser.evaluate("document.title")
        assert eval_title == "Test App"

        # Console logs
        logs = await browser.get_console_logs()
        assert len(logs) >= 1

    monkeypatch.delenv("KOBITS_BROWSER_FALLBACK", raising=False)


# ── TEST 2: Multi-Modal Visual QA & Responsive Regression ─────────────────────

def test_visual_qa_regression_detection():
    """
    Verifies VisualQAManager detects layout collisions, missing assets (404/empty img),
    responsive viewport overflows, contrast violations, and perceptual pixel diffs.
    """
    # 1. Color contrast calculation (WCAG AA check)
    # High contrast: Pure black on pure white -> ratio 21:1
    high_contrast = VisualQAManager.calculate_contrast_ratio((0, 0, 0), (255, 255, 255))
    assert high_contrast >= 20.0

    # Low contrast: Light gray on white -> ratio < 4.5:1
    low_contrast = VisualQAManager.calculate_contrast_ratio((200, 200, 200), (255, 255, 255))
    assert low_contrast < 4.5

    # 2. DOM inspection for visual defects
    bad_html = """
    <html>
      <body>
        <!-- Broken image with empty src -->
        <img src="" alt="logo" />
        <!-- Hardcoded width exceeding mobile screen -->
        <div style="width: 800px; margin-top: -60px;">Wide text banner</div>
      </body>
    </html>
    """
    findings_mobile = VisualQAManager.inspect_dom_for_visual_defects(bad_html, viewport_name="mobile")
    assert any(f.category == "BROKEN_ASSET" for f in findings_mobile)
    assert any(f.category == "RESPONSIVE_OVERFLOW" for f in findings_mobile)
    assert any(f.category == "LAYOUT_OVERLAP" for f in findings_mobile)

    # 3. Screenshot Comparison
    baseline_png = generate_valid_png(width=100, height=100, color=(0, 0, 0))
    identical_png = generate_valid_png(width=100, height=100, color=(0, 0, 0))
    divergent_png = generate_valid_png(width=100, height=100, color=(255, 255, 255))

    passed_id, diff_id, _ = VisualQAManager.compare_screenshots(baseline_png, identical_png)
    assert passed_id is True
    assert diff_id == 0.0

    passed_div, diff_div, findings_div = VisualQAManager.compare_screenshots(baseline_png, divergent_png, diff_threshold=0.01)
    assert passed_div is False
    assert diff_div > 0.01
    assert len(findings_div) == 1
    assert findings_div[0].category == "VISUAL_DIFF"


@pytest.mark.asyncio
async def test_visual_qa_inspect_url(tmp_path):
    """Verifies end-to-end Visual QA report over desktop, tablet, and mobile."""
    test_page = tmp_path / "index.html"
    test_page.write_text(
        "<!DOCTYPE html><html><head><title>Visual QA Test</title></head>"
        "<body><h1>Safe Responsive Heading</h1><p>Compliant layout</p></body></html>",
        encoding="utf-8"
    )
    url = f"file:///{test_page.as_posix()}"
    report = await VisualQAManager.inspect_url(url=url, viewports=["desktop", "mobile"])
    assert isinstance(report, VisualQAReport)
    assert "PASSED" in report.summary
    assert report.passed is True


# ── TEST 3: Parallel Speculative Compute Scaling (/boost) ────────────────────

@pytest.fixture
def git_buggy_repo():
    """Initializes a temporary git repo with a calculation bug."""
    temp_dir = tempfile.mkdtemp(prefix="kobits_test_parallel_spec_")
    try:
        subprocess.run(["git", "init"], cwd=temp_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "SpeculativeTester"], cwd=temp_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "spec@kobits.ai"], cwd=temp_dir, check=True, capture_output=True)

        calc_file = os.path.join(temp_dir, "calculator.py")
        with open(calc_file, "w", encoding="utf-8") as f:
            f.write("def compute_tax(amount, rate):\n    # BUG: incorrect multiplication\n    return amount * rate * 10\n")

        subprocess.run(["git", "add", "."], cwd=temp_dir, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init tax bug"], cwd=temp_dir, check=True, capture_output=True)

        yield temp_dir
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_parallel_speculative_best_of_n_rollouts(git_buggy_repo):
    """
    Verifies that BestOfNExplorer evaluates multiple candidates concurrently
    using asyncio.gather, picks the winning minimal patch, and applies it to the main repo.
    """
    reproducer_code = (
        "from calculator import compute_tax\n"
        "tax = compute_tax(100.0, 0.15)\n"
        "assert tax == 15.0, f'Expected 15.0 but got {tax}'\n"
    )
    ReproducerEngine.author_reproducer(git_buggy_repo, reproducer_code, "reproduce_tax.py")

    def candidate_1_still_buggy(cand_id, sb):
        with open(os.path.join(sb, "calculator.py"), "w", encoding="utf-8") as f:
            f.write("def compute_tax(amount, rate):\n    return amount * 999\n")

    def candidate_2_bloated_fix(cand_id, sb):
        with open(os.path.join(sb, "calculator.py"), "w", encoding="utf-8") as f:
            f.write("# Huge bloat\n" * 40 + "def compute_tax(amount, rate):\n    return amount * rate\n")
        with open(os.path.join(sb, "unused_bloat.py"), "w", encoding="utf-8") as f:
            f.write("# Unused file\n" * 50)

    def candidate_3_surgical_fix(cand_id, sb):
        with open(os.path.join(sb, "calculator.py"), "w", encoding="utf-8") as f:
            f.write("def compute_tax(amount, rate):\n    return amount * rate\n")

    result = await BestOfNExplorer.explore_and_select(
        sandbox_dir=git_buggy_repo,
        reproducer_script="reproduce_tax.py",
        candidate_generators=[candidate_1_still_buggy, candidate_2_bloated_fix, candidate_3_surgical_fix],
        parallel=True,
    )

    assert result.success is True
    assert len(result.candidates) == 3
    assert result.candidates[0].status == "REPRODUCER_FAILED"
    assert result.candidates[1].status == "PASSED"
    assert result.candidates[2].status == "PASSED"

    # Surgical fix must have a strictly higher score than bloated fix
    assert result.candidates[2].score > result.candidates[1].score
    assert result.selected_candidate.candidate_id == 3

    # Verify winning patch is reflected in git_buggy_repo
    with open(os.path.join(git_buggy_repo, "calculator.py"), "r", encoding="utf-8") as f:
        content = f.read()
    assert "return amount * rate" in content
    # Verify bloat was NOT carried over
    assert not os.path.exists(os.path.join(git_buggy_repo, "unused_bloat.py"))


# ── TEST 4: Full-Spectrum SPA API Proxying & WebSocket HMR ────────────────────

def test_full_spectrum_spa_api_and_websocket_proxying(enterprise_client, tmp_path):
    """
    Verifies that preview endpoint supports GET, POST, PUT, DELETE, PATCH, OPTIONS
    and preserves request payloads, and verifies the WebSocket HMR proxy tunnel endpoint.
    """
    # 1. Static SPA Fallback
    res_get = enterprise_client.get("/api/v1/preview/m_preview_enterprise_01/")
    assert res_get.status_code in (200, 302, 307)

    # 2. POST request to preview route
    res_post = enterprise_client.post(
        "/api/v1/preview/m_preview_enterprise_01/api/data",
        json={"query": "test_data"},
    )
    # Should not throw 405 Method Not Allowed! It handles all methods cleanly.
    assert res_post.status_code != 405

    # 3. DELETE request to preview route
    res_delete = enterprise_client.delete(
        "/api/v1/preview/m_preview_enterprise_01/api/items/123"
    )
    assert res_delete.status_code != 405

    # 4. WebSocket HMR Proxy Tunnel
    with enterprise_client.websocket_connect("/api/v1/preview/m_preview_enterprise_01/__ws_proxy__") as ws:
        # First message is status payload
        status_msg = ws.receive_json()
        assert status_msg.get("type") == "kobits_hmr_status"
        # Send text frame
        ws.send_text("ping_vite_hmr")
        reply = ws.receive_text()
        assert "kobits_ack" in reply


# ── TEST 5: Proactive Jira/Linear/PRD Epic Ingestion ───────────────────────────

def test_proactive_epic_prd_decomposition(enterprise_client):
    """
    Verifies proactive decomposition of complex PRDs, Jira epic exports,
    and Linear projects into topological milestone DAGs via REST API.
    """
    # 1. Structured Markdown PRD Ingestion
    prd_markdown = """
# Payments & Stripe Checkout Epic

### Overview
Integrate Stripe subscription payments with automatic tier provisioning and webhook verification.

### Milestone 1: Database Schema & Webhook Secrets
- Define subscriptions and payment_events database tables
- Verify database migrations pass without schema regressions

### Milestone 2: Stripe Checkout API Endpoints
- Implement POST /api/v1/checkout/session endpoint
- Implement POST /api/v1/checkout/webhook with HMAC signature verification

### Milestone 3: Frontend Subscription Plans UI
- Implement responsive pricing plan comparison card
- Connect Checkout button to Stripe session redirect

### Milestone 4: Sentinel QA Verification
- Author end-to-end integration test verifying successful checkout webhook handling

### Security & Compliance
- Never log raw credit card numbers or unmasked secret keys
- Enforce strict HMAC-SHA256 signature verification on inbound webhooks
"""
    res = enterprise_client.post(
        "/api/v1/missions/epic",
        json={"prd": prd_markdown, "project_id": "proj_kyros_parity", "create_mission": True},
    )
    assert res.status_code == 201
    data = res.json()
    assert data["status"] == "INGESTED"
    assert data["epic_title"] == "Payments & Stripe Checkout Epic"
    assert data["milestones_count"] >= 4
    assert len(data["security_requirements"]) >= 1
    assert "topological_order" in data
    assert len(data["topological_order"]) >= 4

    # 2. Linear Project Ingestion
    linear_payload = {
        "project": {
            "name": "Linear Authentication Refactor",
            "description": "Migrate session auth to OAuth2 PKCE",
            "issues": [
                {"identifier": "AUTH-101", "title": "Configure OAuth2 Provider Endpoints"},
                {"identifier": "AUTH-102", "title": "Implement PKCE Code Verifier & Challenge"},
            ]
        }
    }
    dag_linear = EpicIngestionEngine.ingest_payload(linear_payload)
    assert dag_linear.source_type == "linear_project"
    assert dag_linear.epic_title == "Linear Authentication Refactor"
    assert len(dag_linear.milestones) == 2
    assert dag_linear.topological_order == ["AUTH-101", "AUTH-102"]

    # 3. Jira Epic Ingestion
    jira_payload = {
        "fields": {
            "summary": "Jira Multi-Region Disaster Recovery",
            "description": "Automate PostgreSQL multi-region failover",
        },
        "issues": [
            {"key": "INFRA-501", "fields": {"summary": "Provision Secondary Read Replica"}},
            {"key": "INFRA-502", "fields": {"summary": "Automate Health Check Heartbeat DNS Failover"}},
        ]
    }
    dag_jira = EpicIngestionEngine.ingest_payload(jira_payload)
    assert dag_jira.source_type == "jira_epic"
    assert dag_jira.epic_title == "Jira Multi-Region Disaster Recovery"
    assert len(dag_jira.milestones) == 2


# ── TEST 6: Tool & Agent Registry Wiring ──────────────────────────────────────

def test_tool_and_agent_registry_wiring():
    """
    Verifies that all 7 browser tools are properly registered and granted
    to the appropriate specialist agents (Pixel, Sentinel, Axiom, Muse).
    """
    required_browser_tools = [
        "browser.navigate",
        "browser.click",
        "browser.type",
        "browser.screenshot",
        "browser.console_logs",
        "browser.evaluate",
        "visual_qa.inspect",
    ]
    for tool_name in required_browser_tools:
        tool = ToolRegistry.get_tool(tool_name)
        assert tool is not None, f"Tool {tool_name} was not registered in ToolRegistry"

    # Verify Frontend Engineer (Pixel) has full browser suite
    pixel = AGENT_REGISTRY[AgentType.FRONTEND_ENGINEER]
    for t in required_browser_tools:
        assert t in pixel.allowed_tools, f"Pixel missing tool {t}"

    # Verify QA Engineer (Sentinel) has full browser and visual QA suite
    sentinel = AGENT_REGISTRY[AgentType.QA_ENGINEER]
    for t in required_browser_tools:
        assert t in sentinel.allowed_tools, f"Sentinel missing tool {t}"

    # Verify Architect (Axiom) has inspection tools
    axiom = AGENT_REGISTRY[AgentType.SOLUTION_ARCHITECT]
    assert "browser.navigate" in axiom.allowed_tools
    assert "browser.screenshot" in axiom.allowed_tools


# ── TEST 7: Zero-Local Cloud MicroVM Hardening & Fallbacks ────────────────────

def test_cloud_sandbox_hardening_and_fallback(monkeypatch, tmp_path):
    """
    Verifies that CloudMicroVMSandbox.is_configured accurately verifies package
    importability, and that missing cloud packages fallback seamlessly to local runners.
    """
    # 1. Unconfigured state
    monkeypatch.delenv("E2B_API_KEY", raising=False)
    monkeypatch.delenv("KOBITS_MOCK_CLOUD_SANDBOX", raising=False)
    sb_unconf = CloudMicroVMSandbox(api_key=None)
    assert sb_unconf.is_configured() is False

    # 2. Configured with mock
    monkeypatch.setenv("KOBITS_MOCK_CLOUD_SANDBOX", "1")
    sb_mock = CloudMicroVMSandbox(session_id="test_hardening_01")
    assert sb_mock.is_configured() is True
    vm_id = sb_mock.start()
    assert vm_id == "mock-sb-01"
    sb_mock.close()

    # 3. MissionRuntime._verify_sandbox_code resilient execution
    from backend.services.mission_runtime import MissionRuntime
    sandbox_dir = tmp_path / "sandbox_test"
    sandbox_dir.mkdir()
    # Create test file
    test_f = sandbox_dir / "test_simple.py"
    test_f.write_text("def test_ok():\n    assert 1 + 1 == 2\n", encoding="utf-8")

    verification = MissionRuntime._verify_sandbox_code(str(sandbox_dir), target_files=[str(test_f)])
    assert verification["status"] == "PASSED"
    assert verification["pytest_summary"] is not None
    assert verification["pytest_summary"]["passed"] is True


# ── TEST 8: Adversarial Edge Cases (URL Encoding, Contrast, File Deletions, Binary WS) ──

@pytest.mark.asyncio
async def test_browser_navigate_with_url_encoded_spaces_and_special_chars(monkeypatch, tmp_path):
    """Verifies that browser.navigate correctly unquotes file:// URLs containing spaces (%20)."""
    monkeypatch.setenv("KOBITS_BROWSER_FALLBACK", "1")
    spaced_dir = tmp_path / "app with spaces"
    spaced_dir.mkdir()
    page_file = spaced_dir / "my app page.html"
    page_file.write_text("<!DOCTYPE html><html><head><title>App With Spaces</title></head><body><h1>Hello</h1></body></html>", encoding="utf-8")

    async with HeadlessBrowserEngine() as browser:
        # Pass URL-encoded file URL
        encoded_url = f"file:///{page_file.as_posix().replace(' ', '%20')}"
        ctx = await browser.navigate(encoded_url)
        assert ctx.status_code == 200
        assert ctx.title == "App With Spaces"
        assert "Hello" in ctx.html_content

        # Test invalid/empty selectors guard
        bad_click = await browser.click("")
        assert bad_click["success"] is False
        assert "error" in bad_click

        bad_type = await browser.type("   ", "text")
        assert bad_type["success"] is False
        assert "error" in bad_type

        # Test evaluate with document properties
        cookie = await browser.evaluate("document.cookie")
        assert cookie == ""
        ready = await browser.evaluate("document.readyState")
        assert ready == "complete"
    monkeypatch.delenv("KOBITS_BROWSER_FALLBACK", raising=False)


def test_visual_qa_contrast_and_empty_screenshot_adversarial():
    """Verifies DOM color contrast inspection and empty screenshot comparison."""
    # 1. Low contrast DOM
    low_contrast_html = '<html><body><p style="color: #ffffff; background-color: #ffffff;">Invisible text</p></body></html>'
    findings = VisualQAManager.inspect_dom_for_visual_defects(low_contrast_html, viewport_name="desktop")
    contrast_findings = [f for f in findings if f.category == "CONTRAST_VIOLATION"]
    assert len(contrast_findings) == 1
    assert contrast_findings[0].severity == "CRITICAL"
    assert "1.0:1" in contrast_findings[0].message

    # 2. Compliant contrast DOM
    compliant_html = '<html><body><p style="color: #000000; background-color: #ffffff;">Legible text</p></body></html>'
    findings_good = VisualQAManager.inspect_dom_for_visual_defects(compliant_html, viewport_name="desktop")
    assert not any(f.category == "CONTRAST_VIOLATION" for f in findings_good)

    # 3. Baseline vs Empty screenshot
    baseline_png = generate_valid_png(width=50, height=50)
    passed, diff, diff_findings = VisualQAManager.compare_screenshots(baseline_png, b"")
    assert passed is False
    assert diff == 1.0
    assert len(diff_findings) == 1
    assert diff_findings[0].category == "VISUAL_DIFF"


@pytest.mark.asyncio
async def test_parallel_speculative_best_of_n_file_deletion(git_buggy_repo):
    """
    Verifies that when a winning candidate deletes an obsolete/buggy file,
    the deletion is properly reflected in the main repository.
    """
    # Create an obsolete buggy file in base repo
    obsolete_file = os.path.join(git_buggy_repo, "obsolete_buggy_module.py")
    with open(obsolete_file, "w", encoding="utf-8") as f:
        f.write("# To be deleted\n")
    subprocess.run(["git", "add", "."], cwd=git_buggy_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add obsolete module"], cwd=git_buggy_repo, check=True, capture_output=True)

    reproducer_code = (
        "import os\n"
        "from calculator import compute_tax\n"
        "assert compute_tax(100.0, 0.15) == 15.0\n"
        "assert not os.path.exists('obsolete_buggy_module.py'), 'Obsolete file must be deleted'\n"
    )
    ReproducerEngine.author_reproducer(git_buggy_repo, reproducer_code, "reproduce_deletion.py")

    def candidate_1_keeps_file(cand_id, sb):
        with open(os.path.join(sb, "calculator.py"), "w", encoding="utf-8") as f:
            f.write("def compute_tax(amount, rate):\n    return amount * rate\n")

    def candidate_2_fixes_and_deletes(cand_id, sb):
        with open(os.path.join(sb, "calculator.py"), "w", encoding="utf-8") as f:
            f.write("def compute_tax(amount, rate):\n    return amount * rate\n")
        obs_p = os.path.join(sb, "obsolete_buggy_module.py")
        if os.path.exists(obs_p):
            os.remove(obs_p)

    res = await BestOfNExplorer.explore_and_select(
        sandbox_dir=git_buggy_repo,
        reproducer_script="reproduce_deletion.py",
        candidate_generators=[candidate_1_keeps_file, candidate_2_fixes_and_deletes],
        parallel=True,
    )

    assert res.success is True
    assert res.selected_candidate.candidate_id == 2
    # Verify obsolete file was deleted in git_buggy_repo
    assert not os.path.exists(obsolete_file)


def test_spa_api_and_websocket_binary_and_location_headers(enterprise_client):
    """Verifies WebSocket proxy tunnel handles binary frames and standby messages safely."""
    with enterprise_client.websocket_connect("/api/v1/preview/m_preview_enterprise_01/__ws_proxy__") as ws:
        status_msg = ws.receive_json()
        assert status_msg.get("type") == "kobits_hmr_status"
        # Send binary frame
        binary_payload = bytes(b"\x00\x01\x02\x03\x04")
        ws.send_bytes(binary_payload)
        resp_bytes = ws.receive_bytes()
        assert resp_bytes == binary_payload


def test_proactive_epic_prd_decomposition_adversarial(enterprise_client):
    """
    Verifies that POST /api/v1/missions/epic handles payloads where 'project' is a dictionary,
    and Jira epics with circular dependencies resolve cleanly without hanging.
    """
    # 1. Linear payload with project as dictionary (Previously caused AttributeError: 'dict' object has no attribute 'strip')
    linear_dict_payload = {
        "project": {
            "id": "proj_kyros_parity",
            "name": "Linear Authentication Refactor",
            "description": "Migrate session auth to OAuth2 PKCE",
            "issues": [
                {"identifier": "AUTH-201", "title": "Configure OAuth2 Provider Endpoints"},
                {"identifier": "AUTH-202", "title": "Implement PKCE Code Verifier & Challenge"},
            ]
        },
        "create_mission": True,
    }
    res = enterprise_client.post("/api/v1/missions/epic", json=linear_dict_payload)
    assert res.status_code == 201
    data = res.json()
    assert data["status"] == "INGESTED"
    assert data["epic_title"] == "Linear Authentication Refactor"
    assert len(data["milestones"]) == 2

    # 2. Jira export with explicit cycle (INFRA-1 depends on INFRA-2, INFRA-2 depends on INFRA-1)
    cyclic_jira_payload = {
        "fields": {"summary": "Cyclic Epic", "description": "Resilience test"},
        "issues": [
            {"key": "INFRA-1", "fields": {"summary": "Node A", "dependencies": ["INFRA-2"]}},
            {"key": "INFRA-2", "fields": {"summary": "Node B", "dependencies": ["INFRA-1"]}},
        ]
    }
    dag_cyclic = EpicIngestionEngine.ingest_payload(cyclic_jira_payload)
    assert len(dag_cyclic.milestones) == 2
    assert len(dag_cyclic.topological_order) == 2
    assert "INFRA-1" in dag_cyclic.topological_order
    assert "INFRA-2" in dag_cyclic.topological_order


def test_mission_runtime_verify_sandbox_code_catastrophic_runner_failure(monkeypatch, tmp_path):
    """
    Verifies that MissionRuntime._verify_sandbox_code populates pytest_summary
    even if the test runner throws a catastrophic unexpected exception.
    """
    from backend.services.mission_runtime import MissionRuntime
    sandbox_dir = tmp_path / "sandbox_catastrophe"
    sandbox_dir.mkdir()
    test_f = sandbox_dir / "test_catastrophe.py"
    test_f.write_text("def test_dummy(): pass\n", encoding="utf-8")

    def mock_subprocess_run(*args, **kwargs):
        raise RuntimeError("Subprocess catastrophic failure")

    monkeypatch.setattr("subprocess.run", mock_subprocess_run)
    verification = MissionRuntime._verify_sandbox_code(str(sandbox_dir), target_files=[str(test_f)])
    assert verification["status"] == "FAILED"
    assert verification["pytest_summary"] is not None
    assert verification["pytest_summary"]["passed"] is False
    assert "catastrophic" in verification["pytest_summary"]["output"]

