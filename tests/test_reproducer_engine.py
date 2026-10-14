"""
Comprehensive Test Suite for Negative-to-Positive TDD Reproducer Engine.

Verifies:
1. Hermetic script authoring and path traversal protection.
2. Negative Baseline Check: asserts reproducer failure (exit_code != 0); rejects passing scripts.
3. Positive Verification: asserts reproducer passes after patch and verifies regression suites.
4. Atomic Rollback on failure: automatically rolls back corrupt worktrees via SandboxRollbackManager.
5. End-to-end SWE-bench TDD cycle execution and MissionRuntime integration.
"""

import os
import shutil
import tempfile
import subprocess
import pytest
from unittest.mock import AsyncMock, patch

from backend.services.sandbox_rollback import SandboxRollbackManager
from backend.services.reproducer_engine import (
    ReproducerEngine,
    ReproducerExecution,
    BaselineValidationResult,
    PositiveVerificationResult,
    TDDExecutionResult,
)
from backend.services.mission_runtime import MissionRuntime


@pytest.fixture
def git_sandbox():
    """Creates a temporary git repository configured as a hermetic sandbox worktree."""
    temp_dir = tempfile.mkdtemp(prefix="kobits_test_tdd_")
    try:
        subprocess.run(["git", "init"], cwd=temp_dir, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.name=Tester", "-c", "user.email=test@kobits.ai", "config", "user.name", "Tester"],
            cwd=temp_dir, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "-c", "user.name=Tester", "-c", "user.email=test@kobits.ai", "config", "user.email", "test@kobits.ai"],
            cwd=temp_dir, check=True, capture_output=True
        )

        # Create a buggy calculator module
        calc_file = os.path.join(temp_dir, "calculator.py")
        with open(calc_file, "w", encoding="utf-8") as f:
            f.write(
                "def add(a, b):\n"
                "    # BUG: Subtracts instead of adds\n"
                "    return a - b\n"
            )

        # Create an existing regression test file
        test_file = os.path.join(temp_dir, "test_existing.py")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write(
                "def test_baseline_health():\n"
                "    assert True\n"
            )

        subprocess.run(["git", "add", "."], cwd=temp_dir, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.name=Tester", "-c", "user.email=test@kobits.ai", "commit", "-m", "initial buggy commit"],
            cwd=temp_dir, check=True, capture_output=True
        )

        yield temp_dir
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_author_reproducer_hermetic_and_path_traversal(git_sandbox):
    """Verify writing reproducer script inside sandbox and blocking path traversal."""
    script_content = "import sys\nprint('reproducer running')\nsys.exit(1)\n"
    target = ReproducerEngine.author_reproducer(git_sandbox, script_content, "reproduce_issue.py")

    assert os.path.isfile(target)
    with open(target, "r", encoding="utf-8") as f:
        assert f.read() == script_content

    # Path traversal protection
    with pytest.raises(ValueError, match="Path traversal"):
        ReproducerEngine.author_reproducer(git_sandbox, script_content, "../escaped.py")


def test_negative_baseline_check_valid_reproducer(git_sandbox):
    """Negative Baseline Check: Must pass when reproducer correctly fails on buggy code."""
    reproducer_script = (
        "from calculator import add\n"
        "res = add(2, 3)\n"
        "assert res == 5, f'Expected 5 but got {res}'\n"
    )
    ReproducerEngine.author_reproducer(git_sandbox, reproducer_script, "reproduce_issue.py")

    baseline = ReproducerEngine.verify_negative_baseline(git_sandbox, "reproduce_issue.py")
    assert baseline.valid is True
    assert baseline.status == "VALID_BASELINE"
    assert baseline.exit_code != 0
    assert "AssertionError" in baseline.stderr or "Expected 5 but got" in baseline.stderr


def test_negative_baseline_check_invalid_reproducer(git_sandbox):
    """Negative Baseline Check: Must reject when reproducer passes on unpatched buggy code."""
    # A flawed reproducer that checks a trivial or already-working assertion
    invalid_script = (
        "from calculator import add\n"
        "# Flawed assertion that doesn't trigger the bug\n"
        "res = add(2, 3)\n"
        "assert isinstance(res, int)\n"
    )
    ReproducerEngine.author_reproducer(git_sandbox, invalid_script, "flawed_reproduce.py")

    baseline = ReproducerEngine.verify_negative_baseline(git_sandbox, "flawed_reproduce.py")
    assert baseline.valid is False
    assert baseline.status == "INVALID_BASELINE"
    assert baseline.exit_code == 0
    assert "A valid reproducer must fail" in baseline.rejection_reason


def test_positive_verification_success_and_failure(git_sandbox):
    """Verify positive verification after bug fix vs when bug remains unfixed."""
    reproducer_script = (
        "from calculator import add\n"
        "res = add(2, 3)\n"
        "assert res == 5, f'Expected 5 but got {res}'\n"
    )
    ReproducerEngine.author_reproducer(git_sandbox, reproducer_script, "reproduce_issue.py")

    # Before fix: positive verification must fail
    res_before = ReproducerEngine.verify_positive(git_sandbox, "reproduce_issue.py")
    assert res_before.success is False
    assert res_before.reproducer_passed is False

    # Apply fix to calculator.py
    calc_file = os.path.join(git_sandbox, "calculator.py")
    with open(calc_file, "w", encoding="utf-8") as f:
        f.write("def add(a, b):\n    return a + b\n")

    # After fix: positive verification must succeed
    res_after = ReproducerEngine.verify_positive(git_sandbox, "reproduce_issue.py")
    assert res_after.success is True
    assert res_after.reproducer_passed is True


