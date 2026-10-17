"""
Verification Suite for the 3-Tier Agency Dashboard System:
1. Dashboard 1: Executive Client Portal (`/api/v1/client-portal/*`, `#portal`)
2. Dashboard 2: Agency Operations & Financial Hub (`/api/v1/agency-admin/*`, `#admin`)
3. Dashboard 3: Deep Systems & MicroVM Mission Control (`/api/v1/agency-admin/console/*`, `#console`)
"""

import uuid
import pytest
from httpx import ASGITransport, AsyncClient

from backend.core.database import AsyncSessionLocal
from backend.main import app
from backend.models.mission import ApprovalStatus, Milestone, MilestoneStatus, Mission, MissionPriority, MissionStatus, WorkflowPhase
from backend.models.organization import Organization, OrgMember, OrgRole
from backend.models.project import Project
from backend.models.user import User
from backend.services.auth_service import create_access_token, get_password_hash



def _auth_headers(user_id: str, org_id: str | None = None) -> dict[str, str]:
    token = create_access_token({"sub": user_id})
    headers = {"Authorization": f"Bearer {token}"}
    if org_id:
        headers["X-Organization-Id"] = org_id
    return headers


@pytest.mark.asyncio
async def test_three_dashboard_triad_full_lifecycle():
    """
    End-to-end verification of:
    1. Multi-Tenant Data Isolation (HTTP 403 on cross-tenant access across Portal, Discovery, Preview, Admin)
    2. RBAC Surface Isolation (Client & Member blocked with HTTP 403 from Dashboard 2 & Dashboard 3)
    3. Dashboard 1 Executive Client Portal (Sprint Hero, 4-Week Roadmap, Living Architecture Map, 3 Consensus Seals, Multi-Device Preview, ADR Log, Intake)
    4. Dashboard 2 Agency Operations & Financial Hub (P&L Margin math, 5% Operator Approval Gate: Approve, Tweak, Reject, and 1-Click Client Onboarding)
    5. Dashboard 3 Deep Systems & MicroVM Mission Control (21-Agent DAG, Headless LSP Explorer, MicroVM Cgroup & Egress Firewall telemetry)
    """
    suffix = uuid.uuid4().hex[:8]

    async with AsyncSessionLocal() as db:
        # Create two distinct Client Organizations (Tenant A & Tenant B)
        org_a = Organization(
            id=f"org-alpha-{suffix}",
            name=f"Alpha FinTech {suffix}",
            slug=f"alpha-fintech-{suffix}",
            description='{"monthly_retainer_usd": 6500.0, "billing_tier": "Autonomous Pod Retainer", "sla_status": "99.9% On-Time SLA"}',
            is_active=True,
        )
        org_b = Organization(
            id=f"org-beta-{suffix}",
            name=f"Beta BioLabs {suffix}",
            slug=f"beta-biolabs-{suffix}",
            description='{"monthly_retainer_usd": 8000.0, "billing_tier": "Autonomous Pod Retainer", "sla_status": "99.9% On-Time SLA"}',
            is_active=True,
        )
        db.add_all([org_a, org_b])

        # Users:
        # 1. Agency Admin (manages Org A and Org B)
        # 2. Lead Engineer (manages Org A)
        # 3. Standard Member in Org A (no approval/admin rights)
        # 4. Client A Executive (READ_ONLY_REVIEWER in Org A only)
        # 5. Client B Executive (READ_ONLY_REVIEWER in Org B only)
        admin_user = User(
            id=f"u-admin-{suffix}",
            email=f"admin-{suffix}@kobits.agency",
            hashed_password=get_password_hash("AdminPass!123"),
            full_name="Agency Managing Partner",
            is_active=True,
        )
        lead_user = User(
            id=f"u-lead-{suffix}",
            email=f"lead-{suffix}@kobits.agency",
            hashed_password=get_password_hash("LeadPass!123"),
            full_name="Principal Lead Engineer",
            is_active=True,
        )
        member_user = User(
            id=f"u-member-{suffix}",
            email=f"member-{suffix}@kobits.agency",
            hashed_password=get_password_hash("MemberPass!123"),
            full_name="Junior Member",
            is_active=True,
        )
        client_a_user = User(
            id=f"u-client-a-{suffix}",
            email=f"ceo-{suffix}@alphafintech.io",
            hashed_password=get_password_hash("ClientAPass!123"),
            full_name="Alpha FinTech CEO",
            is_active=True,
        )
        client_b_user = User(
            id=f"u-client-b-{suffix}",
            email=f"cto-{suffix}@betabiolabs.io",
            hashed_password=get_password_hash("ClientBPass!123"),
            full_name="Beta BioLabs CTO",
            is_active=True,
        )
        db.add_all([admin_user, lead_user, member_user, client_a_user, client_b_user])
        await db.flush()

        # Memberships
        db.add_all(
            [
                OrgMember(id=str(uuid.uuid4()), user_id=admin_user.id, organization_id=org_a.id, role=OrgRole.ADMIN),
                OrgMember(id=str(uuid.uuid4()), user_id=admin_user.id, organization_id=org_b.id, role=OrgRole.ADMIN),
                OrgMember(id=str(uuid.uuid4()), user_id=lead_user.id, organization_id=org_a.id, role=OrgRole.LEAD_ENGINEER),
                OrgMember(id=str(uuid.uuid4()), user_id=member_user.id, organization_id=org_a.id, role=OrgRole.MEMBER),
                OrgMember(id=str(uuid.uuid4()), user_id=client_a_user.id, organization_id=org_a.id, role=OrgRole.READ_ONLY_REVIEWER),
                OrgMember(id=str(uuid.uuid4()), user_id=client_b_user.id, organization_id=org_b.id, role=OrgRole.READ_ONLY_REVIEWER),
            ]
        )

        # Projects for Tenant A and Tenant B
        proj_a = Project(
            id=f"proj-a-{suffix}",
            name="Alpha Core Ledger",
            description="Zero-trust payment ledger",
            repository_url=".",
            organization_id=org_a.id,
            created_by=admin_user.id,
        )
        proj_b = Project(
            id=f"proj-b-{suffix}",
            name="Beta Genomics Engine",
            description="Genomic pipeline",
            repository_url=".",
            organization_id=org_b.id,
            created_by=admin_user.id,
        )
        db.add_all([proj_a, proj_b])
        await db.flush()

        # Seed a Milestone on Project A
        from backend.models.mission import MilestoneStatus
        ms_a1 = Milestone(
            id=f"ms-a1-{suffix}",
            title="Week 1: Architecture Discovery & Security Hardening",
            description="Baseline audit and test suite",
            status=MilestoneStatus.IN_PROGRESS,
            progress=50,
            project_id=proj_a.id,
        )
        db.add(ms_a1)

        # Seed a Mission on Tenant B (to test cross-tenant preview & mission isolation)
        mission_b = Mission(
            id=f"msn-b-{suffix}",
            title="Beta Confidential Pipeline",
            objective="Confidential tenant B mission",
            description="Confidential tenant B mission",
            status=MissionStatus.AWAITING_APPROVAL,
            priority=MissionPriority.HIGH,
            phase=WorkflowPhase.DELIVERY_REVIEW,
            approval_status=ApprovalStatus.PENDING,
            requires_approval=True,
            organization_id=org_b.id,
            project_id=proj_b.id,
            created_by=admin_user.id,
        )
        db.add(mission_b)
        await db.commit()


    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # =====================================================================
        # 1. MULTI-TENANT DATA ISOLATION TESTS (Client A vs Client B -> 403)
        # =====================================================================
        headers_client_a = _auth_headers(client_a_user.id, org_a.id)
        headers_client_b = _auth_headers(client_b_user.id, org_b.id)

        # Client A can read their own Executive Portal Summary
        res_a = await ac.get("/api/v1/client-portal/portal-summary", headers=headers_client_a)
        assert res_a.status_code == 200, res_a.text
        portal_a = res_a.json()
        assert portal_a["organization"]["id"] == org_a.id
        assert portal_a["project"]["id"] == proj_a.id
        assert "sprint_hero" in portal_a
        assert "compute_transparency" in portal_a["sprint_hero"]
        assert len(portal_a["milestone_roadmap"]) >= 1
        assert 0 <= portal_a["architecture_map"]["health_score"] <= 100
        mermaid_src = portal_a["architecture_map"]["mermaid_diagram"]
        assert "flowchart TD" in mermaid_src or "graph TD" in mermaid_src


        # Client A attempting to access Tenant B's portal via query param -> 403 Forbidden
        res_cross_portal_q = await ac.get(
            f"/api/v1/client-portal/portal-summary?organization_id={org_b.id}",
            headers=headers_client_a,
        )
        assert res_cross_portal_q.status_code == 403

        # Client A attempting to access Tenant B's portal via X-Organization-Id header -> 403 Forbidden
        res_cross_portal_h = await ac.get(
            "/api/v1/client-portal/portal-summary",
            headers=_auth_headers(client_a_user.id, org_b.id),
        )
        assert res_cross_portal_h.status_code == 403

        # Client A attempting to submit intake into Tenant B's project -> 403 Forbidden
        res_cross_intake = await ac.post(
            "/api/v1/client-portal/intake",
            headers=headers_client_a,
            json={
                "title": "Unauthorized Cross-Tenant Injection",
                "description": "Trying to write into Org B",
                "priority": "Urgent",
                "organization_id": org_b.id,
            },
        )
        assert res_cross_intake.status_code == 403

        # Client A attempting to view Tenant B's discovery audit report -> 403 Forbidden
        res_cross_disc = await ac.get(
            f"/api/v1/discovery/{proj_b.id}/report",
            headers=headers_client_a,
        )
        assert res_cross_disc.status_code == 403

        # Client A attempting to view Tenant B's live preview -> 403 Forbidden
        res_cross_preview = await ac.get(
            f"/api/v1/preview/{mission_b.id}/info",
            headers=headers_client_a,
        )
        assert res_cross_preview.status_code == 403

        # =====================================================================
        # 2. RBAC SURFACE ISOLATION (Client & Member blocked from Admin & Console)
        # =====================================================================
        restricted_endpoints = [
            ("GET", "/api/v1/agency-admin/financials"),
            ("GET", "/api/v1/admin/financials"),
            ("GET", "/api/v1/agency-admin/clients"),
            ("GET", "/api/v1/agency-admin/approval-queue"),
            ("GET", "/api/v1/agency-admin/console/telemetry"),
            ("GET", "/api/v1/console/telemetry"),
            ("POST", "/api/v1/agency-admin/console/lsp"),
        ]
        for method, ep in restricted_endpoints:
            # Client A (READ_ONLY_REVIEWER) -> 403 Forbidden
            r_client = await ac.request(method, ep, headers=headers_client_a)
            assert r_client.status_code == 403, f"Expected 403 for Client on {method} {ep}, got {r_client.status_code}"

            # Standard Member (MEMBER, rank < LEAD_ENGINEER) -> 403 Forbidden
            r_member = await ac.request(method, ep, headers=_auth_headers(member_user.id, org_a.id))
            assert r_member.status_code == 403, f"Expected 403 for Member on {method} {ep}, got {r_member.status_code}"

        # Lead Engineer belonging ONLY to Org A attempting to access Org B financials -> 403 Forbidden
        r_lead_cross = await ac.get(
            f"/api/v1/agency-admin/financials?organization_id={org_b.id}",
            headers=_auth_headers(lead_user.id, org_b.id),
        )
        assert r_lead_cross.status_code == 403

        # =====================================================================
        # 3. THE 5% OPERATOR APPROVAL GATE WORKFLOW (Intake -> Gate -> Approve)
        # =====================================================================
        # Step 3a: Client A submits a feature request via the Executive Client Portal Intake Modal
        intake_res = await ac.post(
            "/api/v1/client-portal/intake",
            headers=headers_client_a,
            json={
                "title": "Add HMAC-SHA256 Webhook Verification & Idempotency Key",
                "description": "Ensure all incoming Stripe webhooks validate signature headers and prevent duplicate ledger entries.",
                "priority": "Urgent",
                "project_id": proj_a.id,
            },
        )
        assert intake_res.status_code == 201, intake_res.text
        intake_data = intake_res.json()
        mission_a_id = intake_data["mission_id"]
        assert intake_data["ticket_id"].startswith("KOB-")
        assert intake_data["approval_status"] == "PENDING"

        # Step 3b: Verify mission is blocked from direct delivery before passing the 5% Operator Approval Gate
        headers_lead_a = _auth_headers(lead_user.id, org_a.id)
        premature_deliver = await ac.post(
            f"/api/v1/missions/{mission_a_id}/deliver",
            headers=headers_lead_a,
            json={"push_and_open_pr": True},
        )
        assert premature_deliver.status_code == 403

        # Step 3c: Verify mission appears in Dashboard 2's 5% Operator Approval Gate Queue
        queue_res = await ac.get("/api/v1/agency-admin/approval-queue", headers=headers_lead_a)
        assert queue_res.status_code == 200, queue_res.text
        queue_items = queue_res.json()["approval_queue"]
        matching_gate_items = [item for item in queue_items if item["mission_id"] == mission_a_id]
        assert len(matching_gate_items) == 1
        gate_item = matching_gate_items[0]
        assert gate_item["status"] == "AWAITING_APPROVAL"
        assert gate_item["preview_url"] == f"/api/v1/preview/{mission_a_id}/"
        assert gate_item["console_url"] == f"#console/{mission_a_id}"
        assert len(gate_item["microvm_logs"]) >= 3

        # Step 3d: Test "Request Agent Tweak" action at the 5% Gate
        tweak_res = await ac.post(
            "/api/v1/agency-admin/request-tweak",
            headers=headers_lead_a,
            json={
                "mission_id": mission_a_id,
                "feedback": "Enforce constant-time signature comparison via hmac.compare_digest.",
                "target_agent": "Forge",
            },
        )
        assert tweak_res.status_code == 200, tweak_res.text
        tweak_data = tweak_res.json()
        assert tweak_data["status"] == "tweak_dispatched"
        assert tweak_data["mission_status"] == "ACTIVE"

        # Step 3e: Lead Engineer approves & ships PR via `POST /api/v1/agency-admin/approve-pr`
        approve_res = await ac.post(
            "/api/v1/agency-admin/approve-pr",
            headers=headers_lead_a,
            json={
                "mission_id": mission_a_id,
                "operator_notes": "Verified constant-time HMAC check and pytest coverage.",
                "push_to_github": True,
            },
        )
        assert approve_res.status_code == 200, approve_res.text
        approved_payload = approve_res.json()
        assert approved_payload["status"] == "approved_and_shipped"
        assert approved_payload["mission_status"] == "COMPLETED"
        assert approved_payload["approval_status"] == "APPROVED"
        assert approved_payload["pr"]["status"] == "MERGED"
        assert approved_payload["consensus_seal_hash"].startswith("sha256:")

        # Step 3f: Verify all 3 Cryptographic Consensus Seals + Multi-Device Preview appear on Client A's Portal
        portal_after = (await ac.get("/api/v1/client-portal/portal-summary", headers=headers_client_a)).json()
        delivered_prs = portal_after["delivered_prs"]
        shipped_pr = next((p for p in delivered_prs if p["mission_id"] == mission_a_id), None)
        assert shipped_pr is not None
        assert shipped_pr["status"] == "MERGED"
        assert shipped_pr["all_seals_verified"] is True

        seals = shipped_pr["consensus_seals"]
        assert seals["security"]["agent"] == "Aegis"
        assert seals["security"]["status"] == "VERIFIED"
        assert seals["qa"]["agent"] == "Sentinel"
        assert seals["qa"]["status"] == "VERIFIED"
        assert seals["architecture"]["agent"] == "Nexus"
        assert seals["architecture"]["status"] == "VERIFIED"

        # Verify Multi-Device Preview viewports (Desktop 1920px, Tablet 768px, Mobile 375px)
        viewports = shipped_pr["preview"]["viewports"]
        assert viewports["desktop"]["width"] == 1920
        assert viewports["tablet"]["width"] == 768
        assert viewports["mobile"]["width"] == 375

        # Verify Milestone 1 was automatically advanced to COMPLETED (100%)
        roadmap_after = portal_after["milestone_roadmap"]
        assert roadmap_after[0]["status"] == "COMPLETED"
        assert roadmap_after[0]["progress"] == 100

        # Step 3g: Test "Reject & Roll Back" on Tenant B's pending mission by Agency Admin
        headers_admin_b = _auth_headers(admin_user.id, org_b.id)
        reject_res = await ac.post(
            "/api/v1/agency-admin/reject-pr",
            headers=headers_admin_b,
            json={
                "mission_id": mission_b.id,
                "reason": "Failed boundary benchmark; rolling back sandbox.",
            },
        )
        assert reject_res.status_code == 200, reject_res.text
        assert reject_res.json()["mission_status"] == "CANCELLED"
        assert reject_res.json()["approval_status"] == "REJECTED"

        # =====================================================================
        # 4. 1-CLICK CLIENT ONBOARDING & REAL-TIME P&L MARGIN MATH
        # =====================================================================
        headers_admin_a = _auth_headers(admin_user.id, org_a.id)
        fin_before = (await ac.get("/api/v1/agency-admin/financials", headers=headers_admin_a)).json()
        mrr_before = fin_before["monthly_mrr_usd"]
        clients_before = fin_before["active_clients_count"]

        onboard_res = await ac.post(
            "/api/v1/agency-admin/onboard",
            headers=headers_admin_a,
            json={
                "client_name": f"Hyperion Robotics {suffix}",
                "monthly_retainer_usd": 7500.0,
                "github_repo_url": ".",
                "client_admin_email": f"founder-{suffix}@hyperion.io",
                "initial_sprint_goal": "Week 1: Autonomous Telemetry & Safety Interlocks",
            },
        )
        assert onboard_res.status_code == 201, onboard_res.text
        onboard_data = onboard_res.json()
        assert onboard_data["status"] == "client_onboarded"
        assert len(onboard_data["milestones"]) == 4
        new_client_org_id = onboard_data["organization"]["id"]

        fin_after = (await ac.get("/api/v1/agency-admin/financials", headers=headers_admin_a)).json()
        assert fin_after["active_clients_count"] == clients_before + 1
        assert round(fin_after["monthly_mrr_usd"] - mrr_before, 2) == 7500.0
        assert round(fin_after["monthly_mrr_usd"] - fin_after["total_cost_usd"], 2) == fin_after["net_gross_margin_usd"]
        assert fin_after["net_gross_margin_pct"] > 90.0

        # Verify the newly onboarded Client Executive can access ONLY their own new organization's portal
        new_client_user_id = onboard_data["organization"]["client_user_id"]
        new_client_headers = _auth_headers(new_client_user_id, new_client_org_id)
        new_portal_res = await ac.get("/api/v1/client-portal/portal-summary", headers=new_client_headers)
        assert new_portal_res.status_code == 200
        assert new_portal_res.json()["organization"]["id"] == new_client_org_id

        # And is blocked from Org A -> 403 Forbidden
        cross_check = await ac.get(
            f"/api/v1/client-portal/portal-summary?organization_id={org_a.id}",
            headers=new_client_headers,
        )
        assert cross_check.status_code == 403

        # =====================================================================
        # 5. DASHBOARD 3: DEEP SYSTEMS, 21-AGENT DAG, LSP & MICROVM TELEMETRY
        # =====================================================================
        telem_res = await ac.get(
            f"/api/v1/agency-admin/console/telemetry?mission_id={mission_a_id}",
            headers=headers_lead_a,
        )
        assert telem_res.status_code == 200, telem_res.text
        telem = telem_res.json()
        assert telem["agent_dag"]["total_agents"] == 21
        core_names = [a["name"] for a in telem["agent_dag"]["core_leads"]]
        assert core_names == ["Axiom", "Nexus", "Forge", "Sentinel", "Aegis"]
        assert telem["microvm_fleet"]["egress_firewall"]["status"] == "ACTIVE"
        assert "169.254.169.254/32" in telem["microvm_fleet"]["egress_firewall"]["blocked_imds_ips"]

        # Query HeadlessLSPService via `/api/v1/agency-admin/console/lsp`
        lsp_def_res = await ac.post(
            "/api/v1/agency-admin/console/lsp",
            headers=headers_lead_a,
            json={"action": "definition", "symbol": "CodebaseAuditor"},
        )
        assert lsp_def_res.status_code == 200, lsp_def_res.text
        lsp_def = lsp_def_res.json()
        assert lsp_def["result"]["found"] is True
        assert "codebase_auditor.py" in lsp_def["result"]["file_path"]

        lsp_diag_res = await ac.post(
            "/api/v1/agency-admin/console/lsp",
            headers=headers_lead_a,
            json={"action": "diagnostics", "file_path": "backend/api/v1/client_portal.py"},
        )
        assert lsp_diag_res.status_code == 200
        assert lsp_diag_res.json()["result"]["status"] == "clean"

        # Verify Frontend HTML & JS View Assets are served cleanly
        for asset_path in (
            "/client-portal?demo=true",
            "/admin?demo=true",
            "/console?demo=true",
            "/js/views/client_portal.js",
            "/js/views/admin_console.js",
            "/js/views/console_view.js",
        ):
            r_asset = await ac.get(asset_path)
            assert r_asset.status_code == 200, f"Asset {asset_path} returned {r_asset.status_code}"


