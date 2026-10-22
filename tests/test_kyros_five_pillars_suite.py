"""
Comprehensive Kyros Five Pillars Integration and Extreme Verification Suite

Tests all 5 Kyros pillars requested by the user:
1. Initial Brief Form (Website Application Form: "Tell us what you're building" + repo link)
2. Weekly 30-Min Sync (Regular scheduled call to scope out upcoming sprint deliverables)
3. Direct Founder Slack (Shared Slack channel for ad-hoc requests and quick scope adjustments)
4. GitHub PR Review Hooks (Clients leave comments on GitHub PRs; agents auto-fix and re-push)
5. Client Progress Dashboard (Shows Sprints, Milestones, Deliverables, Team, Reports + multi-tenant isolation)
Plus 100% bit-for-bit parity and zero-defect LSP diagnostics checks.
"""

import json
import os
import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import patch, AsyncMock
from sqlalchemy import select

from backend.main import app
from backend.core.database import AsyncSessionLocal
from backend.models.waitlist import WaitlistLead
from backend.models.organization import Organization, User, OrganizationMember
from backend.models.project import Project, Task, TaskStatus, Activity
from backend.models.github import (
    GitHubConnection, GitHubConnectionStatus, Repository,
    RepositoryStatus, PullRequest, PullRequestStatus
)
from backend.models.mission import Mission, MissionStatus, WorkflowPhase, ApprovalStatus
from backend.services.auth_service import create_access_token
from backend.services.notifications import notify_slack, notify_task_transition
from backend.services.lsp_service import HeadlessLSPService


def _auth_headers(user_id: str, org_id: str | None = None) -> dict[str, str]:
    token = create_access_token({"sub": user_id})
    headers = {"Authorization": f"Bearer {token}"}
    if org_id:
        headers["X-Organization-Id"] = org_id
    return headers


# ==============================================================================
# PILLAR 1: INITIAL BRIEF FORM
# ==============================================================================

@pytest.mark.asyncio
async def test_pillar1_apply_endpoint_valid_submission():
    """
    Verify POST /api/v1/client-portal/apply persists all application form fields
    (email, name, company, stack, repo_url, brief) into WaitlistLead, triggers Slack alert,
    and returns 200 with application_id and tracking_url.
    """
    transport = ASGITransport(app=app)
    uid = uuid.uuid4().hex[:6]
    test_email = f"founder_{uid}@hypergrowth.io"

    payload = {
        "email": test_email,
        "name": "Sarah Connor",
        "company": "Cyberdyne Systems",
        "stack": "TypeScript, Python, FastAPI, React",
        "repo_url": "https://github.com/cyberdyne/skynet-defense",
        "brief": "We need an autonomous incident remediation engine that catches bugs and heals code in CI."
    }

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("backend.api.v1.client_portal.notify_slack") as mock_slack:
            res = await client.post("/api/v1/client-portal/apply", json=payload)
            assert res.status_code == 201, res.text
            data = res.json()

            assert data.get("status") == "ok"
            app_id = data.get("application_id")
            assert app_id is not None
            tracking_url = data.get("tracking_url")
            assert tracking_url is not None
            assert tracking_url.startswith("/portal?org=")

            # Verify Slack notification was dispatched with applicant details
            assert mock_slack.called
            call_kwargs = mock_slack.call_args.kwargs or {}
            call_args = mock_slack.call_args.args or ()
            chan = call_kwargs.get("channel") or (call_args[0] if len(call_args) > 0 else "")
            msg_body = call_kwargs.get("message") or (call_args[1] if len(call_args) > 1 else "")
            assert "applications" in chan or "intake" in chan
            assert "Sarah Connor" in msg_body
            assert "Cyberdyne Systems" in msg_body
            assert "skynet-defense" in msg_body

    # Verify persistence in Database
    async with AsyncSessionLocal() as db:
        lead_stmt = select(WaitlistLead).where(WaitlistLead.id == app_id)
        result = await db.execute(lead_stmt)
        lead = result.scalar_one_or_none()

        assert lead is not None
        assert lead.email == test_email
        assert lead.name == "Sarah Connor"
        assert lead.company == "Cyberdyne Systems"
        assert lead.stack == "TypeScript, Python, FastAPI, React"
        assert lead.repo_url == "https://github.com/cyberdyne/skynet-defense"
        assert lead.brief == "We need an autonomous incident remediation engine that catches bugs and heals code in CI."


