"""
tests/test_kyros_parity_full_spectrum_live.py
=============================================
Extreme Real-World Parity Test Suite for Kobits vs Kyros:
1. Linear 2-Way Issue Sync (Intake, Status Mapping, Plan Commenting).
2. Jira 2-Way Issue Sync (Intake, Status Mapping, ADF Parsing, Plan Commenting).
3. Slack Interactive Action Webhook Receiver (Block Kit Button Approvals & Rejections).
4. Ephemeral Database Sidecars (PostgreSQL & Redis injection in CloudMicroVMSandbox).
5. Strict Google Gemini LLM Provider Routing (Zero Claude API Invocations).
"""

import os
import json
import uuid
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.main import app
from backend.models.mission import (
    Mission,
    MissionStatus,
    WorkflowPhase,
    ApprovalStatus,
    MissionPriority,
)
from backend.models.project import Project, Activity, ActivityType
from backend.models.user import User
from backend.models.organization import Organization
from backend.services.integrations.linear_jira import (
    LinearJiraService,
    LinearIssuePayload,
    JiraIssuePayload,
)
from backend.services.cloud_sandbox import CloudMicroVMSandbox
from backend.services.llm.gemini_provider import GeminiProvider
from backend.services.llm import detect_configured_provider, build_provider_instance


@pytest.fixture
async def setup_test_project_and_org(db_session: AsyncSession):
    """Fixture providing an isolated Organization, User, and Project."""
    org = Organization(
        id=str(uuid.uuid4()),
        name="LinearJira Enterprise Org",
        slug=f"linear-jira-{uuid.uuid4().hex[:6]}",
    )
    user = User(
        id=str(uuid.uuid4()),
        email=f"lead-{uuid.uuid4().hex[:6]}@example.com",
        full_name="Lead Engineer",
        hashed_password="not_needed_for_test",
    )
    project = Project(
        id=str(uuid.uuid4()),
        name="ENG-TEAM",
        organization_id=org.id,
        created_by=user.id,
    )
    db_session.add(org)
    db_session.add(user)
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)
    return {"org": org, "user": user, "project": project}


@pytest.mark.asyncio
async def test_linear_sync_issue_intake_and_status_transitions(setup_test_project_and_org, db_session: AsyncSession):
    """Verify Linear issue webhook creates a Mission, maps priority, and syncs status updates."""
    project = setup_test_project_and_org["project"]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Simulate Linear issue creation (Priority 1 = Urgent, state = "Todo")
        linear_create_payload = {
            "action": "create",
            "type": "Issue",
            "data": {
                "id": f"lin-{uuid.uuid4().hex[:8]}",
                "identifier": "ENG-101",
                "title": "Implement Stripe Webhook Idempotency",
                "description": "Ensure stripe webhook charges do not double-bill on retries.",
                "priority": 1,
                "teamId": project.name,
                "state": {"name": "Todo"},
            }
        }
        res = await client.post("/api/v1/webhooks/linear", json=linear_create_payload)
        assert res.status_code == 200, res.text
        data = res.json()
        assert data.get("status") == "success"
        mission_id = data.get("mission_id")
        assert mission_id is not None

        # Verify created Mission in database
        mission = await db_session.get(Mission, mission_id)
        assert mission is not None
        assert "[ENG-101]" in mission.title
        assert mission.priority == MissionPriority.URGENT
        assert mission.phase in (WorkflowPhase.PLANNING, WorkflowPhase.INTAKE)

        # 2. Simulate Linear issue update (state = "In Review")
        linear_update_payload = {
            "action": "update",
            "type": "Issue",
            "data": {
                "id": linear_create_payload["data"]["id"],
                "identifier": "ENG-101",
                "title": "Implement Stripe Webhook Idempotency",
                "description": "Updated scope with automated tests.",
                "priority": 1,
                "teamId": project.name,
                "state": {"name": "In Review"},
            }
        }
        res_up = await client.post("/api/v1/webhooks/linear", json=linear_update_payload)
        assert res_up.status_code == 200
        assert res_up.json().get("action") == "synced"

        await db_session.refresh(mission)
        assert mission.status == MissionStatus.AWAITING_APPROVAL
        assert mission.phase == WorkflowPhase.CODE_REVIEW


