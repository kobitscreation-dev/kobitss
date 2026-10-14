"""
ReproducerEngine — SWE-bench-grade Negative-to-Positive TDD Reproducer Engine.

Executes hermetic test-driven reproduction cycles for autonomous bug fixing:
1. Authors a standalone reproducer test script (`reproduce_issue.py`, `.js`, etc.) in the sandbox worktree.
2. Negative Baseline Check: Executes the reproducer on unpatched code; asserts failure (exit_code != 0).
   If exit_code == 0, rejects the reproducer as invalid (does not capture the bug).
3. Coder Implementation: Dispatches task to Coder Agent.
4. Positive Verification Check: Executes reproducer after code modifications (must pass, exit_code == 0)
   and verifies that the full project regression test suite remains green.
5. Atomic Rollback: Automatically invokes SandboxRollbackManager.rollback() if verification fails.
"""

import os
import sys
import time
import shutil
import shlex
import logging
import inspect
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable, Any, Union

from backend.services.sandbox_rollback import SandboxRollbackManager

logger = logging.getLogger("kobits.reproducer_engine")


@dataclass
class ReproducerExecution:
    """Execution output and telemetry for a single reproducer or regression run."""
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    command: list[str]
    timed_out: bool = False
    error: Optional[str] = None

    @property
    def is_success(self) -> bool:
        """Returns True if the process completed with zero exit code."""
        return self.exit_code == 0


@dataclass
class BaselineValidationResult:
    """Result of the Negative Baseline Check on unpatched code."""
    valid: bool
    exit_code: int
    stdout: str
    stderr: str
    status: str  # "VALID_BASELINE" | "INVALID_BASELINE" | "EXECUTION_ERROR" | "TIMEOUT"
    rejection_reason: Optional[str] = None
    execution: Optional[ReproducerExecution] = None


@dataclass
class PositiveVerificationResult:
    """Result of Positive Verification Check after coder patch."""
    success: bool
    reproducer_passed: bool
    regression_passed: bool
    reproducer_execution: Optional[ReproducerExecution] = None
    regression_execution: Optional[ReproducerExecution] = None
    reason: Optional[str] = None


@dataclass
class TDDExecutionResult:
    """Complete summary of an end-to-end negative-to-positive TDD cycle."""
    success: bool
    stage: str  # "NEGATIVE_BASELINE" | "CODER_EXECUTION" | "POSITIVE_VERIFICATION" | "COMPLETED"
    baseline_result: Optional[BaselineValidationResult] = None
    verification_result: Optional[PositiveVerificationResult] = None
    initial_checkpoint_sha: Optional[str] = None
    pre_patch_checkpoint_sha: Optional[str] = None
    rolled_back: bool = False
    error: Optional[str] = None
    telemetry: dict = field(default_factory=dict)


