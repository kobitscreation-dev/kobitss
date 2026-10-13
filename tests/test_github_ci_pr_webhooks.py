import pytest
import json
import uuid
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
from backend.models.mission import Mission, MissionStatus, WorkflowPhase


@pytest.fixture
async def setup_gh_mission_and_pr():
    """Setup test organization, user, project, repository, mission, and pull request."""
    u_suffix = uuid.uuid4().hex[:6]
    async with AsyncSessionLocal() as db:
        user = User(email=f"gh_ci_{u_suffix}@kobits.space", full_name="CI Tester", hashed_password="pw")
        db.add(user)
        await db.commit()
        await db.refresh(user)

        org = Organization(name=f"CI Test Org {u_suffix}")
        db.add(org)
        await db.commit()
        await db.refresh(org)

        member = OrganizationMember(organization_id=org.id, user_id=user.id, role="OWNER")
        db.add(member)

        project = Project(name="CI Automated App", organization_id=org.id, created_by=user.id)
        db.add(project)
        await db.commit()
        await db.refresh(project)

        conn = GitHubConnection(
            organization_id=org.id,
            user_id=user.id,
            github_username="citestdev",
            status=GitHubConnectionStatus.CONNECTED,
            metadata_json=json.dumps({"token": "ghp_mock_ci_token_456"})
        )
        db.add(conn)
        await db.commit()
        await db.refresh(conn)

        repo = Repository(
            project_id=project.id,
            organization_id=org.id,
            github_connection_id=conn.id,
            github_repo_id=f"123{u_suffix}",
            owner="citestdev",
            name="ci-test-repo",
            full_name="citestdev/ci-test-repo",
            default_branch="main",
            selected_branch="main",
            clone_url="https://github.com/citestdev/ci-test-repo.git",
            web_url="https://github.com/citestdev/ci-test-repo",
            status=RepositoryStatus.READY
        )
        db.add(repo)
        await db.commit()
        await db.refresh(repo)

        branch_name = f"kobits/feature-auth-{u_suffix}"
        mission = Mission(
            project_id=project.id,
            organization_id=org.id,
            created_by=user.id,
            title="Implement OAuth Feature",
            objective="Build full OAuth integration and tests",
            status=MissionStatus.EXECUTING,
            phase=WorkflowPhase.VALIDATION,
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
            github_pr_id="77",
            number=77,
            title="Implement OAuth Feature",
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
async def test_check_run_failure_triggers_remediation(setup_gh_mission_and_pr):
    ctx = setup_gh_mission_and_pr
    transport = ASGITransport(app=app)

    check_run_payload = {
        "action": "completed",
        "check_run": {
            "name": "lint-and-typecheck",
            "head_sha": ctx["head_sha"],
            "status": "completed",
            "conclusion": "failure",
            "check_suite": {
                "head_branch": ctx["branch"]
            },
            "output": {
                "title": "ESLint and Mypy Failed",
                "summary": "3 errors found in user_service.py",
                "text": "user_service.py:42: error: Incompatible types in assignment",
                "annotations": [
                    {
                        "path": "backend/user_service.py",
                        "start_line": 42,
                        "line": 42,
                        "message": "Incompatible types in assignment (expression has type 'int', variable has type 'str')",
                        "annotation_level": "failure"
                    }
                ]
            },
            "pull_requests": [{"number": 77}]
        },
        "repository": {
            "full_name": "citestdev/ci-test-repo"
        }
    }

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("backend.services.github_app.GitHubAppService.verify_webhook_signature", return_value=True), \
             patch("backend.services.github_service.GitHubService.create_issue_comment", new_callable=AsyncMock) as mock_comment, \
             patch("backend.services.mission_runtime.MissionRuntime.run_mission_background", new_callable=AsyncMock) as mock_runtime:

            resp = await client.post(
                "/api/v1/github/webhooks/events",
                json=check_run_payload,
                headers={"X-GitHub-Event": "check_run"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ci_remediation_dispatched"
            assert data["mission_id"] == ctx["mission"].id
            assert "task_id" in data

            # Verify task was created in database
            async with AsyncSessionLocal() as db:
                task = await db.get(Task, data["task_id"])
                assert task is not None
                assert task.is_correction is True
                assert task.status == TaskStatus.PENDING
                assert "Incompatible types in assignment" in task.description
                assert "lint-and-typecheck" in task.title

                # Verify PR checks_status is marked failure
                pr = await db.get(PullRequest, ctx["pr"].id)
                assert pr.checks_status == "failure"
                assert pr.status == PullRequestStatus.CHECKS_FAILED


@pytest.mark.asyncio
async def test_workflow_run_failure_triggers_remediation(setup_gh_mission_and_pr):
    ctx = setup_gh_mission_and_pr
    transport = ASGITransport(app=app)

    workflow_payload = {
        "action": "completed",
        "workflow_run": {
            "name": "Integration Tests Suite",
            "head_branch": ctx["branch"],
            "head_sha": ctx["head_sha"],
            "status": "completed",
            "conclusion": "failure",
            "html_url": "https://github.com/citestdev/ci-test-repo/actions/runs/987654321",
            "pull_requests": [{"number": 77}]
        },
        "repository": {
            "full_name": "citestdev/ci-test-repo"
        }
    }

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("backend.services.github_app.GitHubAppService.verify_webhook_signature", return_value=True), \
             patch("backend.services.github_service.GitHubService.create_issue_comment", new_callable=AsyncMock), \
             patch("backend.services.mission_runtime.MissionRuntime.run_mission_background", new_callable=AsyncMock):

            resp = await client.post(
                "/api/v1/github/webhooks/events",
                json=workflow_payload,
                headers={"X-GitHub-Event": "workflow_run"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ci_remediation_dispatched"
            assert data["mission_id"] == ctx["mission"].id


@pytest.mark.asyncio
async def test_check_run_success_updates_pr(setup_gh_mission_and_pr):
    ctx = setup_gh_mission_and_pr
    transport = ASGITransport(app=app)

    success_payload = {
        "action": "completed",
        "check_run": {
            "name": "all-tests-green",
            "head_sha": ctx["head_sha"],
            "status": "completed",
            "conclusion": "success",
            "check_suite": {"head_branch": ctx["branch"]}
        },
        "repository": {
            "full_name": "citestdev/ci-test-repo"
        }
    }

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("backend.services.github_app.GitHubAppService.verify_webhook_signature", return_value=True):
            resp = await client.post(
                "/api/v1/github/webhooks/events",
                json=success_payload,
                headers={"X-GitHub-Event": "check_run"}
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "processed"
            assert resp.json()["conclusion"] == "success"

            async with AsyncSessionLocal() as db:
                pr = await db.get(PullRequest, ctx["pr"].id)
                assert pr.checks_status == "success"


@pytest.mark.asyncio
async def test_pr_review_comment_on_kobits_pr_without_mention(setup_gh_mission_and_pr):
    ctx = setup_gh_mission_and_pr
    transport = ASGITransport(app=app)

    # Human reviewer writes "Please rename this variable" without explicit @kobits tag
    review_comment_payload = {
        "action": "created",
        "pull_request": {
            "number": 77,
            "id": 997700,
            "head": {"ref": ctx["branch"]},
            "base": {"ref": "main"}
        },
        "comment": {
            "id": 554433,
            "path": "backend/auth.py",
            "line": 88,
            "diff_hunk": "@@ -85,6 +85,7 @@ def verify_token():\n+    x = parse_jwt(t)",
            "body": "Please rename this variable 'x' to 'decoded_token' and add a docstring"
        },
        "repository": {
            "full_name": "citestdev/ci-test-repo"
        },
        "sender": {
            "login": "senior-reviewer"
        }
    }

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("backend.services.github_app.GitHubAppService.verify_webhook_signature", return_value=True), \
             patch("backend.services.github_service.GitHubService.reply_to_pr_review_comment", new_callable=AsyncMock) as mock_reply, \
             patch("backend.services.github_service.GitHubService.create_commit_status", new_callable=AsyncMock), \
             patch("backend.services.mission_runtime.MissionRuntime.run_mission_background", new_callable=AsyncMock) as mock_runtime:

            resp = await client.post(
                "/api/v1/github/webhooks/events",
                json=review_comment_payload,
                headers={"X-GitHub-Event": "pull_request_review_comment"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "pr_review_correction_dispatched"
            assert data["mission_id"] == ctx["mission"].id
            assert "task_id" in data

            # Verify task was added to existing mission
            async with AsyncSessionLocal() as db:
                task = await db.get(Task, data["task_id"])
                assert task is not None
                assert task.is_correction is True
                assert "decoded_token" in task.description


@pytest.mark.asyncio
async def test_pr_conversation_issue_comment_triggers_correction(setup_gh_mission_and_pr):
    ctx = setup_gh_mission_and_pr
    transport = ASGITransport(app=app)

    # General conversation comment on PR
    issue_comment_payload = {
        "action": "created",
        "issue": {
            "number": 77,
            "title": "Implement OAuth Feature",
            "pull_request": {"url": "https://api.github.com/repos/citestdev/ci-test-repo/pulls/77"}
        },
        "comment": {
            "id": 998877,
            "body": "The password validation regex should enforce at least 8 characters and a special symbol."
        },
        "repository": {
            "full_name": "citestdev/ci-test-repo"
        },
        "sender": {
            "login": "tech-lead"
        }
    }

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("backend.services.github_app.GitHubAppService.verify_webhook_signature", return_value=True), \
             patch("backend.services.github_service.GitHubService.create_issue_comment", new_callable=AsyncMock) as mock_reply, \
             patch("backend.services.mission_runtime.MissionRuntime.run_mission_background", new_callable=AsyncMock):

            resp = await client.post(
                "/api/v1/github/webhooks/events",
                json=issue_comment_payload,
                headers={"X-GitHub-Event": "issue_comment"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "pr_comment_correction_dispatched"
            assert data["mission_id"] == ctx["mission"].id
            assert "task_id" in data

            async with AsyncSessionLocal() as db:
                task = await db.get(Task, data["task_id"])
                assert task.is_correction is True
                assert "password validation regex" in task.description


@pytest.mark.asyncio
async def test_ci_failure_reactivates_completed_mission(setup_gh_mission_and_pr):
    """Ensure CI failure reactivates a COMPLETED mission to ACTIVE in IMPLEMENTATION phase."""
    ctx = setup_gh_mission_and_pr
    transport = ASGITransport(app=app)

    # Put mission into COMPLETED state (simulating previous mission finish)
    async with AsyncSessionLocal() as db:
        m = await db.get(Mission, ctx["mission"].id)
        m.status = MissionStatus.COMPLETED
        m.phase = WorkflowPhase.DEPLOYMENT
        await db.commit()

    ci_payload = {
        "action": "completed",
        "check_run": {
            "name": "pytest-ci-suite",
            "head_sha": ctx["head_sha"],
            "status": "completed",
            "conclusion": "failure",
            "check_suite": {"head_branch": ctx["branch"]},
            "output": {
                "title": "Unit tests failed",
                "summary": "1 test failed: test_login_token_expired",
                "text": "AssertionError: expected 401 but got 200",
                "annotations": [
                    {
                        "path": "tests/test_auth.py",
                        "start_line": 42,
                        "annotation_level": "failure",
                        "message": "Token expiration assertion failed"
                    }
                ]
            }
        },
        "repository": {
            "full_name": "citestdev/ci-test-repo"
        }
    }

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("backend.services.github_app.GitHubAppService.verify_webhook_signature", return_value=True), \
             patch("backend.services.github_service.GitHubService.create_issue_comment", new_callable=AsyncMock), \
             patch("backend.services.mission_runtime.MissionRuntime.run_mission_background", new_callable=AsyncMock):

            resp = await client.post(
                "/api/v1/github/webhooks/events",
                json=ci_payload,
                headers={"X-GitHub-Event": "check_run"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ci_remediation_dispatched"

            # Verify mission was reactivated to ACTIVE and phase to IMPLEMENTATION
            async with AsyncSessionLocal() as db:
                reloaded_mission = await db.get(Mission, ctx["mission"].id)
                assert reloaded_mission.status == MissionStatus.ACTIVE
                assert reloaded_mission.phase == WorkflowPhase.IMPLEMENTATION

                remediation_task = await db.get(Task, data["task_id"])
                assert remediation_task.status == TaskStatus.PENDING
                assert remediation_task.phase == WorkflowPhase.IMPLEMENTATION
                assert remediation_task.is_correction is True


@pytest.mark.asyncio
async def test_persist_mission_deliverable_pushes_when_existing_pr(setup_gh_mission_and_pr, tmp_path):
    """Ensure persist_mission_deliverable executes git push even when PullRequest already exists."""
    from backend.services.sandbox_review import persist_mission_deliverable
    ctx = setup_gh_mission_and_pr

    # Create dummy git sandbox directory
    sb_dir = tmp_path / "mock_sandbox"
    sb_dir.mkdir()
    (sb_dir / ".git").mkdir()

    with patch("backend.services.sandbox_review.get_mission_diff", new_callable=AsyncMock) as mock_diff, \
         patch("subprocess.run") as mock_subprocess_run:

        mock_diff.return_value = {
            "sandbox_dir": str(sb_dir),
            "totals": {"files": 1, "added": 10, "removed": 0},
            "files": ["backend/api/auth.py"]
        }

        async with AsyncSessionLocal() as db:
            m = await db.get(Mission, ctx["mission"].id)
            res = await persist_mission_deliverable(db, m, push_and_open_pr=True)

            assert res["pr"] is not None
            assert res["pr"]["source_branch"] == ctx["branch"]
            # Verify git push was invoked
            assert mock_subprocess_run.called
            push_args = mock_subprocess_run.call_args[0][0]
            assert "git" in push_args[0]
            assert "push" in push_args[1]

