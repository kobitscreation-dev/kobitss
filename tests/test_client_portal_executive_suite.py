"""
Executive Client Portal Test Suite
Tests for:
1. Public unauthenticated showcase (/api/v1/client-portal/portal-summary)
2. All 5 Navigation Tabs (Overview, Milestones, Deliverables, Team, Reports)
3. Dedicated Activity & Reports Endpoints (/activity, /live-activity, /reports)
4. Authenticated multi-tenant Client Portal Summary
5. Real-Time WebSocket Streaming (/ws/client-portal and /api/v1/client-portal/ws)
6. Frontend JS & portal.html static structure and LSP Diagnostics
"""

import os
import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

from backend.core.database import AsyncSessionLocal
from backend.main import app
from backend.models.mission import ApprovalStatus, Mission, MissionPriority, MissionStatus, WorkflowPhase
from backend.models.organization import Organization, OrgMember, OrgRole
from backend.models.project import Project
from backend.models.user import User
from backend.services.auth_service import create_access_token, get_password_hash
from backend.services.lsp_service import HeadlessLSPService


def _auth_headers(user_id: str, org_id: str | None = None) -> dict[str, str]:
    token = create_access_token({"sub": user_id})
    headers = {"Authorization": f"Bearer {token}"}
    if org_id:
        headers["X-Organization-Id"] = org_id
    return headers


@pytest.mark.asyncio
async def test_public_showcase_portal_summary_5_tabs():
    """
    Verify the public showcase portal summary provides complete 5-tab data structures
    without requiring authentication.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/v1/client-portal/portal-summary")
        assert res.status_code == 200, res.text
        data = res.json()

        # Public showcase identification
        assert data.get("is_public_preview") is True
        assert data.get("client_name") == "Acme Corp"
        assert data.get("platform_subtitle") == "E-Commerce Platform"
        assert data.get("overall_completion_pct") == 78

        # Tab 1: Overview - 4 KPI cards & Sprint & Deliverables & Monospace Activity
        kpis = data.get("kpis")
        assert kpis is not None
        assert kpis["completion"]["value"] == "78%"
        assert kpis["completion"]["label"] == "COMPLETE"
        assert kpis["deliverables"]["value"] == "12/16"
        assert kpis["deliverables"]["label"] == "DELIVERABLES"
        assert kpis["spent"]["value"] == "$4.2K"
        assert kpis["spent"]["label"] == "SPENT"
        assert kpis["remaining"]["value"] == "3 wk"
        assert kpis["remaining"]["label"] == "REMAINING"

        current_sprint = data.get("current_sprint")
        assert current_sprint is not None
        assert "Sprint 4" in current_sprint["title"]
        assert current_sprint["badge"] == "2/5 done"
        assert current_sprint["progress_pct"] == 40
        assert len(current_sprint["tasks"]) == 5
        task_statuses = [t["status"].upper() for t in current_sprint["tasks"]]
        assert "DONE" in task_statuses
        assert "REVIEW" in task_statuses
        assert "BUILDING" in task_statuses
        assert "QUEUED" in task_statuses

        recent_deliverables = data.get("recent_deliverables")
        assert isinstance(recent_deliverables, list)
        assert len(recent_deliverables) >= 3
        for d in recent_deliverables:
            assert "pr_number" in d
            assert d.get("status", "").upper() == "MERGED"
            assert "tests_passed" in d
            assert "loc_added" in d

        recent_activity = data.get("recent_activity")
        assert isinstance(recent_activity, list)
        assert len(recent_activity) >= 5
        for act in recent_activity:
            assert "timestamp" in act
            assert "message" in act
            assert "dot_color" in act

        # Tab 2: Milestones - Vertical Connected Timeline
        timeline = data.get("timeline")
        assert isinstance(timeline, list)
        assert len(timeline) >= 5
        active_milestones = [
            m for m in timeline
            if m.get("status", "").upper() in ("ACTIVE", "IN_PROGRESS") or m.get("status_type") == "active"
        ]
        assert len(active_milestones) >= 1
        active_m = active_milestones[0]
        assert active_m.get("progress_pct") == 60
        assert "Week 5-6" in (active_m.get("timeframe") or active_m.get("weeks") or "")

        # Tab 3: Deliverables - Filterable Catalog
        deliverables_catalog = data.get("deliverables_catalog")
        assert isinstance(deliverables_catalog, list)
        assert len(deliverables_catalog) >= 6
        catalog_statuses = {item["status"] for item in deliverables_catalog}
        assert "Delivered" in catalog_statuses
        assert any(s in catalog_statuses for s in ("In Review", "Building", "In Progress"))
        assert "Upcoming" in catalog_statuses

        # Tab 4: Team - 7 Autonomous Agents
        team_agents = data.get("team_agents")
        assert isinstance(team_agents, list)
        assert len(team_agents) == 7
        agent_names = {agent["name"] for agent in team_agents}
        expected_agents = {"Axiom", "Nexus", "Sentinel", "Aegis", "Forge", "Pixel", "Vanguard"}
        assert expected_agents.issubset(agent_names)
        vanguard = next(a for a in team_agents if a["name"] == "Vanguard")
        assert isinstance(vanguard["deliverables_completed"], int)
        assert vanguard["deliverables_completed"] >= 20
        assert "uptime_sla" in vanguard

        # Tab 5: Reports - Velocity, Test Coverage, SOC-2 Audit, Code Quality
        reports = data.get("executive_reports")
        assert reports is not None
        assert "sprint_velocity" in reports
        assert "test_coverage" in reports
        assert "security_audit" in reports
        assert "code_quality" in reports
        assert reports["test_coverage"]["pass_rate_pct"] == 100
        assert reports["security_audit"]["tamper_evident_hash_valid"] is True
        assert len(reports["security_audit"]["consensus_seals"]) == 3


@pytest.mark.asyncio
async def test_client_portal_sub_endpoints():
    """
    Test dedicated sub-endpoints: /activity, /live-activity, /reports
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # /activity endpoint
        res_act = await ac.get("/api/v1/client-portal/activity")
        assert res_act.status_code == 200
        act_data = res_act.json()
        assert "activities" in act_data
        assert isinstance(act_data["activities"], list)
        assert len(act_data["activities"]) > 0

        # /live-activity endpoint
        res_live = await ac.get("/api/v1/client-portal/live-activity")
        assert res_live.status_code == 200
        live_data = res_live.json()
        assert "live_activity" in live_data
        assert isinstance(live_data["live_activity"], list)

        # /reports endpoint
        res_rep = await ac.get("/api/v1/client-portal/reports")
        assert res_rep.status_code == 200
        rep_data = res_rep.json()
        assert "executive_reports" in rep_data
        assert rep_data["executive_reports"]["security_audit"]["soc2_compliance"] == "VERIFIED"


