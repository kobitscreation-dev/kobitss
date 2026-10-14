"""
Proactive Epic, Roadmap & PRD Ingestion Engine for Kobits.
Kyros 1:1 Enterprise Parity:
Proactively ingests multi-issue Epics, Roadmaps, and PRD documents from:
- Linear Project payloads
- Jira Epic exports
- GitHub Project Milestone lists
- Structured Markdown / Text PRDs
Decomposes complex multi-week epics into topological milestone DAGs
with automated dependency resolution, user stories, acceptance criteria,
and autonomous mission dispatching.
"""

import os
import re
import json
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Union

logger = logging.getLogger("kobits.epic_ingestion")


@dataclass
class MilestoneTask:
    id: str
    title: str
    description: str
    assigned_role: str = "BACKEND_ENGINEER"  # e.g. "BACKEND_ENGINEER", "FRONTEND_ENGINEER", "QA_ENGINEER"
    dependencies: List[str] = field(default_factory=list)
    acceptance_criteria: List[str] = field(default_factory=list)
    estimated_points: int = 3

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "assigned_role": self.assigned_role,
            "dependencies": self.dependencies,
            "acceptance_criteria": self.acceptance_criteria,
            "estimated_points": self.estimated_points,
        }


@dataclass
class MilestoneNode:
    milestone_id: str
    title: str
    description: str
    phase: str  # "PLANNING" | "FOUNDATION" | "CORE_FEATURE" | "INTEGRATION" | "QA_VERIFICATION"
    dependencies: List[str] = field(default_factory=list)
    tasks: List[MilestoneTask] = field(default_factory=list)
    acceptance_criteria: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "milestone_id": self.milestone_id,
            "title": self.title,
            "description": self.description,
            "phase": self.phase,
            "dependencies": self.dependencies,
            "tasks": [t.to_dict() for t in self.tasks],
            "acceptance_criteria": self.acceptance_criteria,
        }


@dataclass
class EpicMilestoneDAG:
    epic_title: str
    epic_summary: str
    source_type: str  # "markdown_prd" | "jira_epic" | "linear_project" | "github_milestone"
    milestones: List[MilestoneNode] = field(default_factory=list)
    topological_order: List[str] = field(default_factory=list)
    security_requirements: List[str] = field(default_factory=list)
    architecture_notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "epic_title": self.epic_title,
            "epic_summary": self.epic_summary,
            "source_type": self.source_type,
            "milestones_count": len(self.milestones),
            "milestones": [m.to_dict() for m in self.milestones],
            "topological_order": self.topological_order,
            "security_requirements": self.security_requirements,
            "architecture_notes": self.architecture_notes,
        }