@pytest.mark.asyncio
async def test_three_dashboard_edge_cases_and_rbac_aliases():
    """
    Tests boundary conditions, invalid inputs, 404/400/422 error paths, and RBAC role alias normalization.
    """
    # 1. Verify RBAC role alias normalization without breaking ENTERPRISE_ROLES
    assert OrgRole.normalize("AGENCY_ADMIN") == OrgRole.ADMIN
    assert OrgRole.normalize("agency-admin") == OrgRole.ADMIN
    assert OrgRole.normalize("CLIENT") == OrgRole.READ_ONLY_REVIEWER
    assert OrgRole.normalize("CLIENT_EXEC") == OrgRole.READ_ONLY_REVIEWER
    assert OrgRole.normalize("LEAD_ENGINEER") == OrgRole.LEAD_ENGINEER

    suffix = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        org = Organization(
            id=f"org-edge-{suffix}",
            name=f"Edge Case Corp {suffix}",
            slug=f"edge-case-{suffix}",
        )
        lead = User(
            id=f"u-edge-lead-{suffix}",
            email=f"edge-lead-{suffix}@kobits.agency",
            hashed_password=get_password_hash("EdgePass!123"),
            full_name="Edge Lead",
            is_active=True,
        )
        db.add_all([org, lead])
        await db.flush()
        db.add(OrgMember(id=str(uuid.uuid4()), user_id=lead.id, organization_id=org.id, role=OrgRole.LEAD_ENGINEER))
        await db.commit()

    headers = _auth_headers(lead.id, org.id)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Empty / too-short intake payload -> 422 Unprocessable Entity
        r_bad_intake = await ac.post(
            "/api/v1/client-portal/intake",
            headers=headers,
            json={"title": "A", "description": "x"},
        )
        assert r_bad_intake.status_code == 422

        # Non-existent project_id on portal-summary -> 404 Not Found
        r_missing_proj = await ac.get(
            "/api/v1/client-portal/portal-summary?project_id=non-existent-proj-999",
            headers=headers,
        )
        assert r_missing_proj.status_code == 404

        # Non-existent mission_id on approve-pr -> 404 Not Found
        r_missing_msn = await ac.post(
            "/api/v1/agency-admin/approve-pr",
            headers=headers,
            json={"mission_id": "non-existent-mission-999"},
        )
        assert r_missing_msn.status_code == 404

        # Missing mission_id on approve-pr / request-tweak / reject-pr -> 422
        for ep in ("/api/v1/agency-admin/approve-pr", "/api/v1/agency-admin/request-tweak", "/api/v1/agency-admin/reject-pr"):
            r_empty = await ac.post(ep, headers=headers, json={})
            assert r_empty.status_code == 422

        # Unsupported LSP action -> 400 Bad Request
        r_bad_lsp = await ac.post(
            "/api/v1/agency-admin/console/lsp",
            headers=headers,
            json={"action": "invalid_compiler_action"},
        )
        assert r_bad_lsp.status_code == 400

        # Valid LSP references & symbol_graph queries -> 200 OK
        r_refs = await ac.post(
            "/api/v1/agency-admin/console/lsp",
            headers=headers,
            json={"action": "references", "symbol": "CodebaseAuditor"},
        )
        assert r_refs.status_code == 200
        assert r_refs.json()["result"]["count"] >= 1

        r_graph = await ac.post(
            "/api/v1/agency-admin/console/lsp",
            headers=headers,
            json={"action": "symbol_graph"},
        )
        assert r_graph.status_code == 200
        assert "nodes" in r_graph.json()
        assert "edges" in r_graph.json()