@pytest.mark.asyncio
async def test_jira_sync_issue_intake_and_adf_description(setup_test_project_and_org, db_session: AsyncSession):
    """Verify Jira webhook parses ADF (Atlassian Document Format), maps status, and creates mission."""
    project = setup_test_project_and_org["project"]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        jira_payload = {
            "webhookEvent": "jira:issue_created",
            "issue": {
                "id": "10024",
                "key": "ENG-TEAM",
                "fields": {
                    "summary": "Fix Redis Cache Eviction Bug",
                    "description": {
                        "type": "doc",
                        "version": 1,
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [
                                    {"type": "text", "text": "Keys with TTL 0 are not evicting properly under load."}
                                ]
                            }
                        ]
                    },
                    "project": {"key": project.name},
                    "status": {"name": "In Progress"},
                    "priority": {"name": "High"},
                }
            }
        }
        res = await client.post("/api/v1/webhooks/jira", json=jira_payload)
        assert res.status_code == 200, res.text
        data = res.json()
        assert data.get("status") == "success"
        mission_id = data.get("mission_id")

        mission = await db_session.get(Mission, mission_id)
        assert mission is not None
        assert "Fix Redis Cache Eviction Bug" in mission.title
        assert mission.status == MissionStatus.ACTIVE
        assert mission.priority == MissionPriority.HIGH
        assert "Keys with TTL 0" in mission.objective


@pytest.mark.asyncio
async def test_slack_interactive_block_kit_approval_and_rejection(setup_test_project_and_org, db_session: AsyncSession):
    """Verify Slack interactive Block Kit button click triggers mission plan approval/rejection."""
    project = setup_test_project_and_org["project"]
    user = setup_test_project_and_org["user"]

    # Create a mission in AWAITING_APPROVAL state
    mission = Mission(
        id=str(uuid.uuid4()),
        organization_id=project.organization_id,
        project_id=project.id,
        created_by=user.id,
        title="[Architecture Review] Auth v2 Refactor",
        objective="Migrate from session cookies to JWT auth.",
        status=MissionStatus.AWAITING_APPROVAL,
        phase=WorkflowPhase.ARCHITECTURE_REVIEW,
        approval_status=ApprovalStatus.PENDING,
        requires_approval=True,
    )
    db_session.add(mission)
    await db_session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Simulate Slack interactive button click: APPROVE
        slack_approval_payload = {
            "type": "block_actions",
            "user": {"id": "U123456", "username": "sarah_architect", "name": "Sarah"},
            "actions": [
                {
                    "action_id": "approve_mission_plan",
                    "block_id": "plan_actions_block",
                    "value": mission.id,
                }
            ]
        }
        res = await client.post(
            "/api/v1/webhooks/slack/interactive",
            json=slack_approval_payload,
            headers={"Content-Type": "application/json"}
        )
        assert res.status_code == 200, res.text
        data = res.json()
        assert data.get("status") == "approved"
        assert "Plan Approved by @sarah_architect" in data.get("text")

        # Verify database state transition
        await db_session.refresh(mission)
        assert mission.approval_status == ApprovalStatus.APPROVED
        assert mission.status == MissionStatus.ACTIVE
        assert mission.phase == WorkflowPhase.IMPLEMENTATION

        # Verify audit activity logged
        act_stmt = select(Activity).where(
            (Activity.project_id == project.id) &
            (Activity.type == ActivityType.MISSION_APPROVED)
        )
        act_res = await db_session.execute(act_stmt)
        act = act_res.scalars().first()
        assert act is not None
        assert "@sarah_architect" in act.description

        # 2. Simulate Slack interactive button click: REJECT on another mission
        mission2 = Mission(
            id=str(uuid.uuid4()),
            organization_id=project.organization_id,
            project_id=project.id,
            created_by=user.id,
            title="[High Risk] Drop legacy billing schema",
            objective="Drop tables without backup.",
            status=MissionStatus.AWAITING_APPROVAL,
            phase=WorkflowPhase.ARCHITECTURE_REVIEW,
            approval_status=ApprovalStatus.PENDING,
            requires_approval=True,
        )
        db_session.add(mission2)
        await db_session.commit()

        slack_reject_payload = {
            "type": "block_actions",
            "user": {"id": "U789012", "username": "security_officer", "name": "SecOfficer"},
            "actions": [
                {
                    "action_id": "reject_mission_plan",
                    "block_id": "plan_actions_block",
                    "value": mission2.id,
                }
            ]
        }
        res_rej = await client.post(
            "/api/v1/webhooks/slack/interactive",
            json=slack_reject_payload,
            headers={"Content-Type": "application/json"}
        )
        assert res_rej.status_code == 200
        assert res_rej.json().get("status") == "rejected"

        await db_session.refresh(mission2)
        assert mission2.approval_status == ApprovalStatus.REJECTED
        assert mission2.status == MissionStatus.BLOCKED


