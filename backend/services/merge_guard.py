from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Dict, Any

from backend.models.mission import Mission, MissionStatus, Milestone, MilestoneStatus
from backend.models.project import Task, TaskStatus
from backend.models.finding import Finding, FindingStatus, FindingSeverity
from backend.models.github import PullRequest, PullRequestStatus

class MergeGuardService:
    @staticmethod
    async def evaluate_merge_readiness(db: AsyncSession, mission_id: str) -> Dict[str, Any]:
        result = {
            "ready": False,
            "reasons": [],
            "blocking_items": []
        }
        
        mission = await db.get(Mission, mission_id)
        if not mission:
            result["blocking_items"].append("Mission not found")
            return result
            
        # 1. Mission requirements/status
        # If it's already RELEASE_READY or COMPLETED, we are good on the mission front
        # But we need to ensure it's not BLOCKED or CANCELLED or FAILED.
        if mission.status in [MissionStatus.BLOCKED, MissionStatus.CANCELLED, MissionStatus.FAILED]:
            result["blocking_items"].append(f"Mission is in blocking status: {mission.status}")
            
        # 2. Required tasks completed
        stmt_tasks = select(Task).where(Task.mission_id == mission_id)
        tasks = (await db.execute(stmt_tasks)).scalars().all()
        active_tasks = [t for t in tasks if t.status in [TaskStatus.PENDING, TaskStatus.IN_PROGRESS]]
        if active_tasks:
            result["blocking_items"].append(f"Mission has {len(active_tasks)} active tasks")
            
        # 3. Required milestones completed
        stmt_milestones = select(Milestone).where(Milestone.mission_id == mission_id)
        milestones = (await db.execute(stmt_milestones)).scalars().all()
        active_milestones = [m for m in milestones if m.status not in [MilestoneStatus.COMPLETED, MilestoneStatus.SKIPPED]]
        if active_milestones:
            result["blocking_items"].append(f"Mission has {len(active_milestones)} incomplete milestones")
            
        # 4. No blocking findings (OPEN, ASSIGNED, FIXING, REVALIDATING, BLOCKED)
        stmt_findings = select(Finding).where(Finding.mission_id == mission_id)
        findings = (await db.execute(stmt_findings)).scalars().all()
        blocking_findings = [f for f in findings if f.status not in [FindingStatus.RESOLVED, FindingStatus.ACCEPTED_RISK]]
        if blocking_findings:
            result["blocking_items"].append(f"Mission has {len(blocking_findings)} blocking findings")
            
        # 5. Pull Request checks (Reviews, CI, conflicts)
        stmt_pr = select(PullRequest).where(PullRequest.mission_id == mission_id)
        pr = (await db.execute(stmt_pr)).scalars().first()
        
        if not pr:
            result["blocking_items"].append("No Pull Request found for mission")
        else:
            if pr.status == PullRequestStatus.CONFLICT:
                result["blocking_items"].append("Pull Request has merge conflicts")
                
            if pr.review_status == "changes_requested":
                result["blocking_items"].append("Pull Request has requested changes")
            elif pr.review_status != "approved":
                # Depending on policy, maybe we require explicit approval. Let's assume yes.
                result["blocking_items"].append(f"Pull Request is not approved (Current: {pr.review_status})")
                
            if pr.checks_status == "failure":
                result["blocking_items"].append("Pull Request CI checks failed")
            elif pr.checks_status != "success":
                result["blocking_items"].append(f"Pull Request CI checks not successful (Current: {pr.checks_status})")
                
            # Gate 16: Human Merge Approval & Stale Approval Protection
            if not pr.kobits_approved_sha:
                result["blocking_items"].append("Pending Kobits human merge approval")
            elif pr.kobits_approved_sha != pr.head_sha:
                result["blocking_items"].append(f"Approval is stale (Approved SHA: {pr.kobits_approved_sha}, Head SHA: {pr.head_sha})")
                
        if not result["blocking_items"]:
            result["ready"] = True
            result["reasons"].append("All readiness criteria passed")
        else:
            result["reasons"].append("Failing readiness criteria")
            
        return result

    @staticmethod
    async def approve_merge(db: AsyncSession, mission_id: str, user_id: str) -> bool:
        stmt_pr = select(PullRequest).where(PullRequest.mission_id == mission_id)
        pr = (await db.execute(stmt_pr)).scalars().first()
        if not pr:
            return False
            
        pr.kobits_approved_by = user_id
        pr.kobits_approved_sha = pr.head_sha
        await db.commit()
        return True