def test_three_dashboard_frontend_js_and_backend_diagnostics():
    """
    Runs HeadlessLSPService compiler diagnostics across all newly created/updated
    Python routers and Frontend JavaScript view modules to guarantee zero syntax or import errors.
    """
    from pathlib import Path
    from backend.services.lsp_service import HeadlessLSPService

    target_files = [
        "backend/api/v1/client_portal.py",
        "backend/api/v1/agency_admin.py",
        "backend/api/v1/discovery.py",
        "backend/api/v1/preview.py",
        "web/js/views/client_portal.js",
        "web/js/views/admin_console.js",
        "web/js/views/console_view.js",
        "js/views/client_portal.js",
        "js/views/admin_console.js",
        "js/views/console_view.js",
    ]
    for rel_path in target_files:
        diag = HeadlessLSPService.get_diagnostics(file_path=rel_path, repo_path=".")
        assert diag["error_count"] == 0, f"Diagnostics error in {rel_path}: {diag['diagnostics']}"

    # Verify CSS .hidden utility and SVG topology helper exist so modals never render inline expanded
    for css_path in ("web/css/theme.css", "css/theme.css"):
        css_text = Path(css_path).read_text(encoding="utf-8")
        assert ".hidden" in css_text and "display: none !important" in css_text

    for portal_js in ("web/js/views/client_portal.js", "js/views/client_portal.js"):
        js_text = Path(portal_js).read_text(encoding="utf-8")
        assert "buildArchitectureSvg" in js_text


