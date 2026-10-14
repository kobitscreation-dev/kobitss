"""
Comprehensive Test Suite for GitHub CI Healing, Code Review Webhooks,
Negative-to-Positive TDD Reproducer, Best-of-N Candidate Exploration,
and Human Engineer Delivery Review.
"""

import json
import uuid
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, AsyncMock

from backend.main import app
from backend.core.database import AsyncSessionLocal
from backend.models.organization import Organization, User, OrganizationMember
from backend.models.project import Project, Task, TaskStatus
from backend.models.github import (
    GitHubConnection, GitHubConnectionStatus, Repository,
    RepositoryStatus, PullRequest, PullRequestStatus
)
from backend.models.mission import Mission, MissionStatus, WorkflowPhase, ApprovalStatus
from backend.services.sandbox_review import persist_mission_deliverable


@pytest.fixture
async def setup_webhook_context():
    """Setup test organization, user, project, repo, mission, and PR."""
    u_suffix = uuid.uuid4().hex[:6]
    async with AsyncSessionLocal() as db:
        user = User(
            email=f"reviewer_{u_suffix}@kobits.space",
            full_name="Lead Reviewer",
            hashed_password="hashed_pw"
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

        org = Organization(name=f"Enterprise Org {u_suffix}")
        db.add(org)
        await db.commit()
        await db.refresh(org)

        member = OrganizationMember(organization_id=org.id, user_id=user.id, role="OWNER")
        db.add(member)

        project = Project(
            name="Autonomous Payment Service",
            organization_id=org.id,
            created_by=user.id,
            repository_url="https://github.com/enterprise-dev/payments-service"
        )
        db.add(project)
        await db.commit()
        await db.refresh(project)

        conn = GitHubConnection(
            organization_id=org.id,
            user_id=user.id,
            github_username="enterprise-dev",
            status=GitHubConnectionStatus.CONNECTED,
            metadata_json=json.dumps({"token": "ghp_mock_enterprise_pat_999"})
        )
        db.add(conn)
        await db.commit()
        await db.refresh(conn)

        repo = Repository(
            project_id=project.id,
            organization_id=org.id,
            github_connection_id=conn.id,
            github_repo_id=f"999{u_suffix}",
            owner="enterprise-dev",
            name="payments-service",
            full_name="enterprise-dev/payments-service",
            default_branch="main",
            selected_branch="main",
            clone_url="https://github.com/enterprise-dev/payments-service.git",
            web_url="https://github.com/enterprise-dev/payments-service",
            status=RepositoryStatus.READY
        )
        db.add(repo)
        await db.commit()
        await db.refresh(repo)

        branch_name = f"kobits/fix-stripe-timeout-{u_suffix}"
        mission = Mission(
            project_id=project.id,
            organization_id=org.id,
            created_by=user.id,
            title="Fix Stripe Webhook Timeout",
            objective="Ensure timeout handling does not hang indefinitely",
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
            github_pr_id="101",
            number=101,
            title="Fix Stripe Webhook Timeout",
            source_branch=branch_name,
            target_branch="main",
            head_sha=f"sha_{u_suffix}_head",
            status=PullRequestStatus.OPEN,
            checks_status="pending",
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
            "head_sha": pr.head_sha,
        }


@pytest.mark.asyncio
async def test_webhook_check_run_ci_failure_dispatches_tdd_healing(setup_webhook_context):
    """Verify check_run failure in webhooks.py extracts trace, creates TDD task, and dispatches healing."""
    ctx = setup_webhook_context
    transport = ASGITransport(app=app)

    payload = {
        "action": "completed",
        "check_run": {
            "name": "pytest-unit-tests",
            "head_sha": ctx["head_sha"],
            "status": "completed",
            "conclusion": "failure",
            "check_suite": {"head_branch": ctx["branch"]},
            "output": {
                "title": "Unit Tests Failed",
                "summary": "1 test failed: test_timeout_zero_raises",
                "text": "TimeoutError: connection timed out after 0 seconds",
                "annotations": [
                    {
                        "path": "payments/stripe_client.py",
                        "start_line": 84,
                        "line": 84,
                        "message": "ZeroDivisionError or TimeoutError not caught",
                        "annotation_level": "failure"
                    }
                ]
            },
            "pull_requests": [{"number": 101}]
        },
        "repository": {
            "full_name": "enterprise-dev/payments-service",
            "html_url": "https://github.com/enterprise-dev/payments-service"
        }
    }

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("backend.services.github_service.GitHubService.create_issue_comment", new_callable=AsyncMock) as mock_comment, \
             patch("backend.services.mission_runtime.MissionRuntime.run_mission_background", new_callable=AsyncMock) as mock_runtime:

            resp = await client.post(
                "/api/v1/webhooks/github",
                json=payload,
                headers={"X-GitHub-Event": "check_run"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ci_remediation_dispatched"
            assert data["mission_id"] == ctx["mission"].id
            assert "task_id" in data

            async with AsyncSessionLocal() as db:
                task = await db.get(Task, data["task_id"])
                assert task is not None
                assert task.is_correction is True
                assert task.status == TaskStatus.PENDING
                meta = json.loads(task.metadata_json)
                assert meta["tdd_mode"] is True
                assert meta["best_of_n"] is True
                assert "payments/stripe_client.py" in task.description

                # Verify PR checks status
                pr = await db.get(PullRequest, ctx["pr"].id)
                assert pr.checks_status == "failure"
                assert pr.status == PullRequestStatus.CHECKS_FAILED

                # Verify mission was reactivated
                m = await db.get(Mission, ctx["mission"].id)
                assert m.status == MissionStatus.ACTIVE
                assert m.phase == WorkflowPhase.IMPLEMENTATION


@pytest.mark.asyncio
async def test_webhook_workflow_run_ci_failure(setup_webhook_context):
    """Verify workflow_run failure dispatches remediation."""
    ctx = setup_webhook_context
    transport = ASGITransport(app=app)

    payload = {
        "action": "completed",
        "workflow_run": {
            "name": "Production Integration Gates",
            "head_branch": ctx["branch"],
            "head_sha": ctx["head_sha"],
            "status": "completed",
            "conclusion": "failure",
            "html_url": "https://github.com/enterprise-dev/payments-service/actions/runs/12345",
            "pull_requests": [{"number": 101}]
        },
        "repository": {
            "full_name": "enterprise-dev/payments-service"
        }
    }

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("backend.services.github_service.GitHubService.create_issue_comment", new_callable=AsyncMock), \
             patch("backend.services.mission_runtime.MissionRuntime.run_mission_background", new_callable=AsyncMock):

            resp = await client.post(
                "/api/v1/webhooks/github",
                json=payload,
                headers={"X-GitHub-Event": "workflow_run"}
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "ci_remediation_dispatched"


@pytest.mark.asyncio
async def test_webhook_check_run_success_marks_mergeable(setup_webhook_context):
    """Verify passing check_run updates PR checks_status and marks mergeable if approved."""
    ctx = setup_webhook_context
    transport = ASGITransport(app=app)

    # Set PR review status as approved
    async with AsyncSessionLocal() as db:
        pr = await db.get(PullRequest, ctx["pr"].id)
        pr.review_status = "approved"
        await db.commit()

    payload = {
        "action": "completed",
        "check_run": {
            "name": "all-gates-passed",
            "head_sha": ctx["head_sha"],
            "status": "completed",
            "conclusion": "success",
            "check_suite": {"head_branch": ctx["branch"]},
        },
        "repository": {
            "full_name": "enterprise-dev/payments-service"
        }
    }

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/webhooks/github",
            json=payload,
            headers={"X-GitHub-Event": "check_run"}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "processed"
        assert resp.json()["conclusion"] == "success"

        async with AsyncSessionLocal() as db:
            updated_pr = await db.get(PullRequest, ctx["pr"].id)
            assert updated_pr.checks_status == "success"
            assert updated_pr.status == PullRequestStatus.MERGEABLE


@pytest.mark.asyncio
async def test_webhook_review_comment_dispatches_reproducer_task(setup_webhook_context):
    """Verify human reviewer comment on line of code automatically triggers TDD fix loop."""
    ctx = setup_webhook_context
    transport = ASGITransport(app=app)

    payload = {
        "action": "created",
        "pull_request": {
            "number": 101,
            "head": {"ref": ctx["branch"]},
            "base": {"ref": "main"}
        },
        "comment": {
            "id": 887766,
            "path": "payments/stripe_client.py",
            "line": 95,
            "diff_hunk": "@@ -90,6 +90,7 @@ def process_charge(timeout):\n+    resp = stripe.charge(timeout=timeout)",
            "body": "Please handle the case where timeout is 0 to avoid zero-division or freeze"
        },
        "repository": {
            "full_name": "enterprise-dev/payments-service"
        },
        "sender": {
            "login": "senior-lead"
        }
    }

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("backend.services.github_service.GitHubService.reply_to_pr_review_comment", new_callable=AsyncMock) as mock_reply, \
             patch("backend.services.mission_runtime.MissionRuntime.run_mission_background", new_callable=AsyncMock):

            resp = await client.post(
                "/api/v1/webhooks/github",
                json=payload,
                headers={"X-GitHub-Event": "pull_request_review_comment"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "pr_review_correction_dispatched"
            assert data["mission_id"] == ctx["mission"].id

            async with AsyncSessionLocal() as db:
                task = await db.get(Task, data["task_id"])
                assert task.is_correction is True
                assert "timeout is 0" in task.description
                meta = json.loads(task.metadata_json)
                assert meta["tdd_mode"] is True
                assert meta["best_of_n"] is True


@pytest.mark.asyncio
async def test_human_engineer_delivery_review_approval(setup_webhook_context):
    """Verify Human Engineer Delivery Review approval advances mission to DEPLOYMENT."""
    ctx = setup_webhook_context
    transport = ASGITransport(app=app)

    # Put mission into DELIVERY_REVIEW phase
    async with AsyncSessionLocal() as db:
        m = await db.get(Mission, ctx["mission"].id)
        m.phase = WorkflowPhase.DELIVERY_REVIEW
        m.status = MissionStatus.AWAITING_APPROVAL
        m.approval_status = ApprovalStatus.PENDING
        await db.commit()

    review_approval_payload = {
        "action": "submitted",
        "review": {
            "state": "approved",
            "body": "LGTM! Verified by human enterprise engineer."
        },
        "pull_request": {
            "number": 101
        },
        "repository": {
            "full_name": "enterprise-dev/payments-service"
        }
    }

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/webhooks/github",
            json=review_approval_payload,
            headers={"X-GitHub-Event": "pull_request_review"}
        )
        assert resp.status_code == 200
        assert resp.json()["review_status"] == "approved"

        async with AsyncSessionLocal() as db:
            m = await db.get(Mission, ctx["mission"].id)
            assert m.approval_status == ApprovalStatus.APPROVED
            assert m.phase == WorkflowPhase.DEPLOYMENT


@pytest.mark.asyncio
async def test_persist_mission_deliverable_posts_pr_comment_on_heal(setup_webhook_context, tmp_path):
    """Verify persist_mission_deliverable pushes to existing PR and posts explanatory comment."""
    ctx = setup_webhook_context

    sb_dir = tmp_path / "mock_sandbox"
    sb_dir.mkdir()
    (sb_dir / ".git").mkdir()

    with patch("backend.services.sandbox_review.get_mission_diff", new_callable=AsyncMock) as mock_diff, \
         patch("backend.services.github_service.GitHubService.create_issue_comment", new_callable=AsyncMock) as mock_comment, \
         patch("subprocess.run") as mock_subproc:

        mock_diff.return_value = {
            "sandbox_dir": str(sb_dir),
            "totals": {"files": 2, "added": 14, "removed": 3},
            "files": ["payments/stripe_client.py", "tests/test_stripe.py"]
        }

        async with AsyncSessionLocal() as db:
            m = await db.get(Mission, ctx["mission"].id)
            res = await persist_mission_deliverable(db, m, push_and_open_pr=True)

            assert res["pr"] is not None
            assert res["pr"]["source_branch"] == ctx["branch"]
            assert mock_subproc.called
            assert mock_comment.called
            comment_body = mock_comment.call_args.kwargs.get("body") or (mock_comment.call_args[0][2] if len(mock_comment.call_args[0]) > 2 else "")
            assert "Kobits Autonomous CI/Review Remediation Deployed" in comment_body
            assert ctx["branch"] in comment_body


@pytest.mark.asyncio
async def test_human_engineer_delivery_approval_via_pr_comment(setup_webhook_context):
    """Verify human engineer delivery approval via PR conversation comment advances to DEPLOYMENT."""
    ctx = setup_webhook_context
    transport = ASGITransport(app=app)

    async with AsyncSessionLocal() as db:
        m = await db.get(Mission, ctx["mission"].id)
        m.phase = WorkflowPhase.DELIVERY_REVIEW
        m.status = MissionStatus.AWAITING_APPROVAL
        m.approval_status = ApprovalStatus.PENDING
        await db.commit()

    pr_comment_approval = {
        "action": "created",
        "issue": {
            "number": 101,
            "pull_request": {"url": "https://api.github.com/repos/enterprise-dev/payments-service/pulls/101"}
        },
        "comment": {
            "id": 998877,
            "body": "LGTM! Approved for delivery by Principal Engineer."
        },
        "repository": {
            "full_name": "enterprise-dev/payments-service"
        },
        "sender": {
            "login": "principal-architect"
        }
    }

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/webhooks/github",
            json=pr_comment_approval,
            headers={"X-GitHub-Event": "issue_comment"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "delivery_review_approved"
        assert data["phase"] == "DEPLOYMENT"

        async with AsyncSessionLocal() as db:
            m = await db.get(Mission, ctx["mission"].id)
            assert m.approval_status == ApprovalStatus.APPROVED
            assert m.phase == WorkflowPhase.DEPLOYMENT


@pytest.mark.asyncio
async def test_human_engineer_delivery_approval_via_api_endpoint(setup_webhook_context):
    """Verify /api/v1/missions/{id}/approve endpoint accepts DELIVERY_REVIEW missions."""
    ctx = setup_webhook_context
    from backend.api.deps import get_current_active_user

    async with AsyncSessionLocal() as db:
        m = await db.get(Mission, ctx["mission"].id)
        m.phase = WorkflowPhase.DELIVERY_REVIEW
        m.status = MissionStatus.COMPLETED
        m.approval_status = ApprovalStatus.PENDING
        await db.commit()

    app.dependency_overrides[get_current_active_user] = lambda: ctx["user"]
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/api/v1/missions/{ctx['mission'].id}/approve")
            assert resp.status_code == 200
            assert resp.json()["status"] == "APPROVED"
            assert resp.json()["approval_status"] == "APPROVED"

            async with AsyncSessionLocal() as db:
                m = await db.get(Mission, ctx["mission"].id)
                assert m.phase == WorkflowPhase.DEPLOYMENT
                assert m.approval_status == ApprovalStatus.APPROVED
    finally:
        app.dependency_overrides.pop(get_current_active_user, None)


@pytest.mark.asyncio
async def test_reproducer_engine_synthesizers(tmp_path):
    """Verify ReproducerEngine synthesizers generate deterministic negative-to-positive reproducers."""
    from backend.services.reproducer_engine import ReproducerEngine

    sb = tmp_path / "test_sb"
    sb.mkdir()

    # Case 1: CI failure synthesizer
    code_file = sb / "service.py"
    code_file.write_text("def handle_request(timeout):\n    return timeout\n", encoding="utf-8")

    ci_script = ReproducerEngine.synthesize_ci_reproducer(
        check_name="timeout-validation",
        failure_details="Timeout zero check missing in service.py: timeout=0 must be rejected",
        annotations=[{"path": "service.py", "line": 2, "message": "Missing timeout guard"}]
    )
    ReproducerEngine.author_reproducer(str(sb), ci_script, file_name="reproduce_ci.py")

    # Baseline on unpatched code must fail (valid negative baseline)
    base_res = ReproducerEngine.verify_negative_baseline(str(sb), "reproduce_ci.py")
    assert base_res.valid is True
    assert base_res.exit_code != 0

    # Apply patch
    code_file.write_text("def handle_request(timeout):\n    if timeout <= 0:\n        raise ValueError('timeout <= 0')\n    return timeout\n", encoding="utf-8")

    # Positive verification on patched code must pass
    pos_res = ReproducerEngine.verify_positive(str(sb), "reproduce_ci.py")
    assert pos_res.success is True
    assert pos_res.reproducer_passed is True

