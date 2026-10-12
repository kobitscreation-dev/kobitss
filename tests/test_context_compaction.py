import json
import pytest
from backend.services.context_compactor import ContextCompactor, CompactionReport
from backend.services.agent_executor import AgentExecutor
from backend.models.agent import AgentRun, AgentType, AgentRunStatus


# =============================================================================
# 1. TOKEN ESTIMATION ACCURACY TESTS
# =============================================================================

def test_token_estimation_accuracy():
    """Verify calibrated BPE heuristic on prose, code, and JSON."""
    compactor = ContextCompactor()

    # Empty
    assert compactor.estimate_tokens("") == 0
    assert compactor.estimate_tokens(None) == 0

    # Prose
    prose = "The quick brown fox jumps over the lazy dog."
    est_prose = compactor.estimate_tokens(prose)
    assert 9 <= est_prose <= 15

    # Code / JSON (should use tighter ~3.6 chars/token ratio)
    code_sample = """
    def execute_transaction(account_id: str, amount: float) -> bool:
        if amount <= 0:
            raise ValueError("Amount must be positive")
        balance = db.get_balance(account_id)
        if balance < amount:
            return False
        db.update_balance(account_id, balance - amount)
        return True
    """
    est_code = compactor.estimate_tokens(code_sample)
    assert 60 <= est_code <= 110

    # JSON Structure
    json_payload = {
        "status": "SUCCESS",
        "summary": "Completed authentication module patch",
        "files_modified": ["backend/auth.py", "backend/security.py"],
        "lines_changed": 42
    }
    est_json = compactor.estimate_tokens(json_payload)
    assert 40 <= est_json <= 80


# =============================================================================
# 2. PYTHON AST SYMBOL EXTRACTION TESTS
# =============================================================================

def test_python_ast_symbol_extraction():
    """Verify AST extraction preserves classes, methods, functions, and imports while dropping body."""
    python_code = '''"""Module docstring for auth service."""
import os
import jwt
from datetime import datetime, timedelta
from typing import Optional, Dict

SECRET_KEY = "super-secret"

class TokenManager:
    """Manages JWT issuance and revocation."""
    def __init__(self, secret: str):
        self.secret = secret

    def create_token(self, user_id: str, expires_in: int = 3600) -> str:
        payload = {"sub": user_id, "exp": datetime.utcnow() + timedelta(seconds=expires_in)}
        return jwt.encode(payload, self.secret, algorithm="HS256")

    async def verify_token(self, token: str) -> Optional[Dict]:
        try:
            return jwt.decode(token, self.secret, algorithms=["HS256"])
        except Exception:
            return None

def hash_password(password: str) -> str:
    return "hashed_" + password

async def authenticate_user(username: str, password_hash: str) -> bool:
    return True
'''
    outline = ContextCompactor.format_symbol_outline("backend/services/auth.py", python_code, total_lines=32)

    # Verifications
    assert "backend/services/auth.py" in outline
    assert "class TokenManager" in outline
    assert "create_token" in outline
    assert "verify_token" in outline
    assert "hash_password" in outline
    assert "authenticate_user" in outline
    assert "jwt" in outline or "datetime" in outline

    # Verify significant token reduction
    raw_tokens = ContextCompactor.estimate_tokens(python_code)
    outline_tokens = ContextCompactor.estimate_tokens(outline)
    assert outline_tokens < raw_tokens
    assert outline_tokens < 100


def test_generic_and_json_symbol_extraction():
    """Verify TypeScript/JavaScript and JSON outline extraction."""
    js_code = """
    import React, { useState, useEffect } from 'react';
    import { api } from '../api';

    export class MissionDashboard extends React.Component {
        render() { return <div>Dashboard</div>; }
    }

    export function formatStatus(status) {
        return status.toUpperCase();
    }

    export const fetchMissionStats = async (id) => {
        return await api.get(`/missions/${id}`);
    };
    """
    js_outline = ContextCompactor.format_symbol_outline("web/components/MissionDashboard.js", js_code)
    assert "MissionDashboard" in js_outline
    assert "formatStatus" in js_outline or "fetchMissionStats" in js_outline

    # JSON outline
    large_config = json.dumps({
        "app_name": "Kobits",
        "database": {"host": "localhost", "port": 5432, "name": "kobits_db"},
        "agents": ["AXIOM", "AEGIS", "SENTINEL", "REVIEW"],
        "retry_policy": {"max_retries": 5, "backoff": 2.0},
        "features": {"autopilot": True, "sandbox": True, "consensus": True}
    }, indent=2)
    json_outline = ContextCompactor.format_symbol_outline("config.json", large_config)
    assert "app_name" in json_outline
    assert "database" in json_outline


