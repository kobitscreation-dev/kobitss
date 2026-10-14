"""
Interactive Sandbox PTY Terminal Service for Kobits.

Provides robust, cross-platform interactive pseudo-terminal / shell sessions attached
directly to active mission sandbox worktrees:
- Linux / macOS: Native pseudo-terminal via `pty.openpty()` with master/slave fds and termios resize.
- Windows: Interactive asynchronous subprocess with non-blocking stdin/stdout/stderr streaming.
- Supports raw binary bytes, ANSI escape sequences, live command execution (`pytest`, `git diff`, `ls`),
  terminal resize events (TIOCSWINSZ), and clean process lifecycle termination.
"""

import os
import sys
import time
import shutil
import asyncio
import logging
import subprocess
from typing import Optional, AsyncGenerator, Union

logger = logging.getLogger("kobits.pty_terminal")

IS_WINDOWS = (os.name == "nt")

if not IS_WINDOWS:
    try:
        import pty
        import termios
        import fcntl
        import struct
    except ImportError:
        pass


class PTYSession:
    """
    Manages an interactive, bidirectional pseudo-terminal shell session
    running hermetically inside a mission sandbox worktree.
    """

    def __init__(
        self,
        session_id: str,
        cwd: str,
        cols: int = 80,
        rows: int = 24,
        env: Optional[dict] = None,
    ):
        self.session_id = session_id
        self.cwd = os.path.abspath(cwd) if cwd and os.path.isdir(cwd) else os.getcwd()
        self.cols = cols
        self.rows = rows
        self.env = env or os.environ.copy()

        self.proc: Optional[asyncio.subprocess.Process] = None
        self.master_fd: Optional[int] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._listeners: list[asyncio.Queue] = []
        self._history_buffer: list[str] = []
        self._max_history = 1000
        self._closed = False
        self._lock = asyncio.Lock()
        self.last_activity = time.time()

    @property
    def is_alive(self) -> bool:
        """Returns True if the underlying shell process is currently running."""
        if self._closed or not self.proc:
            return False
        return self.proc.returncode is None

    def get_history(self) -> str:
        """Returns buffered terminal output history."""
        return "".join(self._history_buffer)

    async def start(self):
        """Spawns the interactive shell process inside the sandbox worktree."""
        async with self._lock:
            if self.is_alive:
                return

            if not os.path.isdir(self.cwd):
                os.makedirs(self.cwd, exist_ok=True)

            env = self.env.copy()
            env["TERM"] = "xterm-256color"
            env["COLORTERM"] = "truecolor"
            env["PYTHONUNBUFFERED"] = "1"
            # Ensure sandbox directory is in PYTHONPATH
            existing_py = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = (
                f"{self.cwd}{os.pathsep}{existing_py}" if existing_py else self.cwd
            )

            if IS_WINDOWS:
                await self._start_windows(env)
            else:
                await self._start_unix(env)

            self._reader_task = asyncio.create_task(self._read_loop())
            logger.info(
                f"PTY session {self.session_id} started (PID={self.proc.pid if self.proc else 'unknown'}) in {self.cwd}"
            )

    async def _start_windows(self, env: dict):
        """Spawns PowerShell or CMD interactive shell on Windows."""
        shell_cmd = shutil.which("powershell.exe") or shutil.which("cmd.exe") or "cmd.exe"
        args = []
        if "powershell" in shell_cmd.lower():
            args = ["-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass"]

        self.proc = await asyncio.create_subprocess_exec(
            shell_cmd,
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=self.cwd,
            env=env,
        )

    async def _start_unix(self, env: dict):
        """Spawns native pseudo-terminal via pty.openpty() on POSIX."""
        master, slave = pty.openpty()
        self.master_fd = master

        # Set initial terminal size
        try:
            winsize = struct.pack("HHHH", self.rows, self.cols, 0, 0)
            fcntl.ioctl(master, termios.TIOCSWINSZ, winsize)
        except Exception:
            pass

        shell = os.environ.get("SHELL") or shutil.which("bash") or shutil.which("sh") or "/bin/sh"

        self.proc = await asyncio.create_subprocess_exec(
            shell,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=self.cwd,
            env=env,
            close_fds=True,
        )
        os.close(slave)

    async def _read_loop(self):
        """Continuously reads output from PTY/subprocess and dispatches to subscriber queues."""
        try:
            if IS_WINDOWS:
                while not self._closed and self.proc and self.proc.stdout:
                    chunk = await self.proc.stdout.read(4096)
                    if not chunk:
                        break
                    text = chunk.decode("utf-8", errors="replace")
                    self._append_history(text)
                    self._broadcast(text)
            else:
                loop = asyncio.get_running_loop()
                while not self._closed and self.master_fd is not None:
                    # Non-blocking read from master_fd
                    chunk = await loop.run_in_executor(None, self._read_master_fd)
                    if not chunk:
                        break
                    text = chunk.decode("utf-8", errors="replace")
                    self._append_history(text)
                    self._broadcast(text)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"PTY read loop ended for {self.session_id}: {e}")
        finally:
            self._broadcast(f"\r\n[Process terminated with returncode {self.proc.returncode if self.proc else 'unknown'}]\r\n")

    def _read_master_fd(self) -> bytes:
        if self.master_fd is None:
            return b""
        try:
            return os.read(self.master_fd, 4096)
        except OSError:
            return b""

    def _append_history(self, text: str):
        self.last_activity = time.time()
        self._history_buffer.append(text)
        if len(self._history_buffer) > self._max_history:
            self._history_buffer = self._history_buffer[-self._max_history:]

    def _broadcast(self, text: str):
        for q in list(self._listeners):
            try:
                q.put_nowait(text)
            except asyncio.QueueFull:
                pass

    def subscribe(self) -> asyncio.Queue:
        """Subscribes an async queue to receive streamed output chunks."""
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._listeners.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        """Unsubscribes a listener queue."""
        if q in self._listeners:
            self._listeners.remove(q)

    async def stream_output(self) -> AsyncGenerator[str, None]:
        """Asynchronously yields output chunks as they arrive from the shell."""
        q = self.subscribe()
        try:
            while not self._closed or not q.empty():
                try:
                    chunk = await asyncio.wait_for(q.get(), timeout=0.2)
                    yield chunk
                except asyncio.TimeoutError:
                    if not self.is_alive and q.empty():
                        break
                    if self._closed and q.empty():
                        break
                    continue
        finally:
            self.unsubscribe(q)

    async def write(self, data: Union[str, bytes]):
        """Writes raw characters or bytes into the terminal process's stdin."""
        self.last_activity = time.time()
        if not self.is_alive or not self.proc:
            return

        if isinstance(data, str):
            payload = data.encode("utf-8")
        else:
            payload = data

        if IS_WINDOWS:
            if self.proc.stdin:
                try:
                    self.proc.stdin.write(payload)
                    await self.proc.stdin.drain()
                except (OSError, BrokenPipeError):
                    pass
        else:
            if self.master_fd is not None:
                try:
                    os.write(self.master_fd, payload)
                except OSError:
                    pass

    def resize(self, cols: int, rows: int):
        """Resizes the terminal geometry (cols x rows)."""
        self.cols = max(1, cols)
        self.rows = max(1, rows)

        if not IS_WINDOWS and self.master_fd is not None:
            try:
                winsize = struct.pack("HHHH", self.rows, self.cols, 0, 0)
                fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
            except Exception as e:
                logger.debug(f"Failed to resize terminal {self.session_id}: {e}")

    async def terminate(self):
        """Cleanly terminates the interactive process and frees file descriptors."""
        async with self._lock:
            self._closed = True
            if self._reader_task and not self._reader_task.done():
                self._reader_task.cancel()

            if self.proc:
                try:
                    if self.proc.stdin:
                        try:
                            self.proc.stdin.close()
                        except Exception:
                            pass
                    if self.proc.returncode is None:
                        if IS_WINDOWS:
                            try:
                                subprocess.run(
                                    ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                                    capture_output=True,
                                    timeout=5,
                                )
                            except Exception:
                                self.proc.terminate()
                        else:
                            self.proc.terminate()
                        try:
                            await asyncio.wait_for(self.proc.wait(), timeout=2.0)
                        except asyncio.TimeoutError:
                            self.proc.kill()
                            await self.proc.wait()
                except Exception:
                    pass

            if not IS_WINDOWS and self.master_fd is not None:
                try:
                    os.close(self.master_fd)
                except OSError:
                    pass
                self.master_fd = None