@pytest.mark.asyncio
async def test_pillar1_apply_endpoint_validation_errors():
    """Verify validation handling for missing or malformed applicant fields."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Invalid email
        res_bad_email = await client.post("/api/v1/client-portal/apply", json={
            "email": "not-an-email",
            "name": "Jane Doe",
            "company": "Acme",
            "stack": "Python",
            "repo_url": "https://github.com/org/repo",
            "brief": "Building something great."
        })
        assert res_bad_email.status_code == 422

        # Missing required brief field
        res_missing_brief = await client.post("/api/v1/client-portal/apply", json={
            "email": "valid@domain.com",
            "name": "Jane Doe",
            "company": "Acme",
            "stack": "Python",
            "repo_url": "https://github.com/org/repo"
        })
        assert res_missing_brief.status_code == 422


# ==============================================================================
# PILLAR 2: WEEKLY 30-MIN SYNC
# ==============================================================================

@pytest.mark.asyncio
async def test_pillar2_weekly_30min_sync_get_and_update():
    """
    Verify GET and POST /api/v1/client-portal/sync-schedule endpoints
    manage 30-minute sprint scoping calls and cal.com booking URLs.
    """
    transport = ASGITransport(app=app)
    org_slug = f"acme-sync-{uuid.uuid4().hex[:6]}"

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. GET default sync schedule
        res_get_default = await client.get("/api/v1/client-portal/sync-schedule")
        assert res_get_default.status_code == 200
        sched_default = res_get_default.json()
        assert sched_default["duration_minutes"] == 30
        assert "cal.com" in sched_default["booking_url"]
        assert sched_default["status"] == "scheduled"

        # 2. POST update sync schedule for organization
        with patch("backend.api.v1.client_portal.notify_slack") as mock_slack:
            res_post = await client.post("/api/v1/client-portal/sync-schedule", json={
                "datetime": "Thursday, 2:30 PM EST",
                "notes": "Scope Sprint 4: Payment webhooks, idempotency keys, and Consensus Seals.",
                "organization_id": org_slug
            })
            assert res_post.status_code == 200
            post_data = res_post.json()
            assert post_data["status"] == "ok"
            assert post_data["next_sync"] == "Thursday, 2:30 PM EST"
            assert "Scope Sprint 4" in post_data["notes"]
            assert mock_slack.called

        # 3. GET organization-specific updated schedule
        res_get_org = await client.get(f"/api/v1/client-portal/sync-schedule?organization_id={org_slug}")
        assert res_get_org.status_code == 200
        sched_org = res_get_org.json()
        assert sched_org["next_sync"] == "Thursday, 2:30 PM EST"
        assert "Scope Sprint 4" in sched_org["notes"]
        assert sched_org["duration_minutes"] == 30


# ==============================================================================
# PILLAR 3: DIRECT FOUNDER SLACK
# ==============================================================================

@pytest.mark.asyncio
async def test_pillar3_direct_founder_slack_status():
    """Verify GET /api/v1/client-portal/slack-status provides dedicated #kobits-[slug] channel."""
    transport = ASGITransport(app=app)
    test_slug = "hyper-orbit"

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(f"/api/v1/client-portal/slack-status?organization_id={test_slug}")
        assert res.status_code == 200
        data = res.json()

        assert data["channel"] == f"#kobits-{test_slug}"
        assert data["status"] == "connected"
        assert f"channel=kobits-{test_slug}" in data["direct_url"]
        assert data["members_count"] >= 1


