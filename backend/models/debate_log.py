"""
DebateLog Model — Persists every round of multi-agent debate for full transparency.

Each time a coding agent produces output, it goes through a debate pipeline where
Security, Code Review, and QA agents independently review it. Each round of this
debate is stored as a DebateLog entry.
"""
import enum
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Text, Boolean, Integer, Enum, DateTime
from backend.models.base import Base


class ConsensusVerdict(str, enum.Enum):
    """Final verdict after all reviewers have spoken."""
    APPROVED = "APPROVED"           # All reviewers approve
    REJECTED = "REJECTED"           # At least one reviewer rejects
    NEEDS_REVISION = "NEEDS_REVISION"  # Coder must revise and resubmit
    BLOCKED = "BLOCKED"             # Critical security finding — pipeline halted
    MAX_ROUNDS_EXCEEDED = "MAX_ROUNDS_EXCEEDED"  # Debate timed out


class DebateLog(Base):
    """
    Records one round of the multi-agent debate pipeline.
    
    A single task may have multiple DebateLog entries (one per round).
    The full debate transcript is the ordered list of DebateLog entries
    for a given task_id, sorted by round_number.
    """
    __tablename__ = "debate_logs"

    task_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    mission_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    round_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    
    # Who wrote the code
    coder_agent_type: Mapped[str] = mapped_column(String, nullable=False)
    coder_output_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    code_artifacts_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Reviewer verdicts (JSON array of {agent_type, verdict, findings, feedback})
    reviewer_verdicts_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Overall consensus for this round
    consensus: Mapped[ConsensusVerdict] = mapped_column(
        Enum(ConsensusVerdict), 
        nullable=False, 
        default=ConsensusVerdict.NEEDS_REVISION
    )
    
    # If revision was needed, the prompt sent back to the coder
    revision_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Whether this round achieved final consensus
    consensus_reached: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Timing
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
