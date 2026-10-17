"""
SandboxRollbackManager — Atomic Git Checkpoint & Rollback Engine for Kobits Sandboxes.

Creates clean git commit checkpoints before coder agents modify files,
and performs atomic hard rollbacks (`git reset --hard` + `git clean -fdx`)
when validation, test, or AST verification gates fail.
"""

import os
import shutil
import logging
import subprocess
from typing import Optional

logger = logging.getLogger("kobits.sandbox_rollback")


class SandboxRollbackManager:
    """
    Manages atomic git checkpoints and rollbacks in isolated sandbox directories.
    """

    def __init__(self, sandbox_dir: Optional[str] = None):
        self.sandbox_dir = sandbox_dir

    def checkpoint(self, step_name: str = "pre_task") -> Optional[str]:
        """Instance method to create a checkpoint in self.sandbox_dir."""
        if not self.sandbox_dir:
            return None
        return self.create_checkpoint(self.sandbox_dir, step_name)

    def revert(self, checkpoint_sha: str) -> bool:
        """Instance method to rollback self.sandbox_dir to checkpoint_sha."""
        if not self.sandbox_dir:
            return False
        return self.rollback(self.sandbox_dir, checkpoint_sha)

    @staticmethod
    def _is_git_repo(sandbox_dir: str) -> bool:
        if not sandbox_dir or not os.path.isdir(sandbox_dir):
            return False
        git_dir = os.path.join(sandbox_dir, ".git")
        if os.path.exists(git_dir):
            return True
        # Check via git rev-parse if inside worktree or submodule
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=sandbox_dir,
                capture_output=True,
                text=True,
                timeout=5,
            )
            return res.returncode == 0 and res.stdout.strip() == "true"
        except Exception:
            return False

    @classmethod
    def get_current_sha(cls, sandbox_dir: str) -> Optional[str]:
        """Return the current HEAD commit SHA of the sandbox workspace."""
        if not sandbox_dir or not cls._is_git_repo(sandbox_dir):
            return None
        try:
            res = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=sandbox_dir,
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            )
            return res.stdout.strip()
        except Exception as e:
            logger.debug(f"Failed to get current sha for {sandbox_dir}: {e}")
            return None

    @classmethod
    def create_checkpoint(cls, sandbox_dir: str, step_name: str = "pre_task") -> Optional[str]:
        """
        Creates an atomic git commit checkpoint of the sandbox workspace.
        Returns the resulting commit SHA, or None if the directory is not a git workspace.
        """
        if not sandbox_dir or not cls._is_git_repo(sandbox_dir):
            logger.debug(f"Sandbox {sandbox_dir} is not a git repository. Checkpoint skipped.")
            return None

        clean_step = str(step_name).strip() or "pre_task"
        commit_msg = f"checkpoint: {clean_step}"

        # 1. Commit locally if sandbox_dir is a git workspace
        local_sha = None
        if sandbox_dir and cls._is_git_repo(sandbox_dir):
            git_cmd = [
                "git",
                "-c", "user.name=Kobits",
                "-c", "user.email=bot@kobits.ai",
            ]
            try:
                subprocess.run(
                    git_cmd + ["add", "-A"],
                    cwd=sandbox_dir,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=15,
                )
                subprocess.run(
                    git_cmd + ["reset", "-q", "--", ".kobits_rootfs", ".kobits_sandbox.json"],
                    cwd=sandbox_dir,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                subprocess.run(
                    git_cmd + ["commit", "--allow-empty", "-m", commit_msg],
                    cwd=sandbox_dir,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=15,
                )
                local_sha = cls.get_current_sha(sandbox_dir)
                logger.info(f"Created local git checkpoint '{clean_step}' -> {local_sha} in {sandbox_dir}")
            except Exception as e:
                logger.debug(f"Local checkpoint error in {sandbox_dir}: {e}")

        # 2. Cloud MicroVM remote git checkpoint routing for zero-local footprint
        try:
            from backend.services.cloud_sandbox import CloudMicroVMSandbox
            from backend.services.sandbox_manager import SandboxManager
            if CloudMicroVMSandbox.is_configured() or CloudMicroVMSandbox.is_cloud_native():
                session = SandboxManager.get_session_by_dir(sandbox_dir) if sandbox_dir else None
                if session is not None or not sandbox_dir or str(sandbox_dir).startswith("/workspace"):
                    sid = session.session_id if session else "default"
                    cloud_sb = CloudMicroVMSandbox.get_or_create(sid)
                    res = cloud_sb.run_command(
                        f"git -C /workspace reset -q -- .kobits* 2>/dev/null || true; "
                        f"git -C /workspace add -A && "
                        f"git -C /workspace -c user.name=Kobits -c user.email=bot@kobits.ai commit --allow-empty -m '{commit_msg}' && "
                        f"git -C /workspace rev-parse HEAD"
                    )
                    if res.get("exit_code") == 0 and res.get("stdout"):
                        cloud_sha = res["stdout"].strip().splitlines()[-1]
                        if len(cloud_sha) == 40:
                            logger.info(f"Created cloud microvm git checkpoint '{clean_step}' -> {cloud_sha}")
                            return local_sha or cloud_sha
        except Exception as e:
            logger.debug(f"Cloud MicroVM checkpoint error: {e}")

        return local_sha

    @classmethod
    def rollback(cls, sandbox_dir: str, checkpoint_sha: str) -> bool:
        """
        Performs an atomic hard rollback to the specified checkpoint SHA,
        reverting all file modifications and cleaning untracked files (`git reset --hard` + `git clean -fdx`).
        """
        if not checkpoint_sha or not isinstance(checkpoint_sha, str):
            logger.warning(f"Invalid checkpoint SHA '{checkpoint_sha}' for rollback.")
            return False

        target_sha = checkpoint_sha.strip()
        local_ok = False

        # 1. Rollback locally if sandbox_dir is a git workspace
        if sandbox_dir and cls._is_git_repo(sandbox_dir):
            try:
                subprocess.run(["git", "reset", "--hard", target_sha], cwd=sandbox_dir, capture_output=True, check=True, timeout=10)
                subprocess.run(["git", "clean", "-fdx"], cwd=sandbox_dir, capture_output=True, check=True, timeout=10)
                local_ok = True
                logger.info(f"Successfully rolled back local sandbox {sandbox_dir} to {target_sha}")
            except Exception as e:
                logger.debug(f"Local rollback error in {sandbox_dir}: {e}")

        # 2. Cloud MicroVM remote rollback routing for zero-local footprint
        try:
            from backend.services.cloud_sandbox import CloudMicroVMSandbox
            from backend.services.sandbox_manager import SandboxManager
            if CloudMicroVMSandbox.is_configured() or CloudMicroVMSandbox.is_cloud_native():
                session = SandboxManager.get_session_by_dir(sandbox_dir) if sandbox_dir else None
                if session is not None or not sandbox_dir or str(sandbox_dir).startswith("/workspace"):
                    sid = session.session_id if session else "default"
                    cloud_sb = CloudMicroVMSandbox.get_or_create(sid)
                    res = cloud_sb.run_command(
                        f"git -C /workspace reset --hard {target_sha} 2>/dev/null || git -C /workspace reset --hard HEAD; "
                        f"git -C /workspace clean -fdx"
                    )
                    if res.get("exit_code") == 0:
                        logger.info(f"Successfully rolled back cloud MicroVM /workspace to {target_sha}")
                        return True
        except Exception as e:
            logger.debug(f"Cloud MicroVM rollback error: {e}")

        return local_ok

    @classmethod
    def checkpoint_and_apply(
        cls,
        sandbox_dir: str,
        apply_fn,
        verify_fn=None,
        step_name: str = "pre_file_write",
    ) -> dict:
        """
        Creates an automatic git checkpoint before an agent writes a file change,
        applies the change, validates syntax and optional verification tests,
        and immediately rolls back to the clean checkpoint if syntax is broken or tests fail.
        """
        from backend.services.sandbox_manager import SandboxManager

        checkpoint_sha = cls.create_checkpoint(sandbox_dir, step_name=step_name)
        try:
            apply_res = apply_fn()
        except Exception as exc:
            rolled = cls.rollback(sandbox_dir, checkpoint_sha) if checkpoint_sha else False
            return {
                "success": False,
                "rolled_back": rolled,
                "checkpoint_sha": checkpoint_sha,
                "reason": f"Exception during file write: {exc}",
            }

        # Check if apply_res reported a syntax error
        if isinstance(apply_res, dict) and apply_res.get("syntax_valid") is False:
            rolled = cls.rollback(sandbox_dir, checkpoint_sha) if checkpoint_sha else False
            return {
                "success": False,
                "rolled_back": rolled,
                "checkpoint_sha": checkpoint_sha,
                "reason": f"Syntax validation failed: {apply_res.get('syntax_error')}",
                "apply_result": apply_res,
            }

        # Scan modified Python files in sandbox_dir for broken syntax
        if sandbox_dir and os.path.isdir(sandbox_dir):
            for root, dirs, files in os.walk(sandbox_dir):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__", "venv", ".venv")]
                for fname in files:
                    if fname.endswith(".py"):
                        fpath = os.path.join(root, fname)
                        rel = os.path.relpath(fpath, sandbox_dir).replace("\\", "/")
                        try:
                            with open(fpath, "r", encoding="utf-8") as rf:
                                syn = SandboxManager._validate_file_syntax(rel, rf.read())
                            if not syn.get("syntax_valid", True):
                                rolled = cls.rollback(sandbox_dir, checkpoint_sha) if checkpoint_sha else False
                                return {
                                    "success": False,
                                    "rolled_back": rolled,
                                    "checkpoint_sha": checkpoint_sha,
                                    "reason": f"Broken syntax in {rel}: {syn.get('syntax_error')}",
                                }
                        except Exception:
                            pass

        # Run optional test verification callback
        if callable(verify_fn):
            ver_res = verify_fn()
            if isinstance(ver_res, dict) and "exit_code" in ver_res:
                ver_ok = ver_res.get("exit_code", 1) == 0
            elif isinstance(ver_res, tuple) and len(ver_res) >= 1:
                ver_ok = bool(ver_res[0])
            else:
                ver_ok = bool(ver_res)
            if not ver_ok:
                rolled = cls.rollback(sandbox_dir, checkpoint_sha) if checkpoint_sha else False
                return {
                    "success": False,
                    "rolled_back": rolled,
                    "checkpoint_sha": checkpoint_sha,
                    "reason": "Verification test failed after file change",
                    "verification": ver_res,
                }

        return {
            "success": True,
            "rolled_back": False,
            "checkpoint_sha": checkpoint_sha,
            "apply_result": apply_res,
        }
