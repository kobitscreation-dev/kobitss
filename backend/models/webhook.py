"""
backend/models/webhook.py
=========================
SQLAlchemy data models for Enterprise Outbound Webhooks & Transactional Outbox.

Provides:
- OutboxEvent: ACID-compliant outbox table ensuring zero dropped alerts.
- WebhookEndpoint: Webhook destination configuration, secret management, and circuit status.
- WebhookMessageState: Tracks posted message IDs (Slack ts / Discord message ID) for in-place updating.
"""

from __future__ import annotations

import enum
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import Column, String, Integer, DateTime, Text, Index
from backend.models.base import Base


class OutboxStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    DEAD_LETTER = "DEAD_LETTER"


class WebhookHealth(str, enum.Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DISABLED = "DISABLED"


class OutboxEvent(Base):
    """
    Transactional Outbox record written atomically with mission state changes.
    Guarantees that alerts are never dropped even if workers crash or external APIs are down.
    """
    __tablename__ = "outbox_events"

    id = Column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id = Column(String(64), nullable=True, index=True)
    event_type = Column(String(64), nullable=False, index=True)  # e.g. "mission.completed", "mission.failed", "mission.progress"
    aggregate_type = Column(String(64), nullable=False, default="mission", index=True)
    aggregate_id = Column(String(64), nullable=False, index=True)
    payload_json = Column(Text, nullable=False)
    destination_url = Column(String(2048), nullable=False)
    destination_provider = Column(String(32), nullable=False, default="slack")  # "slack", "discord", "generic"
    status = Column(String(32), nullable=False, default=OutboxStatus.PENDING.value, index=True)
    
    retry_count = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=5)
    next_retry_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True
    )
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)
    headers_json = Column(Text, nullable=True)
    metadata_json = Column(Text, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )
    delivered_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_outbox_events_poll", "status", "next_retry_at"),
        Index("idx_outbox_events_aggregate", "aggregate_type", "aggregate_id"),
    )

    def get_payload(self) -> Dict[str, Any]:
        """Deserialize stored payload JSON."""
        if not self.payload_json:
            return {}
        try:
            return json.loads(self.payload_json)
        except Exception:
            return {"raw": self.payload_json}

    def set_payload(self, data: Dict[str, Any] | str) -> None:
        """Serialize payload into JSON string."""
        if isinstance(data, str):
            self.payload_json = data
        else:
            self.payload_json = json.dumps(data)

    def get_headers(self) -> Dict[str, str]:
        """Deserialize custom headers JSON."""
        if not self.headers_json:
            return {}
        try:
            return json.loads(self.headers_json)
        except Exception:
            return {}

    def set_headers(self, headers: Dict[str, str]) -> None:
        """Serialize headers into JSON string."""
        self.headers_json = json.dumps(headers)

    def get_metadata(self) -> Dict[str, Any]:
        """Deserialize metadata JSON."""
        if not self.metadata_json:
            return {}
        try:
            return json.loads(self.metadata_json)
        except Exception:
            return {}

    def set_metadata(self, metadata: Dict[str, Any]) -> None:
        """Serialize metadata into JSON string."""
        self.metadata_json = json.dumps(metadata)


class WebhookEndpoint(Base):
    """
    Target webhook endpoint registry, maintaining circuit breaker state,
    signing secrets, and delivery health metrics.
    """
    __tablename__ = "webhook_endpoints"

    id = Column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id = Column(String(64), nullable=True, index=True)
    provider = Column(String(32), nullable=False, default="slack")  # "slack", "discord", "generic"
    url = Column(String(2048), nullable=False)
    secret = Column(String(256), nullable=True)  # Secret for HMAC-SHA256 signing
    status = Column(String(32), nullable=False, default=WebhookHealth.HEALTHY.value, index=True)
    consecutive_failures = Column(Integer, nullable=False, default=0)
    last_failure_reason = Column(Text, nullable=True)
    last_success_at = Column(DateTime(timezone=True), nullable=True)
    last_failure_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )


class WebhookMessageState(Base):
    """
    Tracks posted message references (Slack timestamp or Discord message ID)
    per mission and provider to enable live in-place message updates instead of channel spam.
    """
    __tablename__ = "webhook_message_states"

    id = Column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    mission_id = Column(String(64), nullable=False, index=True)
    provider = Column(String(32), nullable=False)  # "slack", "discord"
    destination_url = Column(String(2048), nullable=True)
    channel_id = Column(String(128), nullable=True)
    message_id = Column(String(128), nullable=False)  # Slack ts or Discord message ID
    last_status = Column(String(64), nullable=True)
    last_percentage = Column(Integer, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("idx_webhook_msg_mission", "mission_id", "provider"),
    )
