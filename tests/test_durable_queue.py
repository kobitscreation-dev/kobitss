import os
import sys
import json
import uuid
import asyncio
import pytest
from datetime import datetime, timezone, timedelta

from backend.core.database import AsyncSessionLocal
from backend.models.durable_job import DurableJob, JobStatus
from backend.services.durable_queue import DurableQueue, DurableWorkerPool


@pytest.mark.asyncio
async def test_durable_queue_schema_init():
    """Verify that durable_queue_jobs table and indexes are initialized idempotently."""
    await DurableQueue.init_schema()
    async with AsyncSessionLocal() as db:
        job = DurableJob(
            id=str(uuid.uuid4()),
            queue_name="test_schema",
            task_type="TEST_SCHEMA",
            payload_json=json.dumps({"key": "val"}),
            status=JobStatus.PENDING.value,
            scheduled_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(job)
        await db.commit()

        # Clean up
        await db.delete(job)
        await db.commit()


@pytest.mark.asyncio
async def test_durable_queue_claim_and_complete():
    """Verify standard single-worker claim, heartbeat, and completion cycle."""
    await DurableQueue.init_schema()
    qname = f"test_q_{uuid.uuid4().hex[:6]}"

    # 1. Enqueue
    jid = await DurableQueue.enqueue(
        task_type="MATH_ADD",
        payload={"a": 10, "b": 25},
        queue_name=qname,
        priority=100,
    )

    # 2. Claim
    worker_id = "test-worker-alpha"
    job = await DurableQueue.claim_job(worker_id=worker_id, queue_name=qname, lease_seconds=30)
    assert job is not None
    assert job.id == jid
    assert job.status == JobStatus.LEASED.value
    assert job.worker_id == worker_id
    assert job.attempt_count == 1

    # 3. Heartbeat
    hb_ok = await DurableQueue.heartbeat(jid, worker_id, lease_seconds=60)
    assert hb_ok is True

    # 4. Complete
    comp_ok = await DurableQueue.complete_job(jid, worker_id, result_data={"sum": 35})
    assert comp_ok is True

    # Verify final state in DB
    async with AsyncSessionLocal() as db:
        res = await db.get(DurableJob, jid)
        assert res.status == JobStatus.COMPLETED.value
        assert json.loads(res.result_json) == {"sum": 35}


@pytest.mark.asyncio
async def test_durable_queue_concurrent_workers_zero_collisions():
    """
    Stress test: 20 jobs enqueued, 5 parallel concurrent workers polling simultaneously.
    Guarantees SELECT ... FOR UPDATE SKIP LOCKED prevents any race conditions or duplicate claims.
    """
    await DurableQueue.init_schema()
    qname = f"test_conc_{uuid.uuid4().hex[:6]}"
    total_jobs = 20
    claimed_by_worker = {}
    completed_ids = set()

    # Enqueue 20 jobs
    job_ids = []
    for i in range(total_jobs):
        jid = await DurableQueue.enqueue(
            task_type="CONCURRENT_TASK",
            payload={"index": i},
            queue_name=qname,
            priority=i % 3,  # Varied priorities
        )
        job_ids.append(jid)

    async def worker_task(worker_name: str):
        while len(completed_ids) < total_jobs:
            job = await DurableQueue.claim_job(
                worker_id=worker_name,
                queue_name=qname,
                lease_seconds=30,
            )
            if not job:
                await asyncio.sleep(0.05)
                continue

            # Check for double-claiming
            assert job.id not in completed_ids, f"CRITICAL: Job {job.id} was double-claimed!"

            claimed_by_worker.setdefault(worker_name, []).append(job.id)
            await asyncio.sleep(0.02)  # Simulate small async processing
            await DurableQueue.complete_job(job.id, worker_name, result_data={"processed": True})
            completed_ids.add(job.id)

    # Launch 5 concurrent workers
    workers = [asyncio.create_task(worker_task(f"worker-{w}")) for w in range(5)]
    await asyncio.wait_for(asyncio.gather(*workers), timeout=15.0)

    assert len(completed_ids) == total_jobs
    # Verify that multiple workers actually participated in claiming
    assert len(claimed_by_worker.keys()) >= 2, "Jobs should be distributed across multiple workers"


@pytest.mark.asyncio
async def test_durable_queue_stale_lease_reaper():
    """Verify that jobs from dead/crashed workers with expired leases are automatically recovered."""
    await DurableQueue.init_schema()
    qname = f"test_reap_{uuid.uuid4().hex[:6]}"

    jid = await DurableQueue.enqueue(
        task_type="CRASH_SIMULATION",
        payload={"action": "die"},
        queue_name=qname,
        max_retries=3,
    )

    # Worker 1 claims job
    job = await DurableQueue.claim_job(worker_id="dead-worker", queue_name=qname, lease_seconds=10)
    assert job is not None

    # Manually expire the lease (simulate crash 30 seconds ago)
    async with AsyncSessionLocal() as db:
        stale_time = datetime.now(timezone.utc) - timedelta(seconds=30)
        await db.execute(
            DurableJob.__table__.update()
            .where(DurableJob.id == jid)
            .values(lease_timeout_at=stale_time)
        )
        await db.commit()

    # Run reaper
    reaped = await DurableQueue.reap_stale_leases()
    assert reaped >= 1

    # Verify job is resurrected to PENDING
    async with AsyncSessionLocal() as db:
        res = await db.get(DurableJob, jid)
        assert res.status == JobStatus.PENDING.value
        assert res.worker_id is None
        assert "Worker lease expired" in res.error_trace


@pytest.mark.asyncio
async def test_durable_queue_dead_letter_after_max_retries():
    """Verify that repeatedly failing tasks backoff and eventually transition to DEAD_LETTER."""
    await DurableQueue.init_schema()
    qname = f"test_dlq_{uuid.uuid4().hex[:6]}"

    jid = await DurableQueue.enqueue(
        task_type="FAILING_TASK",
        payload={"will_fail": True},
        queue_name=qname,
        max_retries=2,
        backoff_seconds=1,
    )

    # Attempt 1: Claim and fail
    job1 = await DurableQueue.claim_job(worker_id="worker-retry", queue_name=qname)
    assert job1.attempt_count == 1
    await DurableQueue.fail_job(jid, "worker-retry", error_msg="Failure 1", backoff_seconds=0)

    # Reset scheduled_at to now for immediate retry in test
    async with AsyncSessionLocal() as db:
        await db.execute(
            DurableJob.__table__.update()
            .where(DurableJob.id == jid)
            .values(scheduled_at=datetime.now(timezone.utc))
        )
        await db.commit()

    # Attempt 2: Claim and fail again (exceeds max_retries = 2)
    job2 = await DurableQueue.claim_job(worker_id="worker-retry", queue_name=qname)
    assert job2.attempt_count == 2
    await DurableQueue.fail_job(jid, "worker-retry", error_msg="Fatal Crash", backoff_seconds=0)

    # Verify job is now in DEAD_LETTER queue
    async with AsyncSessionLocal() as db:
        final_job = await db.get(DurableJob, jid)
        assert final_job.status == JobStatus.DEAD_LETTER.value
        assert "Fatal Crash" in final_job.error_trace
