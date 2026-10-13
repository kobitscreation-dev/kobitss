"""
Enterprise-Grade PostgreSQL Durable Queue Engine for Kobits.
Implements ACID-compliant multi-worker task execution using SELECT ... FOR UPDATE SKIP LOCKED.
Strict zero-external-broker architecture: Pure Python asyncio + PostgreSQL / SQLAlchemy asyncpg.
"""

import os
import sys
import json
import uuid
import time
import asyncio
import logging
import traceback
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, Callable, Coroutine, List

from sqlalchemy import select, update, and_, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database import AsyncSessionLocal, engine
from backend.models.durable_job import DurableJob, JobStatus

logger = logging.getLogger("kobits.durable_queue")

TaskHandler = Callable[[Dict[str, Any]], Coroutine[Any, Any, Any]]


class DurableQueue:
    """
    Core static operations for the PostgreSQL durable queue.
    Guarantees zero lock contention using SELECT ... FOR UPDATE SKIP LOCKED.
    """
    _handlers: Dict[str, TaskHandler] = {}

    @classmethod
    def register_handler(cls, task_type: str, handler: TaskHandler) -> None:
        """Register an async function handler for a specific task_type."""
        cls._handlers[task_type] = handler
        logger.info(f"Registered durable task handler for '{task_type}'")

    @classmethod
    async def init_schema(cls) -> None:
        """
        Idempotent DDL ensuring the durable_queue_jobs table and indexes exist.
        Works across PostgreSQL and SQLite.
        """
        async with engine.begin() as conn:
            is_postgres = "postgresql" in str(engine.url).lower()
            
            if is_postgres:
                ddl = """
                CREATE TABLE IF NOT EXISTS durable_queue_jobs (
                    id VARCHAR(64) PRIMARY KEY,
                    queue_name VARCHAR(64) NOT NULL DEFAULT 'default',
                    task_type VARCHAR(128) NOT NULL,
                    payload_json TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 0,
                    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
                    worker_id VARCHAR(128),
                    lease_timeout_at TIMESTAMPTZ,
                    heartbeat_at TIMESTAMPTZ,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    max_retries INTEGER NOT NULL DEFAULT 3,
                    backoff_seconds INTEGER NOT NULL DEFAULT 10,
                    scheduled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    result_json TEXT,
                    error_trace TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_durable_jobs_claim 
                    ON durable_queue_jobs (queue_name, status, scheduled_at, priority DESC, created_at ASC);
                CREATE INDEX IF NOT EXISTS idx_durable_jobs_stale_reap 
                    ON durable_queue_jobs (status, lease_timeout_at);
                """
            else:
                ddl = """
                CREATE TABLE IF NOT EXISTS durable_queue_jobs (
                    id TEXT PRIMARY KEY,
                    queue_name TEXT NOT NULL DEFAULT 'default',
                    task_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    worker_id TEXT,
                    lease_timeout_at DATETIME,
                    heartbeat_at DATETIME,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    max_retries INTEGER NOT NULL DEFAULT 3,
                    backoff_seconds INTEGER NOT NULL DEFAULT 10,
                    scheduled_at DATETIME NOT NULL,
                    result_json TEXT,
                    error_trace TEXT,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_durable_jobs_claim 
                    ON durable_queue_jobs (queue_name, status, scheduled_at, priority, created_at);
                CREATE INDEX IF NOT EXISTS idx_durable_jobs_stale_reap 
                    ON durable_queue_jobs (status, lease_timeout_at);
                """
            for stmt in ddl.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    await conn.execute(text(stmt))
            logger.info("Durable queue table and indexes verified.")

    @classmethod
    async def enqueue(
        cls,
        task_type: str,
        payload: Dict[str, Any],
        queue_name: str = "missions",
        priority: int = 0,
        max_retries: int = 3,
        backoff_seconds: int = 10,
        delay_seconds: int = 0,
        job_id: Optional[str] = None,
    ) -> str:
        """
        Atomically enqueue a durable task to PostgreSQL.
        Returns the unique job ID.
        """
        jid = job_id or str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        scheduled_at = now + timedelta(seconds=delay_seconds) if delay_seconds > 0 else now

        job = DurableJob(
            id=jid,
            queue_name=queue_name,
            task_type=task_type,
            payload_json=json.dumps(payload),
            priority=priority,
            status=JobStatus.PENDING.value,
            attempt_count=0,
            max_retries=max_retries,
            backoff_seconds=backoff_seconds,
            scheduled_at=scheduled_at,
            created_at=now,
            updated_at=now,
        )

        async with AsyncSessionLocal() as db:
            db.add(job)
            await db.commit()

        logger.info(f"Enqueued durable job {jid[:8]} (type={task_type}, queue={queue_name}, priority={priority})")
        return jid

    _sqlite_claim_lock = asyncio.Lock()

    @classmethod
    async def claim_job(
        cls,
        worker_id: str,
        queue_name: str = "missions",
        lease_seconds: int = 60,
    ) -> Optional[DurableJob]:
        """
        Atomically claim the highest-priority pending job using SELECT ... FOR UPDATE SKIP LOCKED.
        The database transaction completes in < 5ms to avoid connection pool lock starvation.
        """
        now = datetime.now(timezone.utc)
        lease_timeout = now + timedelta(seconds=lease_seconds)
        is_postgres = "postgresql" in str(engine.url).lower()

        if is_postgres:
            async with AsyncSessionLocal() as db:
                try:
                    stmt = text("""
                    WITH next_job AS (
                        SELECT id
                        FROM durable_queue_jobs
                        WHERE queue_name = :queue_name
                          AND status = 'PENDING'
                          AND scheduled_at <= :now
                        ORDER BY priority DESC, created_at ASC
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    UPDATE durable_queue_jobs
                    SET status = 'LEASED',
                        worker_id = :worker_id,
                        lease_timeout_at = :lease_timeout_at,
                        heartbeat_at = :now,
                        attempt_count = durable_queue_jobs.attempt_count + 1,
                        updated_at = :now
                    FROM next_job
                    WHERE durable_queue_jobs.id = next_job.id
                    RETURNING durable_queue_jobs.*;
                    """)
                    res = await db.execute(stmt, {
                        "queue_name": queue_name,
                        "now": now,
                        "worker_id": worker_id,
                        "lease_timeout_at": lease_timeout,
                    })
                    row = res.mappings().first()
                    if not row:
                        await db.commit()
                        return None
                    job = DurableJob(**dict(row))
                    await db.commit()
                    return job
                except Exception as e:
                    logger.error(f"Error claiming job from queue '{queue_name}' in postgres: {e}")
                    return None
        else:
            # SQLite / Local Test Suite mode with atomic claim lock
            async with cls._sqlite_claim_lock:
                async with AsyncSessionLocal() as db:
                    try:
                        stmt = (
                            select(DurableJob)
                            .where(
                                DurableJob.queue_name == queue_name,
                                DurableJob.status == JobStatus.PENDING.value,
                                DurableJob.scheduled_at <= now,
                            )
                            .order_by(DurableJob.priority.desc(), DurableJob.created_at.asc())
                            .limit(1)
                        )

                        result = await db.execute(stmt)
                        job = result.scalars().first()

                        if not job:
                            return None

                        # Update lease fields
                        job.status = JobStatus.LEASED.value
                        job.worker_id = worker_id
                        job.lease_timeout_at = lease_timeout
                        job.heartbeat_at = now
                        job.attempt_count += 1
                        job.updated_at = now

                        await db.commit()
                        await db.refresh(job)
                        return job

                    except Exception as e:
                        await db.rollback()
                        logger.error(f"Error claiming job from queue '{queue_name}': {e}")
                        return None

    @classmethod
    async def heartbeat(cls, job_id: str, worker_id: str, lease_seconds: int = 60) -> bool:
        """Extend lease duration while job is actively running."""
        now = datetime.now(timezone.utc)
        lease_timeout = now + timedelta(seconds=lease_seconds)

        async with AsyncSessionLocal() as db:
            try:
                stmt = (
                    update(DurableJob)
                    .where(
                        DurableJob.id == job_id,
                        DurableJob.worker_id == worker_id,
                        DurableJob.status == JobStatus.LEASED.value,
                    )
                    .values(
                        heartbeat_at=now,
                        lease_timeout_at=lease_timeout,
                        updated_at=now,
                    )
                )
                res = await db.execute(stmt)
                await db.commit()
                return res.rowcount > 0
            except Exception as e:
                await db.rollback()
                logger.warning(f"Heartbeat failed for job {job_id[:8]}: {e}")
                return False

    @classmethod
    async def complete_job(cls, job_id: str, worker_id: str, result_data: Optional[Any] = None) -> bool:
        """Mark job as successfully COMPLETED."""
        now = datetime.now(timezone.utc)
        res_json = json.dumps(result_data) if result_data is not None else None

        async with AsyncSessionLocal() as db:
            try:
                stmt = (
                    update(DurableJob)
                    .where(
                        DurableJob.id == job_id,
                        DurableJob.worker_id == worker_id,
                    )
                    .values(
                        status=JobStatus.COMPLETED.value,
                        result_json=res_json,
                        lease_timeout_at=None,
                        updated_at=now,
                    )
                )
                res = await db.execute(stmt)
                await db.commit()
                logger.info(f"Durable job {job_id[:8]} marked COMPLETED")
                return res.rowcount > 0
            except Exception as e:
                await db.rollback()
                logger.error(f"Failed to complete job {job_id[:8]}: {e}")
                return False

    @classmethod
    async def fail_job(
        cls,
        job_id: str,
        worker_id: str,
        error_msg: str,
        backoff_seconds: int = 10,
    ) -> bool:
        """
        Handle job failure: retry with exponential backoff, or move to DEAD_LETTER if retries exhausted.
        """
        now = datetime.now(timezone.utc)

        async with AsyncSessionLocal() as db:
            try:
                stmt = select(DurableJob).where(DurableJob.id == job_id)
                res = await db.execute(stmt)
                job = res.scalars().first()

                if not job:
                    return False

                if job.attempt_count >= job.max_retries:
                    # Retries exhausted -> DEAD_LETTER
                    job.status = JobStatus.DEAD_LETTER.value
                    job.error_trace = error_msg
                    job.lease_timeout_at = None
                    job.updated_at = now
                    logger.error(f"Job {job_id[:8]} exhausted retries ({job.attempt_count}/{job.max_retries}). Moved to DEAD_LETTER.")
                else:
                    # Retry with exponential backoff: delay = backoff * (2 ** (attempt - 1))
                    exp_delay = backoff_seconds * (2 ** max(0, job.attempt_count - 1))
                    job.status = JobStatus.PENDING.value
                    job.worker_id = None
                    job.lease_timeout_at = None
                    job.scheduled_at = now + timedelta(seconds=exp_delay)
                    job.error_trace = error_msg
                    job.updated_at = now
                    logger.warning(
                        f"Job {job_id[:8]} failed (attempt {job.attempt_count}/{job.max_retries}). "
                        f"Re-queued with {exp_delay}s backoff."
                    )

                await db.commit()
                return True
            except Exception as e:
                await db.rollback()
                logger.error(f"Error handling failure for job {job_id[:8]}: {e}")
                return False

    @classmethod
    async def reap_stale_leases(cls) -> int:
        """
        Background cleanup: Find LEASED jobs whose workers died (lease_timeout_at < NOW()).
        Automatically resurrect them or move to DEAD_LETTER if max_retries exceeded.
        """
        now = datetime.now(timezone.utc)
        reaped_count = 0

        async with AsyncSessionLocal() as db:
            try:
                stmt = (
                    select(DurableJob)
                    .where(
                        DurableJob.status == JobStatus.LEASED.value,
                        DurableJob.lease_timeout_at < now,
                    )
                    .with_for_update(skip_locked=True)
                )
                res = await db.execute(stmt)
                stale_jobs = res.scalars().all()

                for job in stale_jobs:
                    if job.attempt_count >= job.max_retries:
                        job.status = JobStatus.DEAD_LETTER.value
                        job.error_trace = f"Worker lease expired ({job.worker_id} crashed or OOM killed). Max retries exceeded."
                        job.lease_timeout_at = None
                        job.updated_at = now
                    else:
                        job.status = JobStatus.PENDING.value
                        job.worker_id = None
                        job.lease_timeout_at = None
                        job.scheduled_at = now + timedelta(seconds=5)  # 5s grace period
                        job.error_trace = f"Worker lease expired ({job.worker_id} died). Automatically re-queued by reaper."
                        job.updated_at = now
                    reaped_count += 1

                if reaped_count > 0:
                    await db.commit()
                    logger.warning(f"Reaper resurrected/cleared {reaped_count} stale worker job(s)")
                return reaped_count

            except Exception as e:
                await db.rollback()
                logger.error(f"Error in stale lease reaper: {e}")
                return 0


