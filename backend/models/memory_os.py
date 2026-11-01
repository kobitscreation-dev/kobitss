"""
Biologically Inspired Memory OS Models.
Defines Episodic, Semantic, Procedural, and Cryptographic Mutation Ledger models.
"""
from __future__ import annotations

import enum
import hashlib
import json
import math
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

import numpy as np
from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, synonym

from backend.models.base import Base

GENESIS_HASH: str = "0" * 64


def compute_sha256_hex(data: Any) -> str:
    """Compute deterministic lowercase hex SHA-256 digest."""
    if data is None:
        raw = b""
    elif isinstance(data, bytes):
        raw = data
    elif isinstance(data, str):
        raw = data.encode("utf-8")
    else:
        raw = str(data).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _normalize_utc(dt: Optional[datetime]) -> datetime:
    """Ensure datetime is timezone-aware in UTC."""
    if dt is None:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalize_memory_timestamp(dt: Optional[Union[datetime, str]]) -> str:
    """Normalize datetime to deterministic ISO-8601 UTC microsecond string."""
    if dt is None:
        return ""
    if isinstance(dt, str):
        cleaned = dt.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(cleaned)
        except ValueError:
            return dt
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class EpisodicMemoryRecord(Base):
    """
    Episodic Memory record representing time-bound experiences, sessions, and events.
    Computes real-time retention probability using the Ebbinghaus forgetting curve:
    R(t) = exp(-t / S)
    """
    __tablename__ = "episodic_memory_records"

    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    stability: Mapped[float] = mapped_column(Float, default=86400.0, nullable=False)
    last_recalled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    recall_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tags: Mapped[Optional[Any]] = mapped_column(JSON, default=list, nullable=True)
    embedding: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)

    def compute_retention(self, current_time: Optional[datetime] = None) -> float:
        """
        Compute real-time retention probability R(t) = exp(-t / S).
        t is the elapsed seconds since last_recalled_at (clamped to >= 0.0).
        S is stability (guarded > 1e-6).
        """
        now = _normalize_utc(current_time)
        recalled = _normalize_utc(self.last_recalled_at)
        t = max(0.0, (now - recalled).total_seconds())
        s = max(float(self.stability or 86400.0), 1e-6)
        return float(math.exp(-t / s))

    def get_embedding_vector(self) -> np.ndarray:
        """Return 384-dimensional numpy vector representation."""
        if not self.embedding:
            return np.zeros(384, dtype=np.float64)
        if isinstance(self.embedding, str):
            try:
                parsed = json.loads(self.embedding)
                return np.array(parsed, dtype=np.float64)
            except Exception:
                return np.zeros(384, dtype=np.float64)
        try:
            return np.array(self.embedding, dtype=np.float64)
        except Exception:
            return np.zeros(384, dtype=np.float64)

    def get_tags(self) -> List[str]:
        """Return tags as a list of strings."""
        if not self.tags:
            return []
        if isinstance(self.tags, str):
            try:
                parsed = json.loads(self.tags)
                return [str(t) for t in parsed] if isinstance(parsed, list) else [self.tags]
            except Exception:
                return [self.tags]
        if isinstance(self.tags, list):
            return [str(t) for t in self.tags]
        return [str(self.tags)]

    def canonical_payload(self) -> Dict[str, Any]:
        """Deterministic dictionary for cryptographic hashing."""
        return {
            "content": str(self.content or ""),
            "id": str(self.id or ""),
            "organization_id": str(self.organization_id or ""),
            "project_id": str(self.project_id or ""),
            "recall_count": int(self.recall_count or 0),
            "stability": round(float(self.stability or 0.0), 4),
            "tags": sorted(self.get_tags()),
        }

    def data_hash(self) -> str:
        """SHA-256 digest of canonical episodic payload."""
        canonical_json = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"))
        return compute_sha256_hex(canonical_json)

    def to_dict(self, current_time: Optional[datetime] = None) -> Dict[str, Any]:
        retention = self.compute_retention(current_time=current_time)
        return {
            "id": self.id,
            "organization_id": self.organization_id,
            "project_id": self.project_id,
            "content": self.content,
            "stability": float(self.stability),
            "retention": round(retention, 6),
            "recall_count": self.recall_count,
            "last_recalled_at": self.last_recalled_at.isoformat() if self.last_recalled_at else None,
            "tags": self.get_tags(),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class SemanticConstraintStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    CONFLICTING = "CONFLICTING"
    SUPERSEDED = "SUPERSEDED"


class SemanticConstraintRecord(Base):
    """
    Semantic Memory record representing generalized codebase facts,
    architectural invariants, and domain constraints.
    """
    __tablename__ = "semantic_constraint_records"

    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )

    key: Mapped[str] = mapped_column(String(256), index=True, nullable=False)
    subject: Mapped[str] = mapped_column(String(256), index=True, nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)

    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    base_confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    status: Mapped[SemanticConstraintStatus] = mapped_column(
        Enum(SemanticConstraintStatus),
        default=SemanticConstraintStatus.ACTIVE,
        index=True,
        nullable=False,
    )

    superseded_by_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("semantic_constraint_records.id", ondelete="SET NULL"),
        nullable=True,
    )
    conflicted_with_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("semantic_constraint_records.id", ondelete="SET NULL"),
        nullable=True,
    )
    conflict_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    depends_on_ids: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)
    embedding: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)

    def canonical_payload(self) -> Dict[str, Any]:
        """Deterministic dictionary for cryptographic hashing."""
        status_str = self.status.value if isinstance(self.status, SemanticConstraintStatus) else str(self.status)
        deps = list(self.depends_on_ids or [])
        if isinstance(deps, str):
            try:
                deps = json.loads(deps)
            except Exception:
                deps = [deps]
        return {
            "base_confidence": round(float(self.base_confidence or 0.0), 6),
            "confidence": round(float(self.confidence or 0.0), 6),
            "depends_on_ids": sorted([str(d) for d in deps]),
            "id": str(self.id or ""),
            "key": str(self.key or ""),
            "organization_id": str(self.organization_id or ""),
            "project_id": str(self.project_id or ""),
            "statement": str(self.statement or ""),
            "status": status_str,
            "subject": str(self.subject or ""),
        }

    def data_hash(self) -> str:
        """SHA-256 digest of canonical semantic payload."""
        canonical_json = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"))
        return compute_sha256_hex(canonical_json)

    def to_dict(self) -> Dict[str, Any]:
        status_str = self.status.value if isinstance(self.status, SemanticConstraintStatus) else str(self.status)
        deps = list(self.depends_on_ids or [])
        if isinstance(deps, str):
            try:
                deps = json.loads(deps)
            except Exception:
                deps = [deps]
        return {
            "id": self.id,
            "organization_id": self.organization_id,
            "project_id": self.project_id,
            "key": self.key,
            "subject": self.subject,
            "statement": self.statement,
            "confidence": round(float(self.confidence), 6),
            "base_confidence": round(float(self.base_confidence), 6),
            "status": status_str,
            "superseded_by_id": self.superseded_by_id,
            "conflicted_with_id": self.conflicted_with_id,
            "conflict_reason": self.conflict_reason,
            "depends_on_ids": [str(d) for d in deps],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ProceduralPlaybook(Base):
    """
    Procedural Memory Playbook for multi-step executable workflows.
    Tracks empirical reliability via Laplace-smoothed success/failure telemetry:
    Reliability = (success_count + 1) / (success_count + failure_count + 2)
    """
    __tablename__ = "procedural_playbooks"

    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    project_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=True
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    trigger_tags: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)
    preconditions: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)
    steps: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    parent_playbook_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    success_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    @property
    def total_runs(self) -> int:
        return int(self.success_count or 0) + int(self.failure_count or 0)

    @property
    def reliability(self) -> float:
        """Laplace-smoothed empirical success probability: (success + 1) / (total + 2)."""
        s = int(self.success_count or 0)
        f = int(self.failure_count or 0)
        return float((s + 1.0) / (s + f + 2.0))

    def compute_tag_relevance(
        self,
        query_tags: Optional[List[str]] = None,
        context_query: Optional[str] = None,
    ) -> float:
        """Calculates normalized relevance [0.0, 1.0] matching tags and context tokens."""
        q_tags = {t.strip().lower() for t in (query_tags or []) if t and t.strip()}
        pb_raw_tags = self.trigger_tags or []
        if isinstance(pb_raw_tags, str):
            try:
                pb_raw_tags = json.loads(pb_raw_tags)
            except Exception:
                pb_raw_tags = [pb_raw_tags]
        pb_tags = {str(t).strip().lower() for t in pb_raw_tags if t and str(t).strip()}

        tag_score = 0.0
        if q_tags:
            overlap = len(q_tags & pb_tags)
            tag_score = float(overlap) / float(len(q_tags))

        text_score = 0.0
        if context_query and context_query.strip():
            from backend.services.embedding import tokenize_code_identifiers
            q_tokens = set(tokenize_code_identifiers(context_query.lower()))
            pb_text = f"{self.name} {self.description or ''} {' '.join(pb_tags)}"
            pb_tokens = set(tokenize_code_identifiers(pb_text.lower()))
            if q_tokens:
                text_score = float(len(q_tokens & pb_tokens)) / float(len(q_tokens))

        if q_tags and context_query:
            return 0.7 * tag_score + 0.3 * text_score
        elif q_tags:
            return tag_score
        elif context_query:
            return text_score
        return 1.0

    def compute_ranking_score(
        self,
        query_tags: Optional[List[str]] = None,
        context_query: Optional[str] = None,
    ) -> float:
        """Ranking formula: TagRelevance * (0.4 + 0.6 * Reliability)."""
        relevance = self.compute_tag_relevance(query_tags, context_query)
        if relevance <= 0.0:
            return 0.0
        return float(relevance * (0.4 + 0.6 * self.reliability))

    def canonical_payload(self) -> Dict[str, Any]:
        """Deterministic dictionary for cryptographic hashing."""
        tags = self.trigger_tags or []
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except Exception:
                tags = [tags]
        preconds = self.preconditions or []
        if isinstance(preconds, str):
            try:
                preconds = json.loads(preconds)
            except Exception:
                preconds = [preconds]
        steps_list = self.steps or []
        if isinstance(steps_list, str):
            try:
                steps_list = json.loads(steps_list)
            except Exception:
                steps_list = [{"raw": steps_list}]

        return {
            "description": str(self.description or ""),
            "failure_count": int(self.failure_count or 0),
            "id": str(self.id or ""),
            "name": str(self.name or ""),
            "organization_id": str(self.organization_id or ""),
            "parent_playbook_id": str(self.parent_playbook_id or "") if self.parent_playbook_id else None,
            "preconditions": [str(p) for p in preconds],
            "project_id": str(self.project_id or "") if self.project_id else None,
            "steps": list(steps_list),
            "success_count": int(self.success_count or 0),
            "trigger_tags": sorted([str(t) for t in tags]),
            "version": int(self.version or 1),
        }

    def data_hash(self) -> str:
        """SHA-256 digest of canonical playbook payload."""
        canonical_json = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"))
        return compute_sha256_hex(canonical_json)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "organization_id": self.organization_id,
            "project_id": self.project_id,
            "name": self.name,
            "description": self.description,
            "trigger_tags": list(self.trigger_tags or []),
            "preconditions": list(self.preconditions or []),
            "steps": list(self.steps or []),
            "version": self.version,
            "parent_playbook_id": self.parent_playbook_id,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "total_runs": self.total_runs,
            "reliability": round(self.reliability, 4),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class MemoryLedgerEntry(Base):
    """
    Immutable cryptographic ledger entry tracking all long-term memory mutations.
    Maintains a verifiable SHA-256 monotonic hash chain.
    """
    __tablename__ = "memory_ledger_entries"

    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    project_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=True
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    previous_hash: Mapped[str] = mapped_column(String(64), default=GENESIS_HASH, nullable=False)
    entry_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    mutation_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    record_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    data_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    # Synonym for prev_hash attribute access and queries
    prev_hash = synonym("previous_hash")

    __table_args__ = (
        UniqueConstraint("organization_id", "project_id", "sequence_number", name="uq_memory_ledger_org_proj_seq"),
        Index("ix_memory_ledger_org_proj_seq", "organization_id", "project_id", "sequence_number"),
    )

    def canonical_payload(self) -> Dict[str, Any]:
        """Deterministic dictionary for entry hash computation."""
        prev = str(self.previous_hash or GENESIS_HASH)
        # If payload_json is present, ensure data_hash reflects it
        d_hash = compute_sha256_hex(self.payload_json) if self.payload_json else str(self.data_hash or "")
        return {
            "data_hash": d_hash,
            "id": str(self.id or ""),
            "mutation_type": str(self.mutation_type or ""),
            "organization_id": str(self.organization_id or ""),
            "prev_hash": prev,
            "project_id": str(self.project_id or "") if self.project_id else None,
            "record_id": str(self.record_id or ""),
            "sequence_number": int(self.sequence_number or 1),
            "timestamp": normalize_memory_timestamp(self.timestamp),
        }

    def canonical_json(self) -> str:
        return json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"))

    def compute_entry_hash(self) -> str:
        """SHA-256 digest over previous_hash and canonical JSON."""
        prev = str(self.previous_hash or GENESIS_HASH)
        material = f"{prev}:{self.canonical_json()}"
        return compute_sha256_hex(material)

    def seal(self, previous_hash: Optional[str] = None, sequence_number: Optional[int] = None, prev_hash: Optional[str] = None) -> "MemoryLedgerEntry":
        p_hash = previous_hash if previous_hash is not None else prev_hash
        if sequence_number is not None:
            self.sequence_number = int(sequence_number)
        if p_hash is not None:
            self.previous_hash = str(p_hash)
        if not self.previous_hash:
            self.previous_hash = GENESIS_HASH
        if not self.sequence_number:
            self.sequence_number = 1
        self.entry_hash = self.compute_entry_hash()
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "organization_id": self.organization_id,
            "project_id": self.project_id,
            "sequence_number": self.sequence_number,
            "previous_hash": self.previous_hash,
            "prev_hash": self.previous_hash,
            "entry_hash": self.entry_hash,
            "mutation_type": self.mutation_type,
            "record_id": self.record_id,
            "data_hash": self.data_hash,
            "payload_json": self.payload_json,
            "timestamp": normalize_memory_timestamp(self.timestamp),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# Model alias for test suite compatibility
LedgerEntry = MemoryLedgerEntry
