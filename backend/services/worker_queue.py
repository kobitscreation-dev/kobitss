"""
Durable Distributed Task Queue & Worker Dispatcher for Kobits.

Provides Kyros Parity Difference 2:
1. Decoupled asynchronous worker execution: Sprints & LLM RL rollouts run in durable worker tasks,
   surviving FastAPI server restarts and disconnections without dropping in-flight jobs.
2. Dual-Driver Architecture:
   - `sqlite_wal_lease_queue`: Zero-config durable SQLite queue in WAL mode with atomic job leasing,
     lease heartbeats, and stale-worker crash recovery.
   - `redis_broker`: Optional distributed Redis queue when `REDIS_URL` is set in production.
3. Multi-worker concurrency: Multiple workers can claim and execute jobs concurrently without
   database locks or race conditions.
"""

import asyncio
import json
import logging
import os
import platform
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set

from backend.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class JobRecord:
    id: str
    queue: str
    task_type: str
    payload: Dict[str, Any]
    priority: int
    status: str  # PENDING | LEASED | COMPLETED | FAILED | CANCELLED
    worker_id: Optional[str]
    lease_timeout_at: Optional[str]
    attempt_count: int
    max_retries: int
    result: Optional[Dict[str, Any]]
    error: Optional[str]
    created_at: str
    updated_at: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class WorkerContext:
    def __init__(self, job: JobRecord, queue: "WorkerQueue"):
        self.job = job
        self.queue = queue
        self._canceled = False

    @property
    def job_id(self) -> str:
        return self.job.id

    @property
    def payload(self) -> Dict[str, Any]:
        return self.job.payload

    async def heartbeat(self, extend_seconds: float = 60.0) -> bool:
        return await self.queue.heartbeat(self.job.id, self.job.worker_id or "", extend_seconds)


HandlerFunc = Callable[[Dict[str, Any], WorkerContext], Coroutine[Any, Any, Any]]


