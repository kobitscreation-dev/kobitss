import pytest
import uuid
from backend.services.durable_queue import DurableQueue, JobStatus
import backend.core.database as db_core
from backend.models.durable_job import DurableJob

@pytest.mark.asyncio
async def test_durable_queue_idempotent_re_enqueue():
    test_job_id = f"test_idempotent_{uuid.uuid4().hex[:8]}"
    
    # 1. Enqueue first time
    jid1 = await DurableQueue.enqueue(
        task_type="TEST_TASK",
        payload={"step": 1},
        queue_name="test_queue",
        priority=10,
        job_id=test_job_id,
    )
    assert jid1 == test_job_id
    
    # Verify job is pending
    async with db_core.AsyncSessionLocal() as db:
        job = await db.get(DurableJob, test_job_id)
        assert job is not None
        assert job.status == JobStatus.PENDING.value
        # Simulate worker failure or in-progress
        job.status = JobStatus.FAILED.value
        job.attempt_count = 3
        await db.commit()
        
    # 2. Re-enqueue with the same job_id (e.g. mission retry)
    jid2 = await DurableQueue.enqueue(
        task_type="TEST_TASK",
        payload={"step": 2, "retried": True},
        queue_name="test_queue",
        priority=15,
        job_id=test_job_id,
    )
    assert jid2 == test_job_id
    
    # Verify job was reset to PENDING with attempt_count 0 and updated payload
    async with db_core.AsyncSessionLocal() as db:
        updated_job = await db.get(DurableJob, test_job_id)
        assert updated_job is not None
        assert updated_job.status == JobStatus.PENDING.value
        assert updated_job.attempt_count == 0
        assert "retried" in updated_job.payload_json
        
        # Cleanup test job
        await db.delete(updated_job)
        await db.commit()
