import enum
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, DateTime, Text, Index, Enum
from backend.models.base import Base

class JobStatus(str, enum.Enum):
    PENDING = "PENDING"
    LEASED = "LEASED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    DEAD_LETTER = "DEAD_LETTER"

class DurableJob(Base):
    """
    Enterprise-grade durable asynchronous job queue table backed by PostgreSQL.
    Supports atomic multi-worker leasing via SELECT ... FOR UPDATE SKIP LOCKED.
    """
    __tablename__ = "durable_queue_jobs"

    id = Column(String(64), primary_key=True)
    queue_name = Column(String(64), nullable=False, default="default", index=True)
    task_type = Column(String(128), nullable=False, index=True)
    payload_json = Column(Text, nullable=False)
    priority = Column(Integer, default=0, nullable=False)
    
    status = Column(
        String(32),
        default=JobStatus.PENDING.value,
        nullable=False,
        index=True
    )

    worker_id = Column(String(128), nullable=True)
    lease_timeout_at = Column(DateTime(timezone=True), nullable=True, index=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    
    attempt_count = Column(Integer, default=0, nullable=False)
    max_retries = Column(Integer, default=3, nullable=False)
    backoff_seconds = Column(Integer, default=10, nullable=False)
    scheduled_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

    result_json = Column(Text, nullable=True)
    error_trace = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index(
            "idx_durable_jobs_claim",
            "queue_name",
            "status",
            "scheduled_at",
            "priority",
            "created_at"
        ),
        Index(
            "idx_durable_jobs_stale_reap",
            "status",
            "lease_timeout_at"
        ),
    )
