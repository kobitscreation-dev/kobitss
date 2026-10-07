from typing import Optional
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Text, ForeignKey, Enum, Float
import enum
from backend.models.base import Base

class MessageType(str, enum.Enum):
    REQUEST = "REQUEST"
    INFORMATION = "INFORMATION"
    DECISION = "DECISION"
    WARNING = "WARNING"
    FINDING = "FINDING"
    RECOMMENDATION = "RECOMMENDATION"
    HANDOFF = "HANDOFF"
    BLOCKER = "BLOCKER"
    REVIEW = "REVIEW"
    CORRECTION = "CORRECTION"
    COMPLETION = "COMPLETION"

class AgentMessage(Base):
    __tablename__ = "agent_messages"

    sender_agent_id: Mapped[str] = mapped_column(String, index=True) # E.g., 'Core'
    receiver_agent_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True) # Optional for broadcasts
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[Optional[str]] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True, index=True)
    
    type: Mapped[MessageType] = mapped_column(Enum(MessageType))
    content: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class AgentArtifact(Base):
    __tablename__ = "agent_artifacts"

    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    
    producer_agent_id: Mapped[str] = mapped_column(String, index=True)
    consumer_agent_ids_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True) # JSON list of agent IDs
    task_id: Mapped[Optional[str]] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True, index=True)
    
    type: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    dependencies_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    

class AgentDecision(Base):
    __tablename__ = "agent_decisions"

    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    
    agent_id: Mapped[str] = mapped_column(String, index=True)
    decision: Mapped[str] = mapped_column(String)
    reason: Mapped[str] = mapped_column(Text)
    evidence: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    alternatives: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class AgentLessonType(str, enum.Enum):
    FAILURE = "FAILURE"
    REVIEW_REJECTION = "REVIEW_REJECTION"

class AgentLesson(Base):
    __tablename__ = "agent_lessons"

    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    
    agent_id: Mapped[str] = mapped_column(String, index=True)
    type: Mapped[AgentLessonType] = mapped_column(Enum(AgentLessonType))
    cause: Mapped[str] = mapped_column(Text)
    lesson: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