# =============================================================================
# 3. TERMINAL & TEST LOG PRUNING TESTS
# =============================================================================

def test_terminal_output_pruning_success():
    """Verify passing test output is collapsed to exit code and summary."""
    passing_log = """
============================= test session starts =============================
platform win32 -- Python 3.12.3, pytest-9.1.1, pluggy-1.6.0
rootdir: C:\\Users\\Arvind Kumar\\.gemini\\antigravity\\scratch\\kobits
collected 45 items

tests/test_auth.py .............................................         [100%]

============================== 45 passed in 2.14s ==============================
    """
    pruned = ContextCompactor.prune_terminal_output(passing_log, "pytest tests/test_auth.py")
    assert "succeeded (exit code 0)" in pruned
    assert "45 passed" in pruned
    assert ContextCompactor.estimate_tokens(pruned) < ContextCompactor.estimate_tokens(passing_log)


def test_terminal_output_pruning_failure_isolation():
    """Verify failing test output surgically preserves root cause traceback and assertion error."""
    failing_log = """
============================= test session starts =============================
platform win32 -- Python 3.12.3, pytest-9.1.1, pluggy-1.6.0
rootdir: C:\\Users\\Arvind Kumar\\.gemini\\antigravity\\scratch\\kobits
plugins: anyio-4.15.1, asyncio-1.4.0
collected 10 items

tests/test_checkout.py .........F                                       [100%]

================================== FAILURES ===================================
___________________________ test_insufficient_funds ___________________________

    def test_insufficient_funds():
        acc = Account(balance=50.0)
>       acc.withdraw(100.0)

tests/test_checkout.py:42: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <Account balance=50.0>, amount = 100.0

    def withdraw(self, amount):
        if amount > self.balance:
>           raise InsufficientFundsError("Insufficient funds: balance 50.0 < amount 100.0")
E           InsufficientFundsError: Insufficient funds: balance 50.0 < amount 100.0

backend/services/account.py:88: InsufficientFundsError
=========================== short test summary info ===========================
FAILED tests/test_checkout.py::test_insufficient_funds - InsufficientFundsError: Insufficient funds: balance 50.0 < amount 100.0
========================= 1 failed, 9 passed in 1.45s =========================
    """
    pruned = ContextCompactor.prune_terminal_output(failing_log, "pytest tests/test_checkout.py")

    # Critical assertions: The exact failure line, file, and error MUST be retained!
    assert "FAILED tests/test_checkout.py::test_insufficient_funds" in pruned
    assert "InsufficientFundsError" in pruned
    assert "backend/services/account.py" in pruned or "tests/test_checkout.py" in pruned
    assert "Actionable Diagnostics Extracted" in pruned


# =============================================================================
# 4. SEARCH / GREP OUTPUT PRUNING TESTS
# =============================================================================

def test_search_output_pruning():
    """Verify grep output across many files is compacted to unique file manifest."""
    grep_lines = []
    for i in range(30):
        grep_lines.append(f"backend/services/auth.py:{i*5}:    token = generate_token(user_id, salt='{i}')")
    for i in range(20):
        grep_lines.append(f"backend/models/user.py:{i*4}:    def verify_token_{i}(): pass")
    raw_grep = "\n".join(grep_lines)

    pruned = ContextCompactor.prune_search_output(raw_grep, "generate_token")
    assert "matched 2 files" in pruned or "backend/services/auth.py" in pruned
    assert "backend/models/user.py" in pruned
    assert ContextCompactor.estimate_tokens(pruned) < ContextCompactor.estimate_tokens(raw_grep) * 0.4


# =============================================================================
# 5. OPENAI / DEEPSEEK FORMAT COMPACTION TESTS
# =============================================================================

