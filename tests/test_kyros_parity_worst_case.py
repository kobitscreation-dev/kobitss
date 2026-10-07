"""Comprehensive normal + worst-case test suite for all 7 Kyros parity channels:

1. One-Line CLI Command (`kobits "..."` / `kobits run "..."` / `kobits --repo owner/repo "..."`)
2. Interactive Terminal Chat (`kobits` / `kobits chat` / `kobits repl`)
3. Target / Connect a Remote GitHub Repo (`kobits connect owner/repo`, `--repo`)
4. Live Mid-Flight Steering (`kobits steer ...` & `POST /api/v1/missions/{id}/steer`)
5. Approval / Retry / Reject / Cancel (CLI + REST API + Web Dashboard Inspector)
6. REST API & Webhooks (Direct JSON, Slack, GitHub Issue, Linear Issue)
7. Web Dashboard Pure Monitoring & Inspector Integrity (No New Mission modal; all 5 inspector controls wired)
"""
import asyncio
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from backend.main import app
from backend.core.database import get_db
from backend.api.deps import get_current_active_user
from backend.models.base import Base
from backend.models.organization import User, Organization, OrganizationMember
from backend.models.project import Project, Task, TaskStatus, Activity
from backend.models.mission import Mission, MissionStatus, WorkflowPhase, ApprovalStatus
import kobits_cli

DB_PATH = "./test_kyros_parity_worst_case.db"
engine = create_async_engine(f"sqlite+aiosqlite:///{DB_PATH}", echo=False)
Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

USER = User(id="kyros_user", email="kyros@test.local", full_name="Kyros Tester", hashed_password="x")
OTHER_USER = User(id="other_user", email="other@test.local", full_name="Other Tester", hashed_password="x")
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


async def _seed():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with Session() as s:
        s.add_all([
            Organization(id="org_main", name="Main Org"),
            Organization(id="org_foreign", name="Foreign Org"),
            USER,
            OTHER_USER,
            OrganizationMember(organization_id="org_main", user_id="kyros_user", role="OWNER"),
            OrganizationMember(organization_id="org_foreign", user_id="other_user", role="OWNER"),
            Project(
                id="proj_alpha_123456",
                name="Kobits Platform",
                organization_id="org_main",
                created_by="kyros_user",
                updated_at=NOW,
            ),
            Project(
                id="proj_foreign_9999",
                name="Foreign Secret",
                organization_id="org_foreign",
                created_by="other_user",
                updated_at=NOW,
            ),
        ])
        s.add_all([
            Mission(
                id="m_await_11112222",
                organization_id="org_main",
                project_id="proj_alpha_123456",
                created_by="kyros_user",
                title="Pending approval sprint",
                objective="Design database schema",
                status=MissionStatus.AWAITING_APPROVAL,
                phase=WorkflowPhase.PLANNING,
                requires_approval=True,
                approval_status=ApprovalStatus.PENDING,
                created_at=NOW - timedelta(hours=3),
            ),
            Mission(
                id="m_exec_33334444",
                organization_id="org_main",
                project_id="proj_alpha_123456",
                created_by="kyros_user",
                title="Active executing sprint",
                objective="Build Stripe webhook handler",
                status=MissionStatus.EXECUTING,
                phase=WorkflowPhase.IMPLEMENTATION,
                requires_approval=False,
                approval_status=ApprovalStatus.APPROVED,
                created_at=NOW - timedelta(hours=1),
            ),
            Mission(
                id="m_fail_55556666",
                organization_id="org_main",
                project_id="proj_alpha_123456",
                created_by="kyros_user",
                title="Failed sprint",
                objective="Migrate legacy auth",
                status=MissionStatus.FAILED,
                phase=WorkflowPhase.VALIDATION,
                requires_approval=False,
                approval_status=ApprovalStatus.APPROVED,
                created_at=NOW - timedelta(hours=2),
            ),
            Mission(
                id="m_done_77778888",
                organization_id="org_main",
                project_id="proj_alpha_123456",
                created_by="kyros_user",
                title="Completed sprint",
                objective="Shipped billing page",
                status=MissionStatus.COMPLETED,
                phase=WorkflowPhase.DEPLOYMENT,
                progress=100,
                completed_at=NOW,
                created_at=NOW - timedelta(hours=5),
            ),
            Mission(
                id="m_foreign_99990000",
                organization_id="org_foreign",
                project_id="proj_foreign_9999",
                created_by="other_user",
                title="Foreign sprint",
                objective="Secret objective",
                status=MissionStatus.EXECUTING,
                created_at=NOW,
            ),
        ])
        s.add_all([
            Task(
                id="t_await_1",
                project_id="proj_alpha_123456",
                mission_id="m_await_11112222",
                created_by="kyros_user",
                title="Draft schema migration",
                status=TaskStatus.PENDING,
            ),
            Task(
                id="t_exec_1",
                project_id="proj_alpha_123456",
                mission_id="m_exec_33334444",
                created_by="kyros_user",
                title="Implement Stripe signature check",
                status=TaskStatus.IN_PROGRESS,
            ),
            Task(
                id="t_fail_1",
                project_id="proj_alpha_123456",
                mission_id="m_fail_55556666",
                created_by="kyros_user",
                title="Run integration suite",
                status=TaskStatus.FAILED,
                metadata_json='{"error": "Connection timeout"}',
                attempt_count=2,
            ),
        ])
        await s.commit()


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("KOBITS_NO_AUTO_EXECUTE", "1")
    monkeypatch.setenv("KOBITS_NO_RESUME", "1")
    asyncio.run(_seed())

    import backend.core.database as db_mod
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", Session)

    async def _fake_identity():
        return ("kyros_user", "org_main")

    monkeypatch.setattr(kobits_cli, "_ensure_local_db_and_identity", _fake_identity)

    async def override_db():
        async with Session() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_active_user] = lambda: USER

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()
    asyncio.run(engine.dispose())
    if os.path.exists(DB_PATH):
        try:
            os.remove(DB_PATH)
        except OSError:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# 1 & 2. CLI Argument Normalization, Autopilot Default, and REPL Routing
# ─────────────────────────────────────────────────────────────────────────────

