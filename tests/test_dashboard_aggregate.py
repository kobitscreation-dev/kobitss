"""Deterministic test for GET /api/v1/org/dashboard.

Seeds a throwaway SQLite DB with known rows and checks that every dashboard
number is exactly what the rows imply, that org boundaries hold, and that
values with no source data are null rather than invented.
"""
import asyncio
import os
from datetime import datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from backend.main import app
from backend.core.database import get_db
from backend.api.deps import get_current_active_user
from backend.models.base import Base
from backend.models.organization import User, Organization, OrganizationMember
from backend.models.project import Project, Task, TaskStatus, Activity, ActivityType
from backend.models.mission import Mission, MissionStatus, WorkflowPhase
from backend.models.agent import Agent, AgentType, AgentRun, AgentRunStatus
from backend.models.github import PullRequest, Changeset

DB_PATH = "./test_dashboard_aggregate.db"
engine = create_async_engine(f"sqlite+aiosqlite:///{DB_PATH}", echo=False)
Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

USER = User(id="dash_user", email="dash@test.local", full_name="Dash Tester", hashed_password="x")
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


async def _seed():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with Session() as s:
        s.add_all([
            Organization(id="org_a", name="Org A"),
            Organization(id="org_b", name="Org B"),
            USER,
            OrganizationMember(organization_id="org_a", user_id="dash_user", role="OWNER"),
            Project(id="p1", name="Payments", organization_id="org_a", created_by="dash_user",
                    updated_at=NOW),
            Project(id="p_other", name="Other Org Project", organization_id="org_b",
                    created_by="dash_user", updated_at=NOW + timedelta(days=1)),
            Agent(id="ag_backend", name="Core", type=AgentType.BACKEND_ENGINEER),
            Agent(id="ag_qa", name="Sentinel", type=AgentType.QA_ENGINEER),
        ])
        # One completed mission (a deliverable) and one active mission.
        s.add_all([
            Mission(id="m_done", organization_id="org_a", project_id="p1", created_by="dash_user",
                    title="Auth system", objective="x", status=MissionStatus.COMPLETED,
                    budget_max_tokens=100000, current_token_usage=40000,
                    completed_at=NOW, created_at=NOW - timedelta(days=2)),
            Mission(id="m_live", organization_id="org_a", project_id="p1", created_by="dash_user",
                    title="Checkout flow", objective="y", status=MissionStatus.EXECUTING,
                    phase=WorkflowPhase.IMPLEMENTATION, progress=40,
                    budget_max_tokens=200000, current_token_usage=10000,
                    created_at=NOW - timedelta(days=1)),
            Mission(id="m_cancel", organization_id="org_a", project_id="p1", created_by="dash_user",
                    title="Dropped", objective="z", status=MissionStatus.CANCELLED,
                    budget_max_tokens=999999, current_token_usage=5,
                    created_at=NOW - timedelta(days=3)),
        ])
        # Tasks: 3 completed, 1 in progress, 1 failed, 1 cancelled (excluded from %).
        s.add_all([
            Task(id="t1", project_id="p1", mission_id="m_done", created_by="dash_user", title="Login API",
                 status=TaskStatus.COMPLETED, created_at=NOW - timedelta(hours=10)),
            Task(id="t2", project_id="p1", mission_id="m_done", created_by="dash_user", title="Session store",
                 status=TaskStatus.COMPLETED, created_at=NOW - timedelta(hours=9)),
            Task(id="t3", project_id="p1", mission_id="m_live", created_by="dash_user", title="Cart model",
                 status=TaskStatus.COMPLETED, assigned_agent_id="ag_backend", created_at=NOW - timedelta(hours=8)),
            Task(id="t4", project_id="p1", mission_id="m_live", created_by="dash_user", title="Checkout UI",
                 status=TaskStatus.IN_PROGRESS, metadata_json='{"agent_name": "Atlas"}',
                 created_at=NOW - timedelta(hours=7)),
            Task(id="t5", project_id="p1", mission_id="m_live", created_by="dash_user", title="Webhook tests",
                 status=TaskStatus.FAILED, created_at=NOW - timedelta(hours=6)),
            Task(id="t6", project_id="p1", mission_id="m_cancel", created_by="dash_user", title="Old idea",
                 status=TaskStatus.CANCELLED, created_at=NOW - timedelta(hours=5)),
        ])
        s.add_all([
            AgentRun(id="r1", agent_id="ag_backend", project_id="p1", organization_id="org_a",
                     status=AgentRunStatus.COMPLETED, tokens_input=1000, tokens_output=500,
                     estimated_cost=0.25, started_at=NOW - timedelta(hours=8)),
            AgentRun(id="r2", agent_id="ag_backend", project_id="p1", organization_id="org_a",
                     status=AgentRunStatus.COMPLETED, tokens_input=2000, tokens_output=700,
                     estimated_cost=0.50, started_at=NOW - timedelta(hours=4)),
            AgentRun(id="r3", agent_id="ag_qa", project_id="p1", organization_id="org_a",
                     status=AgentRunStatus.FAILED, tokens_input=300, tokens_output=0,
                     estimated_cost=0.05, started_at=NOW - timedelta(hours=3)),
            # Another org's run on the same project id must never be counted.
            AgentRun(id="r_x", agent_id="ag_qa", project_id="p1", organization_id="org_b",
                     status=AgentRunStatus.COMPLETED, tokens_input=99999, tokens_output=99999,
                     estimated_cost=999.0),
        ])
        s.add_all([
            Changeset(id="c1", mission_id="m_done", task_id="t1", repository_id="repo1", branch="b",
                      files_changed=4, lines_added=120, lines_removed=10),
            Changeset(id="c2", mission_id="m_done", task_id="t2", repository_id="repo1", branch="b",
                      files_changed=2, lines_added=30, lines_removed=5),
            PullRequest(id="pr1", repository_id="repo1", project_id="p1", mission_id="m_done",
                        github_pr_id="g1", number=17, title="Auth", url="https://example.invalid/pr/17",
                        source_branch="b", target_branch="main", created_at=NOW),
        ])
        s.add(Activity(id="a1", organization_id="org_a", project_id="p1", type=ActivityType.TASK_COMPLETED,
                       title="Cart model done", created_at=NOW))
        s.add(Activity(id="a_x", organization_id="org_b", project_id="p_other",
                       type=ActivityType.TASK_COMPLETED, title="LEAK", created_at=NOW))
        await s.commit()