class PTYSessionManager:
    """
    Singleton registry managing interactive PTY terminal sessions mapped to missions.
    """

    def __init__(self):
        self._sessions: dict[str, PTYSession] = {}
        self._lock = asyncio.Lock()

    async def get_or_create_session(
        self,
        session_id: str,
        cwd: str,
        cols: int = 80,
        rows: int = 24,
        env: Optional[dict] = None,
    ) -> PTYSession:
        """Returns an active PTY session for the given ID, or creates and starts a new one."""
        async with self._lock:
            session = self._sessions.get(session_id)
            if session and session.is_alive:
                return session

            if session:
                await session.terminate()

            session = PTYSession(session_id=session_id, cwd=cwd, cols=cols, rows=rows, env=env)
            await session.start()
            self._sessions[session_id] = session
            return session

    def get_session(self, session_id: str) -> Optional[PTYSession]:
        """Retrieves existing active session if present."""
        return self._sessions.get(session_id)

    async def close_session(self, session_id: str):
        """Terminates and removes session."""
        async with self._lock:
            session = self._sessions.pop(session_id, None)
            if session:
                await session.terminate()

    async def cleanup_idle(self, max_idle_seconds: float = 1800.0) -> int:
        """Terminates and removes sessions idle longer than max_idle_seconds."""
        now = time.time()
        to_close = []
        async with self._lock:
            for sid, s in list(self._sessions.items()):
                if not s.is_alive or (now - getattr(s, "last_activity", now)) > max_idle_seconds:
                    to_close.append(sid)
            for sid in to_close:
                s = self._sessions.pop(sid, None)
                if s:
                    await s.terminate()
        return len(to_close)

    async def cleanup_all(self):
        """Terminates all active sessions (e.g. on application shutdown)."""
        async with self._lock:
            for s in list(self._sessions.values()):
                await s.terminate()
            self._sessions.clear()


pty_manager = PTYSessionManager()
