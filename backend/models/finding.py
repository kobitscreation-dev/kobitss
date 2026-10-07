import enum
from typing import Optional
from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Text, Enum, ForeignKey, DateTime, Integer
from backend.models.base import Base

class FindingType(str, enum.Enum):
    QA = "QA"
    SECURITY = "SECURITY"
    CODE_REVIEW = "CODE_REVIEW"
    ARCHITECTURE = "ARCHITECTURE"
    PERFORMANCE = "PERFORMANCE"
    ACCESSIBILITY = "ACCESSIBILITY"
    BUILD = "BUILD"
    INTEGRATION = "INTEGRATION"
    REQUIREMENT = "REQUIREMENT"
    MERGE_CONFLICT = "MERGE_CONFLICT"

class FindingSeverity(str, enum.Enum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

class FindingStatus(str, enum.Enum):
    OPEN = "OPEN"
    ASSIGNED = "ASSIGNED"
    FIXING = "FIXING"
    REVALIDATING = "REVALIDATING"
    RESOLVED = "RESOLVED"
    REOPENED = "REOPENED"
    ACCEPTED_RISK = "ACCEPTED_RISK"
    BLOCKED = "BLOCKED"

class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[Optional[str]] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True, nullable=True)
    agent_id: Mapped[Optional[str]] = mapped_column(ForeignKey("agents.id"), nullable=True)
    
    type: Mapped[FindingType] = mapped_column(Enum(FindingType))
    severity: Mapped[FindingSeverity] = mapped_column(Enum(FindingSeverity))
    status: Mapped[FindingStatus] = mapped_column(Enum(FindingStatus), default=FindingStatus.OPEN)
    
    title: Mapped[str] = mapped_column(String)
    description: Mapped[Text] = mapped_column(Text)
    evidence: Mapped[Optional[str]] = mapped_column(Text) # Error logs, diffs, etc.
    affected_files: Mapped[Optional[str]] = mapped_column(Text) # JSON list
    recommended_fix: Mapped[Optional[str]] = mapped_column(Text)
    
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