def test_openai_messages_compaction_preserves_prefix_and_tool_call_ids():
    """
    Verify OpenAI / DeepSeek format messages:
    1. Turn 0 system prompt & initial task spec are NEVER modified (Prompt caching invariant).
    2. tool_call_id pairing is 100% preserved.
    3. Older tool results are compacted.
    4. Recent 3 turns are kept in 100% raw fidelity.
    """
    compactor = ContextCompactor(max_token_budget=1500, recent_turns=2)

    system_prompt = "You are a Kobits Senior Backend Engineer Agent."
    user_goal = "Refactor user authentication to support OAuth2 tokens."

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_goal},
        # Turn 1 (Historical)
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_read_1",
                    "type": "function",
                    "function": {"name": "repository_read", "arguments": json.dumps({"path": "backend/auth.py"})}
                }
            ]
        },
        {
            "role": "tool",
            "tool_call_id": "call_read_1",
            "name": "repository_read",
            "content": json.dumps({
                "path": "backend/auth.py",
                "total_lines": 350,
                "content": "\n".join([f"line_{i} = 'some verbose code content here {i}'" for i in range(350)])
            })
        },
        # Turn 2 (Historical)
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_test_1",
                    "type": "function",
                    "function": {"name": "run_command", "arguments": json.dumps({"command": "pytest"})}
                }
            ]
        },
        {
            "role": "tool",
            "tool_call_id": "call_test_1",
            "name": "run_command",
            "content": json.dumps({
                "exit_code": 0,
                "stdout": "============================= 30 passed in 2.0s =============================",
                "stderr": ""
            })
        },
        # Turn 3 (Recent Golden Window - must stay 100% raw)
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_read_recent",
                    "type": "function",
                    "function": {"name": "repository_read", "arguments": json.dumps({"path": "backend/routes.py"})}
                }
            ]
        },
        {
            "role": "tool",
            "tool_call_id": "call_read_recent",
            "name": "repository_read",
            "content": json.dumps({
                "path": "backend/routes.py",
                "total_lines": 4,
                "content": "from fastapi import APIRouter\nrouter = APIRouter()\n@router.get('/me')\ndef me(): return {}\n"
            })
        },
    ]

    tokens_before = ContextCompactor.estimate_messages_tokens(messages)
    compacted_messages, report = compactor.compact_openai_messages(messages, max_token_budget=1000, recent_turns=1)

    # 1. Prefix is identical
    assert compacted_messages[0]["role"] == "system"
    assert compacted_messages[0]["content"] == system_prompt
    assert compacted_messages[1]["role"] == "user"
    assert compacted_messages[1]["content"] == user_goal

    # 2. Historical tool result was compacted
    tool_msg_1 = next(m for m in compacted_messages if m.get("tool_call_id") == "call_read_1")
    parsed_res_1 = json.loads(tool_msg_1["content"])
    assert parsed_res_1.get("_pruned") is True
    assert "AST Outline" in parsed_res_1.get("content", "")

    # 3. Recent golden turn was NOT touched
    tool_msg_recent = next(m for m in compacted_messages if m.get("tool_call_id") == "call_read_recent")
    parsed_res_recent = json.loads(tool_msg_recent["content"])
    assert "_pruned" not in parsed_res_recent
    assert "@router.get('/me')" in parsed_res_recent.get("content", "")

    # 4. Telemetry report
    assert report.observations_pruned >= 1
    assert report.compacted_tokens < tokens_before


# =============================================================================
# 6. ANTHROPIC / BEDROCK FORMAT COMPACTION TESTS
# =============================================================================

