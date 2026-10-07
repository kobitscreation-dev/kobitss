import enum
from typing import Optional, Any
from datetime import datetime, timezone
from sqlalchemy import String, Text, ForeignKey, JSON, Float, Enum, Boolean, Integer, DateTime
from sqlalchemy.orm import relationship, Mapped, mapped_column
from backend.models.base import Base

class StrategyType(str, enum.Enum):
    SEQUENTIAL = 'SEQUENTIAL'
    PARALLEL = 'PARALLEL'
    SPECIALIST_FIRST = 'SPECIALIST_FIRST'
    RISK_FIRST = 'RISK_FIRST'
    REVIEW_FIRST = 'REVIEW_FIRST'
    MINIMAL_TEAM = 'MINIMAL_TEAM'
    FULL_TEAM = 'FULL_TEAM'


class FailureCategory(str, enum.Enum):
    """Classifies process problems by root cause."""
    MEMORY = 'MEMORY'
    PLANNING = 'PLANNING'
    COORDINATION = 'COORDINATION'
    TOOL = 'TOOL'
    VERIFICATION = 'VERIFICATION'
    SECURITY = 'SECURITY'
    INFRASTRUCTURE = 'INFRASTRUCTURE'
    PROVIDER = 'PROVIDER'
    MODEL = 'MODEL'
    GATEWAY = 'GATEWAY'
    TASK = 'TASK'
    DNS_ERROR = 'DNS_ERROR'
    TIMEOUT = 'TIMEOUT'
    RATE_LIMIT_429 = 'RATE_LIMIT_429'
    SERVER_ERROR_5XX = 'SERVER_ERROR_5XX'
    AUTH_ERROR = 'AUTH_ERROR'
    MALFORMED_RESPONSE = 'MALFORMED_RESPONSE'
    UNKNOWN = 'UNKNOWN'
    NONE = 'NONE'


class TaskContract(Base):
    __tablename__ = 'task_contracts'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey('tasks.id', ondelete='CASCADE'), index=True)
    mission_id: Mapped[str] = mapped_column(ForeignKey('missions.id', ondelete='CASCADE'), index=True)
    
    goal: Mapped[str] = mapped_column(Text)
    inputs_json: Mapped[str] = mapped_column(Text)
    required_outputs_json: Mapped[str] = mapped_column(Text)
    allowed_tools_json: Mapped[str] = mapped_column(Text)
    success_criteria_json: Mapped[str] = mapped_column(Text)
    failure_criteria_json: Mapped[str] = mapped_column(Text)
    risk_constraints_json: Mapped[str] = mapped_column(Text)


class ProcessMemory(Base):
    __tablename__ = 'process_memory'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey('organizations.id', ondelete='CASCADE'), index=True)
    project_id: Mapped[Optional[str]] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=True, index=True)
    task_category: Mapped[str] = mapped_column(String, index=True)
    
    # Team & strategy
    team_composition_json: Mapped[str] = mapped_column(Text)
    strategy_type: Mapped[StrategyType] = mapped_column(Enum(StrategyType))
    parallelization_json: Mapped[str] = mapped_column(Text)
    
    # Aggregated metrics (rolling averages updated with each new sample)
    success_rate: Mapped[float] = mapped_column(Float, default=1.0)
    avg_corrections: Mapped[float] = mapped_column(Float, default=0.0)
    avg_latency: Mapped[float] = mapped_column(Float, default=0.0)
    avg_cost: Mapped[float] = mapped_column(Float, default=0.0)
    
    sample_size: Mapped[int] = mapped_column(Integer, default=1)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    
    # Source evidence
    source_mission_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)