@pytest.fixture(scope="module")
def client():
    asyncio.run(_seed())

    async def _db():
        async with Session() as s:
            yield s

    async def _user():
        return USER

    prev = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = _user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(prev)
        asyncio.run(engine.dispose())
        if os.path.exists(DB_PATH):
            os.remove(DB_PATH)


def test_projects_are_org_scoped(client):
    data = client.get("/api/v1/org/dashboard").json()
    assert [p["id"] for p in data["projects"]] == ["p1"]
    assert data["project"]["name"] == "Payments"


def test_progress_excludes_cancelled_tasks(client):
    data = client.get("/api/v1/org/dashboard?project_id=p1").json()
    # 3 completed of 5 non-cancelled = 60%
    assert data["progress"] == {"percent": 60, "completed_tasks": 3, "total_tasks": 6}


def test_spend_uses_only_own_org_agent_runs(client):
    spend = client.get("/api/v1/org/dashboard?project_id=p1").json()["spend"]
    assert spend["runs"] == 3
    assert spend["usd"] == pytest.approx(0.80)
    assert spend["tokens_input"] == 3300
    assert spend["tokens_output"] == 1200


def test_token_budget_ignores_cancelled_missions(client):
    budget = client.get("/api/v1/org/dashboard?project_id=p1").json()["token_budget"]
    assert budget == {"total": 300000, "used": 50000, "remaining": 250000}


def test_deliverables_come_from_changesets_and_prs(client):
    d = client.get("/api/v1/org/dashboard?project_id=p1").json()["deliverables"]
    assert len(d) == 1
    assert d[0]["title"] == "Auth system"
    assert d[0]["files_changed"] == 6
    assert d[0]["lines_added"] == 150
    assert d[0]["lines_removed"] == 15
    assert d[0]["pr_count"] == 1
    assert d[0]["pr"]["number"] == 17


def test_current_mission_tasks_and_agents(client):
    cur = client.get("/api/v1/org/dashboard?project_id=p1").json()["current"]
    assert cur["mission_id"] == "m_live"
    assert cur["phase"] == "IMPLEMENTATION"
    assert cur["tasks_total"] == 3
    assert cur["tasks_done"] == 1
    agents = {t["title"]: t["agent"] for t in cur["tasks"]}
    assert agents == {"Cart model": "Core", "Checkout UI": "Atlas", "Webhook tests": None}


def test_attention_team_and_activity(client):
    data = client.get("/api/v1/org/dashboard?project_id=p1").json()
    assert data["attention"] == {"awaiting_approval": 0, "failed_tasks": 1, "blocked_tasks": 0}
    assert [(t["name"], t["runs"]) for t in data["team"]] == [("Core", 2), ("Sentinel", 1)]
    assert [a["title"] for a in data["activity"]] == ["Cart model done"]


def test_foreign_project_is_404(client):
    assert client.get("/api/v1/org/dashboard?project_id=p_other").status_code == 404