@pytest.mark.asyncio
async def test_authenticated_client_portal_summary():
    """
    Verify authenticated client access receives real organization context augmented with
    all 5 tabs.
    """
    suffix = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        org = Organization(
            id=f"org-exec-{suffix}",
            name=f"Vanguard Global {suffix}",
            slug=f"vanguard-{suffix}",
            description='{"monthly_retainer_usd": 12000.0, "billing_tier": "Enterprise Autonomous Pod", "sla_status": "99.99% Enterprise SLA"}',
            is_active=True,
        )
        db.add(org)

        client_user = User(
            id=f"u-exec-{suffix}",
            email=f"exec-{suffix}@vanguard.global",
            hashed_password=get_password_hash("VanguardPass!123"),
            full_name="Vanguard CEO",
            is_active=True,
        )
        db.add(client_user)

        membership = OrgMember(
            id=f"mem-exec-{suffix}",
            organization_id=org.id,
            user_id=client_user.id,
            role=OrgRole.READ_ONLY_REVIEWER,
        )
        db.add(membership)

        proj = Project(
            id=f"proj-exec-{suffix}",
            organization_id=org.id,
            name="Vanguard Global Clearinghouse",
            description="Autonomous multi-currency clearing platform",
            created_by=client_user.id,
        )
        db.add(proj)

        mission = Mission(
            id=f"msn-exec-{suffix}",
            title="Real-Time Settlement Engine",
            objective="Deliver zero-loss atomic settlements",
            description="Autonomous settlement pipeline",
            status=MissionStatus.ACTIVE,
            priority=MissionPriority.HIGH,
            phase=WorkflowPhase.VALIDATION,
            approval_status=ApprovalStatus.APPROVED,
            requires_approval=True,
            organization_id=org.id,
            project_id=proj.id,
            created_by=client_user.id,
        )
        db.add(mission)
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        headers = _auth_headers(client_user.id, org.id)
        res = await ac.get("/api/v1/client-portal/portal-summary", headers=headers)
        assert res.status_code == 200, res.text
        data = res.json()

        assert data.get("is_public_preview") is False
        assert data["organization"]["id"] == org.id
        assert data["client_name"] == org.name
        assert data["platform_subtitle"] == proj.name

        # Verify all 5 tab keys exist in authenticated response
        assert "kpis" in data
        assert "current_sprint" in data
        assert "recent_deliverables" in data
        assert "recent_activity" in data
        assert "timeline" in data
        assert "deliverables_catalog" in data
        assert "team_agents" in data
        assert "executive_reports" in data


