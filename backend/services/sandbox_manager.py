"""
SandboxManager — Creates isolated git-branched workspaces for agents to
safely modify code. Each build gets its own branch so changes can be
reviewed via PR before merging.\n"""

import os
import re
import shutil
import subprocess
import uuid
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class SandboxSession:
    """
    Represents a single isolated Kyros-grade Container / MicroVM / Virtual-Rootfs
    sandbox workspace tied to a dedicated Git branch and RL rollout trajectory.
    """

    def __init__(
        self,
        session_id: str,
        project_root: str,
        branch_name: str,
        sandbox_dir: str,
        base_commit_sha: Optional[str] = None,
        vm_id: Optional[str] = None,
        isolation_driver: Optional[str] = None,
        resource_limits: Optional[dict] = None,
    ):
        from backend.services.environment_bootstrapper import EnvironmentBootstrapper

        self.session_id = session_id
        self.project_root = project_root
        self.branch_name = branch_name
        self.sandbox_dir = sandbox_dir
        self.base_commit_sha = base_commit_sha
        self.files_changed: list[str] = []
        self.created_at = datetime.now(timezone.utc)
        self.status = "ACTIVE"  # ACTIVE | COMMITTED | PR_CREATED | FAILED
        self.isolation_driver = isolation_driver or EnvironmentBootstrapper.detect_isolation_driver()
        self.vm_id = vm_id or f"kobits-vm-{session_id}"
        self.resource_limits = resource_limits or {
            "memory_mb": EnvironmentBootstrapper.DEFAULT_MEMORY_LIMIT_MB,
            "cpu_cores": EnvironmentBootstrapper.DEFAULT_CPU_CORES,
            "pids_limit": EnvironmentBootstrapper.DEFAULT_PIDS_LIMIT,
        }
        self.command_history: list[dict] = []
        self.installed_packages: list[str] = []

    def to_dict(self):
        return {
            "session_id": self.session_id,
            "project_root": self.project_root,
            "branch_name": self.branch_name,
            "sandbox_dir": self.sandbox_dir,
            "base_commit_sha": self.base_commit_sha,
            "files_changed": self.files_changed,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "vm_id": self.vm_id,
            "isolation_driver": self.isolation_driver,
            "resource_limits": self.resource_limits,
            "command_history": self.command_history[-100:],
            "installed_packages": self.installed_packages,
        }


