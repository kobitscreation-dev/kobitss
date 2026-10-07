import pytest
import pytest_asyncio
import uuid
from backend.core.database import AsyncSessionLocal, engine
from backend.models.base import Base
from backend.models.organization import User, Organization, OrganizationMember, OrgRole
from backend.models.project import Project, Task, TaskStatus, TaskPriority
from backend.models.agent import ApprovalRequest, ApprovalStatus, AgentRun
from backend.schemas.agent import ApprovalSubmit
from backend.api.v1.projects import get_task, cancel_task, resolve_approval_request
from fastapi import HTTPException



@pytest.mark.asyncio
async def test_task_and_approval_isolation():
    async with AsyncSessionLocal() as db:
        # Create Org A and User A
        org_a = Organization(id=str(uuid.uuid4()), name="Org A")
        user_a = User(id=str(uuid.uuid4()), email="user_a@kobits.dev", hashed_password="pw", is_active=True)
        member_a = OrganizationMember(id=str(uuid.uuid4()), user_id=user_a.id, organization_id=org_a.id, role=OrgRole.ADMIN)
        
        # Create Org B and User B
        org_b = Organization(id=str(uuid.uuid4()), name="Org B")
        user_b = User(id=str(uuid.uuid4()), email="user_b@kobits.dev", hashed_password="pw", is_active=True)
        member_b = OrganizationMember(id=str(uuid.uuid4()), user_id=user_b.id, organization_id=org_b.id, role=OrgRole.ADMIN)

        # Create Project in Org A
        proj_a = Project(id=str(uuid.uuid4()), name="Proj A", organization_id=org_a.id, created_by=user_a.id)
        # Create Project in Org B
        proj_b = Project(id=str(uuid.uuid4()), name="Proj B", organization_id=org_b.id, created_by=user_b.id)

        # Create Task in Org B
        task_b = Task(
            id=str(uuid.uuid4()),
            project_id=proj_b.id,
            title="Secret Task B",
            status=TaskStatus.PENDING,
            priority=TaskPriority.HIGH,
            created_by=user_b.id
        )

        # Create AgentRun and ApprovalRequest in Org B
        run_b = AgentRun(
            id=str(uuid.uuid4()),
            organization_id=org_b.id,
            project_id=proj_b.id,
            agent_id="Aegis",
            task_id=task_b.id,
            status="WAITING_APPROVAL"
        )
        appr_b = ApprovalRequest(
            id=str(uuid.uuid4()),
            project_id=proj_b.id,
            organization_id=org_b.id,
            agent_run_id=run_b.id,
            requested_by="Aegis",
            status=ApprovalStatus.PENDING,
            title="Authorize Sensitive Operation",
            description="High risk DB drop",
            risk_level="CRITICAL"
        )

        db.add_all([org_a, user_a, member_a, org_b, user_b, member_b, proj_a, proj_b, task_b, run_b, appr_b])
        await db.commit()

        # 1. User A attempts to read User B's task -> Should raise 404 (Project not found / forbidden)
        with pytest.raises(HTTPException) as exc_info:
            await get_task(project_id=proj_b.id, task_id=task_b.id, current_user=user_a, db=db)
        assert exc_info.value.status_code == 404

        # 2. User A attempts to cancel User B's task -> Should raise 404
        with pytest.raises(HTTPException) as exc_info:
            await cancel_task(project_id=proj_b.id, task_id=task_b.id, current_user=user_a, db=db)
        assert exc_info.value.status_code == 404

        # 3. User A attempts to approve User B's approval request -> Should raise 404
        with pytest.raises(HTTPException) as exc_info:
            await resolve_approval_request(
                project_id=proj_b.id,
                approval_id=appr_b.id,
                submission=ApprovalSubmit(approved=True),
                db=db,
                current_user=user_a
            )
        assert exc_info.value.status_code == 404

        # 4. User B (authorized owner) can read task
        read_task = await get_task(project_id=proj_b.id, task_id=task_b.id, current_user=user_b, db=db)
        assert read_task.id == task_b.id
        assert read_task.title == "Secret Task B"

        # 5. User B can cancel task
        cancelled_task = await cancel_task(project_id=proj_b.id, task_id=task_b.id, current_user=user_b, db=db)
        assert cancelled_task.status == TaskStatus.CANCELLED

        # 6. User B resolves approval request with feedback
        resolved_appr = await resolve_approval_request(
            project_id=proj_b.id,
            approval_id=appr_b.id,
            submission=ApprovalSubmit(approved=False, feedback="Please sanitize inputs first"),
            db=db,
            current_user=user_b
        )
        assert resolved_appr.status == ApprovalStatus.REJECTED
        assert resolved_appr.feedback == "Please sanitize inputs first"
        assert resolved_appr.resolved_by_user_id == user_b.id