@pytest.mark.asyncio
async def test_three_dashboard_security_audit_and_state_transitions():
    """
    Verifies fixes for:
    1. Path traversal defense on `/api/v1/agency-admin/console/lsp` (HTTP 400)
    2. State-transition conflict protection on 5% Approval Gate (HTTP 409)
    3. Multi-tenant switcher `all_clients` retention when filtered by `organization_id`
    4. `/overview` and `/approval-gate/decide` route aliases
    5. SOC-2 hash-chained `AuditLog` persistence and chain verification
    """
    from backend.services.soc2_audit_service import SOC2AuditService

    suffix = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        org_1 = Organization(
            id=f"org-sec1-{suffix}",
            name=f"Sec Tenant One {suffix}",
            slug=f"sec-one-{suffix}",
            description='{"monthly_retainer_usd": 5500.0}',
            is_active=True,
        )
        org_2 = Organization(
            id=f"org-sec2-{suffix}",
            name=f"Sec Tenant Two {suffix}",
            slug=f"sec-two-{suffix}",
            description='{"monthly_retainer_usd": 7000.0}',
            is_active=True,
        )
        admin = User(
            id=f"u-sec-admin-{suffix}",
            email=f"sec-admin-{suffix}@kobits.agency",
            hashed_password=get_password_hash("AdminPass!123"),
            full_name="Security Admin",
            is_active=True,
        )
        db.add_all([org_1, org_2, admin])
        await db.flush()
        db.add_all([
            OrgMember(id=str(uuid.uuid4()), user_id=admin.id, organization_id=org_1.id, role=OrgRole.ADMIN),
            OrgMember(id=str(uuid.uuid4()), user_id=admin.id, organization_id=org_2.id, role=OrgRole.ADMIN),
        ])
        proj_1 = Project(
            id=f"proj-sec1-{suffix}",
            name="Sec Project One",
            repository_url=".",
            organization_id=org_1.id,
            created_by=admin.id,
        )
        db.add(proj_1)
        await db.commit()

    headers = _auth_headers(admin.id, org_1.id)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Path traversal attempts on LSP endpoint -> 400 Bad Request
        for traversal_path in ("../../../etc/passwd", "..\\..\\Windows\\win.ini", "/etc/shadow"):
            r_trav = await ac.post(
                "/api/v1/agency-admin/console/lsp",
                headers=headers,
                json={"action": "diagnostics", "file_path": traversal_path},
            )
            assert r_trav.status_code == 400, f"Expected 400 for {traversal_path}, got {r_trav.status_code}"

        # 2. `/overview` route aliases on both Client Portal and Agency Admin
        r_portal_ov = await ac.get("/api/v1/client-portal/overview", headers=headers)
        assert r_portal_ov.status_code == 200
        assert "consensus_seals_list" in r_portal_ov.json()["delivered_prs"][0]

        r_admin_ov = await ac.get(
            f"/api/v1/agency-admin/overview?organization_id={org_1.id}",
            headers=headers,
        )
        assert r_admin_ov.status_code == 200
        ov_data = r_admin_ov.json()
        # Filtered `clients` has only org_1, while `all_clients` retains both org_1 and org_2 for the switcher!
        assert len(ov_data["clients"]) == 1
        assert len(ov_data["all_clients"]) >= 2

        # 3. Create intake mission, reject via `/approval-gate/decide`, then verify approving a rejected mission returns 409 Conflict
        r_intake = await ac.post(
            "/api/v1/client-portal/intake",
            headers=headers,
            json={
                "title": "Implement Hardware HSM Key Rotation",
                "description": "Rotate tenant envelope keys every 30 days with zero downtime.",
                "priority": "Urgent",
                "project_id": proj_1.id,
            },
        )
        assert r_intake.status_code == 201
        msn_id = r_intake.json()["mission_id"]

        r_decide_reject = await ac.post(
            "/api/v1/agency-admin/approval-gate/decide",
            headers=headers,
            json={"mission_id": msn_id, "decision": "reject", "reason": "Holding for HSM maintenance window"},
        )
        assert r_decide_reject.status_code == 200
        assert r_decide_reject.json()["approval_status"] == "REJECTED"

        # Attempting to approve a rejected/cancelled mission -> 409 Conflict
        r_conflict_approve = await ac.post(
            "/api/v1/agency-admin/approve-pr",
            headers=headers,
            json={"mission_id": msn_id, "operator_notes": "Trying to approve after rejection"},
        )
        assert r_conflict_approve.status_code == 409

    # 4. Verify SOC-2 hash-chained AuditLog integrity for org_1
    async with AsyncSessionLocal() as db:
        chain_result = await SOC2AuditService.verify_audit_chain(db, organization_id=org_1.id)
        assert chain_result["valid"] is True
        assert chain_result["verified_entries"] >= 2