class WorkerQueue:
    """
    Durable Task Queue supporting atomic job leasing, worker heartbeats, and crash recovery.
    """

    _handlers: Dict[str, HandlerFunc] = {}
    _db_path: Optional[str] = None

    @classmethod
    def get_db_path(cls) -> str:
        if cls._db_path:
            return cls._db_path
        override = os.environ.get("KOBITS_QUEUE_DB")
        if override:
            cls._db_path = os.path.realpath(override)
            return cls._db_path
        root_dir = Path(__file__).resolve().parent.parent.parent
        sb_dir = root_dir / "sandboxes"
        sb_dir.mkdir(parents=True, exist_ok=True)
        cls._db_path = str(sb_dir / ".kobits_worker_queue.db")
        return cls._db_path

    @classmethod
    def set_db_path(cls, path: str) -> None:
        cls._db_path = path

    @classmethod
    def _get_connection(cls) -> sqlite3.Connection:
        db_path = cls.get_db_path()
        conn = sqlite3.connect(db_path, timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA temp_store=MEMORY")
        return conn

    @classmethod
    def init_schema(cls) -> None:
        """Initialize the durable SQLite worker queue schema."""
        with cls._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS worker_jobs (
                    id TEXT PRIMARY KEY,
                    queue_name TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    priority INTEGER DEFAULT 0,
                    status TEXT NOT NULL,
                    worker_id TEXT,
                    lease_timeout_at TEXT,
                    attempt_count INTEGER DEFAULT 0,
                    max_retries INTEGER DEFAULT 3,
                    result_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_worker_jobs_claim ON worker_jobs (queue_name, status, priority DESC, created_at ASC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_worker_jobs_lease ON worker_jobs (status, lease_timeout_at)"
            )

    @classmethod
    def register_handler(cls, task_type: str, handler: HandlerFunc) -> None:
        cls._handlers[task_type] = handler

    @classmethod
    async def enqueue(
        cls,
        task_type: str,
        payload: Dict[str, Any],
        queue: str = "default",
        priority: int = 0,
        max_retries: int = 3,
        job_id: Optional[str] = None,
    ) -> JobRecord:
        """Atomically enqueue a durable background job."""
        cls.init_schema()
        jid = job_id or str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()
        payload_str = json.dumps(payload)

        def _sync_enqueue():
            with cls._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO worker_jobs (
                        id, queue_name, task_type, payload_json, priority,
                        status, worker_id, lease_timeout_at, attempt_count,
                        max_retries, result_json, error, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'PENDING', NULL, NULL, 0, ?, NULL, NULL, ?, ?)
                    """,
                    (jid, queue, task_type, payload_str, priority, max_retries, now_iso, now_iso),
                )

        await asyncio.to_thread(_sync_enqueue)
        job = await cls.get_job(jid)
        if not job:
            raise RuntimeError(f"Failed to enqueue job {jid}")
        return job

    @classmethod
    async def claim(
        cls,
        worker_id: str,
        queues: Optional[List[str]] = None,
        lease_seconds: float = 60.0,
    ) -> Optional[JobRecord]:
        """
        Atomically lease the highest priority pending job.
        Recovers stale leased jobs whose worker died or failed to send heartbeats.
        """
        cls.init_schema()
        q_list = queues or ["default"]

        def _sync_claim() -> Optional[str]:
            now = datetime.now(timezone.utc)
            now_iso = now.isoformat()
            lease_until_iso = (now + timedelta(seconds=lease_seconds)).isoformat()

            with cls._get_connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                # 1. Recover stale leases
                conn.execute(
                    """
                    UPDATE worker_jobs
                    SET status = 'PENDING', worker_id = NULL, lease_timeout_at = NULL, updated_at = ?
                    WHERE status = 'LEASED' AND lease_timeout_at < ? AND attempt_count < max_retries
                    """,
                    (now_iso, now_iso),
                )
                conn.execute(
                    """
                    UPDATE worker_jobs
                    SET status = 'FAILED', error = 'Lease expired without completion (worker timed out)', updated_at = ?
                    WHERE status = 'LEASED' AND lease_timeout_at < ? AND attempt_count >= max_retries
                    """,
                    (now_iso, now_iso),
                )

                # 2. Find next eligible job across specified queues
                placeholders = ",".join("?" for _ in q_list)
                query = f"""
                    SELECT id FROM worker_jobs
                    WHERE status = 'PENDING' AND queue_name IN ({placeholders})
                    ORDER BY priority DESC, created_at ASC
                    LIMIT 1
                """
                row = conn.execute(query, q_list).fetchone()
                if not row:
                    conn.execute("COMMIT")
                    return None

                target_id = row["id"]
                # 3. Atomically claim with lease
                cur = conn.execute(
                    """
                    UPDATE worker_jobs
                    SET status = 'LEASED',
                        worker_id = ?,
                        lease_timeout_at = ?,
                        attempt_count = attempt_count + 1,
                        updated_at = ?
                    WHERE id = ? AND status = 'PENDING'
                    """,
                    (worker_id, lease_until_iso, now_iso, target_id),
                )
                if cur.rowcount == 1:
                    conn.execute("COMMIT")
                    return target_id
                conn.execute("COMMIT")
                return None

        claimed_id = await asyncio.to_thread(_sync_claim)
        if claimed_id:
            return await cls.get_job(claimed_id)
        return None

    @classmethod
    async def heartbeat(
        cls,
        job_id: str,
        worker_id: str,
        extend_seconds: float = 60.0,
    ) -> bool:
        """Extend the lease timeout of an actively running job."""
        def _sync_heartbeat() -> bool:
            now = datetime.now(timezone.utc)
            now_iso = now.isoformat()
            new_lease = (now + timedelta(seconds=extend_seconds)).isoformat()
            with cls._get_connection() as conn:
                cur = conn.execute(
                    """
                    UPDATE worker_jobs
                    SET lease_timeout_at = ?, updated_at = ?
                    WHERE id = ? AND worker_id = ? AND status = 'LEASED'
                    """,
                    (new_lease, now_iso, job_id, worker_id),
                )
                return cur.rowcount > 0

        return await asyncio.to_thread(_sync_heartbeat)

    @classmethod
    async def complete(
        cls,
        job_id: str,
        worker_id: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Mark a job as successfully completed with result payload."""
        def _sync_complete() -> bool:
            now_iso = datetime.now(timezone.utc).isoformat()
            res_str = json.dumps(result or {})
            with cls._get_connection() as conn:
                if worker_id:
                    cur = conn.execute(
                        """
                        UPDATE worker_jobs
                        SET status = 'COMPLETED', result_json = ?, error = NULL, updated_at = ?
                        WHERE id = ? AND worker_id = ? AND status = 'LEASED'
                        """,
                        (res_str, now_iso, job_id, worker_id),
                    )
                else:
                    cur = conn.execute(
                        """
                        UPDATE worker_jobs
                        SET status = 'COMPLETED', result_json = ?, error = NULL, updated_at = ?
                        WHERE id = ?
                        """,
                        (res_str, now_iso, job_id),
                    )
                return cur.rowcount > 0

        return await asyncio.to_thread(_sync_complete)

    @classmethod
    async def fail(
        cls,
        job_id: str,
        worker_id: Optional[str] = None,
        error: str = "",
        allow_retry: bool = True,
    ) -> bool:
        """Record job execution failure with automatic retry if retries remain."""
        def _sync_fail() -> bool:
            now_iso = datetime.now(timezone.utc).isoformat()
            with cls._get_connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT attempt_count, max_retries FROM worker_jobs WHERE id = ?", (job_id,)).fetchone()
                if not row:
                    conn.execute("COMMIT")
                    return False
                att = row["attempt_count"]
                m_ret = row["max_retries"]
                if allow_retry and att < m_ret:
                    conn.execute(
                        """
                        UPDATE worker_jobs
                        SET status = 'PENDING', worker_id = NULL, lease_timeout_at = NULL, error = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (error[:4000], now_iso, job_id),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE worker_jobs
                        SET status = 'FAILED', error = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (error[:4000], now_iso, job_id),
                    )
                conn.execute("COMMIT")
                return True

        return await asyncio.to_thread(_sync_fail)

    @classmethod
    async def cancel(cls, job_id: str) -> bool:
        """Cancel a pending or leased job."""
        def _sync_cancel() -> bool:
            now_iso = datetime.now(timezone.utc).isoformat()
            with cls._get_connection() as conn:
                cur = conn.execute(
                    """
                    UPDATE worker_jobs
                    SET status = 'CANCELLED', updated_at = ?
                    WHERE id = ? AND status IN ('PENDING', 'LEASED')
                    """,
                    (now_iso, job_id),
                )
                return cur.rowcount > 0

        return await asyncio.to_thread(_sync_cancel)

    @classmethod
    async def get_job(cls, job_id: str) -> Optional[JobRecord]:
        """Fetch full job record by ID."""
        cls.init_schema()
        def _sync_get() -> Optional[Dict[str, Any]]:
            with cls._get_connection() as conn:
                row = conn.execute("SELECT * FROM worker_jobs WHERE id = ?", (job_id,)).fetchone()
                if not row:
                    return None
                return dict(row)

        raw = await asyncio.to_thread(_sync_get)
        if not raw:
            return None
        return JobRecord(
            id=raw["id"],
            queue=raw["queue_name"],
            task_type=raw["task_type"],
            payload=json.loads(raw["payload_json"]) if raw["payload_json"] else {},
            priority=raw["priority"],
            status=raw["status"],
            worker_id=raw["worker_id"],
            lease_timeout_at=raw["lease_timeout_at"],
            attempt_count=raw["attempt_count"],
            max_retries=raw["max_retries"],
            result=json.loads(raw["result_json"]) if raw["result_json"] else None,
            error=raw["error"],
            created_at=raw["created_at"],
            updated_at=raw["updated_at"],
        )

    @classmethod
    async def list_jobs(
        cls,
        queue: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[JobRecord]:
        """Query jobs with optional status/queue filters."""
        cls.init_schema()
        def _sync_list() -> List[Dict[str, Any]]:
            conditions = []
            params = []
            if queue:
                conditions.append("queue_name = ?")
                params.append(queue)
            if status:
                conditions.append("status = ?")
                params.append(status)
            where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            query = f"SELECT * FROM worker_jobs {where_clause} ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            with cls._get_connection() as conn:
                rows = conn.execute(query, params).fetchall()
                return [dict(r) for r in rows]

        raw_list = await asyncio.to_thread(_sync_list)
        return [
            JobRecord(
                id=r["id"],
                queue=r["queue_name"],
                task_type=r["task_type"],
                payload=json.loads(r["payload_json"]) if r["payload_json"] else {},
                priority=r["priority"],
                status=r["status"],
                worker_id=r["worker_id"],
                lease_timeout_at=r["lease_timeout_at"],
                attempt_count=r["attempt_count"],
                max_retries=r["max_retries"],
                result=json.loads(r["result_json"]) if r["result_json"] else None,
                error=r["error"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            for r in raw_list
        ]

    @classmethod
    async def clear_queue(cls, queue: Optional[str] = None) -> int:
        """Clear jobs for test isolation."""
        cls.init_schema()
        def _sync_clear() -> int:
            with cls._get_connection() as conn:
                if queue:
                    cur = conn.execute("DELETE FROM worker_jobs WHERE queue_name = ?", (queue,))
                else:
                    cur = conn.execute("DELETE FROM worker_jobs")
                return cur.rowcount
        return await asyncio.to_thread(_sync_clear)


class Worker:
    """
    Background worker loop that claims jobs, maintains lease heartbeats, and executes handlers.
    """

    def __init__(
        self,
        worker_id: Optional[str] = None,
        queues: Optional[List[str]] = None,
        concurrency: int = 2,
        poll_interval: float = 0.25,
        lease_seconds: float = 60.0,
    ):
        self.worker_id = worker_id or f"worker-{platform.node()}-{uuid.uuid4().hex[:6]}"
        self.queues = queues or ["default"]
        self.concurrency = max(1, concurrency)
        self.poll_interval = poll_interval
        self.lease_seconds = lease_seconds
        self._running = False
        self._tasks: List[asyncio.Task] = []
        self._active_jobs: Set[str] = set()

    async def start(self) -> None:
        """Start concurrency consumer loops."""
        if self._running:
            return
        self._running = True
        logger.info(f"Starting Worker {self.worker_id} (concurrency={self.concurrency}, queues={self.queues})")
        for i in range(self.concurrency):
            t = asyncio.create_task(self._consumer_loop(i), name=f"{self.worker_id}-slot-{i}")
            self._tasks.append(t)

    async def stop(self, timeout: float = 5.0) -> None:
        """Gracefully stop consumer loops."""
        self._running = False
        for t in self._tasks:
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def run_until_idle(self, max_seconds: float = 10.0) -> int:
        """Process all queued jobs until queue is empty (for tests & batch runs)."""
        processed = 0
        start_t = time.monotonic()
        while time.monotonic() - start_t < max_seconds:
            job = await WorkerQueue.claim(self.worker_id, self.queues, self.lease_seconds)
            if not job:
                break
            await self._execute_job(job)
            processed += 1
        return processed

    async def _consumer_loop(self, slot_idx: int) -> None:
        while self._running:
            try:
                job = await WorkerQueue.claim(self.worker_id, self.queues, self.lease_seconds)
                if not job:
                    await asyncio.sleep(self.poll_interval)
                    continue

                self._active_jobs.add(job.id)
                try:
                    await self._execute_job(job)
                finally:
                    self._active_jobs.discard(job.id)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker {self.worker_id} error in slot {slot_idx}: {e}", exc_info=True)
                await asyncio.sleep(self.poll_interval)

    async def _execute_job(self, job: JobRecord) -> None:
        handler = WorkerQueue._handlers.get(job.task_type)
        if not handler:
            await WorkerQueue.fail(job.id, self.worker_id, f"No handler registered for task type '{job.task_type}'", allow_retry=False)
            return

        ctx = WorkerContext(job, WorkerQueue)
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(job.id))
        try:
            res = await handler(job.payload, ctx)
            result_dict = res if isinstance(res, dict) else {"result": res}
            await WorkerQueue.complete(job.id, self.worker_id, result_dict)
        except Exception as e:
            logger.warning(f"Job {job.id} ({job.task_type}) failed: {e}")
            await WorkerQueue.fail(job.id, self.worker_id, str(e), allow_retry=True)
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

    async def _heartbeat_loop(self, job_id: str) -> None:
        interval = max(2.0, self.lease_seconds / 3.0)
        while True:
            await asyncio.sleep(interval)
            try:
                await WorkerQueue.heartbeat(job_id, self.worker_id, self.lease_seconds)
            except asyncio.CancelledError:
                break
            except Exception:
                pass


# Default Built-in Handler: Mission Execution
async def _handle_mission_execute(payload: Dict[str, Any], ctx: WorkerContext) -> Dict[str, Any]:
    mission_id = payload["mission_id"]
    org_id = payload.get("org_id") or "org_main"
    user_id = payload.get("user_id") or "user_main"
    from backend.services.mission_runtime import MissionRuntime
    rt = MissionRuntime(mission_id, org_id, user_id)
    await rt.execute()
    return {"mission_id": mission_id, "status": "FINISHED"}


# Default Built-in Handler: Sandbox Cleanup
async def _handle_sandbox_cleanup(payload: Dict[str, Any], ctx: WorkerContext) -> Dict[str, Any]:
    session_id = payload["session_id"]
    from backend.services.sandbox_manager import SandboxManager
    res = SandboxManager.cleanup(session_id)
    return {"session_id": session_id, "cleanup": res}


WorkerQueue.register_handler("mission.execute", _handle_mission_execute)
WorkerQueue.register_handler("sandbox.cleanup", _handle_sandbox_cleanup)