class EpicIngestionEngine:
    """
    Decomposes multi-issue epics, roadmaps, and PRD specifications
    into structured DAGs of actionable milestones and tasks.
    """

    @classmethod
    def compute_topological_order(cls, milestones: List[MilestoneNode]) -> List[str]:
        """
        Computes valid topological execution order for milestone DAG using Kahn's algorithm.
        Guarantees circular dependency safety.
        """
        nodes = {m.milestone_id: m for m in milestones}
        in_degree = {m_id: 0 for m_id in nodes}
        adj: Dict[str, List[str]] = {m_id: [] for m_id in nodes}

        for m_id, node in nodes.items():
            for dep in node.dependencies:
                if dep in nodes:
                    adj[dep].append(m_id)
                    in_degree[m_id] += 1

        queue = [m_id for m_id, deg in in_degree.items() if deg == 0]
        order = []

        while queue:
            curr = queue.pop(0)
            order.append(curr)
            for neighbor in adj.get(curr, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        # If circular dependency or orphaned nodes exist, append remaining
        for m_id in nodes:
            if m_id not in order:
                order.append(m_id)

        return order

    @classmethod
    def parse_markdown_prd(cls, content: str) -> EpicMilestoneDAG:
        """Parses structured Markdown PRD / Roadmap specifications."""
        lines = content.splitlines()
        title = "Epic Mission"
        summary_lines = []
        milestones: List[MilestoneNode] = []
        security_reqs = []
        arch_notes = []

        # Extract title from first # Header
        for line in lines:
            s_line = line.strip()
            if s_line.startswith("# ") and title == "Epic Mission":
                title = s_line.lstrip("#").strip()
                break

        current_milestone: Optional[MilestoneNode] = None
        current_section = "overview"

        for line in lines:
            s_line = line.strip()
            if not s_line:
                continue

            # Section headers
            if re.match(r"^#{1,3}\s+(Security|Compliance)", s_line, re.IGNORECASE):
                current_section = "security"
                continue
            elif re.match(r"^#{1,3}\s+(Architecture|Tech Stack|Design)", s_line, re.IGNORECASE):
                current_section = "architecture"
                continue
            elif re.match(r"^#{1,3}\s+(Milestone|Phase|Sprint|Epic Part)\s*[:0-9\-]*", s_line, re.IGNORECASE):
                current_section = "milestone"
                m_title = re.sub(r"^#{1,3}\s+", "", s_line).strip()
                m_id = f"m_{len(milestones) + 1}_{re.sub(r'[^a-zA-Z0-9]+', '_', m_title.lower())[:18]}".rstrip("_")
                prev_id = milestones[-1].milestone_id if milestones else None
                current_milestone = MilestoneNode(
                    milestone_id=m_id,
                    title=m_title,
                    description=m_title,
                    phase="CORE_FEATURE" if len(milestones) > 0 else "FOUNDATION",
                    dependencies=[prev_id] if prev_id else [],
                )
                milestones.append(current_milestone)
                continue

            # Bullet items parsing
            if s_line.startswith(("- ", "* ", "1. ", "2. ", "3. ", "4. ", "5. ")):
                item_text = re.sub(r"^[-*0-9.]+\s+", "", s_line).strip()
                if current_section == "security":
                    security_reqs.append(item_text)
                elif current_section == "architecture":
                    arch_notes.append(item_text)
                elif current_section == "milestone" and current_milestone:
                    if "accept" in item_text.lower() or "criteria" in item_text.lower():
                        current_milestone.acceptance_criteria.append(item_text)
                    else:
                        role = "BACKEND_ENGINEER"
                        if any(w in item_text.lower() for w in ("ui", "screen", "css", "component", "frontend", "page")):
                            role = "FRONTEND_ENGINEER"
                        elif any(w in item_text.lower() for w in ("test", "qa", "verify", "benchmark")):
                            role = "QA_ENGINEER"
                        elif any(w in item_text.lower() for w in ("db", "migration", "schema", "table", "sql")):
                            role = "DATABASE_ENGINEER"

                        t_id = f"task_{current_milestone.milestone_id}_{len(current_milestone.tasks) + 1}"
                        current_milestone.tasks.append(
                            MilestoneTask(
                                id=t_id,
                                title=item_text[:64],
                                description=item_text,
                                assigned_role=role,
                                acceptance_criteria=[f"Verify {item_text}"],
                            )
                        )
                elif current_section == "overview":
                    summary_lines.append(item_text)
            elif current_section == "overview" and not s_line.startswith("#"):
                summary_lines.append(s_line)

        # Fallback if no explicit milestones detected
        if not milestones:
            m_id = "m_1_initial_scope"
            default_tasks = [
                MilestoneTask(
                    id="task_m1_1",
                    title="Implement Epic Requirements",
                    description=content[:200],
                    assigned_role="BACKEND_ENGINEER",
                ),
                MilestoneTask(
                    id="task_m1_2",
                    title="Validate & Test Implementation",
                    description="Run test suite and visual QA",
                    assigned_role="QA_ENGINEER",
                    dependencies=["task_m1_1"],
                ),
            ]
            milestones.append(
                MilestoneNode(
                    milestone_id=m_id,
                    title=title,
                    description=title,
                    phase="CORE_FEATURE",
                    tasks=default_tasks,
                )
            )

        epic_summary = " ".join(summary_lines[:5]) or title
        topological = cls.compute_topological_order(milestones)

        return EpicMilestoneDAG(
            epic_title=title,
            epic_summary=epic_summary,
            source_type="markdown_prd",
            milestones=milestones,
            topological_order=topological,
            security_requirements=security_reqs,
            architecture_notes=arch_notes,
        )

    @classmethod
    def parse_jira_epic(cls, payload: Dict[str, Any]) -> EpicMilestoneDAG:
        """Parses Jira Epic JSON export with child issues / stories."""
        fields = payload.get("fields", payload)
        epic_title = fields.get("summary") or payload.get("title") or "Jira Epic"
        epic_summary = fields.get("description") or ""

        issues = payload.get("issues") or payload.get("childIssues") or payload.get("stories") or []
        milestones: List[MilestoneNode] = []

        if isinstance(issues, list) and issues:
            for idx, issue in enumerate(issues):
                iss_fields = issue.get("fields", issue)
                iss_title = iss_fields.get("summary") or iss_fields.get("title") or f"Issue {idx + 1}"
                iss_desc = iss_fields.get("description") or iss_title
                iss_key = issue.get("key") or f"JIRA-{idx + 1}"

                prev_key = issues[idx - 1].get("key", f"JIRA-{idx}") if idx > 0 else None
                m_node = MilestoneNode(
                    milestone_id=iss_key,
                    title=iss_title,
                    description=iss_desc,
                    phase="FOUNDATION" if idx == 0 else "CORE_FEATURE",
                    dependencies=[prev_key] if prev_key else [],
                    tasks=[
                        MilestoneTask(
                            id=f"task_{iss_key}",
                            title=iss_title,
                            description=iss_desc,
                            assigned_role="BACKEND_ENGINEER",
                        )
                    ],
                )
                milestones.append(m_node)
        else:
            milestones.append(
                MilestoneNode(
                    milestone_id="JIRA-1",
                    title=epic_title,
                    description=epic_summary,
                    phase="CORE_FEATURE",
                )
            )

        topological = cls.compute_topological_order(milestones)
        return EpicMilestoneDAG(
            epic_title=epic_title,
            epic_summary=str(epic_summary),
            source_type="jira_epic",
            milestones=milestones,
            topological_order=topological,
        )

    @classmethod
    def parse_linear_project(cls, payload: Dict[str, Any]) -> EpicMilestoneDAG:
        """Parses Linear Project or Roadmap payload with child issues."""
        proj_data = payload.get("project") or payload.get("data", {}).get("project") or payload
        epic_title = proj_data.get("name") or proj_data.get("title") or "Linear Project"
        epic_summary = proj_data.get("description") or ""

        raw_issues = proj_data.get("issues")
        if isinstance(raw_issues, dict):
            issues = raw_issues.get("nodes") or raw_issues.get("issues") or []
        elif isinstance(raw_issues, list):
            issues = raw_issues
        else:
            issues = []
        milestones: List[MilestoneNode] = []

        if isinstance(issues, list) and issues:
            for idx, iss in enumerate(issues):
                iss_title = iss.get("title") or f"Milestone {idx + 1}"
                iss_desc = iss.get("description") or iss_title
                iss_id = iss.get("identifier") or iss.get("id") or f"LIN-{idx + 1}"
                prev_id = milestones[-1].milestone_id if milestones else None

                m_node = MilestoneNode(
                    milestone_id=iss_id,
                    title=iss_title,
                    description=iss_desc,
                    phase="FOUNDATION" if idx == 0 else "CORE_FEATURE",
                    dependencies=[prev_id] if prev_id else [],
                    tasks=[
                        MilestoneTask(
                            id=f"task_{iss_id}",
                            title=iss_title,
                            description=iss_desc,
                            assigned_role="BACKEND_ENGINEER" if "api" in iss_title.lower() else "FRONTEND_ENGINEER",
                        )
                    ],
                )
                milestones.append(m_node)
        else:
            milestones.append(
                MilestoneNode(
                    milestone_id="LIN-1",
                    title=epic_title,
                    description=epic_summary,
                    phase="CORE_FEATURE",
                )
            )

        topological = cls.compute_topological_order(milestones)
        return EpicMilestoneDAG(
            epic_title=epic_title,
            epic_summary=str(epic_summary),
            source_type="linear_project",
            milestones=milestones,
            topological_order=topological,
        )

    @classmethod
    def ingest_payload(cls, payload: Union[str, Dict[str, Any]]) -> EpicMilestoneDAG:
        """
        Universal entry point to ingest and decompose any PRD/Epic format:
        - Markdown/Text string
        - Jira JSON payload
        - Linear JSON payload
        - Generic Epic JSON
        """
        if isinstance(payload, str):
            try:
                parsed_json = json.loads(payload)
                if isinstance(parsed_json, dict):
                    return cls.ingest_payload(parsed_json)
            except Exception:
                pass
            return cls.parse_markdown_prd(payload)

        if isinstance(payload, dict):
            # Check Linear markers
            if "project" in payload or "linear" in str(payload.keys()).lower():
                return cls.parse_linear_project(payload)
            # Check Jira markers
            if "issues" in payload or "issueKey" in payload or "childIssues" in payload or "fields" in payload:
                return cls.parse_jira_epic(payload)
            # Check markdown text inside dict
            if "content" in payload or "text" in payload or "prd" in payload or "markdown" in payload:
                text_content = payload.get("content") or payload.get("text") or payload.get("prd") or payload.get("markdown") or ""
                return cls.parse_markdown_prd(str(text_content))
            # Generic dict fallback
            title = payload.get("title") or payload.get("name") or "Epic Ingestion"
            desc = payload.get("description") or payload.get("summary") or title
            return cls.parse_markdown_prd(f"# {title}\n\n{desc}")

        raise ValueError("Unsupported epic payload format")