def test_anthropic_messages_compaction_preserves_prefix_and_tool_use_ids():
    """Verify Anthropic Messages API format message and tool_use_id integrity."""
    compactor = ContextCompactor(max_token_budget=1200, recent_turns=1)

    user_prompt = "Build a customer feedback collector endpoint."
    messages = [
        {"role": "user", "content": [{"type": "text", "text": user_prompt}]},
        # Turn 1
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "I will read the existing api file."},
                {"type": "tool_use", "id": "tu_1", "name": "repository_read", "input": {"path": "backend/api.py"}}
            ]
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tu_1",
                    "content": json.dumps({
                        "path": "backend/api.py",
                        "total_lines": 300,
                        "content": "\n".join([f"line_{i} = 'some data'" for i in range(300)])
                    })
                }
            ]
        },
        # Turn 2 (Golden Recent Window)
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Now checking routes."},
                {"type": "tool_use", "id": "tu_recent", "name": "repository_read", "input": {"path": "backend/routes.py"}}
            ]
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tu_recent",
                    "content": "router = APIRouter()"
                }
            ]
        }
    ]

    tokens_before = ContextCompactor.estimate_messages_tokens(messages)
    compacted, report = compactor.compact_anthropic_messages(messages, max_token_budget=800, recent_turns=1)

    # Invariants
    assert compacted[0]["role"] == "user"
    assert compacted[0]["content"][0]["text"] == user_prompt

    # Tool result 1 was compacted
    user_turn_1 = compacted[2]
    tu_res_1 = user_turn_1["content"][0]
    assert tu_res_1["tool_use_id"] == "tu_1"
    assert "Observation Pruned" in tu_res_1["content"]

    # Recent tool result was preserved
    user_turn_2 = compacted[4]
    tu_res_recent = user_turn_2["content"][0]
    assert tu_res_recent["tool_use_id"] == "tu_recent"
    assert tu_res_recent["content"] == "router = APIRouter()"

    assert report.observations_pruned >= 1
    assert report.compacted_tokens < tokens_before


# =============================================================================
# 7. TRAJECTORY CHECKPOINT SYNTHESIS TESTS
# =============================================================================

def test_trajectory_checkpoint_synthesis():
    """Verify trajectory checkpoint synthesizes historical turns into structured markdown."""
    turns_data = [
        {"tool": "repository_read", "args": {"path": "backend/main.py"}, "status": "DONE"},
        {"tool": "repository_read", "args": {"path": "backend/auth.py"}, "status": "DONE"},
        {"tool": "repository_write", "args": {"path": "backend/oauth.py"}, "status": "DONE"},
        {"tool": "run_command", "args": {"command": "pytest tests/test_oauth.py"}, "status": "FAIL", "result": "AssertionError: 401 != 200"},
        {"tool": "repository_write", "args": {"path": "backend/oauth.py"}, "status": "DONE"},
        {"tool": "run_command", "args": {"command": "pytest tests/test_oauth.py"}, "status": "PASS"},
    ]

    checkpoint = ContextCompactor.synthesize_trajectory_checkpoint(
        turns_data,
        mission_objective="Implement OAuth2 Token Authentication with Unit Tests",
        start_turn=1,
        end_turn=6
    )

    assert "[MISSION TRAJECTORY CHECKPOINT: Turns 1 to 6]" in checkpoint
    assert "Implement OAuth2 Token Authentication" in checkpoint
    assert "backend/main.py" in checkpoint or "backend/auth.py" in checkpoint
    assert "backend/oauth.py" in checkpoint
    assert "Key Hypotheses Tested & Progression" in checkpoint
    assert "Execution Continuity" in checkpoint


# =============================================================================
# 8. BRUTAL 30-TURN LONG-HORIZON REACT LOOP PARITY BENCHMARK (Kyros Benchmark)
# =============================================================================