def test_pillar3_slack_notifications_and_task_transitions():
    """
    Verify notify_slack and notify_task_transition correctly format Block Kit
    and state transitions (Queued -> Building -> Review -> Done).
    """
    with patch("backend.services.notifications.dispatch_notification_http", return_value=(True, 200, "ok")) as mock_dispatch:
        # Test general notify_slack
        res = notify_slack(
            channel="#kobits-acme",
            message="Hello Founder! Sprint 2 is underway.",
            webhook_url="https://hooks.slack.com/services/mock/T00/B00"
        )
        assert res is True
        assert mock_dispatch.called
        call_payload = mock_dispatch.call_args[0][1]
        assert call_payload["channel"] == "#kobits-acme"
        assert "Sprint 2 is underway" in call_payload["text"]

        # Test state transitions
        transitions = [
            ("Queued", "Building"),
            ("Building", "Review"),
            ("Review", "Done")
        ]
        for from_s, to_s in transitions:
            t_id = f"task-{uuid.uuid4().hex[:6]}"
            res_t = notify_task_transition(
                task_title="Zero-Downtime Migration",
                from_state=from_s,
                to_state=to_s,
                channel="#kobits-acme",
                task_id=t_id,
                webhook_url="https://hooks.slack.com/services/mock/T00/B00"
            )
            assert res_t is True
            latest_payload = mock_dispatch.call_args[0][1]
            assert "Zero-Downtime Migration" in str(latest_payload)
            assert from_s in str(latest_payload)
            assert to_s in str(latest_payload)


# ==============================================================================
# PILLAR 4: GITHUB PR REVIEW HOOKS
# ==============================================================================

