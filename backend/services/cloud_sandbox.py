"""
Cloud MicroVM Sandbox Service for Kobits (E2B Firecracker Engine).
Spawns ephemeral, kernel-isolated Linux MicroVMs in <150ms per mission
with full root access, dedicated filesystem, and zero host blast radius.
"""

import os
import time
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

_IGNORE_SYNC_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    ".pytest_cache", ".mypy_cache", ".kobits_rootfs", "dist", "build"
}


class _ClassOrInstanceMethod:
    def __init__(self, func):
        self.func = func

    def __get__(self, obj, objtype=None):
        target = obj if obj is not None else objtype
        def wrapper(*args, **kwargs):
            return self.func(target, *args, **kwargs)
        return wrapper


class _MockE2BCommands:
    def run(self, cmd: str, timeout: int = 120):
        class _Result:
            def __init__(self, cmd):
                self.cmd = cmd
                self.stdout = f"Mock cloud execution: {cmd[:60]}"
                self.stderr = ""
                self.exit_code = 0
                self.error = None
        return _Result(cmd)


class _MockE2BFiles:
    def __init__(self):
        self._storage: Dict[str, str] = {}

    def write(self, path: str, content: str):
        self._storage[path] = content

    def read(self, path: str) -> str:
        return self._storage.get(path, "")


class _MockE2BSandbox:
    def __init__(self):
        self.sandbox_id = "mock-sb-01"
        self.commands = _MockE2BCommands()
        self.files = _MockE2BFiles()
        self._running = True

    def is_running(self) -> bool:
        return self._running

    def get_host(self, port: int) -> str:
        return f"{port}-{self.sandbox_id}.e2b.dev"

    def kill(self):
        self._running = False