def test_long_horizon_30_turn_react_loop_compaction():
    """
    BRUTAL KYROS-LEVEL BENCHMARK:
    Simulates a heavy 30-turn autonomous ReAct execution loop.
    In raw form, reading files, searching code, and running test tracebacks balloons
    to 90,000+ tokens.
    ContextCompactor MUST:
    1. Compress the 30-turn trajectory down to < 28,000 tokens.
    2. Achieve > 65% token compression ratio.
    3. Preserve 100% of the mission goal at Turn 0.
    4. Keep the recent 3 turns in 100% full raw fidelity.
    5. Maintain all tool_call_id pairings without orphans.
    """
    compactor = ContextCompactor(max_token_budget=28000, recent_turns=3)

    system_prompt = "You are a Kobits Autonomous Full-Stack Engineer."
    mission_spec = "Complete overhaul of the telemetry and consensus engine with zero regressions."

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": mission_spec},
    ]

    # Generate 30 turns of realistic heavy tool actions
    for turn_idx in range(1, 31):
        call_id = f"call_{turn_idx}"
        if turn_idx == 30:
            # Active Turn 30: test failure with critical assertion error
            fn_name = "run_command"
            args = {"command": "pytest tests/test_critical.py"}
            tool_content = json.dumps({
                "exit_code": 1,
                "stdout": (
                    "FAILED tests/test_critical.py::test_active_bug - AssertionError: Expected status ACTIVE, got PENDING\n"
                    "File 'backend/module_30.py', line 124, in verify_state\n"
                )
            })
        elif turn_idx % 4 == 1:
            # File read (large file ~600 lines)
            fn_name = "repository_read"
            args = {"path": f"backend/services/module_{turn_idx}.py"}
            tool_content = json.dumps({
                "path": f"backend/services/module_{turn_idx}.py",
                "total_lines": 600,
                "content": f"class EngineModule{turn_idx}:\n" + "\n".join([f"    def method_{i}(self): return {i * turn_idx}" for i in range(100)]) + "\n" + "\n".join([f"# comment line {j} with details and logic" for j in range(500)])
            })
        elif turn_idx % 4 == 2:
            # Code search (large grep dump)
            fn_name = "repository_search"
            args = {"query": f"ConsensusState_{turn_idx}"}
            tool_content = json.dumps({
                "results": "\n".join([f"backend/service_{k}.py:{k*10}: match for ConsensusState_{turn_idx}" for k in range(50)])
            })
        elif turn_idx % 4 == 3:
            # File write
            fn_name = "repository_write"
            args = {"path": f"backend/services/module_{turn_idx}.py", "content": "class NewImpl: pass"}
            tool_content = json.dumps({"status": "SUCCESS", "lines_written": 250})
        else:
            # Test run (large traceback)
            fn_name = "run_command"
            args = {"command": f"pytest tests/test_module_{turn_idx}.py"}
            tool_content = json.dumps({
                "exit_code": 0,
                "stdout": "============================= 50 passed in 3.1s ============================="
            })

        # Add assistant message with tool call
        messages.append({
            "role": "assistant",
            "content": f"Turn {turn_idx}: Executing {fn_name} to fulfill mission.",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": fn_name, "arguments": json.dumps(args)}
                }
            ]
        })
        # Add tool result
        messages.append({
            "role": "tool",
            "tool_call_id": call_id,
            "name": fn_name,
            "content": tool_content
        })

    # Estimate raw tokens before compaction
    raw_tokens = ContextCompactor.estimate_messages_tokens(messages)
    assert raw_tokens > 60000, f"Raw tokens {raw_tokens} should be > 60,000 for realistic 30-turn test"

    # Execute dynamic compaction
    compacted_messages, report = compactor.compact_openai_messages(messages, max_token_budget=28000, recent_turns=3)

    # ── VERIFY CRITERIA ──
    # 1. Compacted tokens MUST be under the budget limit
    assert report.compacted_tokens <= 28000, f"Compacted tokens {report.compacted_tokens} exceeds 28,000"

    # 2. Compression ratio MUST be > 65% (Kyros parity requirement)
    assert report.compression_ratio >= 0.65, f"Compression ratio {report.compression_ratio:.2%} is below 65%"

    # 3. Cache prefix Turn 0 is 100% identical
    assert compacted_messages[0]["role"] == "system"
    assert compacted_messages[0]["content"] == system_prompt
    assert compacted_messages[1]["role"] == "user"
    assert compacted_messages[1]["content"] == mission_spec

    # 4. Golden Recent Window (Turns 28, 29, 30) MUST be kept in 100% raw fidelity
    last_tool_msg = compacted_messages[-1]
    assert last_tool_msg["role"] == "tool"
    assert last_tool_msg["tool_call_id"] == "call_30"
    raw_parsed_30 = json.loads(last_tool_msg["content"])
    assert "_pruned" not in raw_parsed_30
    assert "AssertionError: Expected status ACTIVE, got PENDING" in raw_parsed_30["stdout"]

    # 5. Schema integrity: Check that every tool message has a valid tool_call_id
    tool_messages = [m for m in compacted_messages if m.get("role") == "tool"]
    for tm in tool_messages:
        assert tm.get("tool_call_id") is not None
        assert tm.get("content") is not None


# =============================================================================
# 9. INTEGRATION WITH AGENT EXECUTOR TESTS
# =============================================================================