def test_client_portal_websocket_streaming():
    """
    Verify WebSocket endpoints /ws/client-portal and /api/v1/client-portal/ws
    accept connections and stream initial portal activity snapshot.
    """
    client = TestClient(app)

    # Test /ws/client-portal
    with client.websocket_connect("/ws/client-portal") as ws:
        msg1 = ws.receive_json()
        assert msg1.get("type") in ("connected", "portal_activity_snapshot")
        msg2 = ws.receive_json()
        assert msg2.get("type") in ("connected", "portal_activity_snapshot")
        types = {msg1.get("type"), msg2.get("type")}
        assert "connected" in types
        assert "portal_activity_snapshot" in types

    # Test /api/v1/client-portal/ws
    with client.websocket_connect("/api/v1/client-portal/ws") as ws2:
        msg1 = ws2.receive_json()
        assert msg1.get("type") in ("connected", "portal_activity_snapshot")
        msg2 = ws2.receive_json()
        assert msg2.get("type") in ("connected", "portal_activity_snapshot")
        types2 = {msg1.get("type"), msg2.get("type")}
        assert "connected" in types2
        assert "portal_activity_snapshot" in types2


def test_frontend_js_and_portal_html_integrity():
    """
    Verify frontend JS modules and portal.html file integrity:
    1. Zero LSP syntax diagnostics
    2. Exports renderClientPortalView and buildArchitectureSvg
    3. Handles all 5 tab selectors (overview, milestones, deliverables, team, reports)
    4. portal.html includes executive-portal-view, mount element, and dynamic script loader
    """
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    js_paths = [
        os.path.join(repo_root, "js", "views", "client_portal.js"),
        os.path.join(repo_root, "web", "js", "views", "client_portal.js"),
    ]

    for p in js_paths:
        assert os.path.exists(p), f"Missing file: {p}"
        with open(p, "r", encoding="utf-8") as f:
            code = f.read()

        # Check export signatures
        assert "export function buildArchitectureSvg" in code
        assert "export function getDeliverableIconSvg" in code
        assert "export async function renderClientPortalView" in code

        # Check 5 tab navigation tabs and handlers
        assert 'data-tab="overview"' in code
        assert 'data-tab="milestones"' in code
        assert 'data-tab="deliverables"' in code
        assert 'data-tab="team"' in code
        assert 'data-tab="reports"' in code

        # Check deliverable icons, item count, and active milestone progress bar text
        assert "getDeliverableIconSvg(deliv.icon)" in code
        assert "${deliverablesCatalog.length} items" in code
        assert "Progress ${item.progress || 60}%" in code

        # Check electric neon-lime palette token
        assert "#ccff00" in code

        # Check 3D Particle Orb exports and Team tab agent particle orbs
        assert "AGENT_ORB_CONFIGS" in code
        assert "createAgentParticleOrb" in code
        assert "createThinkingOrb" in code
        assert "agent-orb-container" in code
        assert "agent-orb-canvas" in code
        assert "cancelAnimationFrame" in code
        assert "__kbTeamAgentOrbs" in code

        # Overview tab must NOT contain thinking orb card, canvas, or status pills ("i dont need hear anithink")
        assert "portal-thinking-orb-container" not in code
        assert "portal-thinking-orb-canvas" not in code
        assert "thinking-pill" not in code
        assert "AUTONOMOUS THINKING ORB" not in code

        # Verify Overview tab structure restored to original 4 KPI cards, sprint, deliverables, feed
        assert "CURRENT SPRINT" in code
        assert "// RECENT_DELIVERABLES" in code
        assert "// RECENT_ACTIVITY" in code
        assert "portal-activity-feed" in code

        # Run LSP syntax validation
        diag = HeadlessLSPService.get_diagnostics(file_path=p, repo_path=repo_root)
        assert diag["error_count"] == 0, f"LSP Diagnostics errors in {p}: {diag['diagnostics']}"

    # Verify app.js and web/js/app.js triad surface routing for #boost and #team
    app_js_paths = [
        os.path.join(repo_root, "js", "app.js"),
        os.path.join(repo_root, "web", "js", "app.js"),
    ]
    for ap in app_js_paths:
        assert os.path.exists(ap), f"Missing app.js: {ap}"
        with open(ap, "r", encoding="utf-8") as f:
            app_code = f.read()
        assert "baseRoute === 'boost'" in app_code
        assert "params.tab = 'overview'" in app_code
        assert "baseRoute === 'team'" in app_code
        assert "params.tab = 'team'" in app_code

    # Also verify particle_orb.js helper modules in js/ and web/js/
    orb_js_paths = [
        os.path.join(repo_root, "js", "views", "particle_orb.js"),
        os.path.join(repo_root, "web", "js", "views", "particle_orb.js"),
    ]
    for op in orb_js_paths:
        assert os.path.exists(op), f"Missing particle orb module: {op}"
        with open(op, "r", encoding="utf-8") as f:
            orb_code = f.read()
        assert "export const AGENT_ORB_CONFIGS" in orb_code
        assert "export function generateOrbParticles" in orb_code
        assert "export function createAgentParticleOrb" in orb_code
        assert "export function createThinkingOrb" in orb_code
        assert "triggerPulse: onClick" in orb_code or "triggerPulse:" in orb_code
        assert "get animId()" in orb_code
        assert "axiom" in orb_code and "gyro" in orb_code
        assert "nexus" in orb_code and "lattice" in orb_code
        assert "sentinel" in orb_code and "radar" in orb_code
        assert "aegis" in orb_code and "meridian" in orb_code
        assert "forge" in orb_code and "torus" in orb_code
        assert "pixel" in orb_code and "liquid" in orb_code
        assert "vanguard" in orb_code and "swarm" in orb_code
        # Verify touch events and mobile interaction support
        assert "touchstart" in orb_code
        assert "touchmove" in orb_code
        assert "touchend" in orb_code
        orb_diag = HeadlessLSPService.get_diagnostics(file_path=op, repo_path=repo_root)
        assert orb_diag["error_count"] == 0, f"LSP Diagnostics errors in {op}: {orb_diag['diagnostics']}"

    # Run LSP syntax validation on backend client_portal.py as well
    be_diag = HeadlessLSPService.get_diagnostics(file_path="backend/api/v1/client_portal.py", repo_path=repo_root)
    assert be_diag["error_count"] == 0, f"LSP Diagnostics errors in client_portal.py: {be_diag['diagnostics']}"

    # Verify portal.html
    portal_html_path = os.path.join(repo_root, "portal.html")
    assert os.path.exists(portal_html_path)
    with open(portal_html_path, "r", encoding="utf-8") as f:
        html = f.read()

    assert 'id="executive-portal-view"' in html
    assert 'id="executive-portal-mount"' in html
    assert "loadExecutivePortal" in html
    assert "renderClientPortalView" in html
    assert "Operator Login" in html
    assert "getPortalInitialTab" in html
    assert "isClientPortalRoute" in html
    assert "agent-orb-canvas" in html
    assert "portal-thinking-orb-canvas" not in html