class ReproducerEngine:
    """
    SWE-bench-grade TDD Reproducer Engine with hermetic sandbox isolation
    and atomic rollback safety.
    """

    DEFAULT_SCRIPT_NAME = "reproduce_issue.py"
    DEFAULT_TIMEOUT_SEC = 30
    REGRESSION_TIMEOUT_SEC = 60

    @classmethod
    def author_reproducer(
        cls,
        sandbox_dir: str,
        content: str,
        file_name: str = DEFAULT_SCRIPT_NAME,
    ) -> str:
        """
        Hermetically author a standalone reproducer test script inside the sandbox workspace.
        Ensures strict boundary checking to prevent path traversal outside sandbox_dir.
        Returns the absolute path to the written reproducer script.
        """
        if not sandbox_dir or not os.path.isdir(sandbox_dir):
            raise ValueError(f"Invalid sandbox directory: {sandbox_dir}")

        # Strip drive letter if present on Windows to prevent absolute path escapes
        clean_drive, clean_tail = os.path.splitdrive(file_name.strip())
        clean_name = os.path.normpath(clean_tail).lstrip("/\\")
        if not clean_name or clean_name in (".", ""):
            raise ValueError(f"Target reproducer path cannot be the sandbox directory itself: {file_name}")

        if ".." in clean_name.split(os.sep):
            raise ValueError(f"Path traversal detected in reproducer file_name: {file_name}")

        sandbox_root = os.path.abspath(sandbox_dir)
        target_path = os.path.abspath(os.path.join(sandbox_root, clean_name))

        # Enforce sandbox containment: must be strictly inside sandbox_root
        if target_path == sandbox_root or not target_path.startswith(sandbox_root + os.sep):
            raise ValueError(f"Target reproducer path {target_path} escapes or equals sandbox {sandbox_dir}")

        if os.path.isdir(target_path):
            raise ValueError(f"Target reproducer path is an existing directory: {target_path}")

        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(content)

        # Ensure executable permissions on POSIX
        if os.name != "nt":
            try:
                os.chmod(target_path, 0o755)
            except OSError:
                pass

        logger.info(f"Authored reproducer test at: {target_path}")
        return target_path

    @classmethod
    def resolve_runner(cls, script_path: str) -> list[str]:
        """
        Detects appropriate runtime interpreter based on script extension.
        Defaults to sys.executable for Python.
        """
        ext = os.path.splitext(script_path)[1].lower()
        if ext == ".py":
            return [sys.executable]
        elif ext in (".js", ".mjs", ".cjs"):
            node_bin = shutil.which("node") or "node"
            return [node_bin]
        elif ext == ".ts":
            npx_bin = shutil.which("npx") or "npx"
            return [npx_bin, "ts-node"]
        elif ext in (".sh", ".bash"):
            bash_bin = shutil.which("bash") or "bash"
            return [bash_bin]
        # Fallback to direct execution or Python
        return [sys.executable]

    @classmethod
    def execute_script(
        cls,
        sandbox_dir: str,
        script_path: str,
        timeout: int = DEFAULT_TIMEOUT_SEC,
        env_overrides: Optional[dict] = None,
    ) -> ReproducerExecution:
        """
        Hermetically executes the reproducer script inside sandbox_dir with strict isolation,
        timeout protection, and full stdout/stderr capture.
        """
        if not os.path.isdir(sandbox_dir):
            return ReproducerExecution(
                exit_code=-1,
                stdout="",
                stderr="",
                duration_seconds=0.0,
                command=[],
                error=f"Sandbox directory does not exist: {sandbox_dir}",
            )

        # Resolve relative script names against sandbox_dir
        abs_script = (
            script_path
            if os.path.isabs(script_path)
            else os.path.abspath(os.path.join(sandbox_dir, script_path))
        )

        if not os.path.exists(abs_script):
            return ReproducerExecution(
                exit_code=-1,
                stdout="",
                stderr="",
                duration_seconds=0.0,
                command=[],
                error=f"Reproducer script not found: {abs_script}",
            )

        runner = cls.resolve_runner(abs_script)
        cmd = runner + [abs_script]

        # Prepare isolated execution environment
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        # Prepend sandbox_dir to PYTHONPATH to favor local modules
        existing_py_path = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            f"{sandbox_dir}{os.pathsep}{existing_py_path}" if existing_py_path else sandbox_dir
        )
        if env_overrides:
            env.update(env_overrides)

        start_time = time.perf_counter()
        timed_out = False
        error_msg = None

        try:
            res = subprocess.run(
                cmd,
                cwd=sandbox_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
            exit_code = res.returncode
            stdout = res.stdout
            stderr = res.stderr
        except subprocess.TimeoutExpired as te:
            exit_code = -1
            stdout = te.stdout or "" if isinstance(te.stdout, str) else ""
            stderr = te.stderr or "" if isinstance(te.stderr, str) else ""
            timed_out = True
            error_msg = f"Reproducer script timed out after {timeout} seconds"
        except Exception as e:
            exit_code = -1
            stdout = ""
            stderr = ""
            error_msg = f"Unexpected execution error: {str(e)}"

        duration = time.perf_counter() - start_time
        return ReproducerExecution(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=round(duration, 4),
            command=cmd,
            timed_out=timed_out,
            error=error_msg,
        )

    @classmethod
    def verify_negative_baseline(
        cls,
        sandbox_dir: Optional[str] = None,
        script_path: Optional[str] = None,
        timeout: Optional[int] = None,
        **kwargs,
    ) -> BaselineValidationResult:
        """
        Negative Baseline Check:
        Executes the reproducer on unpatched code.
        ASSERTION: The script must fail (exit_code != 0) deterministically.
        If exit_code == 0, the reproducer is invalid (does not capture the bug); reject and refine.
        If the script times out, reject with status='TIMEOUT'.
        """
        sbox = sandbox_dir or kwargs.get("worktree_dir") or "."
        script = script_path or kwargs.get("reproducer_path")
        if not script and "reproducer_command" in kwargs:
            repro_cmd = kwargs["reproducer_command"]
            if isinstance(repro_cmd, list) and len(repro_cmd) > 1:
                script = repro_cmd[-1]
            elif isinstance(repro_cmd, str):
                script = repro_cmd.split()[-1]
        script = script or cls.DEFAULT_SCRIPT_NAME
        t_out = timeout if timeout is not None else kwargs.get("timeout_seconds", cls.DEFAULT_TIMEOUT_SEC)

        execution = cls.execute_script(sbox, script, timeout=t_out)

        if execution.timed_out:
            reason = (
                f"Negative Baseline Check FAILED: Reproducer script timed out after {t_out} seconds. "
                "A valid reproducer must fail deterministically with a non-zero exit code, not hang."
            )
            logger.warning(reason)
            return BaselineValidationResult(
                valid=False,
                exit_code=execution.exit_code,
                stdout=execution.stdout,
                stderr=execution.stderr,
                status="TIMEOUT",
                rejection_reason=reason,
                execution=execution,
            )

        if execution.error and execution.exit_code == -1:
            return BaselineValidationResult(
                valid=False,
                exit_code=execution.exit_code,
                stdout=execution.stdout,
                stderr=execution.stderr,
                status="EXECUTION_ERROR",
                rejection_reason=execution.error,
                execution=execution,
            )

        if execution.exit_code == 0:
            # Bug not reproduced: the test unexpectedly passed on buggy code
            reason = (
                "Negative Baseline Check FAILED: Reproducer script exited with code 0 on unpatched code. "
                "A valid reproducer must fail (exit_code != 0) before source code fixes begin."
            )
            logger.warning(reason)
            return BaselineValidationResult(
                valid=False,
                exit_code=0,
                stdout=execution.stdout,
                stderr=execution.stderr,
                status="INVALID_BASELINE",
                rejection_reason=reason,
                execution=execution,
            )

        # Baseline succeeded: reproducer accurately caught the defect
        logger.info(
            f"Negative Baseline Check PASSED: Reproducer failed as expected (exit_code={execution.exit_code})"
        )
        return BaselineValidationResult(
            valid=True,
            exit_code=execution.exit_code,
            stdout=execution.stdout,
            stderr=execution.stderr,
            status="VALID_BASELINE",
            rejection_reason=None,
            execution=execution,
        )

    # Method alias for API consistency
    validate_negative_baseline = verify_negative_baseline

    @classmethod
    def verify_positive(
        cls,
        sandbox_dir: str,
        script_path: str = DEFAULT_SCRIPT_NAME,
        regression_cmd: Optional[Union[str, list[str]]] = None,
        timeout: int = REGRESSION_TIMEOUT_SEC,
    ) -> PositiveVerificationResult:
        """
        Positive Verification Check:
        1. Run reproducer after patch. It must pass (exit_code == 0).
        2. Run project regression test suite (if provided/detected). It must remain green (exit_code == 0).
        """
        # 1. Reproducer check
        repro_exec = cls.execute_script(sandbox_dir, script_path, timeout=timeout)
        if repro_exec.exit_code != 0:
            fail_reason = (
                f"Positive verification failed: Reproducer script exited with non-zero code {repro_exec.exit_code}. "
                f"Stderr: {repro_exec.stderr[:500]}"
            )
            logger.warning(fail_reason)
            return PositiveVerificationResult(
                success=False,
                reproducer_passed=False,
                regression_passed=False,
                reproducer_execution=repro_exec,
                reason=fail_reason,
            )

        # 2. Regression test suite check
        regression_exec = None
        if regression_cmd:
            if isinstance(regression_cmd, list):
                cmd = regression_cmd
            else:
                try:
                    cmd = shlex.split(regression_cmd, posix=(os.name != "nt"))
                except Exception:
                    cmd = regression_cmd.split()

            start_t = time.perf_counter()
            try:
                env = os.environ.copy()
                existing_py = env.get("PYTHONPATH", "")
                env["PYTHONPATH"] = (
                    f"{sandbox_dir}{os.pathsep}{existing_py}" if existing_py else sandbox_dir
                )
                res = subprocess.run(
                    cmd,
                    cwd=sandbox_dir,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    env=env,
                )
                reg_dur = time.perf_counter() - start_t
                regression_exec = ReproducerExecution(
                    exit_code=res.returncode,
                    stdout=res.stdout,
                    stderr=res.stderr,
                    duration_seconds=round(reg_dur, 4),
                    command=cmd,
                )
            except subprocess.TimeoutExpired as te:
                reg_dur = time.perf_counter() - start_t
                regression_exec = ReproducerExecution(
                    exit_code=-1,
                    stdout=te.stdout or "" if isinstance(te.stdout, str) else "",
                    stderr=te.stderr or "" if isinstance(te.stderr, str) else "",
                    duration_seconds=round(reg_dur, 4),
                    command=cmd,
                    timed_out=True,
                    error=f"Regression test suite timed out after {timeout} seconds",
                )
            except Exception as e:
                reg_dur = time.perf_counter() - start_t
                regression_exec = ReproducerExecution(
                    exit_code=-1,
                    stdout="",
                    stderr="",
                    duration_seconds=round(reg_dur, 4),
                    command=cmd,
                    error=str(e),
                )

            if regression_exec.exit_code != 0:
                reg_reason = (
                    f"Positive verification failed: Regression test suite failed with exit code {regression_exec.exit_code}. "
                    f"Stderr: {regression_exec.stderr[:500]}"
                )
                logger.warning(reg_reason)
                return PositiveVerificationResult(
                    success=False,
                    reproducer_passed=True,
                    regression_passed=False,
                    reproducer_execution=repro_exec,
                    regression_execution=regression_exec,
                    reason=reg_reason,
                )

        logger.info("Positive verification PASSED: reproducer and regressions are green.")
        return PositiveVerificationResult(
            success=True,
            reproducer_passed=True,
            regression_passed=True,
            reproducer_execution=repro_exec,
            regression_execution=regression_exec,
        )

    @classmethod
    async def run_tdd_cycle(
        cls,
        sandbox_dir: str,
        coder_dispatch_fn: Callable[..., Any],
        issue_description: str = "",
        script_content: Optional[str] = None,
        script_name: str = DEFAULT_SCRIPT_NAME,
        regression_cmd: Optional[Union[str, list[str]]] = None,
        rollback_on_failure: bool = True,
    ) -> TDDExecutionResult:
        """
        Orchestrates an end-to-end SWE-bench negative-to-positive TDD cycle:
        1. Creates pre-baseline checkpoint.
        2. Authors reproducer script in sandbox worktree.
        3. Executes Negative Baseline Check (must fail).
        4. If negative check fails (script passes or errors), rejects and rolls back.
        5. Creates pre-patch checkpoint.
        6. Dispatches to Coder Agent.
        7. Executes Positive Verification Check (reproducer + full test suite must pass).
        8. If verification fails, automatically invokes SandboxRollbackManager.rollback().
        """
        if not os.path.isdir(sandbox_dir):
            return TDDExecutionResult(
                success=False,
                stage="NEGATIVE_BASELINE",
                error=f"Invalid sandbox directory: {sandbox_dir}",
            )

        # 1. Pre-baseline checkpoint
        baseline_cp_sha = SandboxRollbackManager.create_checkpoint(
            sandbox_dir, step_name="tdd_baseline_pre"
        )

        # 2. Author reproducer script
        if script_content:
            try:
                cls.author_reproducer(sandbox_dir, script_content, file_name=script_name)
            except Exception as e:
                return TDDExecutionResult(
                    success=False,
                    stage="NEGATIVE_BASELINE",
                    initial_checkpoint_sha=baseline_cp_sha,
                    error=f"Failed to author reproducer: {e}",
                )

        # 3. Negative Baseline Check
        baseline_res = cls.verify_negative_baseline(sandbox_dir, script_name)
        if not baseline_res.valid:
            logger.error(f"Negative baseline rejected: {baseline_res.rejection_reason}")
            rolled_back = False
            if rollback_on_failure and baseline_cp_sha:
                rolled_back = SandboxRollbackManager.rollback(sandbox_dir, baseline_cp_sha)
            return TDDExecutionResult(
                success=False,
                stage="NEGATIVE_BASELINE",
                baseline_result=baseline_res,
                initial_checkpoint_sha=baseline_cp_sha,
                rolled_back=rolled_back,
                error=baseline_res.rejection_reason,
            )

        # 4. Pre-patch checkpoint (includes the validated reproducer test)
        pre_patch_cp_sha = SandboxRollbackManager.create_checkpoint(
            sandbox_dir, step_name="tdd_pre_patch"
        )

        # 5. Coder Implementation Dispatch
        try:
            if inspect.iscoroutinefunction(coder_dispatch_fn):
                await coder_dispatch_fn(sandbox_dir)
            else:
                coder_dispatch_fn(sandbox_dir)
        except Exception as coder_err:
            logger.error(f"Coder agent dispatch error: {coder_err}")
            rolled_back = False
            if rollback_on_failure and pre_patch_cp_sha:
                rolled_back = SandboxRollbackManager.rollback(sandbox_dir, pre_patch_cp_sha)
            return TDDExecutionResult(
                success=False,
                stage="CODER_EXECUTION",
                baseline_result=baseline_res,
                initial_checkpoint_sha=baseline_cp_sha,
                pre_patch_checkpoint_sha=pre_patch_cp_sha,
                rolled_back=rolled_back,
                error=f"Coder implementation raised exception: {coder_err}",
            )

        # 6. Positive Verification Check
        verify_res = cls.verify_positive(
            sandbox_dir,
            script_path=script_name,
            regression_cmd=regression_cmd,
        )

        # 7. Rollback on verification failure
        if not verify_res.success:
            logger.warning(
                f"Positive verification failed. Rollback invoked: {verify_res.reason}"
            )
            rolled_back = False
            if rollback_on_failure and pre_patch_cp_sha:
                rolled_back = SandboxRollbackManager.rollback(sandbox_dir, pre_patch_cp_sha)
            return TDDExecutionResult(
                success=False,
                stage="POSITIVE_VERIFICATION",
                baseline_result=baseline_res,
                verification_result=verify_res,
                initial_checkpoint_sha=baseline_cp_sha,
                pre_patch_checkpoint_sha=pre_patch_cp_sha,
                rolled_back=rolled_back,
                error=verify_res.reason,
            )

        # 8. SOTA Success
        return TDDExecutionResult(
            success=True,
            stage="COMPLETED",
            baseline_result=baseline_res,
            verification_result=verify_res,
            initial_checkpoint_sha=baseline_cp_sha,
            pre_patch_checkpoint_sha=pre_patch_cp_sha,
            rolled_back=False,
            telemetry={
                "reproducer_duration": (
                    verify_res.reproducer_execution.duration_seconds
                    if verify_res.reproducer_execution
                    else 0.0
                ),
                "regression_duration": (
                    verify_res.regression_execution.duration_seconds
                    if verify_res.regression_execution
                    else 0.0
                ),
            },
        )