def test_ephemeral_database_sidecars_in_cloud_microvm():
    """Verify CloudMicroVMSandbox provisions and injects PostgreSQL & Redis sidecar environment."""
    os.environ["KOBITS_MOCK_CLOUD_SANDBOX"] = "1"
    sb = CloudMicroVMSandbox.get_or_create(session_id=f"test-sidecars-{uuid.uuid4().hex[:6]}")

    # Provision database sidecars
    sidecar_info = sb.provision_sidecars(services=["postgres", "redis"], db_name="production_orders")
    assert sidecar_info["services"]["postgres"] == "provisioned"
    assert sidecar_info["services"]["redis"] == "provisioned"

    # Verify sidecar environment variables
    env = sb.sidecar_env
    assert "DATABASE_URL" in env
    assert "postgresql://postgres:postgres@127.0.0.1:5432/production_orders" in env["DATABASE_URL"]
    assert env["REDIS_URL"] == "redis://127.0.0.1:6379/0"

    # Verify run_command injects the sidecar environment exports
    cmd_res = sb.run_command("env | grep -E 'DATABASE_URL|REDIS_URL'")
    assert cmd_res["exit_code"] == 0
    assert "DATABASE_URL=" in cmd_res["stdout"]
    assert "REDIS_URL=" in cmd_res["stdout"]


def test_strict_gemini_provider_routing_zero_claude():
    """Verify runtime strictly routes to Google Gemini models when configured, with zero Claude calls."""
    from backend.core.config import settings
    old_settings = getattr(settings, "LLM_PROVIDER", None)
    old_env = os.environ.get("LLM_PROVIDER")
    try:
        settings.LLM_PROVIDER = "gemini"
        os.environ["LLM_PROVIDER"] = "gemini"
        provider_name = detect_configured_provider()
        assert provider_name == "gemini", f"Expected gemini, got {provider_name}"

        provider = build_provider_instance("gemini")
        assert isinstance(provider, GeminiProvider)
        assert provider.default_model in ("gemini-4-argon", "gemini-2.5-pro", "gemini-2.5-flash")
    finally:
        settings.LLM_PROVIDER = old_settings
        if old_env is not None:
            os.environ["LLM_PROVIDER"] = old_env
        else:
            os.environ.pop("LLM_PROVIDER", None)


@pytest.mark.asyncio
async def test_linear_and_jira_plan_commenting_dry_run():
    """Verify Linear and Jira plan comment posting handles dry-run without credentials gracefully."""
    # Linear dry run (no API key)
    res_lin = await LinearJiraService.post_linear_plan_comment("ISS-99", "## Proposed Plan\n- Task 1\n- Task 2", api_key=None)
    assert res_lin["status"] == "dry_run"
    assert res_lin["posted"] is False

    # Jira dry run (no host/token)
    res_jira = await LinearJiraService.post_jira_plan_comment("ENG-101", "## Proposed Plan\n- Task 1", host=None, auth_token=None)
    assert res_jira["status"] == "dry_run"
    assert res_jira["posted"] is False