class ProcessOutcomeRecord(Base):
    """Immutable record of a single mission's process evidence.
    
    One record per completed/failed mission.  ProcessMemory aggregates
    across multiple ProcessOutcomeRecords for the same task_category.
    """
    __tablename__ = 'process_outcome_records'
    
    organization_id: Mapped[str] = mapped_column(ForeignKey('organizations.id', ondelete='CASCADE'), index=True)
    project_id: Mapped[Optional[str]] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=True, index=True)
    mission_id: Mapped[str] = mapped_column(ForeignKey('missions.id', ondelete='CASCADE'), index=True, unique=True)
    
    # Classification
    task_category: Mapped[str] = mapped_column(String, index=True)
    mission_type: Mapped[str] = mapped_column(String, default='engineering')
    outcome: Mapped[str] = mapped_column(String)  # COMPLETED or FAILED
    failure_category: Mapped[Optional[str]] = mapped_column(Enum(FailureCategory), nullable=True)
    
    # Team & strategy
    team_composition_json: Mapped[str] = mapped_column(Text)
    strategy_type: Mapped[str] = mapped_column(String)
    workflow_phases_completed_json: Mapped[str] = mapped_column(Text, default='[]')
    
    # Metrics
    # Quality Scorecard Metrics
    correctness_score: Mapped[float] = mapped_column(Float, default=1.0)
    security_score: Mapped[float] = mapped_column(Float, default=1.0)
    architecture_score: Mapped[float] = mapped_column(Float, default=1.0)
    test_quality_score: Mapped[float] = mapped_column(Float, default=1.0)
    reliability_score: Mapped[float] = mapped_column(Float, default=1.0)
    maintainability_score: Mapped[float] = mapped_column(Float, default=1.0)
    evidence_quality_score: Mapped[float] = mapped_column(Float, default=1.0)
    delivery_success: Mapped[bool] = mapped_column(Boolean, default=False)
    efficiency_score: Mapped[float] = mapped_column(Float, default=1.0)
    
    total_tasks: Mapped[int] = mapped_column(Integer, default=0)
    completed_tasks: Mapped[int] = mapped_column(Integer, default=0)
    failed_tasks: Mapped[int] = mapped_column(Integer, default=0)
    corrections: Mapped[int] = mapped_column(Integer, default=0)
    human_interventions: Mapped[int] = mapped_column(Integer, default=0)
    review_findings: Mapped[int] = mapped_column(Integer, default=0)
    security_findings: Mapped[int] = mapped_column(Integer, default=0)
    tests_run: Mapped[int] = mapped_column(Integer, default=0)
    tests_passed: Mapped[int] = mapped_column(Integer, default=0)
    
    # Telemetry
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_cost: Mapped[float] = mapped_column(Float, default=0.0)
    total_latency_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    
    # Tools & agents
    agents_used_json: Mapped[str] = mapped_column(Text, default='[]')
    tools_used_json: Mapped[str] = mapped_column(Text, default='[]')
    
    # Memory
    memories_retrieved: Mapped[int] = mapped_column(Integer, default=0)
    memories_useful: Mapped[int] = mapped_column(Integer, default=0)


class IntelligenceTrace(Base):
    __tablename__ = 'intelligence_traces'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mission_id: Mapped[str] = mapped_column(ForeignKey('missions.id', ondelete='CASCADE'), index=True)
    task_id: Mapped[Optional[str]] = mapped_column(ForeignKey('tasks.id', ondelete='CASCADE'), nullable=True, index=True)
    
    decision_type: Mapped[str] = mapped_column(String, index=True)  # WHY_TEAM, WHY_STRATEGY, WHY_CONTEXT
    decision_value: Mapped[str] = mapped_column(Text)
    
    rule_referenced: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    evidence_json: Mapped[str] = mapped_column(Text, default='{}')
    policy_referenced: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    historical_observation_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class MemoryUsefulnessState(str, enum.Enum):
    RETRIEVED = 'RETRIEVED'
    USEFUL = 'USEFUL'
    NOT_USED = 'NOT_USED'
    CONTRADICTED = 'CONTRADICTED'
    SUPERSEDED = 'SUPERSEDED'
    UNKNOWN = 'UNKNOWN'


class MemoryUsefulness(Base):
    __tablename__ = 'memory_usefulness'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    memory_id: Mapped[str] = mapped_column(String, index=True)
    memory_type: Mapped[str] = mapped_column(String, default='process')  # process, agent, project, mission
    mission_id: Mapped[str] = mapped_column(ForeignKey('missions.id', ondelete='CASCADE'), index=True)
    agent_id: Mapped[str] = mapped_column(String)
    
    state: Mapped[MemoryUsefulnessState] = mapped_column(Enum(MemoryUsefulnessState), default=MemoryUsefulnessState.UNKNOWN)
    outcome_correlation: Mapped[float] = mapped_column(Float, default=0.0)


class HumanFeedback(Base):
    __tablename__ = 'human_feedback'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mission_id: Mapped[str] = mapped_column(ForeignKey('missions.id', ondelete='CASCADE'), index=True)
    task_id: Mapped[Optional[str]] = mapped_column(ForeignKey('tasks.id', ondelete='CASCADE'), nullable=True, index=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey('organizations.id', ondelete='CASCADE'), index=True)
    agent_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    
    feedback_type: Mapped[str] = mapped_column(String)  # APPROVE, REJECT, REQUEST_CHANGES, REASSIGN, OVERRIDE
    structured_feedback_json: Mapped[str] = mapped_column(Text)
    
    applied_to_memory: Mapped[bool] = mapped_column(Boolean, default=False)


class ContextEfficiencyRecord(Base):
    """Tracks context selection quality per mission."""
    __tablename__ = 'context_efficiency_records'
    
    mission_id: Mapped[str] = mapped_column(ForeignKey('missions.id', ondelete='CASCADE'), index=True, unique=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey('organizations.id', ondelete='CASCADE'), index=True)
    
    memories_considered: Mapped[int] = mapped_column(Integer, default=0)
    memories_selected: Mapped[int] = mapped_column(Integer, default=0)
    memories_actually_used: Mapped[int] = mapped_column(Integer, default=0)
    context_tokens: Mapped[int] = mapped_column(Integer, default=0)
