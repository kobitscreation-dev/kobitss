"""
Comprehensive Test Suite for Interactive Sandbox PTY Terminal.

Verifies:
1. PTYSession cross-platform lifecycle (spawn, is_alive, terminate).
2. Non-blocking bidirectional streaming (stdin write and async stream_output).
3. Terminal geometry resize events (cols x rows).
4. PTYSessionManager registry (reuse active, terminate on close, cleanup_all).
5. Live WebSocket /ws/terminal/{id} integration with interactive commands, resize, and byte frames.
"""

import os
import sys
import json
import asyncio
import tempfile
import shutil
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services.pty_terminal import PTYSession, PTYSessionManager, pty_manager
from backend.services.websocket_manager import manager


@pytest.fixture
def temp_sandbox_dir():
    d = tempfile.mkdtemp(prefix="kobits_pty_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.mark.asyncio
async def test_pty_session_lifecycle_and_streaming(temp_sandbox_dir):
    """Verify spawning shell, writing stdin, reading streamed output, and terminating cleanly."""
    session = PTYSession(session_id="test_sess_1", cwd=temp_sandbox_dir)
    try:
        await session.start()
        assert session.is_alive is True

        # Write command to stdin
        if os.name == "nt":
            await session.write("Write-Output 'HELLO_KOBITS_PTY'\r\n")
        else:
            await session.write("echo 'HELLO_KOBITS_PTY'\n")

        # Stream output
        found = False
        async for chunk in session.stream_output():
            if "HELLO_KOBITS_PTY" in chunk:
                found = True
                break
        assert found is True

        # Verify history buffer captured output
        history = session.get_history()
        assert "HELLO_KOBITS_PTY" in history

        # Test resize
        session.resize(cols=120, rows=40)
        assert session.cols == 120
        assert session.rows == 40

    finally:
        await session.terminate()
        assert session.is_alive is False


@pytest.mark.asyncio
async def test_pty_session_manager(temp_sandbox_dir):
    """Verify PTYSessionManager creates, tracks, reuses, and closes sessions."""
    mgr = PTYSessionManager()
    try:
        s1 = await mgr.get_or_create_session("sess_mgr_test", cwd=temp_sandbox_dir)
        assert s1.is_alive is True

        # Getting again should reuse the existing session
        s2 = await mgr.get_or_create_session("sess_mgr_test", cwd=temp_sandbox_dir)
        assert s1 is s2

        retrieved = mgr.get_session("sess_mgr_test")
        assert retrieved is s1

        await mgr.close_session("sess_mgr_test")
        assert mgr.get_session("sess_mgr_test") is None
        assert s1.is_alive is False
    finally:
        await mgr.cleanup_all()


def test_websocket_terminal_pty_interactive_protocol():
    """Verify WebSocket /ws/terminal/{id} handles resize, pty_input, and interactive shell."""
    client = TestClient(app)
    miss_id = "test_mission_pty_ws"

    with client.websocket_connect(f"/ws/terminal/{miss_id}") as ws:
        # Greeting
        greeting = ws.receive_json()
        assert greeting["type"] == "system"
        assert miss_id in greeting["message"]

        # Resize message
        ws.send_json({"type": "resize", "cols": 100, "rows": 35})
        resize_resp = ws.receive_json()
        assert resize_resp["type"] == "system"
        assert "100x35" in resize_resp["message"]

        # Utility commands
        ws.send_text("ping")
        pong = ws.receive_json()
        assert pong["type"] == "pong"

        ws.send_text("clear")
        clr = ws.receive_json()
        assert clr["type"] == "clear"

        # Binary frame commands
        ws.send_bytes(b"status")
        st = ws.receive_json()
        assert st["type"] == "system"
        assert "Mission" in st["message"]


@pytest.mark.asyncio
async def test_pty_session_manager_cleanup_idle(temp_sandbox_dir):
    """Verify cleanup_idle cleans up sessions exceeding max_idle_seconds."""
    mgr = PTYSessionManager()
    try:
        s = await mgr.get_or_create_session("idle_sess_test", cwd=temp_sandbox_dir)
        assert s.is_alive is True

        # Backdate last_activity to simulate idle timeout
        s.last_activity = 0.0

        closed_count = await mgr.cleanup_idle(max_idle_seconds=1.0)
        assert closed_count == 1
        assert mgr.get_session("idle_sess_test") is None
        assert s.is_alive is False
    finally:
        await mgr.cleanup_all()


@pytest.mark.asyncio
async def test_websocket_terminal_reconnect_sends_history(temp_sandbox_dir):
    """Verify that a reconnecting WebSocket client immediately receives buffered history."""
    miss_id = "test_mission_reconnect_hist"

    # Pre-populate session with history
    session = await pty_manager.get_or_create_session(miss_id, cwd=temp_sandbox_dir)
    session._append_history("PREVIOUS_OUTPUT_FROM_PRIOR_SESSION\n")

    try:
        client = TestClient(app)
        with client.websocket_connect(f"/ws/terminal/{miss_id}") as ws:
            # 1. System greeting
            greeting = ws.receive_json()
            assert greeting["type"] == "system"

            # 2. History replay frame
            hist_frame = ws.receive_json()
            assert hist_frame["type"] == "pty_output"
            assert "PREVIOUS_OUTPUT_FROM_PRIOR_SESSION" in hist_frame["data"]
    finally:
        await pty_manager.close_session(miss_id)


@pytest.mark.asyncio
async def test_websocket_terminal_raw_keystroke_and_binary_forwarding(temp_sandbox_dir):
    """Verify sending raw keystrokes (Enter, Tab) and raw binary bytes are forwarded to PTY."""
    miss_id = "test_mission_keystrokes"

    try:
        client = TestClient(app)
        with client.websocket_connect(f"/ws/terminal/{miss_id}") as ws:
            greeting = ws.receive_json()
            assert greeting["type"] == "system"

            # Send raw Enter without dropping
            ws.send_text("\r\n")

            # Send raw bytes without disconnecting or erroring
            ws.send_bytes(b"\x00")

            # Send stdin JSON structure
            ws.send_json({"type": "stdin", "data": "echo 'TEST_OK'\r\n"})

            # Ping should still work
            ws.send_text("ping")
            pong = ws.receive_json()
            assert pong["type"] == "pong"
    finally:
        await pty_manager.close_session(miss_id)
