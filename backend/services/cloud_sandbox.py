"""
Cloud MicroVM Sandbox Service for Kobits (E2B Firecracker Engine).
Spawns ephemeral, kernel-isolated Linux MicroVMs in <150ms per mission
with full root access, dedicated filesystem, and zero host blast radius.
"""

import os
import time
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class CloudMicroVMSandbox:
    """
    E2B Firecracker MicroVM Sandbox Manager.
    Used by Kyros and Devin for instant remote execution.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("E2B_API_KEY")
        self._sandbox = None
        self.sandbox_id: Optional[str] = None

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def start(self) -> str:
        """Spawns a fresh Firecracker MicroVM in the E2B cloud."""
        if not self.api_key:
            raise ValueError("E2B_API_KEY not configured. Get your free key at https://e2b.dev")

        from e2b import Sandbox
        start_ts = time.perf_counter()
        self._sandbox = Sandbox(api_key=self.api_key)
        self.sandbox_id = getattr(self._sandbox, "sandbox_id", str(id(self._sandbox)))
        dur_ms = int((time.perf_counter() - start_ts) * 1000)
        logger.info(f"Spawned E2B Firecracker MicroVM {self.sandbox_id} in {dur_ms}ms")
        return self.sandbox_id

    def run_command(self, command: str, timeout: int = 120, workdir: str = "/workspace") -> Dict[str, Any]:
        """Executes a command inside the isolated cloud MicroVM."""
        if not self._sandbox:
            self.start()

        start_ts = time.perf_counter()
        try:
            # Execute command inside E2B Firecracker Linux microVM
            res = self._sandbox.commands.run(command, timeout=timeout)
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
            except Exception as e:
                logger.warning(f"Error killing sandbox {self.sandbox_id}: {e}")
            finally:
                self._sandbox = None
                self.sandbox_id = None
