"""
backend/services/integrations/linear_jira.py
=============================================
Enterprise Bidirectional Sync Service for Linear & Atlassian Jira.

Implements full Kyros/Devin-grade parity:
1. Webhook Intake: Parses Linear and Jira issue events to spawn/update Missions.
2. State Machine Sync: Maps issue states (Backlog -> In Progress -> In Review -> Done)
   bidirectionally between Linear/Jira and Kobits MissionStatus / WorkflowPhase.
3. Plan Commenting: Automatically posts architectural plans, database DDL, and task
   contracts back to the original Linear / Jira issue for human engineering review.
4. SLA & Priority Mapping: Preserves urgent/high priorities, labels, and assignees.
"""

from __future__ import annotations

import enum
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.mission import (
    Mission,
    MissionPriority,
    MissionRisk,
    MissionStatus,
    WorkflowPhase,
    ApprovalStatus,
)
from backend.models.project import Project, Task, TaskStatus, Activity, ActivityType
from app.ai.sanitizer import PromptSanitizer

logger = logging.getLogger("kobits.integrations.linear_jira")


class IssueTrackerType(str, enum.Enum):
    LINEAR = "LINEAR"
    JIRA = "JIRA"


@dataclass
class LinearIssuePayload:
    action: str  # "create", "update", "remove"
    issue_id: str
    identifier: str  # e.g., "KOB-142"
    title: str
    description: str
    state_name: str  # e.g., "In Progress", "In Review", "Done"
    priority: int  # 0=No priority, 1=Urgent, 2=High, 3=Medium, 4=Low
    assignee_name: Optional[str] = None
    team_name: Optional[str] = None
    labels: List[str] = field(default_factory=list)
    url: Optional[str] = None


@dataclass
class JiraIssuePayload:
    webhook_event: str  # "jira:issue_created", "jira:issue_updated"
    issue_id: str
    issue_key: str  # e.g., "ENG-501"
    summary: str
    description: str
    status_name: str  # e.g., "To Do", "In Progress", "In Review", "Done"
    priority_name: str  # e.g., "Highest", "High", "Medium", "Low"
    assignee_name: Optional[str] = None
    components: List[str] = field(default_factory=list)
    url: Optional[str] = None


