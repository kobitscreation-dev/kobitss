"""
backend/services/outbox.py
=========================
Transactional Outbox Service for reliable asynchronous alert delivery.
Atomically persists OutboxEvent records inside caller database transactions.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from backend.models.webhook import OutboxEvent, OutboxStatus


class OutboxService:
    """Service for enqueueing and managing transactional outbox events."""

    @staticmethod
    async def enqueue_event(
        session: AsyncSession,
        event_type: str,
        aggregate_id: str,
        payload: Dict[str, Any],
        destination_url: str,
        destination_provider: str = "slack",
        organization_id: Optional[str] = None,
        max_retries: int = 5,
        aggregate_type: str = "mission",
    ) -> OutboxEvent:
        payload_str = json.dumps(payload) if isinstance(payload, dict) else str(payload)
        event = OutboxEvent(
            id=str(uuid.uuid4()),
            organization_id=organization_id,
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload_json=payload_str,
            destination_url=destination_url,
            destination_provider=destination_provider,
            status=OutboxStatus.PENDING.value,
            retry_count=0,
            max_retries=max_retries,
            next_retry_at=datetime.now(timezone.utc),
        )
        session.add(event)
        return event
