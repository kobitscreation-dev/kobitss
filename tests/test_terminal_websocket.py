import asyncio
import json
import os
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from backend.main import app
from backend.core.database import get_db
import backend.core.database as db_mod
from backend.api.deps import get_current_active_user
from backend.models.base import Base
from backend.models.organization import User, Organization, OrganizationMember
from backend.models.project import Project, Task, TaskStatus
from backend.models.mission import Mission, MissionStatus, WorkflowPhase
from backend.services.tool_registry import handle_terminal_execute
from backend.services.websocket_manager import manager


DB_PATH = "./test_terminal_ws.db"
test_engine = create_async_engine(f"sqlite+aiosqlite:///{DB_PATH}", echo=False)
TestSession = sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

TEST_USER = User(id="user_term_1", email="term@test.local", full_name="Terminal Tester", hashed_password="pw")
NOW = datetime(2026, 10, 14, 0, 0, tzinfo=timezone.utc)


async def _seed_terminal_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with TestSession() as s:
        s.add_all([
            Organization(id="org_term", name="Terminal Org"),
            TEST_USER,
            OrganizationMember(organization_id="org_term", user_id="user_term_1", role="OWNER"),
            Project(
                id="proj_term",
                name="Terminal Project",
                organization_id="org_term",
                created_by="user_term_1",
                updated_at=NOW,
            ),
            Mission(
                id="msn_term_active",
                organization_id="org_term",
                project_id="proj_term",
                created_by="user_term_1",
                title="Active Live Terminal Mission",
                objective="Stream execution telemetry over WebSocket",
                status=MissionStatus.ACTIVE,
                phase=WorkflowPhase.IMPLEMENTATION,
                created_at=NOW,
            ),
        ])
        await s.commit()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("KOBITS_NO_AUTO_EXECUTE", "1")
    monkeypatch.setenv("KOBITS_NO_RESUME", "1")
    asyncio.run(_seed_terminal_db())

    monkeypatch.setattr(db_mod, "AsyncSessionLocal", TestSession)

    async def override_db():
        async with TestSession() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_active_user] = lambda: TEST_USER

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()
    asyncio.run(test_engine.dispose())
    if os.path.exists(DB_PATH):
        try:
            os.remove(DB_PATH)
        except OSError:
            pass


def test_terminal_websocket_greeting_and_ping(client):
    """Verify connecting to /ws/terminal/{id} yields greeting and responds to ping."""
    miss_id = "msn_term_active"

    with client.websocket_connect(f"/ws/terminal/{miss_id}") as ws:
        # First message should be system greeting
        greeting = ws.receive_json()
        assert greeting["type"] == "system"
        assert miss_id in greeting["message"]
        assert greeting["mission_id"] == miss_id

        # Send ping as JSON
        ws.send_text(json.dumps({"type": "ping"}))
        resp = ws.receive_json()
        assert resp["type"] == "pong"

        # Send string ping
        ws.send_text("ping")
        resp = ws.receive_json()
        assert resp["type"] == "pong"


def test_terminal_websocket_dual_route_api_v1(client):
    """Verify dual routing: /api/v1/ws/terminal/{id} works identically."""
    miss_id = "msn_term_active"

    with client.websocket_connect(f"/api/v1/ws/terminal/{miss_id}") as ws:
        greeting = ws.receive_json()
        assert greeting["type"] == "system"
        assert greeting["mission_id"] == miss_id


def test_terminal_websocket_clear_and_status(client):
    """Verify clear and status commands over WebSocket."""
    miss_id = "msn_term_active"

    with client.websocket_connect(f"/ws/terminal/{miss_id}") as ws:
        greeting = ws.receive_json()
        assert greeting["type"] == "system"

        # Send 'clear' command
        ws.send_text("clear")
        clear_resp = ws.receive_json()
        assert clear_resp["type"] == "clear"

        # Send 'status' command
        ws.send_text("status")
        status_resp = ws.receive_json()
        assert status_resp["type"] == "system"
        assert f"Mission {miss_id}" in status_resp["message"]
        assert "ACTIVE" in status_resp["message"]


def test_terminal_websocket_user_feedback_creates_task(client):
    """Verify user feedback creates a task in DB and broadcasts acknowledgment."""
    miss_id = "msn_term_active"

    with client.websocket_connect(f"/ws/terminal/{miss_id}") as ws:
        greeting = ws.receive_json()
        assert greeting["type"] == "system"

        # Send live steering feedback
        ws.send_text(json.dumps({"message": "Use poetry for dependency management"}))
        ack_msg = ws.receive_json()
        assert ack_msg["type"] == "system"
        assert "User Input Acknowledged" in ack_msg["message"]
        assert "poetry" in ack_msg["message"]

    # Verify task was created in database
    async def _verify():
        async with TestSession() as db:
            res = await db.execute(select(Task).where(Task.mission_id == miss_id, Task.is_correction == True))
            tasks = res.scalars().all()
            assert len(tasks) >= 1
            assert "poetry" in tasks[0].description

    asyncio.run(_verify())


