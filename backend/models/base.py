from datetime import datetime, timezone
from typing import Any
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import DateTime
import uuid

class Base(DeclarativeBase):
    """Base class for all SQLAlchemy declarative models."""
    
    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


from backend.models.organization import Organization
from backend.models.project import Project, Task
from backend.models.mission import Mission
from backend.models.agent import Agent, AgentRun, ApprovalRequest
from backend.models.github import GitHubConnection, Repository
from backend.models.tool import CustomTool
from backend.models.intelligence import ProcessMemory, ProcessOutcomeRecord, IntelligenceTrace, MemoryUsefulness, HumanFeedback, ContextEfficiencyRecord