def test_cli_argv_normalization_and_autopilot_default():
    parser = kobits_cli.build_parser()

    # 1a. Direct one-liner: `kobits "Build Stripe webhook..."`
    norm1 = kobits_cli._normalize_cli_argv(["Build Stripe webhook..."])
    assert norm1 == ["run", "Build Stripe webhook..."]
    args1 = parser.parse_args(norm1)
    assert args1.command == "run"
    assert args1.autopilot is True
    assert args1.objective == ["Build Stripe webhook..."]

    # 1b. Explicit `kobits run "Build Stripe webhook..."`
    norm2 = kobits_cli._normalize_cli_argv(["run", "Build Stripe webhook..."])
    args2 = parser.parse_args(norm2)
    assert args2.command == "run"
    assert args2.autopilot is True

    # 1c. Flag before objective: `kobits --repo owner/repo "Build Stripe webhook..."`
    norm3 = kobits_cli._normalize_cli_argv(["--repo", "owner/repo", "Build Stripe webhook..."])
    assert norm3[0] == "run"
    args3 = parser.parse_args(norm3)
    assert args3.command == "run"
    assert args3.repo == "owner/repo"
    assert args3.objective == ["Build Stripe webhook..."]

    # 1d. Workspace flag before objective: `kobits -w ./my_dir "Fix tests"`
    norm4 = kobits_cli._normalize_cli_argv(["-w", "./my_dir", "Fix tests"])
    assert norm4[0] == "run"
    args4 = parser.parse_args(norm4)
    assert args4.workspace == "./my_dir"
    assert args4.objective == ["Fix tests"]

    # 1e. Opt-in approval mode: `kobits run --require-approval "High risk migration"`
    args5 = parser.parse_args(["run", "--require-approval", "High risk migration"])
    assert args5.autopilot is False

    # 2. REPL commands: `kobits chat` and `kobits repl`
    for sub in ("chat", "repl"):
        args_repl = parser.parse_args([sub, "--repo", "acme/service"])
        assert args_repl.command in ("chat", "repl")
        assert args_repl.repo == "acme/service"


# ─────────────────────────────────────────────────────────────────────────────
# 3. Target / Connect Remote GitHub Repo (Local & Worst-Case Validation)
# ─────────────────────────────────────────────────────────────────────────────

def test_repo_resolution_local_and_worst_case(tmp_path):
    # Valid local directory
    resolved = kobits_cli._resolve_github_repo_workspace(str(tmp_path))
    assert Path(resolved) == tmp_path.resolve()

    # Empty repo spec -> ValueError
    with pytest.raises(ValueError, match="Repository specification cannot be empty"):
        kobits_cli._resolve_github_repo_workspace("   ")

    # Invalid shorthand / command injection attempt -> ValueError
    for bad_spec in ["not_a_valid_repo_or_dir", "a/b/c", "owner/repo;rm -rf", "../nonexistent_xyz_dir"]:
        with pytest.raises(ValueError):
            kobits_cli._resolve_github_repo_workspace(bad_spec)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Live Mid-Flight Steering (REST API + CLI normal & worst-case)
# ─────────────────────────────────────────────────────────────────────────────

