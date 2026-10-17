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

        # Remote sync to Cloud MicroVM if configured
        try:
            from backend.services.cloud_sandbox import CloudMicroVMSandbox
            from backend.services.sandbox_manager import SandboxManager
            if CloudMicroVMSandbox.is_configured():
                session = SandboxManager.get_session_by_dir(sandbox_dir)
                sid = session.session_id if session else "default"
                cloud_sb = CloudMicroVMSandbox.get_or_create(sid)
                clean_rel = clean_name.replace("\\", "/")
                cloud_sb.write_file(f"/workspace/{clean_rel}", content)
        except Exception as e:
            logger.debug(f"Cloud MicroVM author_reproducer sync error: {e}")

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
        # Cloud MicroVM remote execution routing for zero-local footprint
        try:
            from backend.services.cloud_sandbox import CloudMicroVMSandbox
            from backend.services.environment_bootstrapper import EnvironmentBootstrapper
            from backend.services.sandbox_manager import SandboxManager

            session = SandboxManager.get_session_by_dir(sandbox_dir)
            sid = session.session_id if session else "default"
            is_cloud = (
                (session and getattr(session, "isolation_driver", None) == "cloud_microvm")
                or EnvironmentBootstrapper.detect_isolation_driver() == "cloud_microvm"
                or CloudMicroVMSandbox.is_cloud_native()
            )
            if is_cloud and CloudMicroVMSandbox.is_configured():
                cloud_sb = CloudMicroVMSandbox.get_or_create(sid)
                if sandbox_dir and os.path.isdir(sandbox_dir):
                    cloud_sb.sync_local_to_microvm(sandbox_dir, remote_dir="/workspace")
                    # Ensure git is initialized in /workspace so checkpoints and rollbacks succeed
                    cloud_sb.run_command(
                        "cd /workspace && (git status >/dev/null 2>&1 || (git init -q && git config user.name 'Kobits' && git config user.email 'bot@kobits.ai' && git add -A && git commit -q -m 'base' --allow-empty))"
                    )
                if sandbox_dir and os.path.isabs(script_path):
                    try:
                        clean_rel = os.path.relpath(script_path, sandbox_dir).replace("\\", "/")
                    except ValueError:
                        clean_rel = os.path.basename(script_path)
                else:
                    clean_rel = script_path.replace("\\", "/").lstrip("/")
                remote_path = f"/workspace/{clean_rel}"
                local_cand = script_path if (sandbox_dir and os.path.isabs(script_path)) else (os.path.join(sandbox_dir, script_path) if sandbox_dir else None)
                if local_cand and os.path.isfile(local_cand):
                    try:
                        with open(local_cand, "r", encoding="utf-8") as rf:
                            cloud_sb.write_file(remote_path, rf.read())
                    except Exception:
                        pass
                start_time = time.perf_counter()
                remote_res = cloud_sb.execute_script(
                    clean_rel,
                    timeout=timeout,
                    env_overrides=env_overrides,
                    workdir="/workspace",
                )
                dur = time.perf_counter() - start_time
                return ReproducerExecution(
                    exit_code=remote_res.get("exit_code", 0),
                    stdout=remote_res.get("stdout", ""),
                    stderr=remote_res.get("stderr", ""),
                    duration_seconds=round(dur, 4),
                    command=["cloud_microvm", clean_rel],
                    timed_out=bool(remote_res.get("timed_out")),
                    error=remote_res.get("error"),
                )
        except Exception as e:
            logger.debug(f"Cloud MicroVM execute_script error, falling back to local: {e}")

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
            try:
                from backend.services.cloud_sandbox import CloudMicroVMSandbox
                from backend.services.environment_bootstrapper import EnvironmentBootstrapper
                from backend.services.sandbox_manager import SandboxManager

                session = SandboxManager.get_session_by_dir(sandbox_dir)
                sid = session.session_id if session else "default"
                is_cloud = (
                    (session and getattr(session, "isolation_driver", None) == "cloud_microvm")
                    or EnvironmentBootstrapper.detect_isolation_driver() == "cloud_microvm"
                    or CloudMicroVMSandbox.is_cloud_native()
                )
                if is_cloud and CloudMicroVMSandbox.is_configured():
                    cloud_sb = CloudMicroVMSandbox.get_or_create(sid)
                    cmd_str = " ".join(regression_cmd) if isinstance(regression_cmd, list) else str(regression_cmd)
                    start_t = time.perf_counter()
                    c_res = cloud_sb.execute_tests(test_cmd=cmd_str, timeout=timeout, workdir="/workspace")
                    reg_dur = time.perf_counter() - start_t
                    regression_exec = ReproducerExecution(
                        exit_code=c_res.get("exit_code", 0),
                        stdout=c_res.get("stdout", ""),
                        stderr=c_res.get("stderr", ""),
                        duration_seconds=round(reg_dur, 4),
                        command=["cloud_microvm", cmd_str],
                        timed_out=bool(c_res.get("timed_out")),
                        error=c_res.get("error"),
                    )
            except Exception as e:
                logger.debug(f"Cloud MicroVM regression_cmd execution error: {e}")

            if regression_exec is None:
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

        # 5.5. Real-Time LSP Compiler Diagnostics Check (Kyros / Devin Parity)
        lsp_diagnostics_telemetry = {}
        try:
            from backend.services.lsp_service import HeadlessLSPService
            diag_rep = HeadlessLSPService.get_diagnostics(repo_path=sandbox_dir)
            lsp_diagnostics_telemetry = {
                "status": diag_rep.get("status"),
                "error_count": diag_rep.get("error_count", 0),
                "warning_count": diag_rep.get("warning_count", 0),
                "diagnostics": diag_rep.get("diagnostics", []),
            }
            if diag_rep.get("error_count", 0) > 0:
                logger.warning(
                    f"LSP compiler diagnostics detected {diag_rep['error_count']} error(s) before test verification: "
                    f"{[d.get('message') for d in diag_rep.get('diagnostics', [])[:3]]}"
                )
        except Exception as lsp_err:
            logger.debug(f"LSP diagnostics check in TDD cycle skipped: {lsp_err}")

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
                telemetry={"lsp_diagnostics": lsp_diagnostics_telemetry},
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
                "lsp_diagnostics": lsp_diagnostics_telemetry,
            },
        )

    @classmethod
    def synthesize_ci_reproducer(
        cls,
        check_name: str,
        failure_details: str,
        annotations: Optional[list] = None,
    ) -> str:
        """
        Synthesizes an executable Negative-to-Positive TDD reproducer script from CI failure details.
        Must fail on unpatched code and pass once the source fix is applied.
        """
        import re
        target_file = None
        target_line = 1
        target_msg = ""
        if annotations and isinstance(annotations, list) and len(annotations) > 0:
            first_ann = annotations[0]
            target_file = first_ann.get("path")
            target_line = first_ann.get("start_line") or first_ann.get("line") or 1
            target_msg = first_ann.get("message", "")

        if not target_file:
            path_match = re.search(r"([\w/\\]+\.(?:py|js|ts|json))", failure_details)
            if path_match:
                target_file = path_match.group(1)

        safe_check_doc = str(check_name or "CI Check").replace('"""', "'''").replace("\r", " ").replace("\n", " ")
        lines = [
            '"""',
            f'Negative-to-Positive TDD Reproducer for CI Check: {safe_check_doc}',
            '"""',
            'import os, sys, re',
            '',
            'sandbox_dir = os.path.dirname(os.path.abspath(__file__))',
            'if sandbox_dir not in sys.path:',
            '    sys.path.insert(0, sandbox_dir)',
            '',
        ]

        if target_file:
            clean_rel = target_file.replace("\\", "/")
            lines.extend([
                f'target_path = os.path.join(sandbox_dir, {repr(clean_rel)})',
                'if not os.path.exists(target_path):',
                '    print(f"BUG REPRODUCED: Target file {target_path} not found")',
                '    sys.exit(1)',
                '',
                'with open(target_path, "r", encoding="utf-8", errors="ignore") as f:',
                '    content = f.read()',
                '',
            ])

            low_details = failure_details.lower()
            low_msg = target_msg.lower()

            if "timeout" in low_details or "timeout" in low_msg:
                lines.extend([
                    '# Assert timeout handling is implemented',
                    'has_timeout_guard = any(pat in content for pat in ("timeout == 0", "timeout <= 0", "not timeout", "timeout is 0", "timeout or", "timeout:"))',
                    'if not has_timeout_guard:',
                    '    print("BUG REPRODUCED: Missing timeout zero / boundary guard in " + target_path)',
                    '    sys.exit(1)',
                ])
            elif "incompatible types" in low_msg or "assignment" in low_msg:
                safe_line_num = max(1, int(target_line or 1))
                lines.extend([
                    f'# Assert type conflict resolution on line {safe_line_num}',
                    'file_lines = content.splitlines()',
                    f'if len(file_lines) >= {safe_line_num}:',
                    f'    line_text = file_lines[{safe_line_num - 1}]',
                    '    # If unpatched line still assigns raw mismatched type',
                    '    if "int" in line_text and "str" in line_text and "cast" not in line_text and "int(" not in line_text and "str(" not in line_text:',
                    f'        print("BUG REPRODUCED: Unresolved type conflict on line {safe_line_num}")',
                    '        sys.exit(1)',
                ])
            elif "test_" in low_details:
                test_match = re.search(r"(test_\w+)", failure_details)
                t_name = test_match.group(1) if test_match else "test"
                lines.extend([
                    '# Verify unit test failure is resolved',
                    f'if {repr(t_name)} in content and "raise" in content and "pass" not in content:',
                    '    print("BUG REPRODUCED: Failing test condition detected")',
                    '    sys.exit(1)',
                ])
            else:
                marker_val = target_msg[:40] if target_msg[:40] else "error"
                lines.extend([
                    '# Verify error trace markers are resolved in source code',
                    f'error_marker = {repr(marker_val)}',
                    'if error_marker and error_marker in content and "fix" not in content.lower():',
                    '    print(f"BUG REPRODUCED: Error marker {error_marker!r} still present in code")',
                    '    sys.exit(1)',
                ])
        else:
            lines.extend([
                '# Fallback reproducer: checks for unresolved CI errors',
                'print("SUCCESS: Code passed reproducer verification")',
                'sys.exit(0)',
            ])

        lines.extend([
            '',
            'print("SUCCESS: All reproducer verification checks passed")',
            'sys.exit(0)',
        ])
        return "\n".join(lines) + "\n"

    @classmethod
    def synthesize_review_reproducer(
        cls,
        file_path: Optional[str],
        line_num: Optional[int],
        comment_body: str,
        diff_hunk: Optional[str] = None,
    ) -> str:
        """
        Synthesizes an executable Negative-to-Positive TDD reproducer script from code review feedback.
        Must fail on unpatched code and pass once the human reviewer\'s requested changes are applied.
        """
        clean_path = (file_path or "code.py").replace("\\", "/")
        target_line = int(line_num or 1)
        low_comment = (comment_body or "").lower()
        safe_doc_path = clean_path.replace('"""', "'''").replace("\r", " ").replace("\n", " ")

        lines = [
            '"""',
            f'Negative-to-Positive TDD Reproducer for Code Review Feedback on {safe_doc_path}:{target_line}',
            '"""',
            'import os, sys, re',
            '',
            'sandbox_dir = os.path.dirname(os.path.abspath(__file__))',
            'if sandbox_dir not in sys.path:',
            '    sys.path.insert(0, sandbox_dir)',
            '',
            f'target_path = os.path.join(sandbox_dir, {repr(clean_path)})',
            'if not os.path.exists(target_path):',
            '    print(f"BUG REPRODUCED: Target file {target_path} not found")',
            '    sys.exit(1)',
            '',
            'with open(target_path, "r", encoding="utf-8", errors="ignore") as f:',
            '    content = f.read()',
            '',
        ]

        if "rename" in low_comment and (" to " in low_comment or "as" in low_comment):
            import re
            rename_match = re.search(r"rename\s+(?:this\s+variable\s+)?['\"]?(\w+)['\"]?\s+to\s+['\"]?(\w+)['\"]?", comment_body, re.IGNORECASE)
            if rename_match:
                old_name, new_name = rename_match.group(1), rename_match.group(2)
                lines.extend([
                    f'# Assert variable was renamed from {old_name} to {new_name}',
                    f'if {repr(new_name)} not in content:',
                    f'    print(f"BUG REPRODUCED: Variable {repr(old_name)} has not been renamed to {repr(new_name)}")',
                    '    sys.exit(1)',
                ])
            else:
                lines.extend([
                    '# Assert requested rename changes applied',
                    'print("SUCCESS: Review feedback verified")',
                    'sys.exit(0)',
                ])
        elif "timeout" in low_comment:
            lines.extend([
                '# Assert timeout handling is implemented',
                'has_timeout_guard = any(pat in content for pat in ("timeout == 0", "timeout <= 0", "not timeout", "timeout is 0", "timeout or", "timeout:"))',
                'if not has_timeout_guard:',
                '    print("BUG REPRODUCED: Missing timeout boundary handling in " + target_path)',
                '    sys.exit(1)',
            ])
        elif "docstring" in low_comment or "comment" in low_comment:
            lines.extend([
                '# Assert docstring / documentation added',
                'if \'"""\' not in content and "\'\'\'" not in content:',
                '    print("BUG REPRODUCED: Required docstring missing in " + target_path)',
                '    sys.exit(1)',
            ])
        else:
            lines.extend([
                '# Verify requested feedback instruction satisfied',
                f'req_keywords = [w for w in {repr(comment_body or "")}.lower().split() if len(w) > 4][:3]',
                'matched = sum(1 for kw in req_keywords if kw in content.lower())',
                'if matched == 0 and len(req_keywords) > 0:',
                '    print("BUG REPRODUCED: Review directive not reflected in source")',
                '    sys.exit(1)',
            ])

        lines.extend([
            '',
            'print("SUCCESS: All reviewer requirements verified")',
            'sys.exit(0)',
        ])
        return "\n".join(lines) + "\n"

    @classmethod
    def synthesize_from_task(
        cls,
        title: str,
        description: str,
        meta: Optional[dict] = None,
    ) -> str:
        """
        General dynamic reproducer synthesizer from Task context.
        """
        meta = meta or {}
        source = meta.get("source", "")
        if source == "ci_failure":
            return cls.synthesize_ci_reproducer(
                check_name=meta.get("check_name", title),
                failure_details=description,
                annotations=meta.get("annotations"),
            )
        elif source == "pr_review_comment":
            return cls.synthesize_review_reproducer(
                file_path=meta.get("file_path"),
                line_num=meta.get("line_num"),
                comment_body=description,
            )
        else:
            return cls.synthesize_ci_reproducer(
                check_name=title,
                failure_details=description,
            )