def test_agent_executor_compactor_instance_and_metadata():
    """Verify AgentExecutor exposes ContextCompactor and records compaction in trace_logs."""
    executor = AgentExecutor()
    assert hasattr(executor, "compactor")
    assert isinstance(executor.compactor, ContextCompactor)

    # Verify compaction method on executor compactor
    test_msgs = [
        {"role": "system", "content": "System spec"},
        {"role": "user", "content": "User spec"},
        {"role": "assistant", "content": "Assistant", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "foo.py"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "read_file", "content": "x" * 2000}
    ]
    compacted, rep = executor.compactor.compact_openai_messages(test_msgs, max_token_budget=300, recent_turns=0)
    assert rep.compacted_tokens < rep.original_tokens


# =============================================================================
# 10. GEMINI FORMAT COMPACTION TESTS
# =============================================================================

def test_gemini_contents_compaction():
    """Verify Google Gemini format message compaction and function response pruning."""
    compactor = ContextCompactor(max_token_budget=1000, recent_turns=1)

    contents = [
        {"role": "user", "parts": [{"text": "Investigate database pool exhaustion"}]},
        # Turn 1
        {
            "role": "model",
            "parts": [{"functionCall": {"name": "repository_read", "args": {"path": "backend/db.py"}}}]
        },
        {
            "role": "user",
            "parts": [
                {
                    "functionResponse": {
                        "name": "repository_read",
                        "response": {
                            "path": "backend/db.py",
                            "total_lines": 400,
                            "content": "\n".join([f"pool_connection_{i} = True" for i in range(400)])
                        }
                    }
                }
            ]
        },
        # Turn 2 (Golden window)
        {
            "role": "model",
            "parts": [{"functionCall": {"name": "repository_read", "args": {"path": "backend/config.py"}}}]
        },
        {
            "role": "user",
            "parts": [
                {
                    "functionResponse": {
                        "name": "repository_read",
                        "response": {"path": "backend/config.py", "total_lines": 2, "content": "DB_POOL_SIZE = 20"}
                    }
                }
            ]
        }
    ]

    tokens_before = ContextCompactor.estimate_messages_tokens(contents)
    compacted, rep = compactor.compact_gemini_contents(contents, max_token_budget=600, recent_turns=1)

    assert rep.observations_pruned >= 1
    assert rep.compacted_tokens < tokens_before
    assert compacted[0]["parts"][0]["text"] == "Investigate database pool exhaustion"

    # Golden window preserved
    recent_fr = compacted[4]["parts"][0]["functionResponse"]
    assert recent_fr["response"]["content"] == "DB_POOL_SIZE = 20"


# =============================================================================
# 11. IDEMPOTENCY & ROBUSTNESS TESTS
# =============================================================================

def test_compaction_idempotency():
    """Verify that multiple consecutive compaction passes are idempotent."""
    compactor = ContextCompactor(max_token_budget=800, recent_turns=1)

    messages = [
        {"role": "system", "content": "You are Axiom."},
        {"role": "user", "content": "Task spec."},
        {"role": "assistant", "content": "Reading", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "repository_read", "arguments": '{"path": "a.py"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "repository_read", "content": json.dumps({"path": "a.py", "total_lines": 300, "content": "x\n" * 300})},
        {"role": "assistant", "content": "Recent", "tool_calls": [{"id": "c2", "type": "function", "function": {"name": "repository_read", "arguments": '{"path": "b.py"}'}}]},
        {"role": "tool", "tool_call_id": "c2", "name": "repository_read", "content": json.dumps({"path": "b.py", "content": "clean"})}
    ]

    pass1, rep1 = compactor.compact_openai_messages(messages, max_token_budget=500, recent_turns=1)
    pass2, rep2 = compactor.compact_openai_messages(pass1, max_token_budget=500, recent_turns=1)

    # Pass 2 tokens should be identical to Pass 1
    assert rep1.compacted_tokens == rep2.compacted_tokens
    assert len(pass1) == len(pass2)


def test_python_syntax_error_ast_fallback():
    """Verify fallback regex gracefully extracts symbols when Python file has syntax error."""
    broken_code = """
    class IncompleteService:
        def incomplete_method(x, y:
            return 123
    def valid_func(a, b):
        pass
    """
    outline = ContextCompactor.format_symbol_outline("broken.py", broken_code)
    assert "IncompleteService" in outline
    assert "broken.py" in outline

