from typing import List, Optional
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Text, ForeignKey, Enum, Integer, Float, JSON, Boolean
import enum
from backend.models.base import Base
from backend.models.mission import WorkflowPhase


class ProjectStatus(str, enum.Enum):
    PLANNING = "PLANNING"
    BUILDING = "BUILDING"
    REVIEW = "REVIEW"
    TESTING = "TESTING"
    READY = "READY"
    DEPLOYED = "DEPLOYED"
    MONITORING = "MONITORING"
    PAUSED = "PAUSED"


class Project(Base):
    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[ProjectStatus] = mapped_column(Enum(ProjectStatus), default=ProjectStatus.PLANNING)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    health_score: Mapped[int] = mapped_column(Integer, default=100)
    repository_url: Mapped[Optional[str]] = mapped_column(String)
    environment: Mapped[Optional[str]] = mapped_column(String)
    tech_stack: Mapped[Optional[str]] = mapped_column(Text)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)

    tasks: Mapped[List["Task"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    activities: Mapped[List["Activity"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    memories: Mapped[List["ProjectMemory"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class TaskStatus(str, enum.Enum):
    PENDING = "PENDING"
    PLANNED = "PLANNED"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    REVIEW = "REVIEW"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskPriority(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Task(Base):
    __tablename__ = "tasks"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    mission_id: Mapped[Optional[str]] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), nullable=True, index=True)
    milestone_id: Mapped[Optional[str]] = mapped_column(ForeignKey("milestones.id", ondelete="SET NULL"), nullable=True, index=True)
    repository_id: Mapped[Optional[str]] = mapped_column(ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True)
    
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[TaskStatus] = mapped_column(Enum(TaskStatus), default=TaskStatus.PENDING)
    phase: Mapped[Optional[WorkflowPhase]] = mapped_column(Enum(WorkflowPhase), nullable=True)
    priority: Mapped[TaskPriority] = mapped_column(Enum(TaskPriority), default=TaskPriority.MEDIUM)
    risk_level: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    
    assigned_agent_id: Mapped[Optional[str]] = mapped_column(ForeignKey("agents.id"), nullable=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    parent_task_id: Mapped[Optional[str]] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    
    # Correction / Rework Tracking
    is_correction: Mapped[bool] = mapped_column(default=False)
    finding_id: Mapped[Optional[str]] = mapped_column(String, nullable=True) # References findings.id
    parent_correction_task_id: Mapped[Optional[str]] = mapped_column(String, nullable=True) # References tasks.id
    attempt_count: Mapped[int] = mapped_column(default=1)
    
    dependencies_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    input_context_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expected_output: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Budgets & Continuations
    budget_max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    budget_max_provider_retries: Mapped[int] = mapped_column(Integer, default=5)
    budget_max_correction_retries: Mapped[int] = mapped_column(Integer, default=2)
    budget_max_tool_failures: Mapped[int] = mapped_column(Integer, default=10)
    budget_max_wall_time_seconds: Mapped[int] = mapped_column(Integer, default=3600)
    last_valid_state_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    completed_tool_actions_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    project: Mapped["Project"] = relationship(back_populates="tasks")


class ActivityType(str, enum.Enum):
    PROJECT_CREATED = "PROJECT_CREATED"
    PROJECT_UPDATED = "PROJECT_UPDATED"
    MISSION_CREATED = "MISSION_CREATED"
    MISSION_UPDATED = "MISSION_UPDATED"
    MISSION_ANALYZED = "MISSION_ANALYZED"
    MISSION_PLANNED = "MISSION_PLANNED"
    MISSION_APPROVAL_REQUESTED = "MISSION_APPROVAL_REQUESTED"
    MISSION_APPROVED = "MISSION_APPROVED"
    MISSION_REJECTED = "MISSION_REJECTED"
    MISSION_CANCELLED = "MISSION_CANCELLED"
    MISSION_COMPLETED = "MISSION_COMPLETED"
    MISSION_FAILED = "MISSION_FAILED"
    MILESTONE_STARTED = "MILESTONE_STARTED"
    MILESTONE_COMPLETED = "MILESTONE_COMPLETED"
    TASK_CREATED = "TASK_CREATED"
    TASK_STARTED = "TASK_STARTED"
    TASK_ASSIGNED = "TASK_ASSIGNED"
    TASK_UPDATED = "TASK_UPDATED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"
    AGENT_STARTED = "AGENT_STARTED"
    AGENT_COMPLETED = "AGENT_COMPLETED"
    AGENT_FAILED = "AGENT_FAILED"
    AGENT_ASSIGNED = "AGENT_ASSIGNED"
    PLAN_CREATED = "PLAN_CREATED"
    REVIEW_REQUESTED = "REVIEW_REQUESTED"
    REVIEW_STARTED = "REVIEW_STARTED"
    REVIEW_COMPLETED = "REVIEW_COMPLETED"
    TEST_STARTED = "TEST_STARTED"
    TEST_COMPLETED = "TEST_COMPLETED"
    SECURITY_SCAN_COMPLETED = "SECURITY_SCAN_COMPLETED"
    DEPLOYMENT_STARTED = "DEPLOYMENT_STARTED"
    DEPLOYMENT_COMPLETED = "DEPLOYMENT_COMPLETED"
    DEPLOYMENT_FAILED = "DEPLOYMENT_FAILED"
    USER_APPROVED = "USER_APPROVED"
    USER_REJECTED = "USER_REJECTED"
    TOOL_CREATED = "TOOL_CREATED"
    TOOL_EXECUTED = "TOOL_EXECUTED"


class Activity(Base):
    __tablename__ = "activities"

    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[Optional[str]] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)
    user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"), nullable=True)
    type: Mapped[ActivityType] = mapped_column(Enum(ActivityType))
    title: Mapped[str] = mapped_column(String)
    description: Mapped[Optional[str]] = mapped_column(Text)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    project: Mapped[Optional["Project"]] = relationship(back_populates="activities")


class ProjectMemoryStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    CONFLICTING = "CONFLICTING"

class ProjectMemory(Base):
    __tablename__ = "project_memory"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String)  # architecture, stack, conventions, etc.
    key: Mapped[str] = mapped_column(String)
    value: Mapped[str] = mapped_column(Text)
    
    source_agent_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    evidence: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    embedding_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    memory_status: Mapped[ProjectMemoryStatus] = mapped_column(Enum(ProjectMemoryStatus), default=ProjectMemoryStatus.ACTIVE)

    project: Mapped["Project"] = relationship(back_populates="memories")


class Notification(Base):
    __tablename__ = "notifications"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    message: Mapped[Optional[str]] = mapped_column(Text)
    read: Mapped[bool] = mapped_column(default=False)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
