"""
Discovery & Architecture Audit API Endpoints
Provides the Kyros-style "Week 1: Codebase Mapping & Architecture Discovery" endpoints.
"""

import os
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_db, get_current_user
from backend.models.user import User
from backend.models.project import Project
from backend.services.codebase_auditor import CodebaseAuditor, CodebaseAuditResult

router = APIRouter()


class AuditResponse(BaseModel):
    project_id: str
    project_name: str
    audit_id: str
    created_at: str
    health_score: int
    executive_summary: str
    tech_stack: list
    total_loc: int
    total_files: int
    test_coverage_ratio: float
    security_findings_count: int
    mermaid_diagram: str
    milestones: list
    markdown_report: str


@router.post("/{project_id}/audit", response_model=AuditResponse)
async def trigger_codebase_audit(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Triggers an automated codebase discovery and architecture audit for a project.
    Generates the executive Week 1 report, Mermaid DAG diagram, and 4-week milestone sprint plan.
    """
    project = (await db.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    # Determine target repo path (local directory or default project root)
    repo_path = project.repository_url or os.environ.get("KOBITS_WORKSPACE_DIR") or os.getcwd()
    if not os.path.exists(repo_path):
        repo_path = os.getcwd()

    auditor = CodebaseAuditor(repo_path=repo_path, project_name=project.name)
    result: CodebaseAuditResult = auditor.audit()

    test_ratio = round((result.metrics.test_loc / max(1, result.metrics.total_loc)) * 100, 1)

    return AuditResponse(
        project_id=project.id,
        project_name=result.project_name,
        audit_id=result.audit_id,
        created_at=result.created_at,
        health_score=result.health_score,
        executive_summary=result.executive_summary,
        tech_stack=result.tech_stack,
        total_loc=result.metrics.total_loc,
        total_files=result.metrics.total_files,
        test_coverage_ratio=test_ratio,
        security_findings_count=len(result.security_findings),
        mermaid_diagram=result.mermaid_diagram,
        milestones=result.milestones,
        markdown_report=result.markdown_report,
    )


@router.get("/{project_id}/report")
async def get_latest_audit_report(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Returns the latest executive architecture and health audit report in Markdown format."""
    project = (await db.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    repo_path = project.repository_url or os.environ.get("KOBITS_WORKSPACE_DIR") or os.getcwd()
    if not os.path.exists(repo_path):
        repo_path = os.getcwd()

    auditor = CodebaseAuditor(repo_path=repo_path, project_name=project.name)
    result = auditor.audit()

    return {
        "project_id": project.id,
        "project_name": project.name,
        "markdown": result.markdown_report,
        "health_score": result.health_score,
        "mermaid": result.mermaid_diagram,
    }