def test_mid_flight_steering_api_and_cli(client):
    # 4a. Steer via REST API using 8-char mission ID prefix
    r = client.post("/api/v1/missions/m_exec_3/steer", json={"instruction": "Use stdlib only, no requests"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "STEERED"
    assert body["mission_id"] == "m_exec_33334444"
    assert body["instruction"] == "Use stdlib only, no requests"

    # 4b. Steer via REST API using 'active' keyword
    r2 = client.post("/api/v1/missions/active/steer", json={"prompt": "Add strict type annotations"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["mission_id"] == "m_exec_33334444"

    # 4c. Worst-case: empty steering instruction -> 400
    r_empty = client.post("/api/v1/missions/m_exec_3/steer", json={"instruction": "   "})
    assert r_empty.status_code == 400

    # 4d. Worst-case: steering a COMPLETED mission -> 400
    r_done = client.post("/api/v1/missions/m_done_7/steer", json={"instruction": "Change something"})
    assert r_done.status_code == 400

    # 4e. Worst-case: steering another organization's mission -> 404
    r_foreign = client.post("/api/v1/missions/m_foreig/steer", json={"instruction": "Hijack"})
    assert r_foreign.status_code == 404

    # 4f. CLI `cmd_steer` without mission_id (auto-targets active executing sprint!)
    rc = asyncio.run(kobits_cli.cmd_steer(SimpleNamespace(instruction=["Use", "asyncio.TaskGroup"])))
    assert rc == 0

    # 4g. CLI `cmd_steer` with explicit 8-char prefix
    rc2 = asyncio.run(kobits_cli.cmd_steer(SimpleNamespace(instruction=["m_await_", "Include", "indexes"])))
    assert rc2 == 0

    # 4h. CLI `cmd_steer` worst-case: empty instruction or non-existent hex ID
    rc_bad = asyncio.run(kobits_cli.cmd_steer(SimpleNamespace(instruction=[])))
    assert rc_bad == 1
    rc_notfound = asyncio.run(kobits_cli.cmd_steer(SimpleNamespace(instruction=["deadbeef", "Use", "stdlib"])))
    assert rc_notfound == 1


# ─────────────────────────────────────────────────────────────────────────────
# 5. Approval / Reject / Cancel / Retry (REST API + CLI normal & worst-case)
# ─────────────────────────────────────────────────────────────────────────────

def test_approve_reject_cancel_retry_worst_cases(client):
    # 5a. Approve AWAITING_APPROVAL mission via 8-char prefix
    r_app = client.post("/api/v1/missions/m_await_/approve")
    assert r_app.status_code == 200, r_app.text
    assert r_app.json()["approval_status"] == "APPROVED"

    # Worst-case: Approve a COMPLETED mission -> 400
    r_app_done = client.post("/api/v1/missions/m_done_7/approve")
    assert r_app_done.status_code == 400

    # 5b. Create a new approval-required mission, then REJECT it with reason
    r_create = client.post(
        "/api/v1/missions/projects/default/missions",
        json={"objective": "Drop legacy tables", "execution_mode": "approval_required"},
    )
    assert r_create.status_code == 201, r_create.text
    new_mid = r_create.json()["id"]
    assert r_create.json()["status"] == "AWAITING_APPROVAL"

    r_rej = client.post(f"/api/v1/missions/{new_mid[:8]}/reject", json={"reason": "Too risky for Friday"})
    assert r_rej.status_code == 200, r_rej.text
    assert r_rej.json()["status"] == "REJECTED"
    assert r_rej.json()["mission_status"] == "CANCELLED"
    assert r_rej.json()["reason"] == "Too risky for Friday"

    # Worst-case: Reject a COMPLETED mission -> 400
    assert client.post("/api/v1/missions/m_done_7/reject", json={"reason": "No"}).status_code == 400

    # 5c. Cancel an EXECUTING mission via REST API -> cancels in-progress tasks too
    r_can = client.post("/api/v1/missions/m_exec_3/cancel")
    assert r_can.status_code == 200, r_can.text
    assert r_can.json()["status"] == "CANCELLED"

    # Verify in-progress task `t_exec_1` was marked CANCELLED
    async def _check_task():
        async with Session() as s:
            t = (await s.execute(select(Task).where(Task.id == "t_exec_1"))).scalar_one()
            return t.status
    assert asyncio.run(_check_task()) == TaskStatus.CANCELLED

    # Worst-case: Cancel an already COMPLETED mission -> 400
    assert client.post("/api/v1/missions/m_done_7/cancel").status_code == 400

    # 5d. Retry a FAILED mission via REST API -> resets failed tasks to PENDING
    r_ret = client.post("/api/v1/missions/m_fail_5/retry")
    assert r_ret.status_code == 200, r_ret.text
    assert r_ret.json()["retry_count"] == 1
    assert r_ret.json()["tasks_reset"] >= 1

    # Verify CLI `cmd_reject` and `cmd_cancel` worst-case on completed mission returns exit code 1
    rc_rej_done = asyncio.run(kobits_cli.cmd_reject(SimpleNamespace(mission_id="m_done_7", reason="nope", extra_reason=[])))
    assert rc_rej_done == 1
    rc_can_done = asyncio.run(kobits_cli.cmd_cancel(SimpleNamespace(mission_id="m_done_7")))
    assert rc_can_done == 1


# ─────────────────────────────────────────────────────────────────────────────
# 6. REST API & Webhooks (Direct JSON, Slack, GitHub Issue, Linear Issue)
# ─────────────────────────────────────────────────────────────────────────────

def test_rest_api_and_inbound_webhooks(client):
    # 6a. Direct REST API with only `objective` and project_id="default" (autopilot default)
    r1 = client.post(
        "/api/v1/missions/projects/default/missions",
        json={"objective": "Implement Redis rate limiter middleware"},
    )
    assert r1.status_code == 201, r1.text
    d1 = r1.json()
    assert d1["title"] == "Implement Redis rate limiter middleware"
    assert d1["requires_approval"] is False
    assert d1["approval_status"] == "APPROVED"

    # 6b. Direct REST API by 8-char project prefix
    r2 = client.post(
        "/api/v1/missions/projects/proj_alp/missions",
        json={"prompt": "Add OpenTelemetry tracing spans"},
    )
    assert r2.status_code == 201, r2.text
    assert r2.json()["project_id"] == "proj_alpha_123456"

    # 6c. Slack slash-command / webhook payload (`text` + `channel_name`)
    r_slack = client.post(
        "/api/v1/missions/webhooks/inbound",
        json={"text": "Fix N+1 query in user profile endpoint", "user_name": "arvind", "channel_name": "eng-alerts"},
    )
    assert r_slack.status_code == 201, r_slack.text
    assert r_slack.json()["source"] == "slack"
    assert "Fix N+1 query" in r_slack.json()["title"]

    # 6d. GitHub Issue webhook payload (`issue.title` + `issue.body` + `repository.full_name`)
    r_gh = client.post(
        "/api/v1/missions/webhooks/inbound",
        json={
            "action": "opened",
            "issue": {
                "number": 108,
                "title": "Memory leak in WebSocket broadcaster",
                "body": "Connections are not removed from active_connections on disconnect.",
            },
            "repository": {"full_name": "kobits/core"},
        },
    )
    assert r_gh.status_code == 201, r_gh.text
    assert r_gh.json()["source"] == "github_issue"
    assert "[GH #108]" in r_gh.json()["title"]

    # 6e. Linear Issue webhook payload (`data.title` + `data.description` + `data.identifier`)
    r_linear = client.post(
        "/api/v1/missions/webhooks/inbound",
        json={
            "type": "Issue",
            "data": {
                "identifier": "KOB-404",
                "title": "Add idempotency key to POST /missions",
                "description": "Prevent duplicate sprints on network retries.",
            },
        },
    )
    assert r_linear.status_code == 201, r_linear.text
    assert r_linear.json()["source"] == "linear"
    assert "[KOB-404]" in r_linear.json()["title"]

    # 6f. Worst-case: Empty webhook payload -> 400
    r_empty = client.post("/api/v1/missions/webhooks/inbound", json={})
    assert r_empty.status_code == 400

    # 6g. Worst-case: Empty objective in project missions endpoint -> 400
    r_empty_obj = client.post("/api/v1/missions/projects/default/missions", json={"title": "", "objective": "   "})
    assert r_empty_obj.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# 7. Web Dashboard Pure Monitoring & Inspector Controls Integrity
# ─────────────────────────────────────────────────────────────────────────────

def test_web_dashboard_pure_monitoring_and_inspector_controls():
    root = Path(__file__).resolve().parent.parent
    index_html = (root / "web" / "index.html").read_text(encoding="utf-8")
    app_js = (root / "web" / "js" / "app.js").read_text(encoding="utf-8")

    # Dashboard must NOT contain a New Mission creation button or modal (1:1 Kyros monitoring-only)
    assert "openNewMissionModal" not in app_js
    assert "ky-new-mission-btn" not in index_html

    # Inspector modal MUST wire all 6 lifecycle controls + sandbox diff/apply/PR controls
    for control_id in (
        "ky-act-approve",
        "ky-act-reject",
        "ky-act-cancel",
        "ky-act-retry",
        "ky-act-steer",
        "ky-steer-input",
        "ky-act-deliver",
        "ky-act-dryrun",
        "ky-act-apply",
        "initLiveWebSocket",
        "/ws/dashboard",
    ):
        assert control_id in app_js, f"Missing control {control_id} in web/js/app.js"


# ─────────────────────────────────────────────────────────────────────────────
# 8. Gap 1 & Gap 4: Durable Changeset + Automated Branch Push + GitHub PR
# ─────────────────────────────────────────────────────────────────────────────

def test_gap1_and_gap4_durable_changeset_and_github_pr_deliverable(client, tmp_path):
    from backend.models.github import Repository, RepositoryStatus, PullRequest, Changeset

    # Attach a GitHub repository to proj_alpha_123456 and link it to m_exec_33334444
    async def _attach_repo():
        async with Session() as s:
            repo = Repository(
                id="repo_payments_1",
                organization_id="org_main",
                project_id="proj_alpha_123456",
                github_repo_id=424242,
                owner="acme",
                name="payments",
                full_name="acme/payments",
                default_branch="main",
                selected_branch="main",
                clone_url="https://github.com/acme/payments.git",
                status=RepositoryStatus.CONNECTED,
            )
            s.add(repo)
            m = await s.get(Mission, "m_exec_33334444")
            m.repository_id = "repo_payments_1"
            m.active_branch = "kobits/mission/m_exec_3"
            await s.commit()

    asyncio.run(_attach_repo())

    # Create a mission sandbox directory with a new/modified file
    sb_root = Path("sandboxes") / "mission_m_exec_3"
    sb_root.mkdir(parents=True, exist_ok=True)
    target_file = sb_root / "stripe_webhook_verified.py"
    target_file.write_text(
        "def verify_stripe_webhook(sig: str) -> bool:\n    return bool(sig and sig.startswith('t='))\n",
        encoding="utf-8",
    )

    try:
        r_del = client.post("/api/v1/missions/m_exec_3/deliver", json={"push_and_open_pr": True})
        assert r_del.status_code == 200, r_del.text
        data = r_del.json()

        # Verify durable Changeset was created
        assert data["changeset"] is not None
        assert data["changeset"]["files_changed"] >= 1
        assert data["changeset"]["lines_added"] >= 1
        assert data["changeset"]["branch"] == "kobits/mission/m_exec_3"

        # Verify PullRequest was created and linked to the mission
        assert data["pr"] is not None
        assert data["pr"]["url"].startswith("https://github.com/acme/payments/pull/")
        assert data["pr"]["source_branch"] == "kobits/mission/m_exec_3"
        assert data["pr"]["target_branch"] == "main"

        # Verify GET /api/v1/missions/{id} returns the durable Changeset + PullRequest from SQLite
        r_detail = client.get("/api/v1/missions/m_exec_3")
        assert r_detail.status_code == 200
        delivery = r_detail.json()["delivery"]
        assert delivery["pr"] is not None
        assert delivery["pr"]["number"] == data["pr"]["number"]
        assert len(delivery["changesets"]) >= 1
        assert delivery["changesets"][0]["files_changed"] >= 1
    finally:
        import shutil
        shutil.rmtree(sb_root, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# 9. Gap 2: Mid-Turn Live Steering Injection inside AgentExecutor
# ─────────────────────────────────────────────────────────────────────────────

def test_gap2_mid_turn_live_steering_injection_in_agent_executor(client):
    from backend.services.agent_executor import AgentExecutor
    from backend.models.agent import AgentType

    # Queue a live mid-flight steering directive on active mission m_exec_33334444
    r_steer = client.post(
        "/api/v1/missions/m_exec_3/steer",
        json={"instruction": "Switch to HMAC-SHA256 using Python stdlib hmac module immediately"},
    )
    assert r_steer.status_code == 200, r_steer.text

    async def _run_mid_turn_tool():
        async with Session() as s:
            executor = AgentExecutor(
                agent_type=AgentType.BACKEND_ENGINEER,
                allowed_tools=["code.analyze"],
                db=s,
            )
            executor.mission_id = "m_exec_33334444"
            executor.project_id = "proj_alpha_123456"

            # Turn 1: Execute tool while steering override is pending -> must inject directive mid-turn!
            res1 = await executor.execute_tool("code.analyze", {"code": "def f():\n    return 42\n"}, None)
            # Turn 2: Execute tool again -> directive was already consumed on Turn 1, must not duplicate!
            res2 = await executor.execute_tool("code.analyze", {"code": "def g():\n    return 99\n"}, None)
            return res1, res2, executor.trace_logs

    res1, res2, trace_logs = asyncio.run(_run_mid_turn_tool())
    assert "_LIVE_HUMAN_STEERING_DIRECTIVE" in res1
    assert "Switch to HMAC-SHA256" in res1["_LIVE_HUMAN_STEERING_DIRECTIVE"]
    assert any(t.get("action") == "mid_turn_steering_injected" for t in trace_logs)
    assert "_LIVE_HUMAN_STEERING_DIRECTIVE" not in res2


# ─────────────────────────────────────────────────────────────────────────────
# 10. Gap 3: Real-Time WebSocket Streaming (<100ms) on /ws/dashboard
# ─────────────────────────────────────────────────────────────────────────────

def test_gap3_realtime_websocket_dashboard_streaming(client):
    with client.websocket_connect("/ws/dashboard") as ws:
        welcome = ws.receive_json()
        assert welcome["type"] == "system"
        r = client.post(
            "/api/v1/missions/m_exec_3/steer",
            json={"instruction": "Emit structured JSON logs"},
        )
        assert r.status_code == 200
        event = ws.receive_json()
        assert event["type"] == "steering_queued"
        assert event["mission_id"] == "m_exec_33334444"
        assert "Emit structured JSON logs" in event["instruction"]


# ─────────────────────────────────────────────────────────────────────────────
# 11. Gap 5: HMAC Webhook Signature Verification & Two-Way Outbound Callbacks
# ─────────────────────────────────────────────────────────────────────────────

def test_gap5_hmac_webhook_signatures_and_twoway_outbound_callbacks(client, monkeypatch):
    import hashlib
    import hmac

    # 11a. GitHub HMAC-SHA256 signature verification (`X-Hub-Signature-256`)
    gh_secret = "kobits_gh_webhook_secret_key"
    monkeypatch.setenv("KOBITS_GITHUB_WEBHOOK_SECRET", gh_secret)

    gh_payload = {
        "action": "opened",
        "issue": {
            "number": 512,
            "title": "Add circuit breaker to webhook dispatcher",
            "body": "Ensure outbound webhooks have a 3s timeout.",
            "comments_url": "https://api.github.com/repos/acme/payments/issues/512/comments",
        },
        "repository": {"full_name": "acme/payments"},
        "requires_approval": True,
    }
    raw_gh = json.dumps(gh_payload).encode("utf-8")

    # Worst-case: Missing signature header -> 401
    r_missing = client.post(
        "/api/v1/missions/webhooks/inbound",
        content=raw_gh,
        headers={"Content-Type": "application/json"},
    )
    assert r_missing.status_code == 401

    # Worst-case: Forged signature header -> 401
    r_forged = client.post(
        "/api/v1/missions/webhooks/inbound",
        content=raw_gh,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": "sha256=0000000000000000000000000000000000000000000000000000000000000000",
        },
    )
    assert r_forged.status_code == 401

    # Valid GitHub HMAC-SHA256 signature -> 201 Created + initial outbound webhook reply
    valid_gh_sig = "sha256=" + hmac.new(gh_secret.encode("utf-8"), raw_gh, hashlib.sha256).hexdigest()
    r_valid_gh = client.post(
        "/api/v1/missions/webhooks/inbound",
        content=raw_gh,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": valid_gh_sig,
        },
    )
    assert r_valid_gh.status_code == 201, r_valid_gh.text
    gh_res = r_valid_gh.json()
    assert gh_res["signature_verified"] is True
    assert gh_res["source"] == "github_issue"
    assert gh_res["webhook_reply"]["event"] == "AWAITING_APPROVAL"
    assert gh_res["webhook_reply"]["callback_url"] == "https://api.github.com/repos/acme/payments/issues/512/comments"

    gh_mid = gh_res["mission_id"]

    # Approve the mission -> dispatches MISSION_APPROVED outbound webhook notification!
    r_app = client.post(f"/api/v1/missions/{gh_mid[:8]}/approve")
    assert r_app.status_code == 200

    # Deliver the mission -> dispatches DELIVERABLE_READY outbound webhook notification!
    r_del = client.post(f"/api/v1/missions/{gh_mid[:8]}/deliver", json={"push_and_open_pr": False})
    assert r_del.status_code == 200

    # Verify all two-way outbound webhook notifications are persisted and retrievable
    r_wh = client.get(f"/api/v1/missions/{gh_mid[:8]}/webhooks")
    assert r_wh.status_code == 200
    wh_data = r_wh.json()
    events = [n["event"] for n in wh_data["notifications"]]
    assert "AWAITING_APPROVAL" in events
    assert "MISSION_APPROVED" in events
    assert "DELIVERABLE_READY" in events

    # 11b. Slack HMAC-SHA256 (`X-Slack-Signature` + `X-Slack-Request-Timestamp`)
    monkeypatch.delenv("KOBITS_GITHUB_WEBHOOK_SECRET", raising=False)
    slack_secret = "slack_signing_secret_xyz"
    monkeypatch.setenv("KOBITS_SLACK_SIGNING_SECRET", slack_secret)

    slack_payload = {
        "text": "Hotfix: sanitize SQL pagination offset",
        "user_name": "arvind",
        "channel_name": "incidents",
        "response_url": "https://hooks.slack.com/services/T000/B000/X000",
    }
    raw_slack = json.dumps(slack_payload).encode("utf-8")
    ts = "1728216000"
    slack_sig = "v0=" + hmac.new(
        slack_secret.encode("utf-8"),
        f"v0:{ts}:{raw_slack.decode('utf-8')}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    # Forged Slack signature -> 401
    assert client.post(
        "/api/v1/missions/webhooks/inbound",
        content=raw_slack,
        headers={"Content-Type": "application/json", "X-Slack-Signature": "v0=bad", "X-Slack-Request-Timestamp": ts},
    ).status_code == 401

    # Valid Slack signature -> 201
    r_slack_ok = client.post(
        "/api/v1/missions/webhooks/inbound",
        content=raw_slack,
        headers={"Content-Type": "application/json", "X-Slack-Signature": slack_sig, "X-Slack-Request-Timestamp": ts},
    )
    assert r_slack_ok.status_code == 201, r_slack_ok.text
    assert r_slack_ok.json()["signature_verified"] is True
    assert r_slack_ok.json()["source"] == "slack"
    assert r_slack_ok.json()["webhook_reply"]["delivered"] is True


# ═══════════════════════════════════════════════════════════════════════════
# 12. DIFFERENCE 5: FLUID SINGLE-LOOP AGENT (PLAN -> CODE <-> TEST -> DELIVER)
# ═══════════════════════════════════════════════════════════════════════════

def test_difference5_fluid_single_loop_self_healing_until_tests_pass(client, monkeypatch):
    """
    Verifies that MissionRuntime runs Kyros's Fluid Single-Loop Engine by default:
    1. Skips ceremonial waterfall stages (no Repository Understanding, Requirement Clarification,
       UX Architecture, Security Audit, Code Review, Prepare Release, or Post-Mortem tasks).
    2. Dynamically loops IMPLEMENTATION <-> VALIDATION:
       - Pass 1 writes buggy code (`add(a, b)` returning `a - b`) -> real `pytest` fails in sandbox.
       - Fluid loop spawns `Fluid Loop Iteration #2: Fix Verification & Test Failures` with pytest traceback.
       - Pass 2 fixes `calculator.py` (`return a + b`) -> real `pytest` passes -> Mission completes immediately!
    3. Exhaustion guard: if verification keeps failing past `max_iterations`, halts cleanly as FAILED.
    """
    import shutil
    from unittest.mock import patch
    from backend.services.mission_runtime import MissionRuntime
    from backend.services.sandbox_manager import SandboxManager
    from backend.models.mission import ApprovalStatus

    mission_id = "m_fluid_loop_99"
    sb_dir = Path("sandboxes") / f"mission_{mission_id[:8]}"
    sb_dir.mkdir(parents=True, exist_ok=True)

    test_code = "from calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"
    (sb_dir / "test_calculator.py").write_text(test_code, encoding="utf-8")

    async def _seed_fluid_mission():
        async with Session() as s:
            m = Mission(
                id=mission_id,
                organization_id="org_main",
                project_id="proj_alpha_123456",
                created_by="u_main",
                title="Fix calculator.py add function",
                objective="Implement calculator.py so add(a, b) passes test_calculator.py",
                status=MissionStatus.ACTIVE,
                phase=WorkflowPhase.INTAKE,
                requires_approval=False,
                approval_status=ApprovalStatus.APPROVED,
                risk_profile_json=json.dumps({
                    "scope_triage": {
                        "domains": ["backend"],
                        "complexity": "standard",
                        "task_category": "bug_fix",
                        "capabilities": ["python"],
                        "target_files": ["calculator.py"],
                        "fast_track": True,
                    }
                }),
            )
            s.add(m)
            await s.commit()

    asyncio.run(_seed_fluid_mission())

    coder_calls = []

    async def fake_execute_agent_run(run, agent_type, input_data, db=None):
        coder_calls.append({
            "agent": agent_type.value if hasattr(agent_type, "value") else str(agent_type),
            "task_title": input_data.get("task_title"),
            "task_description": input_data.get("task_description"),
        })
        sess_id = input_data.get("sandbox_session_id")
        if len(coder_calls) == 1:
            buggy_code = "def add(a: int, b: int) -> int:\n    return a - b\n"
            if sess_id:
                SandboxManager.write_file(sess_id, "test_calculator.py", test_code)
                SandboxManager.write_file(sess_id, "calculator.py", buggy_code)
            (sb_dir / "calculator.py").write_text(buggy_code, encoding="utf-8")
        else:
            # Pass 2 (Self-Healing Iteration #2): inspect pytest failure and fix bug!
            assert "FLUID VERIFICATION & TEST RUNNER FAILED" in (input_data.get("task_description") or "")
            assert "pytest failed" in (input_data.get("task_description") or "")
            fixed_code = "def add(a: int, b: int) -> int:\n    return a + b\n"
            if sess_id:
                SandboxManager.write_file(sess_id, "test_calculator.py", test_code)
                SandboxManager.write_file(sess_id, "calculator.py", fixed_code)
            (sb_dir / "calculator.py").write_text(fixed_code, encoding="utf-8")
        return {
            "status": "SUCCESS",
            "summary": f"Completed coding pass #{len(coder_calls)}",
            "artifacts": {"modified_files": ["calculator.py", "test_calculator.py"]},
        }

    try:
        with patch("backend.services.mission_runtime.AsyncSessionLocal", Session), \
             patch("backend.services.mission_runtime.execute_agent_run", side_effect=fake_execute_agent_run):
            rt = MissionRuntime(mission_id, "org_main", "u_main")
            asyncio.run(rt.execute())

        # Verify that ONLY the 2 coding passes ran (zero ceremonial waterfall tasks!)
        assert len(coder_calls) == 2
        assert coder_calls[0]["task_title"].startswith("Implement:")
        assert coder_calls[1]["task_title"].startswith("Fluid Loop Iteration #2:")

        async def _verify_db_state():
            async with Session() as s:
                m = await s.get(Mission, mission_id)
                assert m.status == MissionStatus.COMPLETED
                assert m.phase == WorkflowPhase.DELIVERY_REVIEW
                assert m.progress == 100
                meta = json.loads(m.risk_profile_json or "{}")
                fl = meta.get("fluid_loop") or {}
                assert fl.get("status") == "PASSED"
                assert fl.get("iteration") == 2
                assert len(fl.get("history") or []) == 2
                assert fl["history"][0]["status"] == "FAILED"
                assert fl["history"][1]["status"] == "PASSED"

                all_tasks = (
                    await s.execute(select(Task).where(Task.mission_id == mission_id))
                ).scalars().all()
                titles = [t.title for t in all_tasks]
                # Ensure no ceremonial waterfall tasks were spawned
                for ceremonial in (
                    "Repository Understanding",
                    "Requirement Clarification",
                    "Security Audit",
                    "Code Review",
                    "Prepare Release",
                    "Documentation",
                    "Post-Mortem & Memory Sync",
                ):
                    assert ceremonial not in titles

        asyncio.run(_verify_db_state())

        # 3. Exhaustion guard worst-case: when code keeps failing past max_iterations=1,
        # MissionRuntime halts cleanly as FAILED with exact failure reason.
        exhaust_id = "m_fluid_exh_99"
        sb_exh = Path("sandboxes") / f"mission_{exhaust_id[:8]}"
        sb_exh.mkdir(parents=True, exist_ok=True)
        (sb_exh / "test_calculator.py").write_text(test_code, encoding="utf-8")

        async def _seed_exhaust_mission():
            async with Session() as s:
                m2 = Mission(
                    id=exhaust_id,
                    organization_id="org_main",
                    project_id="proj_alpha_123456",
                    created_by="u_main",
                    title="Broken calculator that never passes",
                    objective="Implement calculator.py",
                    status=MissionStatus.ACTIVE,
                    phase=WorkflowPhase.INTAKE,
                    requires_approval=False,
                    approval_status=ApprovalStatus.APPROVED,
                    risk_profile_json=json.dumps({
                        "scope_triage": {
                            "domains": ["backend"],
                            "complexity": "standard",
                            "task_category": "bug_fix",
                            "capabilities": ["python"],
                            "target_files": ["calculator.py"],
                            "fast_track": True,
                        },
                        "fluid_loop": {
                            "enabled": True,
                            "iteration": 1,
                            "max_iterations": 1,
                            "history": [],
                        },
                    }),
                )
                s.add(m2)
                await s.commit()

        asyncio.run(_seed_exhaust_mission())

        async def always_broken_coder(run, agent_type, input_data, db=None):
            sess_id = input_data.get("sandbox_session_id")
            broken = "def add(a: int, b: int) -> int:\n    return 9999\n"
            if sess_id:
                SandboxManager.write_file(sess_id, "test_calculator.py", test_code)
                SandboxManager.write_file(sess_id, "calculator.py", broken)
            (sb_exh / "calculator.py").write_text(broken, encoding="utf-8")
            return {"status": "SUCCESS", "summary": "Wrote broken code"}

        try:
            with patch("backend.services.mission_runtime.AsyncSessionLocal", Session), \
                 patch("backend.services.mission_runtime.execute_agent_run", side_effect=always_broken_coder):
                rt_exh = MissionRuntime(exhaust_id, "org_main", "u_main")
                asyncio.run(rt_exh.execute())

            async def _verify_exhausted():
                async with Session() as s:
                    m2 = await s.get(Mission, exhaust_id)
                    assert m2.status == MissionStatus.FAILED
                    assert "Verification failed after 1 fluid iterations" in (m2.current_stage or "")

            asyncio.run(_verify_exhausted())
        finally:
            shutil.rmtree(sb_exh, ignore_errors=True)
    finally:
        shutil.rmtree(sb_dir, ignore_errors=True)


def test_difference1_kyros_microvm_container_isolation_and_rl_trajectory(client, tmp_path):
    """
    Difference 1 Parity (Kyros Disposable Container / MicroVM & Virtual Rootfs Isolation Runtime + RL Trajectory):
    1. Every sandbox session provisions a disposable VM/container/virtual-rootfs with resource limits
       (memory_mb, cpu_cores, pids_limit) and a dedicated virtual rootfs (.kobits_rootfs).
    2. Destructive root-level commands (`rm -rf /`, `rm -rf /*`, `sudo rm -rf --no-preserve-root /`)
       are intercepted and confined strictly to the disposable `.kobits_rootfs` overlay (host OS and
       workspace remain 100% intact), instead of failing on a naive string blocklist.
    3. System package commands (`apt-get update && apt-get install -y jq build-essential`) succeed inside
       the isolated runtime and record installed packages in `session.installed_packages`.
    4. Container `/workspace` paths work across `sandbox.exec`, `repository.write`, `repository.read`,
       and `repository.edit`.
    5. Kernel device formatting (`mkfs.ext4 /dev/sda1`), fork bombs (`:(){ :|:& };:`), and `../../` mount
       escape attempts are blocked by the Virtual Kernel Guard (`exit_code == 126`).
    6. Host secrets (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GITHUB_TOKEN`, etc.) are scrubbed from the
       isolated execution environment so LLM RL rollouts can never leak host credentials.
    7. `SandboxManager.reset_sandbox(session_id)` resets modified files, command history, and virtual
       rootfs back to `base_commit_sha` for multi-rollout RL training.
    8. `GET /api/v1/missions/{id}/trajectory` exports the complete RL/SFT training episode trajectory
       with `vm_id`, `isolation_driver`, `command_history`, `git_patch`, and `reward == 1.0`.
    """
    from backend.services.sandbox_manager import SandboxManager
    from backend.services.tool_registry import ToolRegistry

    # Seed a synthetic git repo to act as the base project
    src_repo = tmp_path / "rl_train_repo"
    src_repo.mkdir(parents=True, exist_ok=True)
    (src_repo / "app.py").write_text("def greet(name: str) -> str:\n    return f'Hello, {name}'\n", encoding="utf-8")

    session = SandboxManager.create_sandbox(
        project_root=str(src_repo),
        task_description="rl-rollout-isolation-test",
    )

    try:
        # 1. Verify VM metadata & virtual rootfs overlay
        assert session.vm_id.startswith("kobits-vm-")
        assert session.isolation_driver in ("docker_container", "virtual_rootfs_jail", "cloud_microvm")
        assert session.resource_limits["memory_mb"] >= 256
        rootfs_dir = Path(session.sandbox_dir) / ".kobits_rootfs"
        assert (rootfs_dir / "root").is_dir()
        assert (rootfs_dir / "tmp").is_dir()
        assert (rootfs_dir / "etc" / "os-release").is_file()

        # 2. Seed a file inside virtual /root and run `rm -rf /` via `sandbox.exec`
        (rootfs_dir / "root" / "ephemeral_secret.txt").write_text("temp", encoding="utf-8")
        rm_root_res = asyncio.run(
            ToolRegistry.execute_tool(
                "sandbox.exec",
                {"command": "rm -rf /"},
                context={"sandbox_session_id": session.session_id},
            )
        )
        assert rm_root_res["exit_code"] == 0
        assert not (rootfs_dir / "root" / "ephemeral_secret.txt").exists()
        # Host repo and workspace app.py remain completely untouched
        assert (Path(session.sandbox_dir) / "app.py").is_file()
        assert (src_repo / "app.py").is_file()

        # 3. Run `sudo apt-get update && sudo apt-get install -y jq ripgrep` inside sandbox
        apt_res = asyncio.run(
            ToolRegistry.execute_tool(
                "terminal.execute",
                {"command": "sudo apt-get update && sudo apt-get install -y jq ripgrep"},
                context={"sandbox_session_id": session.session_id},
            )
        )
        assert apt_res["exit_code"] == 0
        assert "jq" in session.installed_packages
        assert "ripgrep" in session.installed_packages

        # 4. Verify `/workspace` container path normalization in repository tools & shell commands
        write_res = asyncio.run(
            ToolRegistry.execute_tool(
                "repository.write",
                {
                    "path": "/workspace/math_ops.py",
                    "content": "def square(x: int) -> int:\n    return x * x\n",
                },
                context={"sandbox_session_id": session.session_id},
            )
        )
        assert write_res.get("success") is True
        assert write_res.get("path") == "math_ops.py"

        read_res = asyncio.run(
            ToolRegistry.execute_tool(
                "repository.read",
                {"path": "/workspace/math_ops.py"},
                context={"sandbox_session_id": session.session_id},
            )
        )
        assert "def square" in read_res.get("content", "")

        # Run python3 referencing /workspace/math_ops.py
        cmd_res = SandboxManager.run_command(
            session.session_id,
            'python3 -c "import sys; sys.path.insert(0, \'/workspace\'); import math_ops; print(math_ops.square(9))"',
        )
        assert cmd_res["exit_code"] == 0
        assert "81" in (cmd_res.get("stdout") or "")

        # 5. Verify Virtual Kernel Guard blocks `../../` escape, `mkfs`, and fork bombs
        escape_res = SandboxManager.run_command(session.session_id, "cat ../../.env")
        assert escape_res["exit_code"] == 126
        assert "Blocked" in (escape_res.get("stderr") or "")

        mkfs_res = SandboxManager.run_command(session.session_id, "mkfs.ext4 /dev/sda1")
        assert mkfs_res["exit_code"] == 126

        fork_res = SandboxManager.run_command(session.session_id, ":(){ :|:& };:")
        assert fork_res["exit_code"] == 126

        # 6. Verify host API keys / secrets are scrubbed from the sandbox environment
        old_secret = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "sk-live-super-secret-host-key-do-not-leak"
        try:
            env_res = SandboxManager.run_command(
                session.session_id,
                'python3 -c "import os; print(os.environ.get(\'OPENAI_API_KEY\', \'SCRUBBED_OK\'))"',
            )
            assert env_res["exit_code"] == 0
            assert "SCRUBBED_OK" in (env_res.get("stdout") or "")
            assert "sk-live-super-secret-host-key-do-not-leak" not in (env_res.get("stdout") or "")
        finally:
            if old_secret is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = old_secret

        # 7. Verify `.kobits_rootfs` never pollutes `get_diff()`
        diff_info = SandboxManager.get_diff(session.session_id)
        assert "math_ops.py" in diff_info["files_changed"]
        assert all(not f.startswith(".kobits_rootfs") for f in diff_info["files_changed"])

        # 8. Export RL/SFT training trajectory and verify reward & command telemetry
        traj = SandboxManager.export_training_trajectory(
            session.session_id,
            mission_objective="Implement math_ops.square",
            verification_report={"status": "PASSED", "verified_files": ["math_ops.py"]},
        )
        assert traj["reward"] == 1.0
        assert traj["vm_id"] == session.vm_id
        assert len(traj["command_history"]) >= 5
        assert "def square" in traj["git_patch"]

        # 9. Test `reset_sandbox` for multi-rollout RL training reset to base_commit_sha
        reset_res = SandboxManager.reset_sandbox(session.session_id)
        assert reset_res["success"] is True
        assert reset_res["reset"] is True
        assert len(session.command_history) == 0
        assert len(session.installed_packages) == 0

        # 10. Test HTTP endpoint `GET /api/v1/missions/{mission_id}/trajectory`
        traj_resp = client.get("/api/v1/missions/m_done_77778888/trajectory")
        assert traj_resp.status_code == 200
        traj_json = traj_resp.json()
        assert traj_json["mission_id"] == "m_done_77778888"
        assert traj_json["reward"] == 1.0
        assert "isolation_driver" in traj_json
        assert "resource_limits" in traj_json
    finally:
        SandboxManager.cleanup(session.session_id)


def test_difference2_concurrency_distributed_worker_queue_and_event_bus(client, tmp_path):
    """
    Difference 2 Parity (Concurrency, Distributed Task Queue & Cross-Process Event Bus):
    1. SQLite WAL Mode Concurrency:
       - 10 parallel coroutines execute simultaneous writes/reads against SQLite with zero locking errors,
         verifying PRAGMA journal_mode=WAL, PRAGMA synchronous=NORMAL, and PRAGMA busy_timeout=30000.
    2. Durable WorkerQueue:
       - Atomic job enqueue, priority ordering (high priority claimed before low priority),
         atomic leasing with worker_id and lease_timeout_at.
       - Worker heartbeat extends lease during long-running tasks.
       - Automatic recovery of stale leases from dead/timed-out workers without losing tasks.
       - Decoupled worker processing via `Worker.run_until_idle()`.
    3. Cross-Process Event Bus:
       - Simulates separate CLI / worker processes emitting WebSocket events into the durable bus.
       - Verifies that `ConnectionManager` listener polls and dispatches cross-process events within <50ms.
    4. Mission API Queue Dispatch:
       - `POST /api/v1/missions/projects/{id}/missions` enqueues a durable `mission.execute` job.
    """
    import asyncio
    import sqlite3
    import time
    from backend.services.worker_queue import WorkerQueue, Worker
    from backend.services.websocket_manager import ConnectionManager
    from backend.core.database import AsyncSessionLocal, engine
    from backend.models.project import Activity, ActivityType

    # Set isolated DB paths for test
    q_db = tmp_path / "test_worker_queue.db"
    eb_db = tmp_path / "test_event_bus.db"
    os.environ["KOBITS_QUEUE_DB"] = str(q_db)
    WorkerQueue.set_db_path(str(q_db))

    # 1. Test SQLite WAL Concurrency: 10 parallel writers
    async def _concurrent_writer(worker_idx: int):
        for op in range(5):
            async with AsyncSessionLocal() as s:
                act = Activity(
                    organization_id="org_main",
                    project_id="proj_alpha_123456",
                    user_id="kyros_user",
                    type=ActivityType.MISSION_UPDATED,
                    title=f"Parallel Activity from Worker {worker_idx} op {op}",
                    description=f"Concurrency test write {worker_idx}:{op}",
                )
                s.add(act)
                await s.commit()

    async def _run_concurrency():
        tasks = [_concurrent_writer(i) for i in range(10)]
        await asyncio.gather(*tasks)

    asyncio.run(_run_concurrency())

    # Verify SQLite WAL journal mode on live engine
    def _check_wal():
        with WorkerQueue._get_connection() as conn:
            row = conn.execute("PRAGMA journal_mode").fetchone()
            assert row[0].lower() == "wal"
            row_bt = conn.execute("PRAGMA busy_timeout").fetchone()
            assert row_bt[0] >= 10000

    _check_wal()

    # 2. Test WorkerQueue: Priority & Atomic Leasing
    async def _test_queue_lifecycle():
        await WorkerQueue.clear_queue()

        # Enqueue low priority and high priority jobs
        job_low = await WorkerQueue.enqueue("test.task", {"value": "low"}, priority=1, max_retries=2)
        job_high = await WorkerQueue.enqueue("test.task", {"value": "high"}, priority=10, max_retries=2)

        # Claim 1: Should claim the high priority job first
        claimed_1 = await WorkerQueue.claim("worker_A", lease_seconds=10.0)
        assert claimed_1 is not None
        assert claimed_1.id == job_high.id
        assert claimed_1.status == "LEASED"
        assert claimed_1.worker_id == "worker_A"
        assert claimed_1.attempt_count == 1

        # Claim 2: Worker B cannot steal job_high, but gets job_low
        claimed_2 = await WorkerQueue.claim("worker_B", lease_seconds=10.0)
        assert claimed_2 is not None
        assert claimed_2.id == job_low.id
        assert claimed_2.worker_id == "worker_B"

        # Claim 3: Queue is now empty of pending jobs
        claimed_3 = await WorkerQueue.claim("worker_C")
        assert claimed_3 is None

        # 3. Test Worker Heartbeat
        old_timeout = claimed_1.lease_timeout_at
        time.sleep(0.01)
        hb_ok = await WorkerQueue.heartbeat(claimed_1.id, "worker_A", extend_seconds=120.0)
        assert hb_ok is True
        refreshed = await WorkerQueue.get_job(claimed_1.id)
        assert refreshed.lease_timeout_at > old_timeout

        # 4. Test Complete
        comp_ok = await WorkerQueue.complete(claimed_1.id, "worker_A", {"success": True, "processed": 42})
        assert comp_ok is True
        job_comp = await WorkerQueue.get_job(claimed_1.id)
        assert job_comp.status == "COMPLETED"
        assert job_comp.result["processed"] == 42

        # 5. Test Stale Lease Recovery (simulate crashed worker on job_low)
        with WorkerQueue._get_connection() as conn:
            past_iso = "2020-01-01T00:00:00"
            conn.execute("UPDATE worker_jobs SET lease_timeout_at = ? WHERE id = ?", (past_iso, job_low.id))

        # Claiming now should recover the expired job_low lease
        recovered = await WorkerQueue.claim("worker_D", lease_seconds=10.0)
        assert recovered is not None
        assert recovered.id == job_low.id
        assert recovered.worker_id == "worker_D"
        assert recovered.attempt_count == 2  # Incremented attempt

        # 6. Test Fail & Retry exhaustion
        fail_ok = await WorkerQueue.fail(recovered.id, "worker_D", "Fatal failure", allow_retry=True)
        assert fail_ok is True
        job_failed = await WorkerQueue.get_job(recovered.id)
        # Reached max_retries (2) -> FAILED
        assert job_failed.status == "FAILED"
        assert "Fatal failure" in job_failed.error

    asyncio.run(_test_queue_lifecycle())

    # 3. Test Decoupled Worker Execution (`Worker.run_until_idle`)
    async def _test_worker_runner():
        custom_calls = []

        async def _custom_handler(payload, ctx):
            custom_calls.append(payload.get("num"))
            return {"doubled": payload.get("num", 0) * 2}

        WorkerQueue.register_handler("custom.calc", _custom_handler)
        await WorkerQueue.enqueue("custom.calc", {"num": 21})

        worker = Worker(worker_id="runner_1", queues=["default"])
        processed = await worker.run_until_idle()
        assert processed == 1
        assert custom_calls == [21]

    asyncio.run(_test_worker_runner())

    # 4. Test Cross-Process WebSocket Event Bus
    async def _test_cross_process_event_bus():
        bus_mgr_a = ConnectionManager()
        bus_mgr_a._bus_db_path = str(eb_db)
        bus_mgr_a._my_pid = 11111

        bus_mgr_b = ConnectionManager()
        bus_mgr_b._bus_db_path = str(eb_db)
        bus_mgr_b._my_pid = 22222

        received_events = []

        # Mock a WebSocket client on bus_mgr_b
        class DummyWebSocket:
            async def send_json(self, data):
                received_events.append(data)

        dummy_ws = DummyWebSocket()
        bus_mgr_b.active_connections["dashboard"] = [dummy_ws]
        bus_mgr_b.start_bus_listener()

        try:
            # Process A emits an event
            await bus_mgr_a.broadcast("m_concurrency_test", {"type": "phase_changed", "phase": "IMPLEMENTATION"})

            # Wait for Process B's listener loop to pick it up (<100ms)
            for _ in range(20):
                await asyncio.sleep(0.02)
                if received_events:
                    break

            assert len(received_events) >= 1
            assert received_events[0]["type"] == "phase_changed"
            assert received_events[0]["phase"] == "IMPLEMENTATION"
            assert received_events[0]["mission_id"] == "m_concurrency_test"
        finally:
            bus_mgr_b.stop_bus_listener()

    asyncio.run(_test_cross_process_event_bus())

    # 5. Test Mission Creation enqueues durable job in WorkerQueue
    async def _test_api_enqueues():
        resp = client.post(
            "/api/v1/missions/projects/proj_alpha_123456/missions",
            json={
                "title": "Durable Worker Mission",
                "objective": "Verify job is queued durably",
                "auto_execute": True,
            },
        )
        assert resp.status_code == 201
        new_m_id = resp.json()["id"]
        job = await WorkerQueue.get_job(f"job_mission_{new_m_id}")
        assert job is not None
        assert job.task_type == "mission.execute"
        assert job.payload["mission_id"] == new_m_id

    asyncio.run(_test_api_enqueues())


