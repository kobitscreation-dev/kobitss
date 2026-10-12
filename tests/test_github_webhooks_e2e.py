import pytest
from httpx import AsyncClient, ASGITransport
import json
from unittest.mock import patch, AsyncMock
from backend.main import app
from backend.core.database import AsyncSessionLocal
from backend.models.organization import Organization, User, OrganizationMember
from backend.models.project import Project
from backend.models.github import GitHubConnection, GitHubConnectionStatus, Repository, RepositoryStatus
from backend.models.mission import Mission

@pytest.mark.asyncio
async def test_github_webhook_issue_and_pr_review_intake():
    import uuid
    async with AsyncSessionLocal() as db:
        # Create test org, user, project, repo
        u_suffix = uuid.uuid4().hex[:6]
        user = User(email=f"gh_test_{u_suffix}@kobits.space", full_name="GH Tester", hashed_password="pw")
        db.add(user)
        await db.commit()
        await db.refresh(user)

        org = Organization(name="GH Test Org")
        db.add(org)
        await db.commit()
        await db.refresh(org)

        member = OrganizationMember(organization_id=org.id, user_id=user.id, role="OWNER")
        db.add(member)

        project = Project(name="Payment Microservice", organization_id=org.id, created_by=user.id)
        db.add(project)

        await db.commit()
        await db.refresh(project)

        conn = GitHubConnection(
            organization_id=org.id,
            user_id=user.id,
            github_username="testdev",
            status=GitHubConnectionStatus.CONNECTED,
            metadata_json=json.dumps({"token": "ghp_mock_token_123"})
        )
        db.add(conn)
        await db.commit()
        await db.refresh(conn)

        repo = Repository(
            project_id=project.id,
            organization_id=org.id,
            github_connection_id=conn.id,
            github_repo_id="998877",
            owner="testdev",
            name="payments-repo",
            full_name="testdev/payments-repo",
            default_branch="main",
            selected_branch="main",
            clone_url="https://github.com/testdev/payments-repo.git",
            web_url="https://github.com/testdev/payments-repo",
            status=RepositoryStatus.READY
        )
        db.add(repo)
        await db.commit()
        await db.refresh(repo)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("backend.services.github_app.GitHubAppService.verify_webhook_signature", return_value=True), \
             patch("backend.services.github_service.GitHubService.create_issue_comment", new_callable=AsyncMock) as mock_comment, \
             patch("backend.services.github_service.GitHubService.reply_to_pr_review_comment", new_callable=AsyncMock) as mock_reply, \
             patch("backend.services.mission_runtime.MissionRuntime.run_mission_background", new_callable=AsyncMock) as mock_run:

            # 1. Test GitHub Issue @kobits mention
            issue_payload = {
                "action": "opened",
                "issue": {
                    "number": 42,
                    "title": "500 error in checkout",
                    "body": "@kobits please fix the checkout button crash in payments.py and add a test",
                    "labels": []
                },
                "repository": {
                    "full_name": "testdev/payments-repo"
                },
                "sender": {
                    "login": "lead-engineer"
                }
            }

            resp = await client.post(
                "/api/v1/github/webhooks/events",
                json=issue_payload,
                headers={"X-GitHub-Event": "issues"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "mission_dispatched"
            assert data["issue"] == 42
            assert "kobits/issue-42" in data["branch"]

            # 2. Test GitHub PR Review Line Comment @kobits mention
            pr_review_payload = {
                "action": "created",
                "pull_request": {
                    "number": 105,
                    "id": 991122,
                    "head": {"ref": "feat/payments-stripe-v2"},
                    "base": {"ref": "main"}
                },
                "comment": {
                    "id": 883344,
                    "path": "services/checkout.py",
                    "line": 45,
                    "diff_hunk": "@@ -40,6 +40,8 @@ def process_payment(amount):\n+    charge = stripe.Charge.create()",
                    "body": "@kobits handle stripe.error.CardError and return 402 status code"
                },
                "repository": {
                    "full_name": "testdev/payments-repo"
                },
                "sender": {
                    "login": "security-reviewer"
                }
            }

            pr_resp = await client.post(
                "/api/v1/github/webhooks/events",
                json=pr_review_payload,
                headers={"X-GitHub-Event": "pull_request_review_comment"}
            )
            assert pr_resp.status_code == 200
            pr_data = pr_resp.json()
            assert pr_data["status"] == "pr_review_correction_dispatched"
            assert pr_data["branch"] == "feat/payments-stripe-v2"
            assert pr_data["pr_number"] == 105