@pytest.fixture
async def setup_pr_review_fixture():
    """Create test organization, project, repo, mission, and open PR."""
    uid = uuid.uuid4().hex[:6]
    async with AsyncSessionLocal() as db:
        user = User(
            email=f"client_reviewer_{uid}@client.org",
            full_name="Client Technical Lead",
            hashed_password="pw_hash_test"
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

        org = Organization(name=f"Client Org {uid}")
        db.add(org)
        await db.commit()
        await db.refresh(org)

        member = OrganizationMember(organization_id=org.id, user_id=user.id, role="OWNER")
        db.add(member)

        project = Project(
            name=f"Client Service {uid}",
            organization_id=org.id,
            created_by=user.id,
            repository_url=f"https://github.com/client-org-{uid}/service"
        )
        db.add(project)
        await db.commit()
        await db.refresh(project)

        conn = GitHubConnection(
            organization_id=org.id,
            user_id=user.id,
            github_username=f"client-org-{uid}",
            status=GitHubConnectionStatus.CONNECTED,
            metadata_json=json.dumps({"token": "ghp_mock_token_12345"})
        )
        db.add(conn)
        await db.commit()
        await db.refresh(conn)

        repo = Repository(
            project_id=project.id,
            organization_id=org.id,
            github_connection_id=conn.id,
            github_repo_id=f"777{uid}",
            owner=f"client-org-{uid}",
            name="service",
            full_name=f"client-org-{uid}/service",
            default_branch="main",
            selected_branch="main",
            clone_url=f"https://github.com/client-org-{uid}/service.git",
            web_url=f"https://github.com/client-org-{uid}/service",
            status=RepositoryStatus.READY
        )
        db.add(repo)
        await db.commit()
        await db.refresh(repo)

        branch_name = f"kobits/feature-auth-{uid}"
        mission = Mission(
            project_id=project.id,
            organization_id=org.id,
            created_by=user.id,
            title="Implement JWT Auth & Refresh Tokens",
            objective="Deliver secure token auth",
            status=MissionStatus.COMPLETED,
            phase=WorkflowPhase.DELIVERY_REVIEW,
            active_branch=branch_name,
            repository_id=repo.id,
        )
        db.add(mission)
        await db.commit()
        await db.refresh(mission)

        pr = PullRequest(
            repository_id=repo.id,
            project_id=project.id,
            mission_id=mission.id,
            github_pr_id=f"pr-{uid}",
            number=202,
            title="Implement JWT Auth & Refresh Tokens",
            source_branch=branch_name,
            target_branch="main",
            head_sha=f"sha_{uid}_commit",
            status=PullRequestStatus.OPEN,
            checks_status="success",
        )
        db.add(pr)
        await db.commit()
        await db.refresh(pr)

        return {
            "user": user,
            "org": org,
            "project": project,
            "repo": repo,
            "mission": mission,
            "pr": pr,
            "branch": branch_name,
        }


@pytest.mark.asyncio
async def test_pillar4_pull_request_review_comment_webhook(setup_pr_review_fixture):
    """
    Verify GitHub webhook for pull_request_review_comment triggers Sentinel & Forge pod,
    updates PR status, records client portal activity, and notifies Slack.
    """
    ctx = setup_pr_review_fixture
    transport = ASGITransport(app=app)

    payload = {
        "action": "created",
        "pull_request": {
            "number": 202,
            "head": {"ref": ctx["branch"]},
            "base": {"ref": "main"}
        },
        "comment": {
            "id": 998877,
            "path": "auth/tokens.py",
            "line": 42,
            "diff_hunk": "@@ -40,6 +40,7 @@ def verify_token(token):\n+    return jwt.decode(token, SECRET, algorithms=['HS256'])",
            "body": "Please add explicit token expiration check before verifying signature."
        },
        "repository": {
            "full_name": ctx["repo"].full_name
        },
        "sender": {
            "login": "client-lead-architect"
        }
    }

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("backend.services.github_service.GitHubService.reply_to_pr_review_comment", new_callable=AsyncMock) as mock_reply, \
             patch("backend.services.mission_runtime.MissionRuntime.run_mission_background", new_callable=AsyncMock), \
             patch("backend.api.v1.webhooks.notify_slack") as mock_slack:

            resp = await client.post(
                "/api/v1/webhooks/github",
                json=payload,
                headers={"X-GitHub-Event": "pull_request_review_comment"}
            )
            assert resp.status_code == 200, resp.text
            res_data = resp.json()
            assert res_data["status"] == "pr_review_correction_dispatched"
            assert res_data["mission_id"] == ctx["mission"].id

            # Verify Slack notification was sent for the PR comment
            assert mock_slack.called

            # Check database updates
            async with AsyncSessionLocal() as db:
                # 1. Task created with Forge agent assigned
                task = await db.get(Task, res_data["task_id"])
                assert task is not None
                assert task.assigned_agent == "Forge"
                assert task.is_correction is True
                assert "token expiration check" in task.description

                # 2. PR deliverable checks_status updated to remediating
                updated_pr = await db.get(PullRequest, ctx["pr"].id)
                assert updated_pr.checks_status == "remediating"
                assert updated_pr.status == PullRequestStatus.OPEN

                # 3. Activity recorded in client portal feed
                act_stmt = select(Activity).where(Activity.project_id == ctx["project"].id).order_by(Activity.created_at.desc())
                act_res = await db.execute(act_stmt)
                activity = act_res.scalar_one_or_none()
                assert activity is not None
                assert "Forge" in activity.title or "review" in activity.title.lower()


@pytest.mark.asyncio
async def test_pillar4_pull_request_review_changes_requested_webhook(setup_pr_review_fixture):
    """
    Verify GitHub webhook for pull_request_review (changes_requested)
    assigns Sentinel/Forge pod to auto-fix and re-push.
    """
    ctx = setup_pr_review_fixture
    transport = ASGITransport(app=app)

    payload = {
        "action": "submitted",
        "pull_request": {
            "number": 202,
            "head": {"ref": ctx["branch"]},
            "base": {"ref": "main"}
        },
        "review": {
            "state": "changes_requested",
            "body": "Need integration test covering token expiration edge case and refresh token rotation."
        },
        "repository": {
            "full_name": ctx["repo"].full_name
        },
        "sender": {
            "login": "external-qa-lead"
        }
    }

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("backend.services.github_service.GitHubService.create_issue_comment", new_callable=AsyncMock), \
             patch("backend.services.mission_runtime.MissionRuntime.run_mission_background", new_callable=AsyncMock), \
             patch("backend.api.v1.webhooks.notify_slack") as mock_slack:

            resp = await client.post(
                "/api/v1/webhooks/github",
                json=payload,
                headers={"X-GitHub-Event": "pull_request_review"}
            )
            assert resp.status_code == 200, resp.text
            res_data = resp.json()
            assert "changes_requested_dispatched" in res_data["status"]
            assert mock_slack.called

            async with AsyncSessionLocal() as db:
                task = await db.get(Task, res_data["task_id"])
                assert task is not None
                assert task.assigned_agent == "Sentinel"
                assert "refresh token rotation" in task.description


# ==============================================================================
# PILLAR 5: CLIENT PROGRESS DASHBOARD (5 TABS & MULTI-TENANT ISOLATION)
# ==============================================================================

@pytest.mark.asyncio
async def test_pillar5_client_progress_dashboard_5_tabs():
    """
    Verify GET /api/v1/client-portal/portal-summary returns complete 5 tabs data:
    1. Overview (4 KPI cards, sprint badges, recent deliverables, activity, slack, sync)
    2. Milestones (project timeline with checkpoint nodes)
    3. Deliverables (PRs, LOC, tests %, 3 consensus seals, live preview)
    4. Team (autonomous agent specialists: Axiom, Nexus, Sentinel, Aegis, Forge, Pixel, Vanguard)
    5. Reports (sprint velocity, 100% negative-to-positive TDD pass rate, SOC-2 audit certificate, weekly sync)
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/client-portal/portal-summary?org=hyper-tech")
        assert res.status_code == 200, res.text
        data = res.json()

        # Tab 1: Overview
        assert "kpis" in data
        kpis = data["kpis"]
        assert "completion" in kpis and kpis["completion"]["value"] == "78%"
        assert "deliverables" in kpis and kpis["deliverables"]["value"] == "12/16"
        assert "spent" in kpis and kpis["spent"]["value"] == "$4.2K"
        assert "remaining" in kpis and kpis["remaining"]["value"] == "3 wk"

        assert "current_sprint" in data
        sprint_statuses = [t["status"] for t in data["current_sprint"]["tasks"]]
        assert "Done" in sprint_statuses
        assert "Review" in sprint_statuses
        assert "Building" in sprint_statuses
        assert "Queued" in sprint_statuses

        assert "recent_deliverables" in data
        assert "recent_activity" in data
        assert "slack_connect" in data
        assert data["slack_connect"]["channel"] == "#kobits-hyper-tech"
        assert "weekly_sync" in data
        assert data["weekly_sync"]["duration_minutes"] == 30

        # Tab 2: Milestones
        assert "project_timeline" in data or "timeline" in data
        timeline = data.get("project_timeline") or data.get("timeline")
        assert len(timeline) >= 4
        norm_statuses = [str(node["status"]).lower() for node in timeline]
        assert any("complete" in s for s in norm_statuses)
        assert any("active" in s or "in_progress" in s for s in norm_statuses)
        assert any("upcoming" in s for s in norm_statuses)

        # Tab 3: Deliverables
        assert "deliverables_catalog" in data
        assert len(data["deliverables_catalog"]) >= 1
        first_deliv = data["deliverables_catalog"][0]
        assert "prs_count" in first_deliv or "prs" in first_deliv
        assert "loc" in first_deliv
        assert "tests_pct" in first_deliv or "tests_pass_rate" in first_deliv
        seals = first_deliv.get("consensus_seals") or first_deliv.get("seals")
        assert seals is not None
        assert "Aegis" in seals
        assert "Sentinel" in seals
        assert "Nexus" in seals

        # Tab 4: Team
        assert "team_agents" in data
        agent_names = [a["name"] for a in data["team_agents"]]
        for required_agent in ["Axiom", "Nexus", "Sentinel", "Aegis", "Forge", "Pixel", "Vanguard"]:
            assert required_agent in agent_names

        # Tab 5: Reports
        assert "executive_reports" in data
        reports = data["executive_reports"]
        assert "sprint_velocity" in reports or "velocity" in reports
        velocity = reports.get("sprint_velocity") or reports.get("velocity")
        assert velocity["completion_pct"] == 78
        assert velocity["velocity_multiplier"] == "4.2x"

        assert "test_coverage" in reports or "tdd_metrics" in reports
        coverage = reports.get("test_coverage") or reports.get("tdd_metrics")
        assert coverage["tdd_pass_rate"] == "100%" or coverage.get("reproducer_pass_rate") == "100%"

        assert "security_audit" in reports or "soc2_audit" in reports
        security = reports.get("security_audit") or reports.get("soc2_audit")
        assert security["soc2_compliance"] == "VERIFIED" or security.get("status") in ("COMPLIANT", "PASSED")


@pytest.mark.asyncio
async def test_pillar5_multi_tenant_client_isolation():
    """Verify strict multi-tenant isolation prevents cross-tenant data access."""
    uid1 = uuid.uuid4().hex[:6]
    uid2 = uuid.uuid4().hex[:6]

    async with AsyncSessionLocal() as db:
        user1 = User(email=f"ceo_{uid1}@tenant-alpha.com", full_name="Alpha CEO", hashed_password="pw1")
        user2 = User(email=f"ceo_{uid2}@tenant-beta.com", full_name="Beta CEO", hashed_password="pw2")
        db.add_all([user1, user2])
        await db.commit()
        await db.refresh(user1)
        await db.refresh(user2)

        org1 = Organization(name=f"Tenant Alpha {uid1}", slug=f"tenant-alpha-{uid1}")
        org2 = Organization(name=f"Tenant Beta {uid2}", slug=f"tenant-beta-{uid2}")
        db.add_all([org1, org2])
        await db.commit()
        await db.refresh(org1)
        await db.refresh(org2)

        mem1 = OrganizationMember(organization_id=org1.id, user_id=user1.id, role="OWNER")
        mem2 = OrganizationMember(organization_id=org2.id, user_id=user2.id, role="OWNER")
        db.add_all([mem1, mem2])
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # User 1 authenticated headers accessing Org 2 must be rejected with 403 Forbidden
        headers_user1_target_org2 = _auth_headers(user1.id, org2.id)
        res_cross = await client.get("/api/v1/client-portal/portal-summary", headers=headers_user1_target_org2)
        assert res_cross.status_code == 403

        # User 1 accessing own Org 1 succeeds
        headers_user1_own = _auth_headers(user1.id, org1.id)
        res_own = await client.get("/api/v1/client-portal/portal-summary", headers=headers_user1_own)
        assert res_own.status_code == 200
        assert res_own.json()["organization"]["id"] == org1.id


# ==============================================================================
# BIT-FOR-BIT PARITY & STATIC ASSETS EXTREME VERIFICATION
# ==============================================================================

def test_exact_file_parity_and_static_html_checks():
    """
    Verify 100% bit-for-bit parity:
    1. index.html == landing.html
    2. js/views/client_portal.js == web/js/views/client_portal.js
    3. Form inputs match exact required IDs and action
    4. Zero LSP syntax diagnostics
    """
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    # 1. HTML bit-for-bit parity
    index_path = os.path.join(repo_root, "index.html")
    landing_path = os.path.join(repo_root, "landing.html")
    with open(index_path, "rb") as f1, open(landing_path, "rb") as f2:
        idx_bytes = f1.read()
        lnd_bytes = f2.read()
        assert idx_bytes == lnd_bytes, "index.html and landing.html must remain 100% bit-for-bit identical"

    idx_text = idx_bytes.decode("utf-8")
    assert 'id="form-early-access"' in idx_text
    assert 'id="ea-email"' in idx_text
    assert 'id="ea-name"' in idx_text
    assert 'id="ea-company"' in idx_text
    assert 'id="ea-stack"' in idx_text
    assert 'id="ea-repo"' in idx_text
    assert 'id="ea-brief"' in idx_text
    assert "/api/v1/client-portal/apply" in idx_text

    # 2. JS bit-for-bit parity
    js1 = os.path.join(repo_root, "js", "views", "client_portal.js")
    js2 = os.path.join(repo_root, "web", "js", "views", "client_portal.js")
    with open(js1, "rb") as f1, open(js2, "rb") as f2:
        assert f1.read() == f2.read(), "js/views/client_portal.js and web/js/views/client_portal.js must remain 100% bit-for-bit identical"

    js_text = open(js1, "r", encoding="utf-8").read()
    assert 'id="slack-connect-pill"' in js_text
    assert 'id="weekly-sync-card-overview"' in js_text
    assert 'id="weekly-sync-card-reports"' in js_text
    assert 'id="modal-reschedule-sync"' in js_text
    assert "/client-portal/sync-schedule" in js_text

    # 3. LSP Diagnostics check (0 errors)
    for js_p in [js1, js2]:
        diag = HeadlessLSPService.get_diagnostics(file_path=js_p, repo_path=repo_root)
        assert diag["error_count"] == 0, f"LSP errors found in {js_p}: {diag['diagnostics']}"


# ==============================================================================
# DEEP REGRESSION & EDGE-CASE HARDENING
# ==============================================================================

@pytest.mark.asyncio
async def test_pillar1_apply_endpoint_slack_failure_graceful_recovery():
    """
    Verify that if notify_slack encounters an unexpected error / network exception,
    the apply endpoint catches it, logs via logger, and returns HTTP 201 without
    crashing on undefined names or uncaught exceptions.
    """
    transport = ASGITransport(app=app)
    uid = uuid.uuid4().hex[:6]
    test_email = f"resilient_{uid}@startup.ai"

    payload = {
        "email": test_email,
        "name": "Grace Hopper",
        "company": "Compiler Systems",
        "stack": "Rust, Python, WASM",
        "repo_url": "https://github.com/compiler/nanosec",
        "brief": "We need resilient compiler optimizations and automated benchmark regression checking."
    }

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("backend.api.v1.client_portal.notify_slack", side_effect=RuntimeError("Slack webhook unreachable")):
            res = await client.post("/api/v1/client-portal/apply", json=payload)
            assert res.status_code == 201, res.text
            data = res.json()
            assert data["status"] == "ok"
            assert data["application_id"] is not None


def test_pillar1_and_models_property_setters():
    """
    Verify WaitlistLead and Task models support property setters for alias fields:
    - WaitlistLead(name="...") and lead.name = "..."
    - Task(assigned_agent="Forge") and task.assigned_agent = "..."
    """
    lead = WaitlistLead(
        email="test_prop@example.com",
        name="Ada Lovelace",
        company="Analytical Engine",
        brief="Computing pioneer"
    )
    assert lead.name == "Ada Lovelace"
    assert lead.full_name == "Ada Lovelace"
    lead.name = "Augusta Ada King"
    assert lead.name == "Augusta Ada King"
    assert lead.full_name == "Augusta Ada King"

    task = Task(
        title="Test Task",
        assigned_agent="Sentinel"
    )
    assert task.assigned_agent == "Sentinel"
    assert task.assigned_agent_id == "Sentinel"
    task.assigned_agent = "Forge"
    assert task.assigned_agent == "Forge"
    assert task.assigned_agent_id == "Forge"


@pytest.mark.asyncio
async def test_pillar5_showcase_dynamic_slug_in_summary():
    """
    Verify that unauthenticated / prospective client viewing the portal with ?org=[slug]
    has both organization.slug and slack_connect.channel populated with the dynamic slug.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/client-portal/portal-summary?org=spacex-orbit")
        assert res.status_code == 200
        data = res.json()
        assert data["organization"]["slug"] == "spacex-orbit"
        assert data["slack_connect"]["channel"] == "#kobits-spacex-orbit"

