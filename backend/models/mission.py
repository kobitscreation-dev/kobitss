import enum
from typing import List, Optional
from datetime import datetime, timezone
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Text, ForeignKey, Enum, Integer, Boolean, Float, DateTime
from backend.models.base import Base

class MissionStatus(str, enum.Enum):
    READY = "READY"
    ACTIVE = "ACTIVE"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    BLOCKED = "BLOCKED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

class WorkflowPhase(str, enum.Enum):
    INTAKE = "INTAKE"
    ANALYSIS = "ANALYSIS"
    PLANNING = "PLANNING"
    ARCHITECTURE_REVIEW = "ARCHITECTURE_REVIEW"
    IMPLEMENTATION = "IMPLEMENTATION"
    VALIDATION = "VALIDATION"
    SECURITY = "SECURITY"
    CODE_REVIEW = "CODE_REVIEW"
    DELIVERY_REVIEW = "DELIVERY_REVIEW"
    DEPLOYMENT = "DEPLOYMENT"
    LEARNING = "LEARNING"

class MissionPriority(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    URGENT = "URGENT"

class MissionRisk(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

class ApprovalStatus(str, enum.Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

class MilestoneStatus(str, enum.Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"

class Mission(Base):
    __tablename__ = "missions"

    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    repository_id: Mapped[Optional[str]] = mapped_column(ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True)
    milestone_id: Mapped[Optional[str]] = mapped_column(ForeignKey("milestones.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    
    title: Mapped[str] = mapped_column(String)
    objective: Mapped[Text] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(Text)
    
    status: Mapped[MissionStatus] = mapped_column(Enum(MissionStatus), default=MissionStatus.ACTIVE)
    phase: Mapped[WorkflowPhase] = mapped_column(Enum(WorkflowPhase), default=WorkflowPhase.INTAKE)

    # Budget tracking
    budget_max_tokens: Mapped[int] = mapped_column(Integer, default=500000)
    current_token_usage: Mapped[int] = mapped_column(Integer, default=0)

    execution_lock_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    execution_lock_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    priority: Mapped[MissionPriority] = mapped_column(Enum(MissionPriority), default=MissionPriority.MEDIUM)
    risk_level: Mapped[MissionRisk] = mapped_column(Enum(MissionRisk), default=MissionRisk.MEDIUM)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    
    estimated_duration: Mapped[Optional[int]] = mapped_column(Integer) # in minutes
    estimated_cost: Mapped[Optional[float]] = mapped_column(Float)
    actual_cost: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    approval_status: Mapped[ApprovalStatus] = mapped_column(Enum(ApprovalStatus), default=ApprovalStatus.NOT_REQUIRED)
    
    # Intelligence Layer Fields
    workflow_strategy: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    team_composition_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    risk_profile_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rework_risk: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    
    current_stage: Mapped[Optional[str]] = mapped_column(String)
    active_branch: Mapped[Optional[str]] = mapped_column(String)
    base_commit_sha: Mapped[Optional[str]] = mapped_column(String)
    
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    planning_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    execution_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    milestone: Mapped[Optional["Milestone"]] = relationship(back_populates="missions")
    plans: Mapped[List["MissionPlan"]] = relationship(back_populates="mission", cascade="all, delete-orphan")
    repositories: Mapped[List["MissionRepository"]] = relationship(back_populates="mission", cascade="all, delete-orphan")


class Milestone(Base):
    __tablename__ = "milestones"
    
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String)
    description: Mapped[Optional[str]] = mapped_column(Text)
    order: Mapped[int] = mapped_column(Integer, default=0)
    
    status: Mapped[MilestoneStatus] = mapped_column(Enum(MilestoneStatus), default=MilestoneStatus.PENDING)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    
    start_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    
    missions: Mapped[List["Mission"]] = relationship(back_populates="milestone")

class MissionRepository(Base):
    __tablename__ = "mission_repositories"
    
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), primary_key=True)
    repository_id: Mapped[str] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), primary_key=True)
    
    # Path where this repository is cloned relative to the mission sandbox root
    # e.g., "backend", "frontend", or "" if it's the only repository
    mount_path: Mapped[str] = mapped_column(String, default="")
    
    mission: Mapped["Mission"] = relationship(back_populates="repositories")

class MissionPlan(Base):
    __tablename__ = "mission_plans"
    
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    plan_data: Mapped[str] = mapped_column(Text) # JSON serialized plan
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[Optional[str]] = mapped_column(Text)
    
    mission: Mapped["Mission"] = relationship(back_populates="plans")

class WorkflowCheckpoint(Base):
    __tablename__ = 'workflow_checkpoints'
    
    id: Mapped[str] = mapped_column(String, primary_key=True)
    mission_id: Mapped[str] = mapped_column(ForeignKey('missions.id', ondelete='CASCADE'), index=True)
    task_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    phase: Mapped[str] = mapped_column(String)
    agent_role: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    checkpoint_name: Mapped[str] = mapped_column(String)
    
    artifacts_reference_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    memory_references_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    state_hash: Mapped[str] = mapped_column(String)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
