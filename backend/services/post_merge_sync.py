"""
Gate 18: Post-Merge Synchronization
After a successful GitHub merge, syncs:
- Repository current commit
- Repository indexing
- Codebase understanding refresh
- Architecture refresh
- Project memory (only durable decisions)
- Mission completion
"""
import logging
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

logger = logging.getLogger(__name__)


class PostMergeSyncService:
    @staticmethod
    async def run(db: AsyncSession, mission_id: str, merge_commit_sha: str | None = None) -> dict:
        from backend.models.mission import Mission, MissionStatus
        from backend.models.github import PullRequest, PullRequestStatus, Repository
        from backend.models.project import Activity, ActivityType

        mission = await db.get(Mission, mission_id)
        if not mission:
            return {"success": False, "reason": "Mission not found"}

        stmt = select(PullRequest).where(PullRequest.mission_id == mission_id)
        pr = (await db.execute(stmt)).scalars().first()

        if pr and pr.status != PullRequestStatus.MERGED:
            return {"success": False, "reason": "PR is not yet merged on GitHub side"}

        steps = []
        errors = []

        # Step 1: Update repository current_commit_sha
        try:
            if mission.repository_id and merge_commit_sha:
                repo = await db.get(Repository, mission.repository_id)
                if repo:
                    repo.current_commit_sha = merge_commit_sha
                    repo.last_indexed_at = datetime.now(timezone.utc)
                    await db.commit()
                    steps.append("repository_sha_updated")
        except Exception as e:
            errors.append(f"repo_sha_update_failed: {e}")

        # Step 2: Trigger repository re-indexing (best-effort, non-blocking)
        try:
            from backend.services.repository_indexer import RepositoryIndexer
            if mission.repository_id:
                await RepositoryIndexer.trigger_reindex(mission.repository_id)
                steps.append("reindex_triggered")
        except Exception as e:
            errors.append(f"reindex_failed: {e}")

        # Step 3: Persist only durable decisions to project memory
        try:
            from backend.services.memory_service import MemoryService
            if mission.repository_id:
                await MemoryService.record_merge_event(
                    project_id=mission.project_id,
                    mission_id=mission_id,
                    mission_title=mission.title,
                    merge_commit_sha=merge_commit_sha,
                )
                steps.append("memory_updated")
        except Exception as e:
            errors.append(f"memory_update_failed: {e}")

        # Step 4: Complete the mission — only when sync succeeded enough
        # We allow partial indexing failure but require sha update
        if errors and "repo_sha_update_failed" in str(errors):
            mission.status = MissionStatus.BLOCKED
            mission.current_stage = f"Post-merge sync failed: {errors}"
            await db.commit()
            return {"success": False, "reason": "Repository SHA update failed", "errors": errors}

        mission.status = MissionStatus.COMPLETED
        mission.current_stage = "Mission completed and merged"
        mission.completed_at = datetime.now(timezone.utc)
        mission.progress = 100
        await db.commit()

        # Activity log
        act = Activity(
            organization_id=mission.organization_id,
            project_id=mission.project_id,
            user_id="system",
            type=ActivityType.MISSION_APPROVED,
            title=f"Mission Merged: {mission.title}",
            description=f"Merge commit: {merge_commit_sha}. Steps: {steps}",
        )
        db.add(act)
        await db.commit()

        return {
            "success": True,
            "steps_completed": steps,
            "errors_non_fatal": errors,
            "mission_status": mission.status.value,
        }