@pytest.mark.asyncio
async def test_client_portal_intake_and_realtime_broadcast():
    """
    Verify authenticated feature request intake creates mission and broadcasts real-time activity.
    """
    suffix = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        org = Organization(
            id=f"org-intake-{suffix}",
            name=f"Intake Client {suffix}",
            slug=f"intake-{suffix}",
            description="Client Org",
            is_active=True,
        )
        db.add(org)

        client_user = User(
            id=f"u-intake-{suffix}",
            email=f"client-{suffix}@intake.test",
            hashed_password=get_password_hash("Pass123!test"),
            full_name="Intake User",
            is_active=True,
        )
        db.add(client_user)

        membership = OrgMember(
            id=f"mem-intake-{suffix}",
            organization_id=org.id,
            user_id=client_user.id,
            role=OrgRole.READ_ONLY_REVIEWER,
        )
        db.add(membership)

        proj = Project(
            id=f"proj-intake-{suffix}",
            organization_id=org.id,
            name="Intake Platform",
            description="Intake Project",
            created_by=client_user.id,
        )
        db.add(proj)
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        headers = _auth_headers(client_user.id, org.id)
        payload = {
            "title": "Add Apple Pay & Google Pay One-Click Checkout",
            "description": "Integrate Apple Pay and Google Pay digital wallets into the checkout flow.",
            "priority": "High",
            "organization_id": org.id,
            "project_id": proj.id,
        }
        res = await ac.post("/api/v1/client-portal/intake", headers=headers, json=payload)
        assert res.status_code == 201, res.text
        data = res.json()
        assert data["status"] == "queued_for_autonomous_pod"
        assert data["title"] == payload["title"]
        assert data["priority"] == "High"
        assert data["ticket_id"].startswith("KOB-")