class SandboxManager:
    @staticmethod
    def _normalize_sandbox_rel_path(rel_path: str) -> str:
        """Translate container /workspace paths to sandbox-relative paths."""
        if not rel_path:
            return "."
        clean = str(rel_path).strip()
        norm_slashes = clean.replace("\\", "/")
        if norm_slashes in ("/workspace", "/workspace/"):
            return "."
        if norm_slashes.startswith("/workspace/"):
            return norm_slashes[len("/workspace/"):]
        return clean

    @classmethod
    def _find_git_repos(cls, root_dir: str) -> list[str]:
        if os.path.exists(os.path.join(root_dir, ".git")):
            return [root_dir]
        repos = []
        try:
            for entry in os.listdir(root_dir):
                if entry.startswith(".kobits_"):
                    continue
                path = os.path.join(root_dir, entry)
                if os.path.isdir(path) and os.path.exists(os.path.join(path, ".git")):
                    repos.append(path)
        except OSError:
            pass
        return repos

    @classmethod
    def _init_git_excludes(cls, repo_dir: str):
        if not os.path.exists(os.path.join(repo_dir, ".git")):
            return
        _run_git(repo_dir, "config", "user.name", "Kobits Agent")
        _run_git(repo_dir, "config", "user.email", "agent@kobits.local")
        exclude_dir = os.path.join(repo_dir, ".git", "info")
        exclude_file = os.path.join(exclude_dir, "exclude")
        if not os.path.isdir(exclude_dir):
            try:
                gp = subprocess.run(
                    ["git", "rev-parse", "--git-path", "info/exclude"],
                    cwd=repo_dir, capture_output=True, text=True, timeout=5
                ).stdout.strip()
                if gp:
                    exclude_file = gp if os.path.isabs(gp) else os.path.join(repo_dir, gp)
                    os.makedirs(os.path.dirname(exclude_file), exist_ok=True)
            except Exception:
                return
        try:
            existing = ""
            if os.path.exists(exclude_file):
                with open(exclude_file, "r", encoding="utf-8", errors="ignore") as rf:
                    existing = rf.read()
            required_excludes = [
                ".kobits_sandbox.json",
                ".kobits_rootfs/",
                ".kobits_rootfs",
                ".kobits_run.sh",
                ".kobits_run.bat",
                ".kobits_env.Dockerfile",
                "__pycache__/",
                "*.pyc",
            ]
            missing = [item for item in required_excludes if item not in existing]
            if missing:
                with open(exclude_file, "a", encoding="utf-8") as f:
                    f.write("\n" + "\n".join(missing) + "\n")
        except Exception:
            pass

    @classmethod
    def _seed_local_workspace(cls, dest_dir: str, source_dir: Optional[str] = None):
        """Seed a local CLI sandbox with the active workspace's source files."""
        repo_root = Path(__file__).resolve().parent.parent.parent
        target_ws = source_dir or os.environ.get("KOBITS_TARGET_WORKSPACE")
        ignore = shutil.ignore_patterns(
            "__pycache__", "*.pyc", ".git", "node_modules", "sandboxes",
            "*.db", "*.sqlite3", ".env", "venv", ".venv", "synth_repo_*",
            ".pytest_cache", "requirements.txt", "claude_written_code", ".kobits_rootfs"
        )
        if target_ws:
            ws_path = Path(target_ws).resolve()
            if ws_path.is_dir() and ws_path != repo_root and ws_path != Path.home().resolve():
                shutil.copytree(str(ws_path), dest_dir, ignore=ignore, dirs_exist_ok=True)
                return

        for subdir in ("backend", "js"):
            src = repo_root / subdir
            if src.is_dir():
                shutil.copytree(str(src), os.path.join(dest_dir, subdir), ignore=ignore, dirs_exist_ok=True)
        for top_file in ("kobits_cli.py",):
            src_file = repo_root / top_file
            if src_file.is_file():
                shutil.copy2(str(src_file), os.path.join(dest_dir, top_file))

    @classmethod
    def _create_git_worktree_sandbox(cls, sandbox_dir: str, branch_name: str, source_dir: Optional[str] = None) -> bool:
        """
        Create an isolated Git Worktree sandbox (`git worktree add -B <branch> <sandbox_dir> HEAD`)
        directly from a Git repository or cached worktree anchor (matching Kyros).
        """
        import hashlib
        repo_root = Path(__file__).resolve().parent.parent.parent
        target_ws = source_dir or os.environ.get("KOBITS_TARGET_WORKSPACE")
        if not target_ws or Path(target_ws).resolve() == repo_root:
            return False
        ws_path = Path(target_ws).resolve()

        # 1. If the target workspace is already a valid Git repository with a HEAD commit, use it directly
        anchor_dir = None
        if (ws_path / ".git").exists():
            head_chk = subprocess.run(
                ["git", "rev-parse", "--verify", "HEAD"],
                cwd=str(ws_path), capture_output=True, text=True, timeout=5
            )
            if head_chk.returncode == 0:
                anchor_dir = str(ws_path)

        # 2. Otherwise maintain a fast cached Git worktree anchor inside SANDBOX_ROOT
        if not anchor_dir:
            ws_hash = hashlib.sha1(str(ws_path).encode("utf-8")).hexdigest()[:10]
            anchor_dir = os.path.join(cls.SANDBOX_ROOT, f".worktree_anchor_{ws_hash}")
            head_chk = (
                subprocess.run(
                    ["git", "rev-parse", "--verify", "HEAD"],
                    cwd=anchor_dir, capture_output=True, text=True, timeout=5
                )
                if os.path.exists(os.path.join(anchor_dir, ".git"))
                else None
            )
            if not head_chk or head_chk.returncode != 0:
                shutil.rmtree(anchor_dir, ignore_errors=True)
                os.makedirs(anchor_dir, exist_ok=True)
                cls._seed_local_workspace(anchor_dir, source_dir=str(ws_path))
                _run_git(anchor_dir, "init")
                cls._init_git_excludes(anchor_dir)
                _run_git(anchor_dir, "add", "-A")
                _run_git(anchor_dir, "commit", "--allow-empty", "-m", "Initial worktree anchor")

        # Prune stale worktrees and remove empty target dir before `git worktree add`
        _run_git(anchor_dir, "worktree", "prune")
        if os.path.exists(sandbox_dir):
            shutil.rmtree(sandbox_dir, ignore_errors=True)

        res = subprocess.run(
            ["git", "worktree", "add", "--force", "-B", branch_name, sandbox_dir, "HEAD"],
            cwd=anchor_dir, capture_output=True, text=True, timeout=15
        )
        if res.returncode == 0 and os.path.exists(os.path.join(sandbox_dir, ".git")):
            cls._init_git_excludes(sandbox_dir)
            return True
        return False

    """
    Manages sandboxed workspaces for AI agents.

    Workflow:
      1. create_sandbox()  — creates an isolated Git worktree or clone on a dedicated branch
      2. Agent writes files inside the sandbox via repository.write
      3. commit_changes()  — stages + commits all modifications
      4. create_pr()       — pushes branch and opens a PR (or returns a diff)
      5. cleanup()         — removes the temporary workspace
    """

    # Base directory for all sandboxes
    SANDBOX_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "sandboxes")

    # Keep track of active sessions
    _sessions: dict[str, SandboxSession] = {}

    @classmethod
    def create_sandbox(
        cls, 
        project_root: str, 
        task_description: str = "", 
        clone_url: str = None, 
        base_commit_sha: str = None, 
        branch_name: str = None,
        repositories: list = None
    ) -> SandboxSession:
        """
        Create an isolated sandbox workspace from the given project root or clone URL.
        Supports multi-repository choreography via 'repositories' list.
        """
        session_id = str(uuid.uuid4())[:12]
        
        if not branch_name:
            safe_desc = "".join(c if c.isalnum() or c in "-_" else "-" for c in task_description[:40]).strip("-")
            branch_name = f"kobits/agent-{safe_desc}-{session_id[:6]}"

        os.makedirs(cls.SANDBOX_ROOT, exist_ok=True)
        sandbox_dir = os.path.join(cls.SANDBOX_ROOT, f"sandbox-{session_id}")
        os.makedirs(sandbox_dir, exist_ok=True)

        if repositories:
            for repo in repositories:
                repo_url = repo.get("clone_url")
                mount_path = repo.get("mount_path", "").strip("/")
                if not mount_path:
                    mount_path = repo_url.split("/")[-1].replace(".git", "") if "/" in repo_url else "repo"
                
                target_dir = os.path.join(sandbox_dir, mount_path)
                logger.info(f"Cloning {repo_url} into {target_dir}")
                
                if repo_url == "test_repo" or repo_url == "test" or repo_url.startswith("file://"):
                    if repo_url.startswith("file://"):
                        source_dir = repo_url.replace("file:///", "").replace("file://", "")
                        shutil.copytree(source_dir, target_dir, dirs_exist_ok=True)
                    else:
                        os.makedirs(target_dir, exist_ok=True)
                        if not os.environ.get("PYTEST_CURRENT_TEST"):
                            cls._seed_local_workspace(target_dir)
                    _run_git(target_dir, "init")
                    cls._init_git_excludes(target_dir)
                    _run_git(target_dir, "add", "-A")
                    _run_git(target_dir, "commit", "--allow-empty", "-m", "Initial commit")
                else:
                    _run_git(sandbox_dir, "clone", repo_url, mount_path)
                    cls._init_git_excludes(target_dir)
                    
                repo_base_sha = repo.get("base_commit_sha")
                if repo_base_sha:
                    _run_git(target_dir, "checkout", repo_base_sha)
                
                _run_git(target_dir, "checkout", "-b", branch_name)
        elif clone_url:
            logger.info(f"Cloning {clone_url} into {sandbox_dir}")
            if clone_url == "test_repo" or clone_url == "test" or clone_url.startswith("file://"):
                used_wt = False
                if not os.environ.get("PYTEST_CURRENT_TEST") and clone_url in ("test_repo", "test"):
                    used_wt = cls._create_git_worktree_sandbox(sandbox_dir, branch_name)
                if not used_wt:
                    os.makedirs(sandbox_dir, exist_ok=True)
                    if clone_url.startswith("file://"):
                        source_dir = clone_url.replace("file:///", "").replace("file://", "")
                        shutil.copytree(source_dir, sandbox_dir, dirs_exist_ok=True)
                    else:
                        if not os.environ.get("PYTEST_CURRENT_TEST"):
                            cls._seed_local_workspace(sandbox_dir)
                    _run_git(sandbox_dir, "init")
                    cls._init_git_excludes(sandbox_dir)
                    _run_git(sandbox_dir, "add", "-A")
                    _run_git(sandbox_dir, "commit", "--allow-empty", "-m", "Initial commit")
                    if base_commit_sha:
                        _run_git(sandbox_dir, "checkout", base_commit_sha)
                    _run_git(sandbox_dir, "checkout", "-b", branch_name)
            else:
                shutil.rmtree(sandbox_dir, ignore_errors=True)
                _run_git(cls.SANDBOX_ROOT, "clone", clone_url, f"sandbox-{session_id}")
                cls._init_git_excludes(sandbox_dir)
                if base_commit_sha:
                    _run_git(sandbox_dir, "checkout", base_commit_sha)
                _run_git(sandbox_dir, "checkout", "-b", branch_name)
        else:
            repo_root = Path(__file__).resolve().parent.parent.parent
            is_greenfield = (
                not project_root
                or Path(project_root).resolve() == repo_root
                or not os.path.exists(project_root)
            )
            if is_greenfield:
                os.makedirs(sandbox_dir, exist_ok=True)
                _run_git(sandbox_dir, "init")
                cls._init_git_excludes(sandbox_dir)
                _run_git(sandbox_dir, "commit", "--allow-empty", "-m", "Initial commit for greenfield mission")
                _run_git(sandbox_dir, "checkout", "-b", branch_name)
            elif not cls._create_git_worktree_sandbox(sandbox_dir, branch_name, source_dir=project_root):
                os.makedirs(sandbox_dir, exist_ok=True)
                ignore = shutil.ignore_patterns(
                    "__pycache__", "*.pyc", ".git", "node_modules", "sandboxes",
                    "*.db", "*.sqlite3", ".env", "venv", ".venv", "synth_repo_*", ".pytest_cache"
                )
                shutil.copytree(project_root, sandbox_dir, ignore=ignore, dirs_exist_ok=True)
                _run_git(sandbox_dir, "init")
                cls._init_git_excludes(sandbox_dir)
                _run_git(sandbox_dir, "add", "-A")
                _run_git(sandbox_dir, "commit", "-m", "Initial snapshot (auto-generated by Kobits)")
                _run_git(sandbox_dir, "checkout", "-b", branch_name)

        resolved_base_sha = base_commit_sha
        if not resolved_base_sha and os.path.exists(os.path.join(sandbox_dir, ".git")):
            try:
                head_res = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=sandbox_dir,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=5,
                )
                if head_res.returncode == 0 and head_res.stdout.strip():
                    resolved_base_sha = head_res.stdout.strip()
            except Exception:
                pass

        from backend.services.environment_bootstrapper import EnvironmentBootstrapper
        EnvironmentBootstrapper.ensure_virtual_rootfs(sandbox_dir)

        session = SandboxSession(
            session_id=session_id,
            project_root=sandbox_dir,
            branch_name=branch_name,
            sandbox_dir=sandbox_dir,
            base_commit_sha=resolved_base_sha,
        )
        cls._sessions[session_id] = session
        cls._persist_session(session)
            
        logger.info(f"Sandbox created: {session_id} ({session.isolation_driver}) -> {sandbox_dir}")
        return session

    @classmethod
    def _persist_session(cls, session: SandboxSession):
        if not session or not os.path.isdir(session.sandbox_dir):
            return
        metadata_path = os.path.join(session.sandbox_dir, ".kobits_sandbox.json")
        try:
            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(session.to_dict(), f, indent=2)
        except Exception as e:
            logger.error(f"Failed to persist sandbox metadata: {e}")

    @classmethod
    def _rehydrate_session_from_dict(cls, data: dict) -> SandboxSession:
        session = SandboxSession(
            session_id=data["session_id"],
            project_root=data["project_root"],
            branch_name=data["branch_name"],
            sandbox_dir=data["sandbox_dir"],
            base_commit_sha=data.get("base_commit_sha"),
            vm_id=data.get("vm_id"),
            isolation_driver=data.get("isolation_driver"),
            resource_limits=data.get("resource_limits"),
        )
        session.files_changed = data.get("files_changed", [])
        session.status = data.get("status", "ACTIVE")
        session.command_history = data.get("command_history", [])
        session.installed_packages = data.get("installed_packages", [])
        if "created_at" in data:
            try:
                session.created_at = datetime.fromisoformat(data["created_at"])
            except Exception:
                pass
        return session

    @classmethod
    def create_task_worktree(cls, parent_session_id: str, task_id: str):
        parent_session = cls._sessions.get(parent_session_id)
        if not parent_session:
            raise ValueError(f"Parent sandbox {parent_session_id} not found")
            
        session_id = f"task-{task_id[:8]}"
        sandbox_dir = os.path.join(cls.SANDBOX_ROOT, f"sandbox-{session_id}")
        branch_name = f"kobits/task/{task_id[:8]}"
        
        # Support multi-repo worktrees
        parent_repos = cls._find_git_repos(parent_session.sandbox_dir)
        if not parent_repos:
            # Fallback for environments without git
            os.makedirs(sandbox_dir, exist_ok=True)
            ignore = shutil.ignore_patterns(
                "__pycache__", "*.pyc", "node_modules", "sandboxes",
                "*.db", "*.sqlite3", ".env", "venv", ".venv", ".git", ".kobits_rootfs"
            )
            shutil.copytree(parent_session.sandbox_dir, sandbox_dir, ignore=ignore, dirs_exist_ok=True)
        else:
            for repo_dir in parent_repos:
                cls._init_git_excludes(repo_dir)
                rel_path = os.path.relpath(repo_dir, parent_session.sandbox_dir)
                target_repo_dir = os.path.join(sandbox_dir, rel_path) if rel_path != "." else sandbox_dir
                # Clean up any stale worktree or branch from a previous task attempt
                if os.path.exists(target_repo_dir):
                    _run_git(repo_dir, "worktree", "remove", "--force", target_repo_dir)
                    shutil.rmtree(target_repo_dir, ignore_errors=True)
                _run_git(repo_dir, "worktree", "prune")
                _run_git(repo_dir, "branch", "-D", branch_name)
                if target_repo_dir != sandbox_dir:
                    os.makedirs(os.path.dirname(target_repo_dir), exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(sandbox_dir), exist_ok=True)
                _run_git(repo_dir, "worktree", "add", "-b", branch_name, target_repo_dir, parent_session.branch_name)
                if not os.path.exists(target_repo_dir) or not os.listdir(target_repo_dir):
                    os.makedirs(target_repo_dir, exist_ok=True)
                    ignore = shutil.ignore_patterns(
                        "__pycache__", "*.pyc", "node_modules", "sandboxes",
                        "*.db", "*.sqlite3", ".env", "venv", ".venv", ".git", ".kobits_sandbox.json", ".kobits_rootfs"
                    )
                    shutil.copytree(repo_dir, target_repo_dir, ignore=ignore, dirs_exist_ok=True)
        
        from backend.services.environment_bootstrapper import EnvironmentBootstrapper
        EnvironmentBootstrapper.ensure_virtual_rootfs(sandbox_dir)

        session = SandboxSession(
            session_id=session_id,
            project_root=sandbox_dir,
            branch_name=branch_name,
            sandbox_dir=sandbox_dir,
            base_commit_sha=parent_session.base_commit_sha,
        )
        cls._sessions[session_id] = session
        cls._persist_session(session)
            
        return session

    @classmethod
    def merge_task_worktree(cls, mission_session_id: str, task_session_id: str) -> dict:
        """Merge a task's ephemeral worktree(s) back into the mission branch."""
        mission_session = cls._sessions.get(mission_session_id)
        task_session = cls._sessions.get(task_session_id)
        
        if not mission_session or not task_session:
            return {"error": "Session not found"}
            
        cls.commit_changes(task_session_id, f"Auto-commit task {task_session_id}")
        
        mission_repos = cls._find_git_repos(mission_session.sandbox_dir)
        all_stdout = ""
        all_shas = []
        
        if not mission_repos:
            # Fallback for environments without git: copy task sandbox back to mission sandbox
            ignore = shutil.ignore_patterns(
                "__pycache__", "*.pyc", "node_modules", "sandboxes",
                "*.db", "*.sqlite3", ".env", "venv", ".venv", ".git", ".kobits_sandbox.json", ".kobits_rootfs"
            )
            shutil.copytree(task_session.sandbox_dir, mission_session.sandbox_dir, ignore=ignore, dirs_exist_ok=True)
            all_stdout = "Merged via file copy (git unavailable)\n"
            all_shas.append("fallback-sha")
        else:
            for repo_dir in mission_repos:
                cls._init_git_excludes(repo_dir)
                out = _run_git(repo_dir, "merge", task_session.branch_name, "--no-edit")
                # Also sync any changed files directly if task_session fell back to copytree
                for rel_file in task_session.files_changed:
                    src_f = os.path.join(task_session.sandbox_dir, rel_file)
                    dst_f = os.path.join(mission_session.sandbox_dir, rel_file)
                    if os.path.isfile(src_f):
                        os.makedirs(os.path.dirname(dst_f), exist_ok=True)
                        shutil.copy2(src_f, dst_f)
                _run_git(repo_dir, "add", "-A")
                _run_git(repo_dir, "commit", "-m", f"Merge task {task_session_id}")
                sha = _run_git(repo_dir, "rev-parse", "HEAD")
                all_stdout += out + "\n"
                all_shas.append(sha)
        
        mission_session.files_changed.extend([f for f in task_session.files_changed if f not in mission_session.files_changed])
        for cmd_entry in task_session.command_history:
            mission_session.command_history.append(cmd_entry)
        for pkg in task_session.installed_packages:
            if pkg not in mission_session.installed_packages:
                mission_session.installed_packages.append(pkg)
        cls._persist_session(mission_session)
        return {"success": True, "stdout": all_stdout, "commit_sha": ",".join(all_shas)}

    @classmethod
    def get_session(cls, session_id: str) -> Optional[SandboxSession]:
        if session_id in cls._sessions:
            return cls._sessions[session_id]
        # Persistent state recovery from disk (.kobits_sandbox.json)
        candidate_dir = os.path.join(cls.SANDBOX_ROOT, f"sandbox-{session_id}")
        metadata_path = os.path.join(candidate_dir, ".kobits_sandbox.json")
        if os.path.isdir(candidate_dir) and os.path.isfile(metadata_path):
            try:
                with open(metadata_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                session = cls._rehydrate_session_from_dict(data)
                cls._sessions[session.session_id] = session
                return session
            except Exception as e:
                logger.error(f"Failed to rehydrate sandbox {session_id} from disk: {e}")
        return None

    @classmethod
    def get_session_by_branch(cls, branch_name: str) -> Optional[SandboxSession]:
        for s in cls._sessions.values():
            if s.branch_name == branch_name:
                return s
                
        # CRASH RECOVERY (GAP 2): Scan disk if server restarted (newest first)
        if os.path.exists(cls.SANDBOX_ROOT):
            entries = sorted(
                os.listdir(cls.SANDBOX_ROOT),
                key=lambda d: os.path.getmtime(os.path.join(cls.SANDBOX_ROOT, d)) if os.path.exists(os.path.join(cls.SANDBOX_ROOT, d)) else 0,
                reverse=True
            )
            for d in entries:
                sandbox_dir = os.path.join(cls.SANDBOX_ROOT, d)
                metadata_path = os.path.join(sandbox_dir, ".kobits_sandbox.json")
                if os.path.isdir(sandbox_dir) and os.path.exists(metadata_path):
                    try:
                        with open(metadata_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        if data.get("branch_name") == branch_name:
                            session = cls._rehydrate_session_from_dict(data)
                            cls._sessions[session.session_id] = session
                            logger.info(f"Crash recovery: Restored sandbox {session.session_id} for branch {branch_name}")
                            return session
                    except Exception as e:
                        logger.error(f"Failed to recover sandbox metadata from {metadata_path}: {e}")
                        
        return None

    @classmethod
    def list_sessions(cls) -> list[dict]:
        return [s.to_dict() for s in cls._sessions.values()]

    @staticmethod
    def _scan_balanced_delimiters(rel_path: str, text_content: str, lang: str = "generic") -> str | None:
        """
        Deterministic lexical scanner that tracks strings, raw strings, comments, and line numbers
        across JS/TS/JSX/TSX, Go, Rust, and SQL, returning an error string if any delimiter or
        literal is unbalanced/unterminated, or None if balanced.
        """
        stack: list[tuple[str, int]] = []
        pairs = {"(": ")", "[": "]", "{": "}"}
        closers = {")": "(", "]": "[", "}": "{"}
        n = len(text_content)
        i = 0
        line = 1

        while i < n:
            ch = text_content[i]
            if ch == "\n":
                line += 1
                i += 1
                continue

            # Line comment // (JS/TS/Go/Rust) or -- (SQL)
            if lang in ("javascript", "typescript", "jsx", "tsx", "go", "rust") and text_content.startswith("//", i):
                nl = text_content.find("\n", i + 2)
                if nl == -1:
                    break
                i = nl
                continue
            if lang == "sql" and text_content.startswith("--", i):
                nl = text_content.find("\n", i + 2)
                if nl == -1:
                    break
                i = nl
                continue

            # Block comment /* ... */
            if text_content.startswith("/*", i):
                start_ln = line
                end_idx = text_content.find("*/", i + 2)
                if end_idx == -1:
                    return f"{rel_path}:{start_ln}: SyntaxError: Unterminated block comment '/*'"
                line += text_content.count("\n", i + 2, end_idx + 2)
                i = end_idx + 2
                continue

            # Go or JS/TS backtick raw/template string `...`
            if ch == "`" and lang in ("javascript", "typescript", "jsx", "tsx", "go"):
                start_ln = line
                i += 1
                while i < n:
                    c = text_content[i]
                    if c == "\n":
                        line += 1
                    elif c == "\\" and lang != "go":
                        i += 2
                        continue
                    elif c == "`":
                        i += 1
                        break
                    i += 1
                else:
                    return f"{rel_path}:{start_ln}: SyntaxError: Unterminated backtick string literal"
                continue

            # Rust raw string r#"..."#
            if lang == "rust" and ch == "r" and i + 1 < n and text_content[i + 1] in ("#", '"'):
                j = i + 1
                hashes = 0
                while j < n and text_content[j] == "#":
                    hashes += 1
                    j += 1
                if j < n and text_content[j] == '"':
                    start_ln = line
                    closing_seq = '"' + ("#" * hashes)
                    end_idx = text_content.find(closing_seq, j + 1)
                    if end_idx == -1:
                        return f"{rel_path}:{start_ln}: SyntaxError: Unterminated Rust raw string literal"
                    line += text_content.count("\n", j + 1, end_idx + len(closing_seq))
                    i = end_idx + len(closing_seq)
                    continue

            # Rust lifetime ('a, 'static) vs character literal ('x', '\n')
            if lang == "rust" and ch == "'":
                # If ' is followed by identifier and NOT closed by ' immediately, it's a lifetime
                if i + 2 < n and (text_content[i + 1].isalpha() or text_content[i + 1] == "_") and text_content[i + 2] != "'":
                    i += 2
                    while i < n and (text_content[i].isalnum() or text_content[i] == "_"):
                        i += 1
                    continue

            # Double or single quoted string
            if ch in ('"', "'"):
                quote = ch
                start_ln = line
                i += 1
                while i < n:
                    c = text_content[i]
                    if c == "\n":
                        if lang in ("go", "javascript", "typescript", "jsx", "tsx"):
                            return f"{rel_path}:{start_ln}: SyntaxError: Unterminated string literal ({quote})"
                        line += 1
                    elif c == "\\":
                        if i + 1 < n and text_content[i + 1] == "\n":
                            line += 1
                        i += 2
                        continue
                    elif c == quote:
                        i += 1
                        break
                    i += 1
                else:
                    return f"{rel_path}:{start_ln}: SyntaxError: Unterminated string literal ({quote})"
                continue

            if ch in pairs:
                stack.append((ch, line))
            elif ch in closers:
                if not stack:
                    return f"{rel_path}:{line}: SyntaxError: Unexpected closing '{ch}'"
                top_ch, top_ln = stack.pop()
                if top_ch != closers[ch]:
                    return (
                        f"{rel_path}:{line}: SyntaxError: Mismatched closing '{ch}' "
                        f"(expected '{pairs[top_ch]}' for '{top_ch}' opened at line {top_ln})"
                    )
            i += 1

        if stack:
            open_ch, open_ln = stack[-1]
            return f"{rel_path}:{open_ln}: SyntaxError: Unclosed '{open_ch}' (expected '{pairs[open_ch]}')"
        return None

    @classmethod
    def _validate_js_ts_syntax(cls, rel_path: str, text_content: str, lang: str) -> dict:
        """
        Validate JavaScript (.js/.mjs/.cjs), TypeScript (.ts/.mts/.cts), and JSX/TSX (.jsx/.tsx)
        using balanced delimiter/JSX verification plus Node V8 / SWC stripTypeScriptTypes.
        """
        import re
        import shutil
        import subprocess

        delim_err = cls._scan_balanced_delimiters(rel_path, text_content, lang=lang)
        if delim_err:
            return {"syntax_valid": False, "language": lang, "syntax_error": delim_err}

        # Common syntax error patterns across JS/TS/JSX/TSX (e.g. `= ;`, `return a + ;`, dangling binary op)
        for idx, raw_line in enumerate(text_content.splitlines(), 1):
            code_part = raw_line.split("//", 1)[0].strip()
            if re.search(r"(?:[=+\-*/%&|^]|&&|\|\|)\s*;", code_part):
                return {
                    "syntax_valid": False,
                    "language": lang,
                    "syntax_error": f"{rel_path}:{idx}: SyntaxError: Unexpected ';' after operator in '{code_part}'",
                }
            if re.search(r",\s*\)\s*(?::\s*[^=]+)?\s*=>", code_part) is None and re.search(r"\(\s*,", code_part):
                return {
                    "syntax_valid": False,
                    "language": lang,
                    "syntax_error": f"{rel_path}:{idx}: SyntaxError: Unexpected leading ',' in parameter/argument list",
                }

        # JSX / TSX tag balance check (<Tag>...</Tag>)
        if lang in ("jsx", "tsx"):
            void_tags = {
                "area", "base", "br", "col", "embed", "hr", "img", "input",
                "link", "meta", "param", "source", "track", "wbr",
            }
            tag_pattern = re.compile(r"<\s*(/)?\s*([A-Za-z_][\w.]*)?([^>]*?)(/)?\s*>")
            # Strip comments and string literals roughly for tag matching
            cleaned = re.sub(r"//[^\n]*", "", text_content)
            cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.DOTALL)
            closing_tags = re.findall(r"</\s*([A-Za-z_][\w.]*)?\s*>", cleaned)
            if closing_tags or "/>" in cleaned:
                jsx_stack: list[str] = []
                for m in tag_pattern.finditer(cleaned):
                    is_close = bool(m.group(1))
                    tag_name = m.group(2) or ""
                    rest = m.group(3) or ""
                    is_self = bool(m.group(4)) or rest.strip().endswith("/")
                    # Skip TypeScript generics like <T extends Foo> or <number> where there is no matching closing tag
                    if not is_close and not is_self and tag_name and tag_name not in closing_tags and tag_name[0].isupper() and "extends " in rest:
                        continue
                    if is_self or (tag_name.lower() in void_tags and not is_close):
                        continue
                    if is_close:
                        if not jsx_stack:
                            return {
                                "syntax_valid": False,
                                "language": lang,
                                "syntax_error": f"{rel_path}: SyntaxError: Unexpected closing JSX tag </{tag_name}>",
                            }
                        if jsx_stack[-1] != tag_name:
                            # Could be an unclosed inner tag or a TS generic earlier on the stack; pop any non-closed TS generics
                            while jsx_stack and jsx_stack[-1] != tag_name and jsx_stack[-1] not in closing_tags:
                                jsx_stack.pop()
                            if not jsx_stack or jsx_stack[-1] != tag_name:
                                expected = jsx_stack[-1] if jsx_stack else ""
                                return {
                                    "syntax_valid": False,
                                    "language": lang,
                                    "syntax_error": f"{rel_path}: SyntaxError: Mismatched JSX closing tag </{tag_name}> (expected </{expected}>)",
                                }
                        jsx_stack.pop()
                    else:
                        if tag_name in closing_tags or tag_name == "":
                            jsx_stack.append(tag_name)
                unclosed_real = [t for t in jsx_stack if t in closing_tags]
                if unclosed_real:
                    return {
                        "syntax_valid": False,
                        "language": lang,
                        "syntax_error": f"{rel_path}: SyntaxError: Unclosed JSX tag <{unclosed_real[-1]}>",
                    }
            # For .tsx files, also validate any top-level TypeScript interface/type blocks via Node SWC
            node_bin = shutil.which("node")
            if lang == "tsx" and node_bin:
                ts_type_blocks = re.findall(
                    r"(?:export\s+)?(?:interface\s+[A-Za-z_]\w*\s*\{[^}]*\}|type\s+[A-Za-z_]\w*\s*=\s*[^;]+;)",
                    cleaned,
                    flags=re.DOTALL,
                )
                if ts_type_blocks:
                    res_tsx_types = subprocess.run(
                        [
                            node_bin,
                            "--no-warnings",
                            "-e",
                            "const fs=require('node:fs'), {stripTypeScriptTypes}=require('node:module');"
                            "try{stripTypeScriptTypes(fs.readFileSync(0,'utf8'),{mode:'strip'});}catch(e){"
                            "if(e&&e.code==='ERR_UNSUPPORTED_TYPESCRIPT_SYNTAX')process.exit(0);"
                            "console.error(e.message||String(e));process.exit(1);}",
                        ],
                        input="\n\n".join(ts_type_blocks),
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=6,
                    )
                    if res_tsx_types.returncode != 0:
                        err_m = (res_tsx_types.stderr or "TypeScript SyntaxError").strip().splitlines()[0]
                        return {
                            "syntax_valid": False,
                            "language": lang,
                            "syntax_error": f"{rel_path}: TypeScript SyntaxError: {err_m}",
                        }
            return {"syntax_valid": True, "language": lang, "syntax_error": None}

        node_bin = shutil.which("node")
        if not node_bin:
            return {"syntax_valid": True, "language": lang, "syntax_error": None}

        if lang == "typescript":
            # Use Node's built-in SWC TypeScript parser (`node:module.stripTypeScriptTypes`) + V8 check
            node_script = (
                "const fs = require('node:fs');\n"
                "const vm = require('node:vm');\n"
                "const cp = require('node:child_process');\n"
                "const { stripTypeScriptTypes } = require('node:module');\n"
                "const src = fs.readFileSync(0, 'utf8');\n"
                "try {\n"
                "  const js = stripTypeScriptTypes(src, { mode: 'strip' });\n"
                "  const r = cp.spawnSync(process.execPath, ['--input-type=module', '--check'], { input: js, encoding: 'utf8' });\n"
                "  if (r.status !== 0) {\n"
                "    try { new vm.Script(js); } catch (e2) { console.error(r.stderr || e2.message); process.exit(1); }\n"
                "  }\n"
                "} catch (e) {\n"
                "  if (e && e.code === 'ERR_UNSUPPORTED_TYPESCRIPT_SYNTAX') { process.exit(0); }\n"
                "  console.error(e && e.message ? e.message : String(e));\n"
                "  process.exit(1);\n"
                "}\n"
            )
            res = subprocess.run(
                [node_bin, "--no-warnings", "-e", node_script],
                input=text_content,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
            )
            if res.returncode != 0:
                err_msg = (res.stderr or res.stdout or "TypeScript SyntaxError").strip().splitlines()[0]
                return {
                    "syntax_valid": False,
                    "language": lang,
                    "syntax_error": f"{rel_path}: TypeScript SyntaxError: {err_msg}",
                }
            return {"syntax_valid": True, "language": lang, "syntax_error": None}

        # JavaScript (.js, .mjs, .cjs)
        res_esm = subprocess.run(
            [node_bin, "--no-warnings", "--input-type=module", "--check"],
            input=text_content,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
        )
        if res_esm.returncode == 0:
            return {"syntax_valid": True, "language": lang, "syntax_error": None}

        # Fallback for CommonJS scripts (e.g. top-level return or CJS-specific globals)
        if not rel_path.lower().endswith(".mjs"):
            res_cjs = subprocess.run(
                [
                    node_bin,
                    "--no-warnings",
                    "-e",
                    "const fs=require('node:fs'), vm=require('node:vm'); new vm.Script(fs.readFileSync(0,'utf8'));",
                ],
                input=text_content,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
            )
            if res_cjs.returncode == 0:
                return {"syntax_valid": True, "language": lang, "syntax_error": None}

        err_lines = [
            ln.strip()
            for ln in (res_esm.stderr or "").splitlines()
            if ln.strip() and not ln.strip().startswith("at ") and not ln.strip().startswith("Node.js ")
        ]
        short_err = " | ".join(err_lines[:3]) if err_lines else "JavaScript SyntaxError"
        return {
            "syntax_valid": False,
            "language": lang,
            "syntax_error": f"{rel_path}: {short_err}",
        }

    @classmethod
    def _validate_go_syntax(cls, rel_path: str, text_content: str) -> dict:
        """
        Validate Go (.go) source files via `gofmt -e` (if installed) plus deterministic
        Go grammar/lexical rules (package header, balanced delimiters, function signatures, expressions).
        """
        import re
        import shutil
        import subprocess

        gofmt_bin = shutil.which("gofmt")
        if gofmt_bin:
            res = subprocess.run(
                [gofmt_bin, "-e"],
                input=text_content,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
            )
            if res.returncode != 0:
                first_err = (res.stderr or "Go syntax error").strip().splitlines()[0]
                return {"syntax_valid": False, "language": "go", "syntax_error": f"{rel_path}: {first_err}"}

        delim_err = cls._scan_balanced_delimiters(rel_path, text_content, lang="go")
        if delim_err:
            return {"syntax_valid": False, "language": "go", "syntax_error": delim_err}

        # Strip block and line comments to inspect top-level Go declarations
        no_block = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text_content, flags=re.DOTALL)
        non_empty_code_lines: list[tuple[int, str]] = []
        for idx, raw_ln in enumerate(no_block.splitlines(), 1):
            code_ln = raw_ln.split("//", 1)[0].strip()
            if code_ln:
                non_empty_code_lines.append((idx, code_ln))

        if not non_empty_code_lines:
            return {
                "syntax_valid": False,
                "language": "go",
                "syntax_error": f"{rel_path}:1: SyntaxError: Go file is missing 'package <name>' declaration",
            }

        first_ln_no, first_ln = non_empty_code_lines[0]
        if not re.match(r"^package\s+[A-Za-z_]\w*(?:\s*;)?$", first_ln):
            return {
                "syntax_valid": False,
                "language": "go",
                "syntax_error": f"{rel_path}:{first_ln_no}: SyntaxError: Expected 'package <name>', found '{first_ln[:40]}'",
            }

        for ln_no, code_ln in non_empty_code_lines[1:]:
            # Check malformed func without '('
            if re.match(r"^func\b", code_ln) and "(" not in code_ln:
                return {
                    "syntax_valid": False,
                    "language": "go",
                    "syntax_error": f"{rel_path}:{ln_no}: SyntaxError: Malformed 'func' declaration missing '('",
                }
            # Check dangling assignment or binary operator at end of statement (e.g. `return a +` or `x :=`)
            if re.search(r"(?::=|=|\+|\-|\*|/|%|&&|\|\|)\s*(?:\}|;)?$", code_ln) and not code_ln.endswith(("++", "--", "{", ",")):
                # Note: in Go, trailing `+` on a multiline expression is valid ONLY if the next line continues an expression,
                # not if the line ends with `}` or `;` or is immediately followed by `}`!
                if code_ln.endswith(("}", ";")) or re.search(r"(?::=|=)\s*$", code_ln):
                    return {
                        "syntax_valid": False,
                        "language": "go",
                        "syntax_error": f"{rel_path}:{ln_no}: SyntaxError: Incomplete expression in '{code_ln}'",
                    }

        return {"syntax_valid": True, "language": "go", "syntax_error": None}

    @classmethod
    def _validate_rust_syntax(cls, rel_path: str, text_content: str) -> dict:
        """
        Validate Rust (.rs) source files via deterministic Rust lexical & grammar rules
        (lifetimes vs char literals, raw strings, balanced delimiters, fn/struct/impl headers, expressions).
        """
        import re

        delim_err = cls._scan_balanced_delimiters(rel_path, text_content, lang="rust")
        if delim_err:
            return {"syntax_valid": False, "language": "rust", "syntax_error": delim_err}

        no_block = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text_content, flags=re.DOTALL)
        for idx, raw_ln in enumerate(no_block.splitlines(), 1):
            code_ln = raw_ln.split("//", 1)[0].strip()
            if not code_ln:
                continue
            if re.search(r"(?:=|\+|\-|\*|/|%|&&|\|\|)\s*;", code_ln):
                return {
                    "syntax_valid": False,
                    "language": "rust",
                    "syntax_error": f"{rel_path}:{idx}: SyntaxError: Incomplete Rust expression before ';' in '{code_ln}'",
                }
            if re.match(r"^(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:const\s+)?(?:unsafe\s+)?fn\b", code_ln):
                if "(" not in code_ln:
                    return {
                        "syntax_valid": False,
                        "language": "rust",
                        "syntax_error": f"{rel_path}:{idx}: SyntaxError: Rust 'fn' declaration missing parameter list '('",
                    }

        return {"syntax_valid": True, "language": "rust", "syntax_error": None}

    @classmethod
    def _validate_file_syntax(cls, rel_path: str, text_content: str) -> dict:
        """
        Deterministically validate in-memory syntax of modified code immediately on write/edit.
        Supports:
          - Python (.py, .pyw): ast.parse + bytecode compile
          - TypeScript / TSX (.ts, .mts, .cts, .tsx): Node SWC stripTypeScriptTypes + V8 check + JSX balance
          - JavaScript / JSX (.js, .mjs, .cjs, .jsx): Node V8 syntax check + JSX balance
          - Go (.go): gofmt -e + Go lexical/grammar verification
          - Rust (.rs): Rust lexical/lifetime/delimiter/grammar verification
          - JSON (.json): json.loads
          - TOML (.toml): tomllib.loads
          - SQL (.sql): balanced delimiter & quote verification
        """
        import ast

        lower_p = (rel_path or "").lower()
        if lower_p.endswith((".py", ".pyw")):
            try:
                tree = ast.parse(text_content, filename=rel_path)
                compile(tree, filename=rel_path, mode="exec")
                return {"syntax_valid": True, "language": "python", "syntax_error": None}
            except SyntaxError as e:
                return {
                    "syntax_valid": False,
                    "language": "python",
                    "syntax_error": f"{rel_path}:{e.lineno or '?'}: SyntaxError: {e.msg}",
                }
            except Exception as e:
                return {
                    "syntax_valid": False,
                    "language": "python",
                    "syntax_error": f"{rel_path}: CompileError: {e}",
                }
        elif lower_p.endswith(".json"):
            try:
                json.loads(text_content)
                return {"syntax_valid": True, "language": "json", "syntax_error": None}
            except Exception as e:
                return {
                    "syntax_valid": False,
                    "language": "json",
                    "syntax_error": f"{rel_path}: JSONDecodeError: {e}",
                }
        elif lower_p.endswith(".toml"):
            try:
                import tomllib
                tomllib.loads(text_content)
                return {"syntax_valid": True, "language": "toml", "syntax_error": None}
            except Exception as e:
                return {
                    "syntax_valid": False,
                    "language": "toml",
                    "syntax_error": f"{rel_path}: TOMLDecodeError: {e}",
                }
        elif lower_p.endswith((".ts", ".mts", ".cts")):
            return cls._validate_js_ts_syntax(rel_path, text_content, lang="typescript")
        elif lower_p.endswith(".tsx"):
            return cls._validate_js_ts_syntax(rel_path, text_content, lang="tsx")
        elif lower_p.endswith((".js", ".mjs", ".cjs")):
            return cls._validate_js_ts_syntax(rel_path, text_content, lang="javascript")
        elif lower_p.endswith(".jsx"):
            return cls._validate_js_ts_syntax(rel_path, text_content, lang="jsx")
        elif lower_p.endswith(".go"):
            return cls._validate_go_syntax(rel_path, text_content)
        elif lower_p.endswith(".rs"):
            return cls._validate_rust_syntax(rel_path, text_content)
        elif lower_p.endswith(".sql"):
            err = cls._scan_balanced_delimiters(rel_path, text_content, lang="sql")
            return {"syntax_valid": err is None, "language": "sql", "syntax_error": err}

        return {"syntax_valid": True, "language": "other", "syntax_error": None}

    @classmethod
    def read_file(
        cls,
        session_id: str,
        rel_path: str,
        start_line: int = None,
        end_line: int = None,
    ) -> dict:
        """
        Read a file (or a surgical 1-indexed [start_line, end_line] slice) from the sandbox workspace.
        """
        session = cls.get_session(session_id)
        if not session:
            return {"error": "Sandbox session not found"}

        rel_path = cls._normalize_sandbox_rel_path(rel_path)
        sandbox_abs = os.path.realpath(session.sandbox_dir)
        abs_path = os.path.realpath(os.path.join(sandbox_abs, rel_path))
        if not (abs_path == sandbox_abs or abs_path.startswith(sandbox_abs + os.sep)):
            return {"error": "Security Block: Path traversal attempt detected."}

        if not os.path.exists(abs_path):
            return {"error": f"File not found: {rel_path}"}
        if os.path.isdir(abs_path):
            files = [f for f in os.listdir(abs_path) if not f.startswith(".kobits_")]
            return {"type": "directory", "files": files}
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                full_content = f.read()
            all_lines = full_content.splitlines()
            total_lines = len(all_lines)

            if start_line is not None or end_line is not None:
                s_line = max(1, int(start_line or 1))
                e_line = min(total_lines, int(end_line or total_lines))
                if s_line > e_line and total_lines > 0:
                    return {
                        "error": f"Invalid line range [{s_line}, {e_line}] for {rel_path} ({total_lines} lines)."
                    }
                sliced = all_lines[s_line - 1 : e_line]
                sliced_content = "\n".join(sliced)
                numbered = "\n".join(f"{s_line + idx}: {ln}" for idx, ln in enumerate(sliced))
                return {
                    "type": "file",
                    "path": rel_path,
                    "content": sliced_content,
                    "numbered_content": numbered,
                    "start_line": s_line,
                    "end_line": e_line,
                    "total_lines": total_lines,
                }

            MAX_READ_CHARS = 60000
            truncated = False
            if len(full_content) > MAX_READ_CHARS:
                full_content = (
                    full_content[:MAX_READ_CHARS]
                    + f"\n... [TRUNCATED AT {MAX_READ_CHARS} CHARS — USE start_line/end_line TO READ SPECIFIC RANGES]"
                )
                truncated = True

            return {
                "type": "file",
                "content": full_content,
                "path": rel_path,
                "total_lines": total_lines,
                "truncated": truncated,
            }
        except Exception as e:
            return {"error": str(e)}

    @classmethod
    def _expand_glob_patterns(cls, pattern_str: Optional[str]) -> list[str]:
        if not pattern_str or not pattern_str.strip():
            return []
        raw = pattern_str.strip().replace("\\", "/")
        top_parts = []
        depth = 0
        buf = []
        for ch in raw:
            if ch == "{":
                depth += 1
                buf.append(ch)
            elif ch == "}":
                depth = max(0, depth - 1)
                buf.append(ch)
            elif ch == "," and depth == 0:
                part = "".join(buf).strip()
                if part:
                    top_parts.append(part)
                buf = []
            else:
                buf.append(ch)
        last_part = "".join(buf).strip()
        if last_part:
            top_parts.append(last_part)

        expanded = []
        for part in top_parts:
            brace_match = re.search(r"\{([^{}]+)\}", part)
            if brace_match:
                options = [opt.strip() for opt in brace_match.group(1).split(",") if opt.strip()]
                prefix = part[: brace_match.start()]
                suffix = part[brace_match.end() :]
                for opt in options:
                    expanded.append(f"{prefix}{opt}{suffix}")
            else:
                expanded.append(part)
        return expanded

    @classmethod
    def _glob_single_match(cls, rel_path: str, filename: str, pattern: str) -> bool:
        import fnmatch
        if fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(filename, pattern):
            return True
        if pattern.startswith("**/") and (
            fnmatch.fnmatch(rel_path, pattern[3:]) or fnmatch.fnmatch(filename, pattern[3:])
        ):
            return True
        return False

    @classmethod
    def _matches_file_glob(
        cls,
        rel_path: str,
        filename: str,
        file_pattern: Optional[str],
        exclude_pattern: Optional[str] = None,
    ) -> bool:
        include_pats = []
        exclude_pats = cls._expand_glob_patterns(exclude_pattern)
        for p in cls._expand_glob_patterns(file_pattern):
            if p.startswith("!"):
                neg = p[1:].strip()
                if neg:
                    exclude_pats.append(neg)
            elif p != "*":
                include_pats.append(p)

        for ex in exclude_pats:
            if cls._glob_single_match(rel_path, filename, ex):
                return False

        if not include_pats:
            return True

        return any(cls._glob_single_match(rel_path, filename, inc) for inc in include_pats)

    @classmethod
    def _find_enclosing_symbol(cls, file_lines: list[str], line_idx: int) -> Optional[str]:
        decl_re = re.compile(
            r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?"
            r"(?:def|class|function|interface|type|func|pub\s+fn|fn|struct|impl)\s+([A-Za-z_][A-Za-z0-9_]*)"
        )
        for idx in range(min(line_idx, len(file_lines) - 1), -1, -1):
            raw_line = file_lines[idx]
            m = decl_re.match(raw_line)
            if m:
                return raw_line.strip()[:120]
        return None

    @classmethod
    def _format_context_snippet(
        cls,
        file_lines: list[str],
        match_start_idx: int,
        match_end_idx: int,
        before_ctx: int,
        after_ctx: int,
    ) -> str:
        c_start = max(0, match_start_idx - before_ctx)
        c_end = min(len(file_lines), match_end_idx + after_ctx + 1)
        snippet_lines = []
        for s_idx in range(c_start, c_end):
            marker = ">" if (match_start_idx <= s_idx <= match_end_idx) else " "
            snippet_lines.append(f"{marker} {s_idx + 1:4d} | {file_lines[s_idx][:200]}")
        return "\n".join(snippet_lines)

    @classmethod
    def search_directory(
        cls,
        root_dir: str,
        query: str,
        file_pattern: Optional[str] = None,
        exclude_pattern: Optional[str] = None,
        is_regex: Optional[bool] = None,
        case_sensitive: Optional[bool] = None,
        whole_word: bool = False,
        multiline: bool = False,
        context_lines: int = 2,
        before_context: Optional[int] = None,
        after_context: Optional[int] = None,
        max_results: int = 50,
    ) -> dict:
        """
        Ripgrep-grade (`rg`) regex, smart-case, glob, and hybrid token-window search
        across a repository directory:
          1. True Smart-Case (`case_sensitive=None`): if the query contains uppercase
             characters, searches case-sensitively first and falls back to case-insensitive
             if 0 hits are found; if all-lowercase, searches case-insensitively.
          2. Full Regex (`is_regex`), Whole-Word (`whole_word`), and Multiline (`multiline`) support.
          3. Symmetric (`context_lines` / `-C`) and asymmetric (`before_context` / `-B`,
             `after_context` / `-A`) context snippets formatted with `> line | code`.
          4. Brace & negated file globs (`*.{ts,tsx}`, `!*.spec.ts`, `exclude_pattern`),
             root `.gitignore` filtering, and minified bundle exclusion.
          5. Stage-2 multi-token BM25 sliding-window fallback (`semantic_token_window`)
             when a natural-language query has no contiguous regex/literal match.
        """
        import fnmatch

        if not root_dir or not os.path.isdir(root_dir):
            return {"error": f"Search directory not found: {root_dir}"}

        if not query or not query.strip():
            return {"results": [], "total_matches": 0, "match_mode": "empty_query"}

        sandbox_abs = os.path.realpath(root_dir)
        max_results = max(1, min(int(max_results or 50), 200))
        base_ctx = max(0, min(int(context_lines if context_lines is not None else 2), 15))
        before_ctx = max(0, min(int(before_context if before_context is not None else base_ctx), 20))
        after_ctx = max(0, min(int(after_context if after_context is not None else base_ctx), 20))

        raw_q = query.strip()
        use_multiline = bool(multiline or "\n" in raw_q or r"\n" in raw_q)

        # Determine true Ripgrep smart-case vs explicit case sensitivity
        cleaned_for_case = re.sub(r"\\[A-Za-z]", "", raw_q) if is_regex is not False else raw_q
        has_uppercase = any(ch.isupper() for ch in cleaned_for_case)

        if case_sensitive is True:
            effective_case_sensitive = True
            allow_ci_fallback = True
            mode_prefix = "regex_case_sensitive" if is_regex else ("literal_case_sensitive" if is_regex is False else "smart_case_sensitive")
        elif case_sensitive is False:
            effective_case_sensitive = False
            allow_ci_fallback = False
            mode_prefix = "regex_ignore_case" if is_regex else ("literal_ignore_case" if is_regex is False else "smart_case_insensitive")
        else:
            # Smart-case (`rg -S`): case-sensitive when uppercase letters are present, else case-insensitive
            effective_case_sensitive = has_uppercase
            allow_ci_fallback = has_uppercase
            if is_regex:
                mode_prefix = "regex"
            else:
                mode_prefix = "smart_case_sensitive" if has_uppercase else "smart_case_insensitive"

        flags = 0 if effective_case_sensitive else re.IGNORECASE
        if use_multiline:
            flags |= re.MULTILINE

        def _build_regex(pattern_text: str, re_flags: int):
            nonlocal mode_prefix
            base_pat = re.escape(pattern_text) if is_regex is False else pattern_text
            if whole_word:
                base_pat = rf"(?<!\w)(?:{base_pat})(?!\w)"
            try:
                return re.compile(base_pat, re_flags), False
            except re.error:
                escaped = re.escape(pattern_text)
                if whole_word:
                    escaped = rf"(?<!\w)(?:{escaped})(?!\w)"
                return re.compile(escaped, re_flags), True

        compiled_re, fell_back_literal = _build_regex(raw_q, flags)
        match_mode = "literal_fallback" if fell_back_literal else mode_prefix

        excluded_dirs = {
            "node_modules", "__pycache__", "build", "dist", "out",
            "coverage", "venv", ".venv", ".kobits_sandboxes", ".kobits_rootfs", "_worktrees",
            ".git", ".pytest_cache", ".mypy_cache", "target", ".next", ".nuxt",
            "vendor", "bower_components", "bundle", ".tox", ".serverless",
            "bin", "obj", ".idea", ".vscode", "tmp", "temp", "pods", "Pods",
            ".cache", ".turbo", ".gradle", ".cargo", "site-packages",
        }
        excluded_exts = {
            ".pyc", ".pyo", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg",
            ".pdf", ".zip", ".tar", ".gz", ".sqlite3", ".db", ".lock",
            ".woff", ".woff2", ".ttf", ".eot", ".exe", ".dll", ".so", ".dylib",
            ".map", ".wasm", ".parquet", ".arrow", ".avro", ".bin", ".dat",
        }
        excluded_filenames = {
            "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "cargo.lock",
            "poetry.lock", "composer.lock", "gemfile.lock", "go.sum",
        }
        minified_suffixes = (".min.js", ".min.css", ".bundle.js", ".bundle.css")
        gitignore_patterns = []
        gitignore_path = os.path.join(sandbox_abs, ".gitignore")
        if os.path.isfile(gitignore_path):
            try:
                with open(gitignore_path, "r", encoding="utf-8", errors="ignore") as gf:
                    for gline in gf:
                        gline = gline.strip()
                        if not gline or gline.startswith("#") or gline.startswith("!"):
                            continue
                        clean_pat = gline.strip("/")
                        if "/" not in clean_pat and "*" not in clean_pat and "?" not in clean_pat:
                            excluded_dirs.add(clean_pat)
                        else:
                            gitignore_patterns.append(clean_pat)
            except OSError:
                pass

        candidate_files = []  # (rel_path, file_lines)
        results = []

        def _scan_file_with_regex(rel_path: str, file_lines: list[str], active_re: re.Pattern, active_mode: str):
            if use_multiline:
                full_text = "\n".join(file_lines)
                for m in active_re.finditer(full_text):
                    start_idx = full_text.count("\n", 0, m.start())
                    end_idx = full_text.count("\n", 0, max(m.start(), m.end() - 1))
                    line_start_offset = full_text.rfind("\n", 0, m.start()) + 1
                    col_no = (m.start() - line_start_offset) + 1
                    results.append({
                        "file": rel_path,
                        "line": start_idx + 1,
                        "end_line": end_idx + 1,
                        "column": col_no,
                        "matched_text": m.group(0)[:120],
                        "enclosing_symbol": cls._find_enclosing_symbol(file_lines, start_idx),
                        "content": file_lines[start_idx].strip()[:200] if start_idx < len(file_lines) else "",
                        "snippet": cls._format_context_snippet(file_lines, start_idx, end_idx, before_ctx, after_ctx),
                        "match_mode": active_mode,
                    })
                    if len(results) >= max_results:
                        return
            else:
                for idx, line in enumerate(file_lines):
                    m = active_re.search(line)
                    if m:
                        results.append({
                            "file": rel_path,
                            "line": idx + 1,
                            "end_line": idx + 1,
                            "column": m.start() + 1,
                            "matched_text": m.group(0)[:120],
                            "enclosing_symbol": cls._find_enclosing_symbol(file_lines, idx),
                            "content": line.strip()[:200],
                            "snippet": cls._format_context_snippet(file_lines, idx, idx, before_ctx, after_ctx),
                            "match_mode": active_mode,
                        })
                        if len(results) >= max_results:
                            return

        for root, dirs, files in os.walk(sandbox_abs):
            if len(results) >= max_results:
                break
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in excluded_dirs)
            for file in sorted(files):
                if len(results) >= max_results:
                    break
                if file.startswith("."):
                    continue
                lower_file = file.lower()
                if lower_file in excluded_filenames:
                    continue
                if lower_file.endswith(minified_suffixes):
                    continue
                ext = os.path.splitext(lower_file)[1]
                if ext in excluded_exts:
                    continue
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, sandbox_abs).replace("\\", "/")
                if gitignore_patterns and any(
                    fnmatch.fnmatch(rel_path, gp) or fnmatch.fnmatch(file, gp)
                    for gp in gitignore_patterns
                ):
                    continue

                if not cls._matches_file_glob(rel_path, file, file_pattern, exclude_pattern=exclude_pattern):
                    continue

                try:
                    if os.path.getsize(file_path) > 512_000:
                        continue
                    with open(file_path, "r", encoding="utf-8") as f:
                        file_lines = f.read().splitlines()
                except (UnicodeDecodeError, OSError):
                    continue

                if len(candidate_files) < 2000:
                    candidate_files.append((rel_path, file_lines))
                if len(results) < max_results:
                    _scan_file_with_regex(rel_path, file_lines, compiled_re, match_mode)

        # Automatic case-insensitive fallback when smart-case (uppercase query) or strict case_sensitive=True yielded 0 hits
        if not results and allow_ci_fallback and candidate_files:
            ci_flags = re.IGNORECASE | (re.MULTILINE if use_multiline else 0)
            ci_re, _ = _build_regex(raw_q, ci_flags)
            for rel_path, file_lines in candidate_files:
                _scan_file_with_regex(rel_path, file_lines, ci_re, "case_insensitive_fallback")
                if len(results) >= max_results:
                    break
            if results:
                match_mode = "case_insensitive_fallback"

        # Stage 2: Multi-Token BM25 / Symbol Window Fallback for natural-language queries
        if not results and candidate_files:
            from backend.services.embedding import tokenize_code_identifiers
            q_tokens = set(tokenize_code_identifiers(raw_q))
            if q_tokens:
                decl_re = re.compile(r"^\s*(?:async\s+)?(?:def|class|function|export|interface|type|func|pub\s+fn|fn|struct)\b")
                scored_windows = []
                for rel_path, file_lines in candidate_files:
                    path_tokens = set(tokenize_code_identifiers(rel_path))
                    path_overlap = len(q_tokens & path_tokens)
                    for idx, line in enumerate(file_lines):
                        line_tokens = set(tokenize_code_identifiers(line))
                        line_overlap = len(q_tokens & line_tokens)
                        if line_overlap == 0 and path_overlap == 0:
                            continue
                        w_start = max(0, idx - 3)
                        w_end = min(len(file_lines), idx + 4)
                        window_text = "\n".join(file_lines[w_start:w_end])
                        win_tokens = set(tokenize_code_identifiers(window_text))
                        win_overlap = len(q_tokens & win_tokens)
                        if win_overlap == 0:
                            continue
                        score = (win_overlap / len(q_tokens)) + (0.35 * (line_overlap / len(q_tokens)))
                        if path_overlap > 0:
                            score += 0.15
                        if decl_re.search(line):
                            score += 0.25
                        scored_windows.append((
                            score,
                            {
                                "file": rel_path,
                                "line": idx + 1,
                                "end_line": idx + 1,
                                "column": 1,
                                "matched_text": line.strip()[:80],
                                "enclosing_symbol": cls._find_enclosing_symbol(file_lines, idx),
                                "content": line.strip()[:200],
                                "snippet": cls._format_context_snippet(file_lines, idx, idx, before_ctx, after_ctx),
                                "score": round(score, 4),
                                "match_mode": "semantic_token_window",
                            },
                        ))
                scored_windows.sort(key=lambda x: x[0], reverse=True)
                seen_file_buckets = set()
                for score, item in scored_windows:
                    bucket = (item["file"], item["line"] // 6)
                    if bucket in seen_file_buckets:
                        continue
                    seen_file_buckets.add(bucket)
                    results.append(item)
                    if len(results) >= max_results:
                        break
                if results:
                    match_mode = "semantic_token_window"

        return {
            "results": results[:max_results],
            "total_matches": len(results),
            "match_mode": match_mode,
        }

    @classmethod
    def search_files(
        cls,
        session_id: str,
        query: str,
        file_pattern: Optional[str] = None,
        exclude_pattern: Optional[str] = None,
        is_regex: Optional[bool] = None,
        case_sensitive: Optional[bool] = None,
        whole_word: bool = False,
        multiline: bool = False,
        context_lines: int = 2,
        before_context: Optional[int] = None,
        after_context: Optional[int] = None,
        max_results: int = 50,
    ) -> dict:
        """
        Ripgrep-grade hybrid code search inside the sandbox workspace.
        """
        session = cls.get_session(session_id)
        if not session:
            return {"error": "Sandbox session not found"}

        return cls.search_directory(
            root_dir=session.sandbox_dir,
            query=query,
            file_pattern=file_pattern,
            exclude_pattern=exclude_pattern,
            is_regex=is_regex,
            case_sensitive=case_sensitive,
            whole_word=whole_word,
            multiline=multiline,
            context_lines=context_lines,
            before_context=before_context,
            after_context=after_context,
            max_results=max_results,
        )

    @classmethod
    def write_file(cls, session_id: str, rel_path: str, content: str) -> dict:
        """Write a file inside the sandbox workspace with instant AST/syntax validation."""
        session = cls.get_session(session_id)
        if not session:
            return {"error": "Sandbox session not found"}

        rel_path = cls._normalize_sandbox_rel_path(rel_path)
        sandbox_abs = os.path.realpath(session.sandbox_dir)
        abs_path = os.path.realpath(os.path.join(sandbox_abs, rel_path))
        if not (abs_path == sandbox_abs or abs_path.startswith(sandbox_abs + os.sep)):
            return {"error": "Security Block: Path traversal attempt detected."}

        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        try:
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(content)
            if rel_path not in session.files_changed:
                session.files_changed.append(rel_path)
            cls._persist_session(session)
            syn = cls._validate_file_syntax(rel_path, content)
            res = {
                "success": True,
                "path": rel_path,
                "syntax_valid": syn["syntax_valid"],
            }
            if not syn["syntax_valid"]:
                res["syntax_error"] = syn["syntax_error"]
                res["warning"] = f"File written, but syntax check reported an error: {syn['syntax_error']}. Fix immediately."
            return res
        except Exception as e:
            return {"error": str(e)}

    @classmethod
    def _apply_single_surgical_chunk(
        cls,
        norm_content: str,
        rel_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> dict:
        """
        Internal 3-Tier Surgical Patch Matcher:
        - Tier 1: Exact LF-normalized match
        - Tier 2: Per-line trailing horizontal whitespace match (`rstrip`)
        - Tier 3: Relative-indentation-aware match (automatically shifts `new_string` by the exact indentation delta)
        Returns {"ok": True, "updated_content": ..., "replacements": ..., "snippet": ..., "match_tier": ...}
        or {"ok": False, "error": ...}
        """
        if old_string == "":
            return {
                "ok": False,
                "error": f"old_string cannot be empty when editing an existing file ({rel_path}). Provide the exact lines to replace.",
            }
        if old_string == new_string:
            return {"ok": False, "error": "No-op edit: old_string and new_string are identical."}

        norm_old = old_string.replace("\r\n", "\n")
        norm_new = new_string.replace("\r\n", "\n")
        content_lines = norm_content.split("\n")
        old_lines = norm_old.split("\n")
        new_lines = norm_new.split("\n")
        n_old = len(old_lines)

        # Tier 1: Exact substring match
        occurrences = norm_content.count(norm_old)
        match_tier = "exact"
        edit_line_ranges = []

        if occurrences > 0:
            if occurrences > 1 and not replace_all:
                return {
                    "ok": False,
                    "error": (
                        f"Ambiguous edit: `old_string` matched {occurrences} locations in {rel_path}. "
                        "Include 2-3 additional surrounding context lines in `old_string` to uniquely identify the target block, "
                        "or set `replace_all=True`."
                    ),
                }
            # Locate line index of first match for compact snippet generation
            prefix_before = norm_content.split(norm_old, 1)[0]
            first_start_line = prefix_before.count("\n")
            if replace_all:
                updated_norm = norm_content.replace(norm_old, norm_new)
                replacements_done = occurrences
            else:
                updated_norm = norm_content.replace(norm_old, norm_new, 1)
                replacements_done = 1
            edit_line_ranges.append((first_start_line, first_start_line + len(new_lines)))
        else:
            # Tier 2: Trailing whitespace normalized line-window match
            old_stripped = [ln.rstrip(" \t") for ln in old_lines]
            match_indices = []
            if n_old > 0 and any(s for s in old_stripped):
                for idx in range(len(content_lines) - n_old + 1):
                    window = [content_lines[idx + k].rstrip(" \t") for k in range(n_old)]
                    if window == old_stripped:
                        match_indices.append(idx)

            if len(match_indices) > 0:
                if len(match_indices) > 1 and not replace_all:
                    return {
                        "ok": False,
                        "error": (
                            f"Ambiguous edit: `old_string` matched {len(match_indices)} locations in {rel_path}. "
                            "Include 2-3 additional surrounding context lines in `old_string` to uniquely identify the target block, "
                            "or set `replace_all=True`."
                        ),
                    }
                match_tier = "trailing_whitespace_normalized"
                targets = match_indices if replace_all else match_indices[:1]
                for idx in reversed(targets):
                    content_lines[idx : idx + n_old] = new_lines
                updated_norm = "\n".join(content_lines)
                replacements_done = len(targets)
                edit_line_ranges.append((targets[0], targets[0] + len(new_lines)))
            else:
                # Tier 3: Relative-indentation-aware match
                def _indent_profile(lines_list):
                    non_empty = [ln.rstrip(" \t") for ln in lines_list if ln.strip()]
                    if not non_empty:
                        return None, []
                    min_indent = min(len(ln) - len(ln.lstrip(" ")) for ln in non_empty)
                    rel = [
                        (ln.rstrip(" \t")[min_indent:] if ln.strip() else "")
                        for ln in lines_list
                    ]
                    return min_indent, rel

                old_base_indent, old_rel = _indent_profile(old_lines)
                rel_matches = []
                if old_base_indent is not None and n_old > 0:
                    for idx in range(len(content_lines) - n_old + 1):
                        win = content_lines[idx : idx + n_old]
                        win_base_indent, win_rel = _indent_profile(win)
                        if win_base_indent is not None and win_rel == old_rel:
                            rel_matches.append((idx, win_base_indent - old_base_indent))

                if len(rel_matches) == 0:
                    return {
                        "ok": False,
                        "error": (
                            f"Target block (`old_string`) not found in {rel_path}. "
                            "Call `repository.read` on the file first to inspect the exact current lines and indentation."
                        ),
                    }
                if len(rel_matches) > 1 and not replace_all:
                    return {
                        "ok": False,
                        "error": (
                            f"Ambiguous edit: `old_string` matched {len(rel_matches)} locations (via relative indentation) in {rel_path}. "
                            "Include 2-3 additional surrounding context lines in `old_string` to uniquely identify the target block."
                        ),
                    }
                match_tier = "relative_indentation_adjusted"
                targets = rel_matches if replace_all else rel_matches[:1]
                for idx, indent_delta in reversed(targets):
                    adjusted_new = []
                    for n_ln in new_lines:
                        if not n_ln.strip():
                            adjusted_new.append("")
                        elif indent_delta > 0:
                            adjusted_new.append((" " * indent_delta) + n_ln)
                        elif indent_delta < 0:
                            strip_n = min(abs(indent_delta), len(n_ln) - len(n_ln.lstrip(" ")))
                            adjusted_new.append(n_ln[strip_n:])
                        else:
                            adjusted_new.append(n_ln)
                    content_lines[idx : idx + n_old] = adjusted_new
                updated_norm = "\n".join(content_lines)
                replacements_done = len(targets)
                edit_line_ranges.append((targets[0][0], targets[0][0] + len(new_lines)))

        # Build compact numbered snippet (+/- 4 context lines around edit) instead of echoing full file
        updated_lines = updated_norm.split("\n")
        start_idx, end_idx = edit_line_ranges[0]
        ctx_start = max(0, start_idx - 4)
        ctx_end = min(len(updated_lines), end_idx + 4)
        snippet_lines = [
            f"{line_no}: {updated_lines[line_no - 1]}"
            for line_no in range(ctx_start + 1, ctx_end + 1)
        ]

        return {
            "ok": True,
            "updated_content": updated_norm,
            "replacements": replacements_done,
            "lines_added": len(new_lines) * replacements_done,
            "lines_removed": n_old * replacements_done,
            "match_tier": match_tier,
            "snippet": "\n".join(snippet_lines),
        }

    @classmethod
    def edit_file(
        cls,
        session_id: str,
        rel_path: str,
        old_string: str = None,
        new_string: str = None,
        replace_all: bool = False,
        edits: list = None,
    ) -> dict:
        """
        Kyros-Grade Surgical File Patching (`repository.edit`):
        1. Supports single `(old_string, new_string)` OR atomic multi-chunk `edits=[{old_string, new_string}, ...]`.
        2. 3-tier matching: Exact -> Trailing-Whitespace-Normalized -> Relative-Indentation-Adjusted.
        3. Returns a compact numbered `snippet` (+/- 4 lines around the edit) instead of dumping the full file into LLM context.
        4. Runs instant in-memory AST/syntax verification (`_validate_file_syntax`) and reports `syntax_valid` + `syntax_error`.
        """
        session = cls.get_session(session_id)
        if not session:
            return {"error": "Sandbox session not found"}

        rel_path = cls._normalize_sandbox_rel_path(rel_path)
        sandbox_abs = os.path.realpath(session.sandbox_dir)
        abs_path = os.path.realpath(os.path.join(sandbox_abs, rel_path))
        if not (abs_path == sandbox_abs or abs_path.startswith(sandbox_abs + os.sep)):
            return {"error": "Security Block: Path traversal attempt detected."}

        if not os.path.exists(abs_path):
            if old_string == "" and new_string is not None and not edits:
                return cls.write_file(session_id, rel_path, new_string)
            return {
                "error": f"File not found: {rel_path}. Read the directory or use repository.write to create a new file."
            }
        if os.path.isdir(abs_path):
            return {"error": f"Cannot edit directory: {rel_path}"}

        chunk_list = []
        if edits and isinstance(edits, list):
            for item in edits:
                if isinstance(item, dict):
                    chunk_list.append({
                        "old_string": item.get("old_string", ""),
                        "new_string": item.get("new_string", ""),
                        "replace_all": bool(item.get("replace_all", replace_all)),
                    })
        elif old_string is not None and new_string is not None:
            chunk_list.append({
                "old_string": old_string,
                "new_string": new_string,
                "replace_all": bool(replace_all),
            })
        else:
            return {"error": "Provide either (`old_string`, `new_string`) or a non-empty `edits` list."}

        try:
            with open(abs_path, "rb") as raw_f:
                raw_bytes = raw_f.read()
            uses_crlf = b"\r\n" in raw_bytes
            original_text = raw_bytes.decode("utf-8")
        except Exception as e:
            return {"error": f"Failed to read {rel_path}: {e}"}

        working_norm = original_text.replace("\r\n", "\n")
        total_replacements = 0
        total_added = 0
        total_removed = 0
        snippets = []
        match_tiers = []

        # Apply all chunks atomically in memory; if any chunk fails, disk file is untouched!
        for idx, chunk in enumerate(chunk_list):
            step = cls._apply_single_surgical_chunk(
                working_norm,
                rel_path,
                chunk["old_string"],
                chunk["new_string"],
                replace_all=chunk["replace_all"],
            )
            if not step["ok"]:
                prefix = f"Edit chunk #{idx + 1} failed (atomic rollback — no changes written): " if len(chunk_list) > 1 else ""
                return {"error": prefix + step["error"]}
            working_norm = step["updated_content"]
            total_replacements += step["replacements"]
            total_added += step["lines_added"]
            total_removed += step["lines_removed"]
            snippets.append(step["snippet"])
            match_tiers.append(step["match_tier"])

        final_text = working_norm.replace("\n", "\r\n") if uses_crlf else working_norm
        try:
            with open(abs_path, "wb") as out_f:
                out_f.write(final_text.encode("utf-8"))
            if rel_path not in session.files_changed:
                session.files_changed.append(rel_path)
            cls._persist_session(session)

            syn = cls._validate_file_syntax(rel_path, working_norm)
            result_payload = {
                "success": True,
                "path": rel_path,
                "chunks_applied": len(chunk_list),
                "replacements": total_replacements,
                "lines_added": total_added,
                "lines_removed": total_removed,
                "match_tier": match_tiers[0] if len(match_tiers) == 1 else match_tiers,
                "matched_via_whitespace_normalization": any(t != "exact" for t in match_tiers),
                "syntax_valid": syn["syntax_valid"],
                "snippet": "\n---\n".join(snippets),
            }
            if not syn["syntax_valid"]:
                result_payload["syntax_error"] = syn["syntax_error"]
                result_payload["warning"] = (
                    f"Edit applied, but instant syntax check failed: {syn['syntax_error']}. "
                    "Call repository.edit immediately to fix the syntax error."
                )
            return result_payload
        except Exception as e:
            return {"error": str(e)}

    @classmethod
    def delete_file(cls, session_id: str, rel_path: str) -> dict:
        """Delete a file inside the sandbox workspace safely with path traversal and metadata guards."""
        session = cls.get_session(session_id)
        if not session:
            return {"error": "Sandbox session not found"}

        rel_path = cls._normalize_sandbox_rel_path(rel_path)
        sandbox_abs = os.path.realpath(session.sandbox_dir)
        abs_path = os.path.realpath(os.path.join(sandbox_abs, rel_path))
        if not (abs_path != sandbox_abs and abs_path.startswith(sandbox_abs + os.sep)):
            return {"error": "Security Block: Path traversal attempt detected."}

        norm_rel = os.path.relpath(abs_path, sandbox_abs).replace("\\", "/")
        if norm_rel == ".git" or norm_rel.startswith(".git/") or norm_rel.startswith(".kobits_") or os.path.basename(norm_rel).startswith(".kobits_"):
            return {"error": f"Security Block: Cannot delete protected sandbox metadata ({rel_path})."}

        if not os.path.exists(abs_path):
            return {"error": f"File not found: {rel_path}"}
        if os.path.isdir(abs_path):
            return {"error": f"Cannot delete directory with repository.delete: {rel_path}"}

        try:
            os.remove(abs_path)
            # Prune empty parent directories up to sandbox_abs
            parent_dir = os.path.dirname(abs_path)
            while parent_dir and parent_dir != sandbox_abs and parent_dir.startswith(sandbox_abs + os.sep):
                try:
                    if not os.listdir(parent_dir):
                        os.rmdir(parent_dir)
                        parent_dir = os.path.dirname(parent_dir)
                    else:
                        break
                except OSError:
                    break

            if rel_path not in session.files_changed:
                session.files_changed.append(rel_path)
            cls._persist_session(session)
            return {
                "success": True,
                "path": rel_path,
                "deleted": True,
            }
        except Exception as e:
            return {"error": str(e)}

    @classmethod
    def run_command(cls, session_id: str, command: str, timeout: int = 120) -> dict:
        """
        Execute a shell command inside the session's Kyros-grade Isolated Container /
        MicroVM / Virtual-Rootfs runtime, recording full execution telemetry for
        SWE-bench / RL / SFT LLM training trajectories.
        """
        session = cls.get_session(session_id)
        if not session:
            return {"error": "Sandbox session not found"}

        sandbox_abs = os.path.realpath(session.sandbox_dir)
        from backend.services.environment_bootstrapper import EnvironmentBootstrapper

        res = EnvironmentBootstrapper.execute_isolated(
            sandbox_dir=sandbox_abs,
            command=command,
            session_id=session.session_id,
            timeout=timeout,
        )

        # Update session VM metadata and installed packages from virtual dpkg if any
        if res.get("vm_id"):
            session.vm_id = res["vm_id"]
        if res.get("driver"):
            session.isolation_driver = res["driver"]
        if res.get("installed_packages"):
            for pkg in res["installed_packages"]:
                if pkg not in session.installed_packages:
                    session.installed_packages.append(pkg)

        session.command_history.append({
            "command": command,
            "exit_code": res.get("exit_code", 1),
            "stdout": (res.get("stdout") or "")[:4000],
            "stderr": (res.get("stderr") or "")[:4000],
            "duration_ms": res.get("duration_ms", 0),
            "driver": res.get("driver", session.isolation_driver),
            "vm_id": res.get("vm_id", session.vm_id),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        cls._persist_session(session)
        return res

    @classmethod
    def reset_sandbox(cls, session_id: str) -> dict:
        """
        Reset the sandbox workspace and its virtual rootfs / container state back to
        `base_commit_sha` so RL / Best-of-N LLM training rollouts can run multiple
        independent episodes from the exact same initial repository state.
        """
        session = cls.get_session(session_id)
        if not session:
            return {"error": "Sandbox session not found"}

        from backend.services.environment_bootstrapper import EnvironmentBootstrapper
        sandbox_abs = os.path.realpath(session.sandbox_dir)

        for repo in cls._find_git_repos(sandbox_abs):
            cls._init_git_excludes(repo)
            if session.base_commit_sha:
                _run_git(repo, "reset", "--hard", session.base_commit_sha)
            else:
                _run_git(repo, "reset", "--hard", "HEAD")
            _run_git(repo, "clean", "-fd", "-e", ".kobits_sandbox.json", "-e", ".kobits_rootfs")

        # Reset disposable virtual rootfs overlay
        rootfs_dir = os.path.join(sandbox_abs, EnvironmentBootstrapper.VIRTUAL_ROOTFS_DIRNAME)
        shutil.rmtree(rootfs_dir, ignore_errors=True)
        EnvironmentBootstrapper.ensure_virtual_rootfs(sandbox_abs)

        session.files_changed = []
        session.command_history = []
        session.installed_packages = []
        session.status = "ACTIVE"
        cls._persist_session(session)
        return {
            "success": True,
            "session_id": session.session_id,
            "vm_id": session.vm_id,
            "isolation_driver": session.isolation_driver,
            "base_commit_sha": session.base_commit_sha,
            "reset": True,
        }

    @classmethod
    def export_training_trajectory(
        cls,
        session_id: str,
        mission_objective: str = "",
        verification_report: Optional[dict] = None,
        agent_runs: Optional[list] = None,
    ) -> dict:
        """
        Export a structured SWE-bench / OpenHands / RL / SFT training episode trajectory
        for LLM fine-tuning and reinforcement learning reward calculation.
        """
        session = cls.get_session(session_id)
        if not session:
            return {"error": "Sandbox session not found"}

        diff_data = cls.get_diff(session_id)
        ver_status = (verification_report or {}).get("status", "UNKNOWN")
        passed = ver_status == "PASSED"
        reward = 1.0 if passed else (-0.5 if diff_data.get("files_changed") else -1.0)

        return {
            "session_id": session.session_id,
            "vm_id": session.vm_id,
            "isolation_driver": session.isolation_driver,
            "resource_limits": session.resource_limits,
            "base_commit_sha": session.base_commit_sha,
            "branch_name": session.branch_name,
            "objective": mission_objective,
            "command_history": session.command_history,
            "installed_packages": session.installed_packages,
            "files_changed": diff_data.get("files_changed", []),
            "git_patch": diff_data.get("diff", ""),
            "diff_summary": diff_data.get("summary", ""),
            "verification": verification_report or {},
            "agent_runs": agent_runs or [],
            "reward": reward,
            "exported_at": datetime.now(timezone.utc).isoformat(),
        }

    @classmethod
    def get_diff(cls, session_id: str) -> dict:
        session = cls.get_session(session_id)
        if not session:
            return {"error": "Sandbox session not found"}

        summary = ""
        diff_full = ""
        for repo in cls._find_git_repos(session.sandbox_dir):
            cls._init_git_excludes(repo)
            _run_git(repo, "add", "-A")
            # Ensure .kobits_rootfs is never staged even if git exclude was bypassed
            _run_git(repo, "reset", "-q", "--", ".kobits_rootfs", ".kobits_sandbox.json")
            repo_name = os.path.basename(repo)
            prefix = f"--- {repo_name} ---\n" if repo != session.sandbox_dir else ""
            
            s = _run_git(repo, "diff", "--cached", "--stat")
            d = _run_git(repo, "diff", "--cached")
            names = _run_git(repo, "diff", "--cached", "--name-only")

            if not s.strip() and session.base_commit_sha:
                s = _run_git(repo, "diff", "--stat", session.base_commit_sha)
                d = _run_git(repo, "diff", session.base_commit_sha)
                names = _run_git(repo, "diff", "--name-only", session.base_commit_sha)

            if s.strip(): summary += prefix + s + "\n"
            if d.strip(): diff_full += prefix + d + "\n"

            for n in (names or "").splitlines():
                n = n.strip()
                norm_n = n.replace("\\", "/")
                if (
                    n
                    and not os.path.basename(n).startswith(".kobits_")
                    and not norm_n.startswith(".kobits_rootfs/")
                    and norm_n != ".kobits_rootfs"
                    and n not in session.files_changed
                ):
                    session.files_changed.append(n)
            
        return {
            "summary": summary,
            "diff": diff_full,
            "files_changed": session.files_changed,
        }

    @classmethod
    def commit_changes(cls, session_id: str, message: str) -> dict:
        session = cls._sessions.get(session_id)
        if not session:
            return {"error": "Sandbox session not found"}
            
        repos = cls._find_git_repos(session.sandbox_dir)
        results = []
        for repo in repos:
            cls._init_git_excludes(repo)
            _run_git(repo, "add", "-A")
            _run_git(repo, "reset", "-q", "--", ".kobits_rootfs", ".kobits_sandbox.json")
            diff_text = _run_git(repo, "diff", "--cached")
            if not diff_text.strip():
                continue
                
            if len(diff_text) > 500000:
                _run_git(repo, "reset")
                return {"error": "Safety Block: Diff is too large. Aborting commit."}
                
            secret_patterns = ["api_key=", "secret=", "password=", "token="]
            for pattern in secret_patterns:
                if pattern in diff_text.lower():
                    _run_git(repo, "reset")
                    return {"error": f"Safety Block: Potential secret detected ({pattern}). Aborting commit."}
                    
            res = _run_git(repo, "commit", "-m", message)
            results.append(res)
            
        return {"success": True, "stdout": "\n".join(results)}

    @classmethod
    def create_pr(cls, session_id: str, title: str, body: str) -> dict:
        """
        Create a pull-request-like artifact.

        In a real system this would push the branch and call the GitHub API.
        Here we generate the diff, package the metadata, and return it so
        the UI can display it for human review.
        """
        session = cls._sessions.get(session_id)
        if not session:
            return {"error": "Sandbox session not found"}

        # Ensure changes are committed
        if session.status != "COMMITTED":
            cls.commit_changes(session_id, f"Auto-commit: {title}")

        diff_data = cls.get_diff(session_id)

        # Get the full commit log on this branch
        log = _run_git(session.sandbox_dir, "log", "--oneline", "--all")

        pr_id = str(uuid.uuid4())[:8]
        session.status = "PR_CREATED"

        return {
            "success": True,
            "pr_id": pr_id,
            "title": title,
            "body": body,
            "branch": session.branch_name,
            "files_changed": session.files_changed,
            "diff_summary": diff_data.get("summary", ""),
            "diff": diff_data.get("diff", ""),
            "commit_log": log,
            "pr_url": f"https://github.com/kobits-org/project/pull/{pr_id}",
            "status": "OPEN",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    @classmethod
    def cleanup(cls, session_id: str) -> dict:
        """Destroy any associated ephemeral container/MicroVM and remove the sandbox workspace from disk."""
        session = cls._sessions.get(session_id)
        if not session:
            return {"error": "Sandbox session not found"}
        try:
            from backend.services.environment_bootstrapper import EnvironmentBootstrapper
            EnvironmentBootstrapper.destroy_session_container(session_id)
            shutil.rmtree(session.sandbox_dir, ignore_errors=True)
            del cls._sessions[session_id]
            return {"success": True, "message": f"Sandbox {session_id} and VM {session.vm_id} cleaned up."}
        except Exception as e:
            return {"error": str(e)}


MOCK_GIT_STATE = {}

def _run_git(cwd: str, *args: str) -> str:
    """Helper to run a git command and return stdout."""
    import os
    import subprocess
    is_dev = os.environ.get("KOBITS_DEV_MODE", "0").lower() in ("1", "true", "yes")
    if not is_dev:
        try:
            result = subprocess.run(
                ["git"] + list(args),
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=True
            )
            return result.stdout
        except subprocess.CalledProcessError as e:
            # Fall back to returning stderr or just raising
            return e.stdout or e.stderr

    # MOCK MODE FOR TESTS
    cmd = " ".join(args)
    if not os.path.exists(cwd):
        return ""
        
    repo_id = os.path.basename(cwd)
    if repo_id not in MOCK_GIT_STATE:
        MOCK_GIT_STATE[repo_id] = {"branch": "main", "commits": [], "head": "mock-base-sha"}
        
    state = MOCK_GIT_STATE[repo_id]
        
    if cmd.startswith("init"):
        return "Initialized empty Git repository"
    elif cmd.startswith("clone"):
        return f"Cloning into '{args[1]}'..."
    elif cmd.startswith("checkout -b"):
        state["branch"] = args[2]
        return f"Switched to a new branch '{args[2]}'"
    elif cmd.startswith("checkout"):
        state["branch"] = args[1]
        state["head"] = args[1] # simulate checking out a commit
        return f"Switched to branch '{args[1]}'"
    elif cmd.startswith("add"):
        return ""
    elif cmd.startswith("commit -m"):
        state["head"] = f"mock-sha-{uuid.uuid4().hex[:6]}"
        state["commits"].append({"sha": state["head"], "msg": args[2]})
        return f"[{state['branch']} {state['head']}] {args[2]}"
    elif cmd.startswith("commit --allow-empty"):
        state["head"] = f"mock-sha-{uuid.uuid4().hex[:6]}"
        state["commits"].append({"sha": state["head"], "msg": args[3]})
        return f"[{state['branch']} {state['head']}] {args[3]}"
    elif cmd.startswith("rev-parse HEAD"):
        return state["head"]
    elif cmd.startswith("branch --show-current"):
        return state["branch"]
    elif cmd.startswith("diff --cached --stat"):
        return " 1 file changed, 1 insertion(+)\n"
    elif cmd.startswith("diff --cached"):
        return "diff --git a/test b/test\n+ test"
    elif cmd.startswith("log"):
        return "\\n".join([f"{c['sha']} {c['msg']}" for c in state["commits"]])
    elif cmd.startswith("push"):
        return "To https://github.com/mock/repo.git\n   mock-base..mock-head  branch -> branch"
    elif cmd.startswith("worktree"):
        # mock git worktree by just copying the repo contents to the target
        if args[0] == "worktree" and args[1] == "add":
            target = args[4] # -b <branch> <target> <source>
            import shutil
            os.makedirs(target, exist_ok=True)
            shutil.copytree(cwd, target, dirs_exist_ok=True)
            return "Preparing worktree"

        
    try:
        cmd_list = ["git"] + list(args)
        if os.name == 'nt':
            cmd_list = " ".join(f'"{c}"' if " " in c else c for c in cmd_list)
        result = subprocess.run(
            cmd_list,
            cwd=cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
            shell=True if os.name == 'nt' else False
        )
        if result.returncode != 0:
            logger.warning(f"Git command failed: {cmd_list}\nstderr: {result.stderr}")
        return result.stdout.strip()
    except Exception as e:
        logger.warning(f"Git command failed: git {' '.join(args)} -> {e}")
        return ""
