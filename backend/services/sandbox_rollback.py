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
        if not cls._is_git_repo(sandbox_dir):
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
        if not cls._is_git_repo(sandbox_dir):
            logger.debug(f"Sandbox {sandbox_dir} is not a git repository. Checkpoint skipped.")
            return None

        clean_step = str(step_name).strip() or "pre_task"
        commit_msg = f"checkpoint: {clean_step}"

        git_cmd = [
            "git",
            "-c", "user.name=Kobits",
            "-c", "user.email=bot@kobits.ai",
        ]

        try:
            # 1. Stage all changes
            subprocess.run(
                git_cmd + ["add", "-A"],
                cwd=sandbox_dir,
                capture_output=True,
                text=True,
                check=True,
                timeout=15,
            )

            # 2. Unstage internal metadata files if present
            subprocess.run(
                git_cmd + ["reset", "-q", "--", ".kobits_rootfs", ".kobits_sandbox.json"],
                cwd=sandbox_dir,
                capture_output=True,
                text=True,
                timeout=5,
            )

            # 3. Commit with allow-empty to guarantee a checkpoint SHA even if clean
            subprocess.run(
                git_cmd + ["commit", "--allow-empty", "-m", commit_msg],
                cwd=sandbox_dir,
                capture_output=True,
                text=True,
                check=True,
                timeout=15,
            )

            sha = cls.get_current_sha(sandbox_dir)
            logger.info(f"Created git checkpoint '{clean_step}' -> {sha} in {sandbox_dir}")
            return sha
        except subprocess.CalledProcessError as cpe:
            logger.warning(f"Failed to create git checkpoint in {sandbox_dir}: {cpe.stderr or cpe.stdout}")
            return None
        except Exception as e:
            logger.warning(f"Unexpected error creating checkpoint in {sandbox_dir}: {e}")
            return None

    @classmethod
    def rollback(cls, sandbox_dir: str, checkpoint_sha: str) -> bool:
        """
        Performs an atomic hard rollback to the specified checkpoint SHA,
        reverting all file modifications and cleaning untracked files (`git reset --hard` + `git clean -fdx`).
        """
        if not cls._is_git_repo(sandbox_dir):
            logger.warning(f"Cannot rollback: {sandbox_dir} is not a valid git repository.")
            return False

        if not checkpoint_sha or not isinstance(checkpoint_sha, str):
            logger.warning(f"Invalid checkpoint SHA '{checkpoint_sha}' for rollback.")
            return False

        target_sha = checkpoint_sha.strip()

        try:
            # 1. Hard reset to checkpoint
            res_reset = subprocess.run(
                ["git", "reset", "--hard", target_sha],
                cwd=sandbox_dir,
                capture_output=True,
                text=True,
                check=True,
                timeout=20,
            )

            # 2. Clean untracked files and directories
            res_clean = subprocess.run(
                ["git", "clean", "-fdx"],
                cwd=sandbox_dir,
                capture_output=True,
                text=True,
                check=True,
                timeout=20,
            )

            logger.info(f"Successfully rolled back sandbox {sandbox_dir} to checkpoint {target_sha}")
            return True
        except subprocess.CalledProcessError as cpe:
            logger.error(
                f"Rollback failed in {sandbox_dir} to {target_sha}: {cpe.stderr or cpe.stdout}"
            )
            return False
        except Exception as e:
            logger.error(f"Unexpected error during rollback in {sandbox_dir}: {e}")
            return False
