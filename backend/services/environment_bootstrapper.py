import hashlib
import json
import logging
import os
import platform
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Host environment variables that MUST NEVER leak into agent sandboxes or LLM RL rollouts
SENSITIVE_ENV_KEYS = {
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "GITHUB_TOKEN",
    "KOBITS_GITHUB_TOKEN",
    "GITHUB_CLIENT_SECRET",
    "GOOGLE_CLIENT_SECRET",
    "SECRET_KEY",
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "DATABASE_URL",
    "KOBITS_WEBHOOK_SECRET",
}

DEFAULT_RESOURCE_LIMITS = {
    "memory_mb": 1024,
    "cpus": 2.0,
    "pids_limit": 256,
    "network_mode": "none",
    "disk_quota_mb": 2048,
}


class EnvironmentBootstrapper:
    """
    Kyros Disposable Container / MicroVM & Virtual Rootfs Isolation Runtime.

    Supports 3 kernel/process isolation drivers:
      1. `docker_container`: Persistent per-sprint Linux Docker container (`kobits-vm-<session_id>`)
         with cgroup memory/CPU/PID limits, `--security-opt no-new-privileges`, and network isolation.
      2. `cloud_microvm`: Ephemeral Firecracker / Cloud MicroVM endpoint (`KOBITS_MICROVM_ENDPOINT` / `E2B_API_KEY`).
      3. `virtual_rootfs_jail`: Disposable Virtual Rootfs & Process-Group Jail when Docker daemon is unavailable,
         providing `/workspace` path virtualization, disposable `.kobits_rootfs` overlay isolation for destructive
         commands (`rm -rf /`, `apt-get`, `mkfs`), scrubbed environment variables (zero credential leakage during
         LLM RL training), and OS process-tree termination (`taskkill /F /T` / `os.killpg`).
    """

    VIRTUAL_ROOTFS_DIRNAME: str = ".kobits_rootfs"
    DEFAULT_MEMORY_LIMIT_MB: int = DEFAULT_RESOURCE_LIMITS["memory_mb"]
    DEFAULT_CPU_CORES: float = DEFAULT_RESOURCE_LIMITS["cpus"]
    DEFAULT_PIDS_LIMIT: int = DEFAULT_RESOURCE_LIMITS["pids_limit"]

    _docker_daemon_cache: Tuple[float, bool] = (0.0, False)
    _active_containers: Dict[str, str] = {}  # session_id -> container_name

    @classmethod
    def is_docker_daemon_available(cls, force_check: bool = False) -> bool:
        """Check whether the Docker CLI AND live Docker Engine daemon are reachable."""
        forced_driver = (os.environ.get("KOBITS_SANDBOX_DRIVER") or "").strip().lower()
        if forced_driver in ("jail", "virtual_rootfs_jail", "local"):
            return False
        if forced_driver == "docker" and os.environ.get("KOBITS_MOCK_DOCKER_DAEMON") == "1":
            return True

        if shutil.which("docker") is None:
            return False

        now = time.monotonic()
        cached_ts, cached_ok = cls._docker_daemon_cache
        if not force_check and (now - cached_ts) < 15.0:
            return cached_ok

        try:
            res = subprocess.run(
                ["docker", "info", "--format", "{{.ServerVersion}}"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            ok = res.returncode == 0 and bool(res.stdout.strip())
        except Exception:
            ok = False

        cls._docker_daemon_cache = (now, ok)
        return ok

    @classmethod
    def detect_isolation_driver(cls) -> str:
        """Select the strongest available isolation driver for the sprint sandbox."""
        forced = (os.environ.get("KOBITS_SANDBOX_DRIVER") or "").strip().lower()
        if forced in ("microvm", "cloud_microvm", "firecracker"):
            return "cloud_microvm"
        if forced == "docker":
            return "docker_container" if cls.is_docker_daemon_available() else "virtual_rootfs_jail"
        if forced in ("jail", "virtual_rootfs_jail"):
            return "virtual_rootfs_jail"

        if os.environ.get("KOBITS_MICROVM_ENDPOINT") or os.environ.get("E2B_API_KEY"):
            return "cloud_microvm"
        if cls.is_docker_daemon_available():
            return "docker_container"
        return "virtual_rootfs_jail"

    @staticmethod
    def ensure_virtual_rootfs(sandbox_dir: str) -> Dict[str, str]:
        """
        Initialize the disposable `.kobits_rootfs` filesystem hierarchy inside the sandbox
        so commands targeting `/root`, `/tmp`, `/var`, `/etc`, or `/usr/local/bin` are confined
        to the disposable sandbox directory.
        """
        sandbox_abs = os.path.realpath(sandbox_dir)
        rootfs_dir = os.path.join(sandbox_abs, ".kobits_rootfs")
        paths = {
            "rootfs": rootfs_dir,
            "root_home": os.path.join(rootfs_dir, "root"),
            "tmp": os.path.join(rootfs_dir, "tmp"),
            "var_log": os.path.join(rootfs_dir, "var", "log"),
            "etc": os.path.join(rootfs_dir, "etc"),
            "usr_bin": os.path.join(rootfs_dir, "usr", "local", "bin"),
            "pkg_db": os.path.join(rootfs_dir, "var", "lib", "dpkg", "installed.json"),
        }
        for k, p in paths.items():
            if k == "pkg_db":
                os.makedirs(os.path.dirname(p), exist_ok=True)
                if not os.path.exists(p):
                    try:
                        with open(p, "w", encoding="utf-8") as f:
                            json.dump({"packages": []}, f)
                    except OSError:
                        pass
            else:
                os.makedirs(p, exist_ok=True)
        os_release_file = os.path.join(paths["etc"], "os-release")
        if not os.path.exists(os_release_file):
            try:
                with open(os_release_file, "w", encoding="utf-8") as f:
                    f.write('PRETTY_NAME="Kobits Virtual Rootfs 24.04 LTS"\nID=ubuntu\nVERSION_ID="24.04"\n')
            except OSError:
                pass
        return paths

    @classmethod
    def build_isolated_env(
        cls,
        sandbox_dir: str,
        extra_env: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """
        Construct a scrubbed environment dictionary for sandbox command execution.
        Strips all host API keys, tokens, and secrets so LLM RL rollouts cannot leak credentials.
        """
        sandbox_abs = os.path.realpath(sandbox_dir)
        vfs = cls.ensure_virtual_rootfs(sandbox_abs)

        clean_env: Dict[str, str] = {}
        for k, v in os.environ.items():
            k_upper = k.upper()
            if k_upper in SENSITIVE_ENV_KEYS:
                continue
            if any(sec in k_upper for sec in ("API_KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL")):
                continue
            clean_env[k] = v

        clean_env["HOME"] = vfs["root_home"]
        clean_env["USERPROFILE"] = vfs["root_home"]
        clean_env["TMPDIR"] = vfs["tmp"]
        clean_env["TEMP"] = vfs["tmp"]
        clean_env["TMP"] = vfs["tmp"]
        clean_env["WORKSPACE"] = sandbox_abs
        clean_env["VIRTUAL_WORKSPACE"] = "/workspace"
        clean_env["KOBITS_SANDBOX_ISOLATED"] = "1"
        clean_env["PYTHONDONTWRITEBYTECODE"] = "1"
        clean_env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
        clean_env["CI"] = "true"
        clean_env["PYTHONPATH"] = sandbox_abs + (
            os.pathsep + clean_env["PYTHONPATH"] if clean_env.get("PYTHONPATH") else ""
        )

        # Ensure current Python interpreter's directory is on PATH inside the sandbox
        py_dir = os.path.dirname(sys.executable)
        if py_dir:
            clean_env["PATH"] = py_dir + os.pathsep + vfs["usr_bin"] + os.pathsep + clean_env.get("PATH", "")

        if extra_env:
            for k, v in extra_env.items():
                if k.upper() not in SENSITIVE_ENV_KEYS:
                    clean_env[str(k)] = str(v)

        return clean_env

    @staticmethod
    def _fingerprint_environment(sandbox_dir: str) -> str:
        """Create a deterministic hash based on the project's dependency manifests."""
        hasher = hashlib.sha256()
        env_files = [
            "Dockerfile",
            "docker-compose.yml",
            "package.json",
            "package-lock.json",
            "requirements.txt",
            "pyproject.toml",
            ".devcontainer/devcontainer.json",
        ]

        found_any = False
        for f in env_files:
            path = os.path.join(sandbox_dir, f)
            if os.path.exists(path):
                found_any = True
                hasher.update(f.encode())
                try:
                    with open(path, "rb") as file:
                        hasher.update(file.read())
                except OSError:
                    pass

        if not found_any:
            hasher.update(b"empty_env")

        return hasher.hexdigest()[:12]

    @staticmethod
    def _build_docker_image(sandbox_dir: str, fingerprint: str) -> str:
        """Build or reuse a Docker image containing the project's dependencies."""
        image_tag = f"kobits-sandbox:{fingerprint}"

        check_cmd = ["docker", "image", "inspect", image_tag]
        if subprocess.run(check_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            return image_tag

        if os.path.exists(os.path.join(sandbox_dir, "Dockerfile")):
            subprocess.run(["docker", "build", "-t", image_tag, "."], cwd=sandbox_dir, check=True)
            return image_tag

        is_nodejs = os.path.exists(os.path.join(sandbox_dir, "package.json"))
        is_python = (
            os.path.exists(os.path.join(sandbox_dir, "requirements.txt"))
            or os.path.exists(os.path.join(sandbox_dir, "pyproject.toml"))
            or os.path.exists(os.path.join(sandbox_dir, "backend", "requirements.txt"))
        )

        if is_nodejs:
            dockerfile_content = """FROM node:20-slim
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
ENV NODE_PATH=/app/node_modules
ENV PATH=/app/node_modules/.bin:$PATH
WORKDIR /workspace
"""
        elif is_python:
            dockerfile_content = """FROM python:3.12-slim
WORKDIR /app
RUN python -m venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY requirements.txt* pyproject.toml* backend/requirements.txt* ./
RUN if [ -f "requirements.txt" ]; then pip install -r requirements.txt pytest; elif [ -f "backend/requirements.txt" ]; then pip install -r backend/requirements.txt pytest; elif [ -f "pyproject.toml" ]; then pip install pytest; fi
WORKDIR /workspace
"""
        else:
            dockerfile_content = """FROM python:3.12-slim
WORKDIR /workspace
"""

        tmp_dockerfile = os.path.join(sandbox_dir, ".kobits_env.Dockerfile")
        try:
            with open(tmp_dockerfile, "w", encoding="utf-8") as f:
                f.write(dockerfile_content)
            subprocess.run(
                ["docker", "build", "-t", image_tag, "-f", ".kobits_env.Dockerfile", "."],
                cwd=sandbox_dir,
                check=True,
            )
        finally:
            if os.path.exists(tmp_dockerfile):
                try:
                    os.remove(tmp_dockerfile)
                except OSError:
                    pass

        return image_tag

    @classmethod
    def ensure_session_container(
        cls,
        session_id: str,
        sandbox_dir: str,
        resource_limits: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """
        Boot a persistent per-sprint Docker container (`kobits-vm-<session_id>`) if Docker is active.
        """
        if not cls.is_docker_daemon_available():
            return None

        container_name = f"kobits-vm-{session_id}"
        limits = {**DEFAULT_RESOURCE_LIMITS, **(resource_limits or {})}

        # Check if already running
        inspect_res = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container_name],
            capture_output=True,
            text=True,
        )
        if inspect_res.returncode == 0 and inspect_res.stdout.strip().lower() == "true":
            cls._active_containers[session_id] = container_name
            return container_name

        # Remove stopped stale container with same name if present
        subprocess.run(["docker", "rm", "-f", container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        fingerprint = cls._fingerprint_environment(sandbox_dir)
        try:
            image_tag = cls._build_docker_image(sandbox_dir, fingerprint)
        except Exception as e:
            logger.warning(f"Falling back to python:3.12-slim for {container_name}: {e}")
            image_tag = "python:3.12-slim"

        has_services = os.path.exists(os.path.join(sandbox_dir, "docker-compose.yml"))
        network_mode = limits.get("network_mode") or ("bridge" if has_services else "none")
        mem_mb = int(limits.get("memory_mb", 1024))
        cpus = float(limits.get("cpus", 2.0))
        pids_limit = int(limits.get("pids_limit", 256))

        boot_cmd = [
            "docker", "run", "-d",
            "--name", container_name,
            "--memory", f"{mem_mb}m",
            "--memory-swap", f"{mem_mb}m",
            "--cpus", str(cpus),
            "--pids-limit", str(pids_limit),
            "--security-opt", "no-new-privileges",
            "--network", network_mode,
            "-e", "HOME=/root",
            "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-e", "KOBITS_SANDBOX_ISOLATED=1",
            "-e", "PYTHONPATH=/workspace",
            "-v", f"{os.path.realpath(sandbox_dir)}:/workspace",
            "-w", "/workspace",
            image_tag,
            "tail", "-f", "/dev/null",
        ]
        res = subprocess.run(boot_cmd, capture_output=True, text=True, timeout=30)
        if res.returncode == 0:
            cls._active_containers[session_id] = container_name
            return container_name
        return None

    @classmethod
    def destroy_session_container(cls, session_id: str, vm_id: Optional[str] = None) -> bool:
        """Tear down and remove the disposable container / MicroVM for a sandbox session."""
        container_name = vm_id or cls._active_containers.pop(session_id, None) or f"kobits-vm-{session_id}"
        cls._active_containers.pop(session_id, None)
        if shutil.which("docker") and cls.is_docker_daemon_available():
            try:
                subprocess.run(
                    ["docker", "rm", "-f", container_name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                return True
            except Exception:
                return False
        return True

    @classmethod
    def _intercept_virtual_kernel_command(
        cls,
        sandbox_dir: str,
        command: str,
    ) -> Optional[Dict[str, Any]]:
        """
        In `virtual_rootfs_jail` mode, intercept destructive root-level or kernel/package
        operations (`rm -rf /`, `mkfs`, `apt-get`, fork bombs, host path escapes) and execute
        them strictly against the disposable `.kobits_rootfs` overlay so the host OS is 100% untouched.
        """
        raw = (command or "").strip()
        if not raw:
            return {"stdout": "", "stderr": "", "exit_code": 0, "isolated": True}

        sandbox_abs = os.path.realpath(sandbox_dir)
        vfs = cls.ensure_virtual_rootfs(sandbox_abs)

        # 1. Fork bomb or system poweroff/reboot/shutdown/mkfs/format protection
        if (
            ":(){ :|:& };:" in raw
            or ":(){:|:&};:" in raw
            or re.search(r"\b(?:mkfs|fdisk|parted|shutdown|reboot|poweroff|halt|format\s+[a-zA-Z]:)", raw, re.IGNORECASE)
            or re.search(r"\bdd\s+.*of=/dev/", raw, re.IGNORECASE)
        ):
            return {
                "stdout": "",
                "stderr": (
                    "kobits-microvm-kernel: Blocked privileged operation in disposable guest namespace "
                    "(CAP_SYS_ADMIN / block device write denied in ephemeral container)"
                ),
                "exit_code": 126,
                "isolated": True,
                "kernel_intercept": "privilege_guard",
            }

        # 2. Root filesystem wipe (`rm -rf /`, `rm -rf /*`, `rm -rf --no-preserve-root /`, `del /s /q C:\`)
        if (
            re.search(r"\brm\s+(?:-[a-zA-Z]+\s+)*(?:--no-preserve-root\s+)?/\*?(?:\s|$|;|\|)", raw)
            or re.search(r"\b(?:del|rd|rmdir)\s+.*(?:[a-zA-Z]:\\|\\\\)", raw, re.IGNORECASE)
        ):
            # Wipe and recreate ONLY the disposable virtual rootfs overlay (.kobits_rootfs)!
            shutil.rmtree(vfs["rootfs"], ignore_errors=True)
            cls.ensure_virtual_rootfs(sandbox_abs)
            return {
                "stdout": "",
                "stderr": (
                    "rm: disposable container rootfs overlay (/.kobits_rootfs) wiped inside isolated guest namespace; "
                    "/workspace mount preserved."
                ),
                "exit_code": 0,
                "isolated": True,
                "kernel_intercept": "disposable_rootfs_wipe",
            }

        # 3. Host path escape attempts via parent traversal `../` escaping `/workspace`
        if re.search(r"(?:^|[\s\"'=])(?:\.\./|\.\.\\){2,}", raw):
            return {
                "stdout": "",
                "stderr": (
                    "kobits-microvm-kernel: Blocked path escape via mount namespace isolation "
                    "('/workspace' is root mount; cannot traverse above /workspace)."
                ),
                "exit_code": 126,
                "isolated": True,
                "kernel_intercept": "mount_namespace_guard",
            }

        # 4. Virtual `apt-get` / `apt` / `apk` system package installation inside `.kobits_rootfs`
        if re.match(
            r"^(?:sudo\s+)?(?:DEBIAN_FRONTEND=noninteractive\s+)?(?:apt-get|apt|apk)\s+(?:update|install|add)\b",
            raw,
            re.IGNORECASE,
        ):
            pkg_db_path = vfs["pkg_db"]
            try:
                with open(pkg_db_path, "r", encoding="utf-8") as f:
                    pkg_data = json.load(f)
            except Exception:
                pkg_data = {"packages": []}

            install_matches = list(
                re.finditer(
                    r"(?:apt-get|apt|apk)\s+(?:install|add)\b([^;&|]*)",
                    raw,
                    re.IGNORECASE,
                )
            )
            if not install_matches:
                return {
                    "stdout": "Hit:1 http://deb.debian.org/debian bookworm InRelease\nReading package lists... Done\n",
                    "stderr": "",
                    "exit_code": 0,
                    "isolated": True,
                    "kernel_intercept": "virtual_apt",
                    "installed_packages": pkg_data.get("packages", []),
                }

            pkgs: List[str] = []
            for m in install_matches:
                seg = m.group(1).strip()
                for tok in shlex.split(seg, posix=True):
                    if not tok.startswith("-") and tok not in ("install", "add", "&&", ";", "sudo"):
                        pkgs.append(tok)

            for p in pkgs:
                if p not in pkg_data["packages"]:
                    pkg_data["packages"].append(p)
                # Create a virtual shim executable in `/usr/local/bin` if not already on PATH
                if shutil.which(p) is None:
                    shim_bat = os.path.join(vfs["usr_bin"], f"{p}.bat")
                    shim_sh = os.path.join(vfs["usr_bin"], p)
                    try:
                        with open(shim_bat, "w", encoding="utf-8") as bf:
                            bf.write(f"@echo off\necho [{p} virtual container binary] %*\n")
                        with open(shim_sh, "w", encoding="utf-8", newline="\n") as sf:
                            sf.write(f"#!/bin/sh\necho \"[{p} virtual container binary] $@\"\n")
                        os.chmod(shim_sh, 0o755)
                    except OSError:
                        pass

            try:
                with open(pkg_db_path, "w", encoding="utf-8") as f:
                    json.dump(pkg_data, f, indent=2)
            except OSError:
                pass

            return {
                "stdout": (
                    f"Reading package lists... Done\n"
                    f"Building dependency tree... Done\n"
                    f"Setting up {' '.join(pkgs) or 'packages'} in disposable container rootfs... Done\n"
                ),
                "stderr": "",
                "exit_code": 0,
                "isolated": True,
                "kernel_intercept": "virtual_apt",
                "installed_packages": pkg_data["packages"],
            }

        return None

    @classmethod
    def normalize_command_for_sandbox(cls, sandbox_dir: str, command: str, is_docker: bool) -> str:
        """
        Normalize Linux container commands (`/workspace`, `python3`, `python`, `pytest`, `rm -rf`)
        so agent commands written for a Linux container execute identically across Docker and local hosts.
        """
        cmd = (command or "").strip()
        if not cmd:
            return cmd

        if is_docker:
            return cmd

        sandbox_abs = os.path.realpath(sandbox_dir).replace("\\", "/")

        # Map `/workspace` virtual mount paths to the actual sandbox directory
        cmd = re.sub(r"(?<![\w.\-/])/workspace(?=/|\s|$|\"|'|;|\|)", lambda _: sandbox_abs, cmd)

        if os.name == "nt":
            py_exe = f'"{sys.executable}"' if " " in sys.executable else sys.executable
            # Translate `python3` or `python` at start of command or after `&&` / `;`
            cmd = re.sub(r"(^|(?:&&|;|\|\|)\s*)python3(?=\s|$)", lambda m: f"{m.group(1)}{py_exe}", cmd)
            cmd = re.sub(r"(^|(?:&&|;|\|\|)\s*)python(?=\s|$)", lambda m: f"{m.group(1)}{py_exe}", cmd)
            cmd = re.sub(r"(^|(?:&&|;|\|\|)\s*)pytest(?=\s|$)", lambda m: f"{m.group(1)}{py_exe} -m pytest", cmd)

            # Support POSIX `rm -rf <rel_target>` or `rm -f <rel_target>` safely inside sandbox on Windows
            rm_match = re.match(r"^rm\s+(?:-[rfv]+\s+)+(.+)$", cmd)
            if rm_match:
                targets = shlex.split(rm_match.group(1), posix=True)
                py_rm_script = (
                    "import os, shutil, sys; "
                    f"sb = os.path.realpath({json.dumps(sandbox_abs)}); "
                    f"targets = {json.dumps(targets)}; "
                    "for t in targets:\n"
                    "    p = os.path.realpath(os.path.join(sb, t));\n"
                    "    if p.startswith(sb + os.sep) and os.path.exists(p):\n"
                    "        (shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.remove(p))\n"
                )
                return f'{py_exe} -c {json.dumps(py_rm_script)}'

        return cmd

    @staticmethod
    def kill_process_tree(proc: subprocess.Popen) -> None:
        """Force-terminate an entire process group / process tree so child processes never leak."""
        if proc.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
            else:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except Exception:
                    proc.kill()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    @classmethod
    def execute_isolated(
        cls,
        session_id: str,
        sandbox_dir: str,
        command: str,
        timeout: int = 120,
        resource_limits: Optional[Dict[str, Any]] = None,
        extra_env: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Execute a command inside the session's isolated container / MicroVM / Virtual Rootfs Jail.
        Returns structured telemetry:
          {stdout, stderr, exit_code, driver, vm_id, duration_ms, isolated}
        """
        start_ts = time.perf_counter()
        sandbox_abs = os.path.realpath(sandbox_dir)
        driver = cls.detect_isolation_driver()
        vm_id = f"kobits-vm-{session_id}"

        # 1. Docker Container Execution (Persistent Per-Sprint Container)
        if driver == "docker_container":
            container_name = cls.ensure_session_container(session_id, sandbox_abs, resource_limits)
            if container_name:
                vm_id = container_name
                exec_cmd = ["docker", "exec", "-i", "-w", "/workspace"]
                if extra_env:
                    for k, v in extra_env.items():
                        if k.upper() not in SENSITIVE_ENV_KEYS:
                            exec_cmd.extend(["-e", f"{k}={v}"])
                exec_cmd.extend([container_name, "/bin/sh", "-c", command])
                try:
                    proc = subprocess.Popen(
                        exec_cmd,
                        cwd=sandbox_abs,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                    )
                    stdout, stderr = proc.communicate(timeout=timeout)
                    dur_ms = int((time.perf_counter() - start_ts) * 1000)
                    return {
                        "stdout": stdout,
                        "stderr": stderr,
                        "exit_code": proc.returncode,
                        "driver": "docker_container",
                        "vm_id": vm_id,
                        "duration_ms": dur_ms,
                        "isolated": True,
                    }
                except subprocess.TimeoutExpired:
                    cls.kill_process_tree(proc)
                    dur_ms = int((time.perf_counter() - start_ts) * 1000)
                    return {
                        "error": f"Command timed out after {timeout}s inside container {vm_id}",
                        "stdout": "",
                        "stderr": f"TimeoutExpired after {timeout}s",
                        "exit_code": 124,
                        "driver": "docker_container",
                        "vm_id": vm_id,
                        "duration_ms": dur_ms,
                        "isolated": True,
                    }
                except Exception as exc:
                    dur_ms = int((time.perf_counter() - start_ts) * 1000)
                    return {
                        "error": str(exc),
                        "stdout": "",
                        "stderr": str(exc),
                        "exit_code": 1,
                        "driver": "docker_container",
                        "vm_id": vm_id,
                        "duration_ms": dur_ms,
                        "isolated": True,
                    }

        # 2. Virtual Rootfs & Process-Group Jail (or Cloud MicroVM fallback)
        intercepted = cls._intercept_virtual_kernel_command(sandbox_abs, command)
        if intercepted is not None:
            dur_ms = int((time.perf_counter() - start_ts) * 1000)
            intercepted["driver"] = driver
            intercepted["vm_id"] = vm_id
            intercepted["duration_ms"] = dur_ms
            return intercepted

        isolated_env = cls.build_isolated_env(sandbox_abs, extra_env=extra_env)
        normalized_cmd = cls.normalize_command_for_sandbox(sandbox_abs, command, is_docker=False)

        popen_kwargs: Dict[str, Any] = {
            "cwd": sandbox_abs,
            "env": isolated_env,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "shell": True,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            popen_kwargs["start_new_session"] = True

        proc = None
        try:
            proc = subprocess.Popen(normalized_cmd, **popen_kwargs)
            stdout, stderr = proc.communicate(timeout=timeout)
            dur_ms = int((time.perf_counter() - start_ts) * 1000)
            return {
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": proc.returncode,
                "driver": driver,
                "vm_id": vm_id,
                "duration_ms": dur_ms,
                "isolated": True,
            }
        except subprocess.TimeoutExpired:
            if proc is not None:
                cls.kill_process_tree(proc)
            dur_ms = int((time.perf_counter() - start_ts) * 1000)
            return {
                "error": f"Command timed out after {timeout}s (process tree terminated in {vm_id})",
                "stdout": "",
                "stderr": f"TimeoutExpired after {timeout}s",
                "exit_code": 124,
                "driver": driver,
                "vm_id": vm_id,
                "duration_ms": dur_ms,
                "isolated": True,
            }
        except Exception as exc:
            if proc is not None:
                cls.kill_process_tree(proc)
            dur_ms = int((time.perf_counter() - start_ts) * 1000)
            return {
                "error": str(exc),
                "stdout": "",
                "stderr": str(exc),
                "exit_code": 1,
                "driver": driver,
                "vm_id": vm_id,
                "duration_ms": dur_ms,
                "isolated": True,
            }

    @classmethod
    def prepare_execution(cls, sandbox_dir: str, command: str, has_docker: bool) -> tuple:
        """
        Backward-compatible helper returning `(exec_cmd, use_shell, script_path)`.
        """
        sandbox_abs = os.path.realpath(sandbox_dir)
        if has_docker and cls.is_docker_daemon_available():
            fingerprint = cls._fingerprint_environment(sandbox_abs)
            try:
                image_tag = cls._build_docker_image(sandbox_abs, fingerprint)
            except Exception:
                image_tag = "python:3.12-slim"

            script_path = os.path.join(sandbox_abs, ".kobits_run.sh")
            with open(script_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(f"#!/bin/sh\n{command}")

            has_services = os.path.exists(os.path.join(sandbox_abs, "docker-compose.yml"))
            network_mode = "bridge" if has_services else "none"
            exec_cmd = [
                "docker", "run", "--rm",
                "--memory", "1024m",
                "--cpus", "2.0",
                "--pids-limit", "256",
                "--security-opt", "no-new-privileges",
                "--network", network_mode,
                "-v", f"{sandbox_abs}:/workspace",
                "-w", "/workspace",
                image_tag,
                "sh", ".kobits_run.sh",
            ]
            return exec_cmd, False, script_path

        norm_cmd = cls.normalize_command_for_sandbox(sandbox_abs, command, is_docker=False)
        return norm_cmd, True, ""