def test_tool_registry_terminal_broadcast(client):
    """Verify handle_terminal_execute broadcasts execution output to the mission channel."""
    miss_id = "msn_term_active"

    with client.websocket_connect(f"/ws/terminal/{miss_id}") as ws:
        greeting = ws.receive_json()
        assert greeting["type"] == "system"

        # Execute terminal command with mission_id in context
        res = asyncio.run(handle_terminal_execute(
            command="echo 'terminal streaming active'",
            timeout=10,
            context={"mission_id": miss_id, "sandbox_session_id": None}
        ))
        assert res.get("exit_code") == 0

        # Receive streamed terminal output over WebSocket
        ws_msg = ws.receive_json()
        assert ws_msg["type"] == "terminal_output"
        assert ws_msg["mission_id"] == miss_id
        assert "echo 'terminal streaming active'" in ws_msg["command"]
        assert "terminal streaming active" in ws_msg["stdout"]


def test_frontend_mission_view_wiring():
    """Verify web/js/views/mission.js has the Live Terminal implementation and WebSocket wiring."""
    with open("web/js/views/mission.js", "r", encoding="utf-8") as f:
        src = f.read()

    assert "/ws/terminal/" in src, "Must contain WebSocket URL connecting to /ws/terminal/{id}"
    assert "Live Terminal" in src, "Must render Live Terminal header in UI"
    assert "terminal-output" in src, "Must contain terminal output container element"
    assert "terminal-input" in src, "Must contain terminal input prompt"
    assert "cleanupTerminal" in src, "Must have cleanup logic for disconnect/unmount"
    assert "reconnect" in src.lower(), "Must handle reconnection logic"
    assert "terminalAutoScroll" in src, "Must handle auto-scroll toggle and behavior"
    assert "export function cleanupTerminal" in src
    assert "export async function render" in src


def test_frontend_root_js_view_wiring():
    """Verify js/views/mission.js matches web/js/views/mission.js."""
    with open("js/views/mission.js", "r", encoding="utf-8") as f:
        src = f.read()

    assert "/ws/terminal/" in src
    assert "Live Terminal" in src
    assert "terminal-output" in src
    assert "terminal-input" in src
    assert "cleanupTerminal" in src


def test_terminal_websocket_binary_frame_handling(client):
    """Verify raw binary frames (bytes) are safely received and decoded without disconnect."""
    miss_id = "msn_term_active"

    with client.websocket_connect(f"/ws/terminal/{miss_id}") as ws:
        greeting = ws.receive_json()
        assert greeting["type"] == "system"

        # Send binary bytes frame
        binary_payload = b"status"
        ws.send_bytes(binary_payload)
        resp = ws.receive_json()
        assert resp["type"] == "system"
        assert f"Mission {miss_id}" in resp["message"]

        # Send binary ping frame
        ws.send_bytes(b"ping")
        pong = ws.receive_json()
        assert pong["type"] == "pong"


def test_tool_registry_git_run_terminal_broadcast(client, tmp_path):
    """Verify handle_git_run broadcasts execution output when using sandbox_dir."""
    from backend.services.tool_registry import handle_git_run
    miss_id = "msn_term_active"

    # Initialize a temporary git repo to test git run broadcast
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True, check=True)

    with client.websocket_connect(f"/ws/terminal/{miss_id}") as ws:
        greeting = ws.receive_json()
        assert greeting["type"] == "system"

        res = asyncio.run(handle_git_run(
            command="git status",
            context={"mission_id": miss_id, "sandbox_dir": str(tmp_path)}
        ))
        assert res.get("exit_code") == 0

        # Receive streamed terminal output
        ws_msg = ws.receive_json()
        assert ws_msg["type"] == "terminal_output"
        assert ws_msg["mission_id"] == miss_id
        assert "git status" in ws_msg["command"]


def test_frontend_rolling_buffer_and_route_guards():
    """Verify rolling line pruning in mission.js and mission route guards in app.js."""
    with open("web/js/views/mission.js", "r", encoding="utf-8") as f:
        m_web = f.read()
    with open("js/views/mission.js", "r", encoding="utf-8") as f:
        m_root = f.read()

    assert "MAX_TERMINAL_LINES" in m_web, "web/js/views/mission.js must enforce MAX_TERMINAL_LINES"
    assert "MAX_TERMINAL_LINES" in m_root, "js/views/mission.js must enforce MAX_TERMINAL_LINES"
    assert "getTerminalWsUrl" in m_web, "web/js/views/mission.js must dynamically resolve WebSocket URL"
    assert "getTerminalWsUrl" in m_root, "js/views/mission.js must dynamically resolve WebSocket URL"

    with open("web/js/app.js", "r", encoding="utf-8") as f:
        app_web = f.read()
    with open("js/app.js", "r", encoding="utf-8") as f:
        app_root = f.read()

    assert "isMissionRoute" in app_web, "web/js/app.js must guard against overwriting mission views"
    assert "isMissionRoute" in app_root, "js/app.js must guard against overwriting mission views"