class DurableWorkerPool:
    """
    Manages concurrent async worker loops polling PostgreSQL for jobs.
    Handles heartbeats, stale job reaping, and graceful shutdown draining.
    """

    def __init__(
        self,
        queue_names: List[str] = None,
        concurrency: int = 2,
        lease_seconds: int = 60,
        worker_id_prefix: str = "worker",
    ):
        self.queue_names = queue_names or ["missions", "default"]
        self.concurrency = concurrency
        self.lease_seconds = lease_seconds
        self.worker_id_prefix = f"{worker_id_prefix}-{os.getpid()}"
        self._is_running = False
        self._tasks: List[asyncio.Task] = []
        self._reaper_task: Optional[asyncio.Task] = None
        self._active_jobs_count = 0

    async def start(self) -> None:
        """Start the worker pool."""
        if self._is_running:
            return

        self._is_running = True
        logger.info(
            f"Starting DurableWorkerPool (queues={self.queue_names}, concurrency={self.concurrency}, lease={self.lease_seconds}s)"
        )

        # 1. Start worker loop coroutines
        for idx in range(self.concurrency):
            wid = f"{self.worker_id_prefix}-{idx}"
            t = asyncio.create_task(self._worker_loop(wid), name=f"durable-worker-{idx}")
            self._tasks.append(t)

        # 2. Start periodic reaper
        self._reaper_task = asyncio.create_task(self._reaper_loop(), name="durable-reaper")

    async def stop(self, drain_timeout: float = 30.0) -> None:
        """
        Graceful shutdown: stop polling for new jobs and drain in-flight jobs up to timeout.
        """
        if not self._is_running:
            return

        logger.info(f"Stopping DurableWorkerPool (draining in-flight jobs, timeout={drain_timeout}s)...")
        self._is_running = False

        if self._reaper_task and not self._reaper_task.done():
            self._reaper_task.cancel()

        # Wait for worker loops to finish active jobs
        start_wait = time.perf_counter()
        while self._active_jobs_count > 0 and (time.perf_counter() - start_wait) < drain_timeout:
            await asyncio.sleep(0.5)

        # Cancel any remaining idle pollers
        for t in self._tasks:
            if not t.done():
                t.cancel()

        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("DurableWorkerPool cleanly stopped.")

    async def _worker_loop(self, worker_id: str) -> None:
        """Individual worker polling loop with backoff."""
        idle_sleep = 0.25
        max_idle_sleep = 2.0

        while self._is_running:
            job_claimed = False

            for qname in self.queue_names:
                if not self._is_running:
                    break

                job = await DurableQueue.claim_job(
                    worker_id=worker_id,
                    queue_name=qname,
                    lease_seconds=self.lease_seconds,
                )

                if job:
                    job_claimed = True
                    idle_sleep = 0.25  # Reset backoff on work
                    self._active_jobs_count += 1
                    try:
                        await self._execute_job_with_heartbeat(job, worker_id)
                    finally:
                        self._active_jobs_count -= 1
                    break  # Claim next job immediately

            if not job_claimed and self._is_running:
                # No jobs found across any queue: backoff sleeping
                await asyncio.sleep(idle_sleep)
                idle_sleep = min(max_idle_sleep, idle_sleep * 1.5)

    async def _execute_job_with_heartbeat(self, job: DurableJob, worker_id: str) -> None:
        """
        Executes a job payload while maintaining an active heartbeat coroutine.
        """
        jid = job.id
        task_type = job.task_type
        payload = json.loads(job.payload_json)

        handler = DurableQueue._handlers.get(task_type)
        if not handler:
            err = f"No handler registered for task_type '{task_type}'"
            logger.error(err)
            await DurableQueue.fail_job(jid, worker_id, err, backoff_seconds=job.backoff_seconds)
            return

        # Start periodic heartbeat task (heartbeat interval = lease / 3)
        heartbeat_interval = max(5, self.lease_seconds // 3)
        hb_stop = asyncio.Event()

        async def _hb_loop():
            while not hb_stop.is_set():
                await asyncio.sleep(heartbeat_interval)
                if hb_stop.is_set():
                    break
                await DurableQueue.heartbeat(jid, worker_id, lease_seconds=self.lease_seconds)

        hb_task = asyncio.create_task(_hb_loop())

        try:
            logger.info(f"Worker {worker_id} executing job {jid[:8]} ({task_type})")
            start_time = time.perf_counter()
            result = await handler(payload)
            dur_ms = int((time.perf_counter() - start_time) * 1000)

            await DurableQueue.complete_job(jid, worker_id, result_data=result)
            logger.info(f"Job {jid[:8]} completed successfully in {dur_ms}ms")

        except Exception as e:
            err_trace = traceback.format_exc()
            logger.error(f"Job {jid[:8]} execution failed: {e}\n{err_trace}")
            await DurableQueue.fail_job(jid, worker_id, err_trace, backoff_seconds=job.backoff_seconds)

        finally:
            hb_stop.set()
            hb_task.cancel()
            try:
                await hb_task
            except asyncio.CancelledError:
                pass

    async def _reaper_loop(self) -> None:
        """Periodic background reaper recovering dead worker jobs."""
        while self._is_running:
            try:
                await asyncio.sleep(30.0)
                if self._is_running:
                    await DurableQueue.reap_stale_leases()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Reaper loop error: {e}")


# Global pool singleton
global_worker_pool: Optional[DurableWorkerPool] = None

def get_worker_pool() -> DurableWorkerPool:
    global global_worker_pool
    if global_worker_pool is None:
        global_worker_pool = DurableWorkerPool(
            queue_names=["missions", "default"],
            concurrency=int(os.environ.get("DURABLE_WORKER_CONCURRENCY", "4")),
            lease_seconds=int(os.environ.get("DURABLE_WORKER_LEASE_SECONDS", "90")),
        )
    return global_worker_pool


async def _handle_execute_mission(payload: Dict[str, Any]) -> Any:
    """Default handler for EXECUTE_MISSION task type."""
    mission_id = payload["mission_id"]
    org_id = payload.get("org_id") or payload.get("organization_id")
    user_id = payload.get("user_id")
    from backend.services.mission_runtime import MissionRuntime
    return await MissionRuntime.run_mission_background(
        mission_id=mission_id,
        organization_id=org_id,
        user_id=user_id,
    )


def register_default_handlers():
    """Register built-in system task handlers."""
    DurableQueue.register_handler("EXECUTE_MISSION", _handle_execute_mission)

# Automatically register default handlers on import
register_default_handlers()
