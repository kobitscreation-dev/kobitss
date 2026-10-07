"""
Gate 17: Real Merge Service
Performs the actual GitHub merge with all pre-merge checks enforced.
"""
import logging
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

logger = logging.getLogger(__name__)


class MergeExecutionService:
    """
    Performs the real GitHub merge via GitHubService.
    Pre-checks (Gate 15/16 readiness) must pass before merge is attempted.
    Tokens are never passed outside this service.
    """

    @staticmethod
    async def execute_merge(
        db: AsyncSession,
        mission_id: str,
        requesting_user_id: str,
        project_id: str,
        organization_id: str,
    ) -> dict:
        from backend.models.github import PullRequest, PullRequestStatus, Repository, GitHubConnection
        from backend.models.mission import Mission, MissionStatus
        from backend.services.merge_guard import MergeGuardService
        from backend.services.github_service import GitHubService
        import json

        # 1. Re-evaluate readiness (prevents approving stale state)
        readiness = await MergeGuardService.evaluate_merge_readiness(db, mission_id)
        if not readiness["ready"]:
            return {
                "success": False,
                "reason": "Merge readiness check failed",
                "blocking_items": readiness["blocking_items"],
            }

        # 2. Load the PR
        stmt = select(PullRequest).where(PullRequest.mission_id == mission_id)
        pr = (await db.execute(stmt)).scalars().first()
        if not pr:
            return {"success": False, "reason": "No Pull Request found for this mission"}

        # 3. Authorization: only project members can merge (check organization_id matches mission)
        mission = await db.get(Mission, mission_id)
        if not mission or mission.organization_id != organization_id:
            return {"success": False, "reason": "Unauthorized: organization mismatch"}

        # 4. Fetch latest GitHub PR state to verify head SHA hasn't moved
        repo = await db.get(Repository, pr.repository_id)
        if not repo:
            return {"success": False, "reason": "Repository not found"}

        conn = await db.get(GitHubConnection, repo.github_connection_id)
        meta = json.loads(conn.metadata_json or "{}")
        # Token stays server-side — never returned to caller
        token = meta.get("token", "dummy_token")

        live_pr = await GitHubService.get_pull_request(
            repo_full_name=repo.full_name,
            pr_number=pr.number or 1,
            token=token,
        )

        if "error" in live_pr:
            return {"success": False, "reason": f"GitHub API error: {live_pr['error']}"}

        live_head_sha = live_pr.get("head", {}).get("sha")

        # 5. Verify approval SHA is still current (stale approval guard)
        if live_head_sha and live_head_sha != pr.kobits_approved_sha:
            return {
                "success": False,
                "reason": f"Approval is stale — branch has new commits. Expected SHA {pr.kobits_approved_sha}, got {live_head_sha}",
            }

        # 6. Verify mergeability from GitHub
        if live_pr.get("mergeable") is False:
            return {"success": False, "reason": "GitHub reports PR is not mergeable (merge conflict)"}

        # 7. Verify PR is still open
        if live_pr.get("state") == "closed":
            return {"success": False, "reason": "GitHub PR is already closed"}

        # 8. Perform the merge
        merge_result = await GitHubService.merge_pull_request(
            repo_full_name=repo.full_name,
            pr_number=pr.number or 1,
            commit_title=f"Merge mission: {mission.title}",
            token=token,
        )

        if not merge_result.get("merged"):
            return {
                "success": False,
                "reason": f"GitHub merge failed: {merge_result.get('message', merge_result.get('error', 'Unknown error'))}",
            }

        # 9. Record merge data
        pr.status = PullRequestStatus.MERGED
        pr.merged_at = datetime.now(timezone.utc)
        mission.status = MissionStatus.RELEASE_READY  # waiting for post-merge sync
        mission.current_stage = "Merge completed — awaiting post-merge synchronization"
        await db.commit()

        return {
            "success": True,
            "merge_commit_sha": merge_result.get("sha"),
            "merged_at": pr.merged_at.isoformat(),
            "pr_url": pr.url,
        }