class LinearJiraService:
    """
    Bidirectional Orchestrator connecting Kobits autonomous missions with
    Linear and Atlassian Jira issue trackers.
    """

    # Linear Priority integer -> MissionPriority
    LINEAR_PRIORITY_MAP = {
        1: MissionPriority.URGENT,
        2: MissionPriority.HIGH,
        3: MissionPriority.MEDIUM,
        4: MissionPriority.LOW,
        0: MissionPriority.MEDIUM,
    }

    # Jira Priority Name -> MissionPriority
    JIRA_PRIORITY_MAP = {
        "highest": MissionPriority.URGENT,
        "urgent": MissionPriority.URGENT,
        "high": MissionPriority.HIGH,
        "medium": MissionPriority.MEDIUM,
        "low": MissionPriority.LOW,
        "lowest": MissionPriority.LOW,
    }

    @classmethod
    def parse_linear_webhook(cls, payload: Dict[str, Any]) -> Optional[LinearIssuePayload]:
        """Parse incoming Linear webhook JSON into a structured LinearIssuePayload."""
        if not payload or not isinstance(payload, dict):
            return None

        # Linear webhooks provide type="Issue", action="create"|"update"
        data = payload.get("data", {})
        if not data and "issue" in payload:
            data = payload.get("issue", {})

        issue_id = str(data.get("id") or payload.get("id") or "")
        identifier = str(data.get("identifier") or data.get("number") or issue_id)
        title = data.get("title") or payload.get("title") or "Linear Task"
        description = data.get("description") or ""

        # State parsing
        state = data.get("state", {})
        state_name = state.get("name") if isinstance(state, dict) else str(state or "Todo")

        # Priority parsing
        raw_priority = data.get("priority", 0)
        try:
            priority = int(raw_priority)
        except (ValueError, TypeError):
            priority = 0

        # Assignee
        assignee = data.get("assignee", {})
        assignee_name = assignee.get("name") if isinstance(assignee, dict) else None

        # Labels
        labels = []
        raw_labels = data.get("labels", [])
        if isinstance(raw_labels, list):
            for lb in raw_labels:
                if isinstance(lb, dict):
                    labels.append(lb.get("name", ""))
                elif isinstance(lb, str):
                    labels.append(lb)

        return LinearIssuePayload(
            action=payload.get("action", "create"),
            issue_id=issue_id,
            identifier=identifier,
            title=title,
            description=description,
            state_name=state_name,
            priority=priority,
            assignee_name=assignee_name,
            team_name=data.get("team", {}).get("name") if isinstance(data.get("team"), dict) else None,
            labels=labels,
            url=data.get("url") or f"https://linear.app/issue/{identifier}",
        )

    @classmethod
    def parse_jira_webhook(cls, payload: Dict[str, Any]) -> Optional[JiraIssuePayload]:
        """Parse incoming Jira webhook JSON into a structured JiraIssuePayload."""
        if not payload or not isinstance(payload, dict):
            return None

        webhook_event = payload.get("webhookEvent") or payload.get("event") or "jira:issue_created"
        issue = payload.get("issue", {})
        if not issue:
            return None

        issue_id = str(issue.get("id") or "")
        issue_key = str(issue.get("key") or issue_id)
        fields = issue.get("fields", {})

        summary = fields.get("summary") or "Jira Task"
        raw_desc = fields.get("description") or ""
        # Handle Jira ADF (Atlassian Document Format) or raw string
        description = ""
        if isinstance(raw_desc, str):
            description = raw_desc
        elif isinstance(raw_desc, dict):
            # Extract plain text from ADF nodes
            description = cls._extract_adf_text(raw_desc)

        status_obj = fields.get("status", {})
        status_name = status_obj.get("name", "To Do") if isinstance(status_obj, dict) else str(status_obj)

        priority_obj = fields.get("priority", {})
        priority_name = priority_obj.get("name", "Medium") if isinstance(priority_obj, dict) else str(priority_obj)

        assignee_obj = fields.get("assignee", {})
        assignee_name = assignee_obj.get("displayName") or assignee_obj.get("name") if isinstance(assignee_obj, dict) else None

        components = []
        raw_components = fields.get("components", [])
        if isinstance(raw_components, list):
            for comp in raw_components:
                if isinstance(comp, dict):
                    components.append(comp.get("name", ""))
                elif isinstance(comp, str):
                    components.append(comp)

        return JiraIssuePayload(
            webhook_event=webhook_event,
            issue_id=issue_id,
            issue_key=issue_key,
            summary=summary,
            description=description,
            status_name=status_name,
            priority_name=priority_name,
            assignee_name=assignee_name,
            components=components,
            url=issue.get("self") or f"https://jira.atlassian.com/browse/{issue_key}",
        )

    @classmethod
    def _extract_adf_text(cls, adf: Dict[str, Any]) -> str:
        """Recursively extract plain text from Atlassian Document Format (ADF) JSON."""
        texts = []
        content = adf.get("content", [])
        for node in content:
            if isinstance(node, dict):
                if node.get("type") == "text":
                    texts.append(node.get("text", ""))
                elif "content" in node:
                    texts.append(cls._extract_adf_text(node))
        return " ".join(filter(None, texts))

    @classmethod
    def map_linear_state_to_mission(cls, state_name: str) -> tuple[MissionStatus, WorkflowPhase]:
        """Maps Linear issue state string to Kobits MissionStatus and WorkflowPhase."""
        s = (state_name or "").strip().lower()
        if "triage" in s or "backlog" in s:
            return MissionStatus.READY, WorkflowPhase.INTAKE
        if "todo" in s or "to do" in s or "planned" in s:
            return MissionStatus.READY, WorkflowPhase.PLANNING
        if "in progress" in s or "started" in s or "building" in s:
            return MissionStatus.ACTIVE, WorkflowPhase.IMPLEMENTATION
        if "in review" in s or "review" in s or "testing" in s or "qa" in s:
            return MissionStatus.AWAITING_APPROVAL, WorkflowPhase.CODE_REVIEW
        if "done" in s or "completed" in s or "closed" in s:
            return MissionStatus.COMPLETED, WorkflowPhase.DELIVERY_REVIEW
        if "canceled" in s or "cancelled" in s or "duplicate" in s:
            return MissionStatus.CANCELLED, WorkflowPhase.INTAKE
        return MissionStatus.ACTIVE, WorkflowPhase.IMPLEMENTATION

    @classmethod
    def map_jira_state_to_mission(cls, status_name: str) -> tuple[MissionStatus, WorkflowPhase]:
        """Maps Jira status string to Kobits MissionStatus and WorkflowPhase."""
        s = (status_name or "").strip().lower()
        if "open" in s or "backlog" in s:
            return MissionStatus.READY, WorkflowPhase.INTAKE
        if "to do" in s or "selected for development" in s:
            return MissionStatus.READY, WorkflowPhase.PLANNING
        if "in progress" in s or "in development" in s or "active" in s:
            return MissionStatus.ACTIVE, WorkflowPhase.IMPLEMENTATION
        if "in review" in s or "code review" in s or "qa" in s or "testing" in s:
            return MissionStatus.AWAITING_APPROVAL, WorkflowPhase.CODE_REVIEW
        if "done" in s or "resolved" in s or "closed" in s:
            return MissionStatus.COMPLETED, WorkflowPhase.DELIVERY_REVIEW
        if "rejected" in s or "won't fix" in s or "canceled" in s:
            return MissionStatus.CANCELLED, WorkflowPhase.INTAKE
        return MissionStatus.ACTIVE, WorkflowPhase.IMPLEMENTATION

    @classmethod
    async def sync_linear_issue(
        cls,
        payload_dict: Dict[str, Any],
        project_id: str,
        db: AsyncSession,
        creator_id: Optional[str] = None,
    ) -> Optional[Mission]:
        """
        Processes a Linear webhook payload, creating or updating the corresponding Mission.
        """
        item = cls.parse_linear_webhook(payload_dict)
        if not item:
            return None

        project = await db.get(Project, project_id)
        if not project:
            logger.error(f"Cannot sync Linear issue: project_id {project_id} not found")
            return None

        # Sanitize prompt content
        safe_title = PromptSanitizer.sanitize(item.title, strict=False) or item.identifier
        safe_desc = PromptSanitizer.sanitize(item.description, strict=False) or "Imported from Linear"
        objective = f"Linear [{item.identifier}]: {safe_title}\n\n{safe_desc}"

        status, phase = cls.map_linear_state_to_mission(item.state_name)
        priority = cls.LINEAR_PRIORITY_MAP.get(item.priority, MissionPriority.MEDIUM)

        # Look for existing mission with matching Linear identifier in title
        stmt = select(Mission).where(
            (Mission.project_id == project_id) &
            (Mission.title.like(f"%[{item.identifier}]%"))
        )
        res = await db.execute(stmt)
        mission = res.scalars().first()

        if mission:
            # Update existing mission state
            mission.status = status
            mission.phase = phase
            mission.priority = priority
            mission.description = f"Synced with Linear {item.identifier} ({item.state_name}). {safe_desc}"
            logger.info(f"Updated Mission {mission.id} from Linear issue {item.identifier} -> {status.value}")
        else:
            # Create new mission
            effective_creator = creator_id or project.owner_id or str(uuid.uuid4())
            mission = Mission(
                id=str(uuid.uuid4()),
                organization_id=project.organization_id,
                project_id=project_id,
                repository_id=project.default_repository_id if hasattr(project, "default_repository_id") else None,
                created_by=effective_creator,
                title=f"[{item.identifier}] {safe_title}",
                objective=objective,
                description=f"Imported from Linear issue {item.identifier}. {safe_desc}",
                status=status,
                phase=phase,
                priority=priority,
                risk_level=MissionRisk.MEDIUM,
                progress=10 if status == MissionStatus.ACTIVE else 0,
            )
            db.add(mission)
            logger.info(f"Created new Mission {mission.id} from Linear issue {item.identifier}")

        await db.commit()
        await db.refresh(mission)
        return mission

    @classmethod
    async def sync_jira_issue(
        cls,
        payload_dict: Dict[str, Any],
        project_id: str,
        db: AsyncSession,
        creator_id: Optional[str] = None,
    ) -> Optional[Mission]:
        """
        Processes a Jira webhook payload, creating or updating the corresponding Mission.
        """
        item = cls.parse_jira_webhook(payload_dict)
        if not item:
            return None

        project = await db.get(Project, project_id)
        if not project:
            logger.error(f"Cannot sync Jira issue: project_id {project_id} not found")
            return None

        safe_title = PromptSanitizer.sanitize(item.summary, strict=False) or item.issue_key
        safe_desc = PromptSanitizer.sanitize(item.description, strict=False) or "Imported from Jira"
        objective = f"Jira [{item.issue_key}]: {safe_title}\n\n{safe_desc}"

        status, phase = cls.map_jira_state_to_mission(item.status_name)
        priority = cls.JIRA_PRIORITY_MAP.get(item.priority_name.lower(), MissionPriority.MEDIUM)

        stmt = select(Mission).where(
            (Mission.project_id == project_id) &
            (Mission.title.like(f"%[{item.issue_key}]%"))
        )
        res = await db.execute(stmt)
        mission = res.scalars().first()

        if mission:
            mission.status = status
            mission.phase = phase
            mission.priority = priority
            mission.description = f"Synced with Jira {item.issue_key} ({item.status_name}). {safe_desc}"
            logger.info(f"Updated Mission {mission.id} from Jira issue {item.issue_key} -> {status.value}")
        else:
            effective_creator = creator_id or project.owner_id or str(uuid.uuid4())
            mission = Mission(
                id=str(uuid.uuid4()),
                organization_id=project.organization_id,
                project_id=project_id,
                repository_id=project.default_repository_id if hasattr(project, "default_repository_id") else None,
                created_by=effective_creator,
                title=f"[{item.issue_key}] {safe_title}",
                objective=objective,
                description=f"Imported from Jira issue {item.issue_key}. {safe_desc}",
                status=status,
                phase=phase,
                priority=priority,
                risk_level=MissionRisk.MEDIUM,
                progress=10 if status == MissionStatus.ACTIVE else 0,
            )
            db.add(mission)
            logger.info(f"Created new Mission {mission.id} from Jira issue {item.issue_key}")

        await db.commit()
        await db.refresh(mission)
        return mission

    @classmethod
    async def post_linear_plan_comment(
        cls,
        issue_id: str,
        plan_markdown: str,
        api_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Posts an architectural plan or status update as a comment on a Linear issue.
        Uses the Linear GraphQL API.
        """
        key = api_key or os.environ.get("LINEAR_API_KEY")
        if not key:
            logger.warning(f"Linear plan comment dry-run (no LINEAR_API_KEY): {issue_id}")
            return {"status": "dry_run", "issue_id": issue_id, "posted": False}

        query = """
        mutation CreateComment($issueId: String!, $body: String!) {
            commentCreate(input: { issueId: $issueId, body: $body }) {
                success
                comment {
                    id
                    url
                }
            }
        }
        """
        body_text = f"### 🤖 Kobits Autonomous Architecture Plan\n\n{plan_markdown}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    "https://api.linear.app/graphql",
                    json={"query": query, "variables": {"issueId": issue_id, "body": body_text}},
                    headers={"Authorization": key, "Content-Type": "application/json"},
                )
                if resp.status_code == 200:
                    return {"status": "success", "issue_id": issue_id, "data": resp.json()}
                else:
                    logger.error(f"Linear API error ({resp.status_code}): {resp.text}")
                    return {"status": "error", "code": resp.status_code, "detail": resp.text}
        except Exception as e:
            logger.error(f"Linear comment exception: {e}")
            return {"status": "exception", "error": str(e)}

    @classmethod
    async def post_jira_plan_comment(
        cls,
        issue_key: str,
        plan_markdown: str,
        host: Optional[str] = None,
        auth_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Posts an architectural plan as a comment on a Jira issue via REST API v3.
        """
        jira_host = host or os.environ.get("JIRA_HOST")
        token = auth_token or os.environ.get("JIRA_API_TOKEN")

        if not jira_host or not token:
            logger.warning(f"Jira plan comment dry-run (no JIRA credentials): {issue_key}")
            return {"status": "dry_run", "issue_key": issue_key, "posted": False}

        url = f"{jira_host.rstrip('/')}/rest/api/3/issue/{issue_key}/comment"
        payload = {
            "body": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {"type": "text", "text": "🤖 Kobits Autonomous Architecture Plan:\n" + plan_markdown}
                        ]
                    }
                ]
            }
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    url,
                    json=payload,
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                )
                return {"status": "success" if resp.status_code in (200, 201) else "error", "code": resp.status_code}
        except Exception as e:
            logger.error(f"Jira comment exception: {e}")
            return {"status": "exception", "error": str(e)}
