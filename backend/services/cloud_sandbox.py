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

    @classmethod
    def is_configured(cls) -> bool:
        """Check whether E2B_API_KEY is available."""
        return bool(os.environ.get("E2B_API_KEY"))

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