@pytest.mark.asyncio
async def test_full_tdd_cycle_end_to_end_success(git_sandbox):
    """Verify complete end-to-end negative-to-positive TDD cycle with coder dispatch."""
    reproducer_script = (
        "from calculator import add\n"
        "res = add(10, 20)\n"
        "assert res == 30, f'Expected 30 but got {res}'\n"
    )

    def coder_agent_fix(sb_dir):
        # Fix the bug in calculator.py
        target = os.path.join(sb_dir, "calculator.py")
        with open(target, "w", encoding="utf-8") as f:
            f.write("def add(a, b):\n    return a + b\n")

    result = await ReproducerEngine.run_tdd_cycle(
        sandbox_dir=git_sandbox,
        coder_dispatch_fn=coder_agent_fix,
        script_content=reproducer_script,
        script_name="reproduce_issue.py",
        rollback_on_failure=True,
    )

    assert result.success is True
    assert result.stage == "COMPLETED"
    assert result.baseline_result.valid is True
    assert result.verification_result.success is True
    assert result.rolled_back is False


@pytest.mark.asyncio
async def test_tdd_cycle_rollback_on_verification_failure(git_sandbox):
    """Verify automatic atomic rollback when coder agent fails to fix bug."""
    reproducer_script = (
        "from calculator import add\n"
        "res = add(10, 20)\n"
        "assert res == 30, f'Expected 30 but got {res}'\n"
    )

    def bad_coder_agent(sb_dir):
        # Coder introduces invalid syntax or fails to fix
        target = os.path.join(sb_dir, "calculator.py")
        with open(target, "w", encoding="utf-8") as f:
            f.write("def add(a, b):\n    return a * b  # Still buggy\n")
        # And adds an untracked garbage file
        garbage = os.path.join(sb_dir, "corrupt_data.tmp")
        with open(garbage, "w") as f:
            f.write("corrupted data")

    result = await ReproducerEngine.run_tdd_cycle(
        sandbox_dir=git_sandbox,
        coder_dispatch_fn=bad_coder_agent,
        script_content=reproducer_script,
        script_name="reproduce_issue.py",
        rollback_on_failure=True,
    )

    assert result.success is False
    assert result.stage == "POSITIVE_VERIFICATION"
    assert result.rolled_back is True

    # Check that worktree was cleanly rolled back and untracked file deleted
    calc_path = os.path.join(git_sandbox, "calculator.py")
    with open(calc_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "return a - b" in content  # Original pre-patch state restored!
    assert not os.path.exists(os.path.join(git_sandbox, "corrupt_data.tmp"))


@pytest.mark.asyncio
async def test_mission_runtime_execute_tdd_reproducer_cycle_integration(git_sandbox):
    """Verify MissionRuntime.execute_tdd_reproducer_cycle class method integration."""
    reproducer_script = (
        "from calculator import add\n"
        "assert add(5, 5) == 10\n"
    )

    def coder_agent(sb_dir):
        with open(os.path.join(sb_dir, "calculator.py"), "w", encoding="utf-8") as f:
            f.write("def add(a, b):\n    return a + b\n")

    res = await MissionRuntime.execute_tdd_reproducer_cycle(
        sandbox_dir=git_sandbox,
        coder_dispatch_fn=coder_agent,
        reproducer_script=reproducer_script,
        script_name="reproduce_issue.py",
    )

    assert res["success"] is True
    assert res["stage"] == "COMPLETED"
    assert res["baseline_result"].valid is True
    assert res["verification_result"].success is True


def test_author_reproducer_blocks_root_dir_and_empty_name(git_sandbox):
    """Verify author_reproducer rejects root directory target or empty name."""
    with pytest.raises(ValueError, match="cannot be the sandbox directory itself"):
        ReproducerEngine.author_reproducer(git_sandbox, "print('bad')", ".")

    with pytest.raises(ValueError, match="cannot be the sandbox directory itself"):
        ReproducerEngine.author_reproducer(git_sandbox, "print('bad')", "")


def test_negative_baseline_rejects_timeout_script(git_sandbox):
    """Verify that a hanging/timeout reproducer is rejected with status TIMEOUT and valid=False."""
    hanging_script = "import time\ntime.sleep(5)\n"
    ReproducerEngine.author_reproducer(git_sandbox, hanging_script, "hang.py")

    baseline = ReproducerEngine.verify_negative_baseline(git_sandbox, "hang.py", timeout=1)
    assert baseline.valid is False
    assert baseline.status == "TIMEOUT"
    assert "timed out after 1 seconds" in baseline.rejection_reason


def test_positive_verification_with_quoted_regression_cmd(git_sandbox):
    """Verify that verify_positive properly handles quoted regression command strings via shlex."""
    # Write a passing reproducer
    repro = "assert 1 + 1 == 2\n"
    ReproducerEngine.author_reproducer(git_sandbox, repro, "pass_repro.py")

    # Command with quotes
    cmd_str = f'python -c "assert True"'
    res = ReproducerEngine.verify_positive(
        git_sandbox,
        script_path="pass_repro.py",
        regression_cmd=cmd_str,
    )
    assert res.success is True
    assert res.regression_passed is True