class CloudMicroVMSandbox:
    """
    E2B Firecracker MicroVM Sandbox Manager.
    Used by Kyros and Devin for instant remote execution with genuine KVM hardware isolation.
    """

    _active_sandboxes: Dict[str, "CloudMicroVMSandbox"] = {}

    def __init__(self, session_id: Optional[str] = None, api_key: Optional[str] = None):
        self.session_id = session_id or "default"
        self.api_key = api_key or os.environ.get("E2B_API_KEY")
        self._sandbox = None
        self.sandbox_id: Optional[str] = None
        self._synced = False

    @_ClassOrInstanceMethod
    def is_configured(self_or_cls) -> bool:
        """Check whether E2B_API_KEY is available."""
        api_key = getattr(self_or_cls, "api_key", None)
        return bool(api_key or os.environ.get("E2B_API_KEY") or os.environ.get("KOBITS_MOCK_CLOUD_SANDBOX") == "1")

    @classmethod
    def get_or_create(cls, session_id: str, api_key: Optional[str] = None) -> "CloudMicroVMSandbox":
        """Get an existing active MicroVM for this session, or spawn a fresh one."""
        if session_id in cls._active_sandboxes:
            sb = cls._active_sandboxes[session_id]
            if sb._sandbox and getattr(sb._sandbox, "is_running", lambda: True)():
                return sb
        sb = cls(session_id=session_id, api_key=api_key)
        sb.start()
        cls._active_sandboxes[session_id] = sb
        return sb

    @classmethod
    def close_session(cls, session_id: str) -> bool:
        """Kill and remove the MicroVM for a completed or cancelled mission session."""
        sb = cls._active_sandboxes.pop(session_id, None)
        if sb:
            sb.close()
            return True
        return False

    @classmethod
    def close_all(cls) -> None:
        """Kill all active MicroVMs (e.g. during server shutdown)."""
        for sid, sb in list(cls._active_sandboxes.items()):
            try:
                sb.close()
            except Exception:
                pass
        cls._active_sandboxes.clear()

    def start(self) -> str:
        """Spawns a fresh Firecracker Linux MicroVM in the E2B cloud."""
        if not self.api_key:
            if os.environ.get("KOBITS_MOCK_CLOUD_SANDBOX") == "1":
                self._sandbox = _MockE2BSandbox()
                self.sandbox_id = "mock-sb-01"
                return self.sandbox_id
            raise ValueError("E2B_API_KEY not configured. Get your key at https://e2b.dev")

        from e2b import Sandbox
        start_ts = time.perf_counter()
        # Initialize Sandbox with timeout (default 10 minutes)
        if hasattr(Sandbox, "create"):
            self._sandbox = Sandbox.create(api_key=self.api_key, timeout=600)
        else:
            self._sandbox = Sandbox(api_key=self.api_key)

        self.sandbox_id = getattr(self._sandbox, "sandbox_id", str(id(self._sandbox)))
        dur_ms = int((time.perf_counter() - start_ts) * 1000)
        logger.info(f"Spawned E2B Firecracker MicroVM {self.sandbox_id} for session {self.session_id} in {dur_ms}ms")
        return self.sandbox_id

    def ensure_synced(self, local_dir: str, remote_dir: str = "/workspace") -> None:
        """Syncs files from the local directory to the MicroVM once per session."""
        if self._synced or not local_dir or not os.path.isdir(local_dir):
            return
        self.sync_local_to_microvm(local_dir, remote_dir)
        self._synced = True

    def sync_local_to_microvm(self, local_dir: str, remote_dir: str = "/workspace") -> int:
        """Uploads files from the local sandbox workspace to /workspace inside the MicroVM."""
        if not self._sandbox:
            self.start()

        synced_count = 0
        local_abs = Path(local_dir).resolve()

        # Ensure remote workdir exists
        try:
            self.run_command(f"mkdir -p {remote_dir}", timeout=10)
        except Exception:
            pass

        for root, dirs, files in os.walk(local_abs):
            dirs[:] = [d for d in dirs if d not in _IGNORE_SYNC_DIRS and not d.startswith(".kobits")]
            for file in files:
                if file.startswith(".kobits") or file.endswith(".pyc"):
                    continue
                file_path = Path(root) / file
                try:
                    rel_path = file_path.relative_to(local_abs).as_posix()
                    dest_path = f"{remote_dir}/{rel_path}"
                    if file_path.stat().st_size > 5_000_000:
                        continue  # Skip files > 5MB
                    content = file_path.read_text(encoding="utf-8", errors="replace")
                    self.write_file(dest_path, content)
                    synced_count += 1
                except Exception as exc:
                    logger.debug(f"Failed to sync {file} to MicroVM: {exc}")

        logger.info(f"Synced {synced_count} files to MicroVM {self.sandbox_id}")
        return synced_count

    def write_file(self, remote_path: str, content: str) -> bool:
        """Write a text file inside the isolated cloud MicroVM."""
        if not self._sandbox:
            self.start()
        try:
            if hasattr(self._sandbox, "files") and hasattr(self._sandbox.files, "write"):
                self._sandbox.files.write(remote_path, content)
                return True
            # Fallback via shell base64 encoding
            import base64
            b64_str = base64.b64encode(content.encode("utf-8")).decode("ascii")
            self.run_command(f"python3 -c \"import base64; open('{remote_path}', 'wb').write(base64.b64decode('{b64_str}'))\"")
            return True
        except Exception as exc:
            logger.error(f"Failed to write file {remote_path} to MicroVM: {exc}")
            return False

    def read_file(self, remote_path: str) -> str:
        """Read a text file from the isolated cloud MicroVM."""
        if not self._sandbox:
            self.start()
        try:
            if hasattr(self._sandbox, "files") and hasattr(self._sandbox.files, "read"):
                return self._sandbox.files.read(remote_path)
            res = self.run_command(f"cat {remote_path}")
            return res.get("stdout", "")
        except Exception as exc:
            logger.error(f"Failed to read file {remote_path} from MicroVM: {exc}")
            return ""

    def run_command(self, command: str, timeout: int = 120, workdir: str = "/workspace") -> Dict[str, Any]:
        """Executes a command inside the isolated cloud MicroVM."""
        if not self._sandbox:
            self.start()

        start_ts = time.perf_counter()
        try:
            # Wrap command to ensure it runs in the proper working directory
            # If command invokes pytest, ensure pytest is present in the MicroVM
            if "pytest" in command:
                cd_cmd = f"cd {workdir} 2>/dev/null || true; which pytest >/dev/null 2>&1 || pip install pytest -q; {command}"
            else:
                cd_cmd = f"cd {workdir} 2>/dev/null || true; {command}"
            res = self._sandbox.commands.run(cd_cmd, timeout=timeout)
            dur_ms = int((time.perf_counter() - start_ts) * 1000)
            return {
                "stdout": getattr(res, "stdout", "") or "",
                "stderr": getattr(res, "stderr", "") or "",
                "exit_code": getattr(res, "exit_code", 0),
                "error": getattr(res, "error", None),
                "driver": "cloud_microvm",
                "vm_id": f"e2b-{self.sandbox_id}",
                "duration_ms": dur_ms,
                "isolated": True,
            }
        except Exception as exc:
            dur_ms = int((time.perf_counter() - start_ts) * 1000)
            logger.error(f"E2B MicroVM command execution error: {exc}")
            return {
                "stdout": "",
                "stderr": str(exc),
                "exit_code": 1,
                "error": str(exc),
                "driver": "cloud_microvm",
                "vm_id": f"e2b-{self.sandbox_id}",
                "duration_ms": dur_ms,
                "isolated": True,
            }

    def clone_repo(
        self,
        repo_url: str,
        branch: Optional[str] = None,
        auth_token: Optional[str] = None,
        workdir: str = "/workspace",
    ) -> Dict[str, Any]:
        """
        Directly clones a git repository into the remote Linux MicroVM.
        Zero files touch the host or local filesystem.
        """
        if not self._sandbox:
            self.start()

        auth_url = repo_url
        if auth_token and "github.com" in repo_url:
            if "https://" in repo_url:
                auth_url = repo_url.replace("https://", f"https://x-access-token:{auth_token}@")

        branch_flag = f"--branch {branch}" if branch else ""
        cmd = (
            f"mkdir -p {workdir} && "
            "git config --global user.name 'Kobits Autonomous Agent' && "
            "git config --global user.email 'agent@kobits.ai' && "
            f"git clone --depth 50 {branch_flag} '{auth_url}' {workdir} || "
            f"(cd {workdir} && git status)"
        )
        return self.run_command(cmd, timeout=180, workdir=workdir)

    def start_dev_server(
        self,
        command: str = "npm run dev",
        port: int = 3000,
        workdir: str = "/workspace",
    ) -> Dict[str, Any]:
        """
        Launches an ephemeral web development server inside the remote MicroVM
        and exposes its port for the live web preview.
        """
        if not self._sandbox:
            self.start()

        bg_cmd = f"cd {workdir} && nohup {command} > /tmp/dev_server.log 2>&1 & echo $!"
        res = self.run_command(bg_cmd, timeout=30, workdir=workdir)
        pid = (res.get("stdout") or "").strip()

        preview_url = self.get_preview_url(port=port)
        return {
            "status": "RUNNING",
            "pid": pid,
            "port": port,
            "preview_url": preview_url,
            "driver": "cloud_microvm",
            "vm_id": f"e2b-{self.sandbox_id}",
        }

    def get_preview_url(self, port: int = 3000) -> str:
        """Computes the live interactive preview URL for a given port."""
        if self._sandbox and hasattr(self._sandbox, "get_host"):
            try:
                host = self._sandbox.get_host(port)
                return f"https://{host}"
            except Exception:
                pass

        preview_base = os.environ.get("KOBITS_CLOUD_PREVIEW_BASE_URL")
        if preview_base:
            return f"{preview_base.rstrip('/')}/preview/{self.session_id}/"

        return f"/api/v1/preview/{self.session_id}/"

    def list_remote_files(self, workdir: str = "/workspace", max_files: int = 500) -> List[str]:
        """Returns the remote repository file tree directly from the MicroVM."""
        cmd = f"find {workdir} -maxdepth 5 -not -path '*/.*' -not -path '*/node_modules*' -not -path '*/venv*' | head -n {max_files}"
        res = self.run_command(cmd, timeout=20, workdir=workdir)
        stdout = res.get("stdout", "")
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        prefix = workdir.rstrip("/") + "/"
        return [l[len(prefix):] if l.startswith(prefix) else l for l in lines]

    def search_remote_code(self, query: str, workdir: str = "/workspace") -> Dict[str, Any]:
        """Executes full-text code search directly inside the remote MicroVM."""
        safe_q = query.replace("'", "'\\''")
        cmd = f"grep -rnI --exclude-dir='.git' --exclude-dir='node_modules' --exclude-dir='venv' --exclude-dir='__pycache__' '{safe_q}' {workdir} | head -n 100"
        return self.run_command(cmd, timeout=30, workdir=workdir)

    def git_commit_and_push(
        self,
        branch: str,
        message: str,
        repo_url: Optional[str] = None,
        auth_token: Optional[str] = None,
        workdir: str = "/workspace",
    ) -> Dict[str, Any]:
        """Commits changes and pushes directly from the remote MicroVM back to GitHub."""
        safe_msg = message.replace("'", "'\\''")
        set_remote_cmd = ""
        if repo_url and auth_token and "github.com" in repo_url:
            authed_url = repo_url.replace("https://", f"https://x-access-token:{auth_token}@")
            set_remote_cmd = f"git remote set-url origin '{authed_url}' && "

        cmd = (
            f"cd {workdir} && "
            f"git checkout -B {branch} && "
            "git add -A && "
            f"git commit -m '{safe_msg}' && "
            f"{set_remote_cmd}"
            f"git push -u origin {branch} --force"
        )
        return self.run_command(cmd, timeout=120, workdir=workdir)

    def close(self) -> None:
        """Kills and destroys the ephemeral cloud microVM."""
        if self._sandbox:
            try:
                self._sandbox.kill()
                logger.info(f"Destroyed E2B Firecracker MicroVM {self.sandbox_id}")
            except Exception as e:
                logger.warning(f"Error killing sandbox {self.sandbox_id}: {e}")
            finally:
                self._sandbox = None
                self.sandbox_id = None
                self._synced = False
