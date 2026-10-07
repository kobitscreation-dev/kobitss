from typing import Optional
from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import UniqueConstraint
from sqlalchemy import String, Text, Enum, Integer, Float, ForeignKey, DateTime
import enum
from backend.models.base import Base


class AgentType(str, enum.Enum):
    ORCHESTRATOR = "ORCHESTRATOR"
    PRODUCT_MANAGER = "PRODUCT_MANAGER"
    UX_DESIGNER = "UX_DESIGNER"
    SOLUTION_ARCHITECT = "SOLUTION_ARCHITECT"
    TECHNICAL_LEAD = "TECHNICAL_LEAD"
    DATABASE_ENGINEER = "DATABASE_ENGINEER"
    BACKEND_ENGINEER = "BACKEND_ENGINEER"
    FRONTEND_ENGINEER = "FRONTEND_ENGINEER"
    MOBILE_ENGINEER = "MOBILE_ENGINEER"
    AI_ML_ENGINEER = "AI_ML_ENGINEER"
    INTEGRATIONS_ENGINEER = "INTEGRATIONS_ENGINEER"
    DEVOPS_ENGINEER = "DEVOPS_ENGINEER"
    QA_ENGINEER = "QA_ENGINEER"
    SECURITY_ENGINEER = "SECURITY_ENGINEER"
    CODE_REVIEWER = "CODE_REVIEWER"
    PERFORMANCE_ENGINEER = "PERFORMANCE_ENGINEER"
    ACCESSIBILITY_ENGINEER = "ACCESSIBILITY_ENGINEER"
    DATA_ANALYTICS_ENGINEER = "DATA_ANALYTICS_ENGINEER"
    DOCUMENTATION_ENGINEER = "DOCUMENTATION_ENGINEER"
    RELEASE_MANAGER = "RELEASE_MANAGER"
    SRE = "SRE"
    BUSINESS_ANALYST = "BUSINESS_ANALYST"


class AgentStatus(str, enum.Enum):
    IDLE = "IDLE"
    THINKING = "THINKING"
    WORKING = "WORKING"
    WAITING = "WAITING"
    REVIEWING = "REVIEWING"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"
    OFFLINE = "OFFLINE"


class Agent(Base):
    __tablename__ = "agents"

    name: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[AgentType] = mapped_column(Enum(AgentType), unique=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    capabilities: Mapped[Optional[str]] = mapped_column(Text)  # JSON list
    system_prompt: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[AgentStatus] = mapped_column(Enum(AgentStatus), default=AgentStatus.IDLE)
    configuration: Mapped[Optional[str]] = mapped_column(Text)  # JSON config


class AgentRunStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ResultClassification(str, enum.Enum):
    """Canonical classification for model result status."""
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_INFERRED_STATUS = "COMPLETED_WITH_INFERRED_STATUS"
    FAILED = "FAILED"
    INDETERMINATE = "INDETERMINATE"
    PARSE_ERROR = "PARSE_ERROR"


# ---------------------------------------------------------------------------
# Evidence keys that count toward structured-output completeness
# ---------------------------------------------------------------------------
_EVIDENCE_KEYS = frozenset({
    "summary", "findings", "changes", "recommendations",
    "risks", "next_actions", "artifacts", "user_stories",
    "mvp_scope", "task", "task_title", "mission_id",
})

_MIN_EVIDENCE_KEYS_REQUIRED = 2  # at least 2 substantive keys present


def _has_sufficient_evidence(result: dict) -> bool:
    """Return True when the structured output contains enough substantive
    content to infer the model completed its work despite omitting ``status``.

    Rules (ALL must hold):
      1. result is a non-empty dict
      2. at least ``_MIN_EVIDENCE_KEYS_REQUIRED`` evidence keys are present
         and non-empty
      3. no ``error`` or ``exception`` key signalling a failure
      4. ``_usage`` block is present (proves the LLM actually ran)
    """
    if not isinstance(result, dict) or not result:
        return False

    # Disqualify if there is an explicit error signal
    if result.get("error") or result.get("exception"):
        return False

    # Count substantive evidence keys
    evidence_count = 0
    for key in _EVIDENCE_KEYS:
        val = result.get(key)
        if val is not None and val != "" and val != [] and val != {}:
            evidence_count += 1

    if evidence_count < _MIN_EVIDENCE_KEYS_REQUIRED:
        return False

    # Require _usage to prove the provider actually completed the call
    if "_usage" not in result:
        return False

    return True


def classify_model_result(result: Optional[dict]) -> tuple:
    """Classify a model result into a canonical status with reasoning.

    Returns:
        (ResultClassification, AgentRunStatus, reason: str)
    """
    if result is None or not isinstance(result, dict):
        return (
            ResultClassification.PARSE_ERROR,
            AgentRunStatus.FAILED,
            "Result is None or not a dict",
        )

    raw_status = result.get("status")

    # --- Explicit status present -------------------------------------------
    if raw_status is not None:
        if hasattr(raw_status, "value"):
            raw_status = raw_status.value
        upper = str(raw_status).upper()

        if upper in ("SUCCESS", "COMPLETED", "COMPLETE"):
            return (
                ResultClassification.COMPLETED,
                AgentRunStatus.COMPLETED,
                f"Explicit status '{raw_status}' maps to COMPLETED",
            )
        if upper == "FAILED":
            return (
                ResultClassification.FAILED,
                AgentRunStatus.FAILED,
                f"Explicit status '{raw_status}' maps to FAILED",
            )
        # Unknown status string
        return (
            ResultClassification.INDETERMINATE,
            AgentRunStatus.FAILED,
            f"Unknown status value '{raw_status}'",
        )

    # --- Missing status ----------------------------------------------------
    if _has_sufficient_evidence(result):
        return (
            ResultClassification.COMPLETED_WITH_INFERRED_STATUS,
            AgentRunStatus.COMPLETED,
            "Status field missing but sufficient structured evidence present",
        )

    return (
        ResultClassification.INDETERMINATE,
        AgentRunStatus.FAILED,
        "Status field missing and insufficient evidence to infer completion",
    )


def normalize_agent_result_status(status_str: Optional[str]) -> AgentRunStatus:
    """Legacy helper — kept for backward compatibility.

    For new code, prefer ``classify_model_result`` which gives full telemetry.
    """
    if not status_str:
        return AgentRunStatus.FAILED

    if hasattr(status_str, "value"):
        status_str = status_str.value

    upper_status = str(status_str).upper()
    if upper_status in ("SUCCESS", "COMPLETED", "COMPLETE"):
        return AgentRunStatus.COMPLETED
    return AgentRunStatus.FAILED


class AgentRun(Base):
    __tablename__ = "agent_runs"

    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[Optional[str]] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True, index=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    
    status: Mapped[AgentRunStatus] = mapped_column(Enum(AgentRunStatus), default=AgentRunStatus.QUEUED)
    
    input_text: Mapped[Optional[str]] = mapped_column(Text)
    output_text: Mapped[Optional[str]] = mapped_column(Text) # The final AgentOutput JSON
    tool_calls_json: Mapped[Optional[str]] = mapped_column(Text) # JSON array of tool usage audit logs
    
    model: Mapped[Optional[str]] = mapped_column(String)
    tokens_input: Mapped[int] = mapped_column(Integer, default=0)
    tokens_output: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0.0)
    
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    failure_category: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    recovery_action: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    approval_request: Mapped[Optional["ApprovalRequest"]] = relationship(back_populates="agent_run", cascade="all, delete-orphan")


class ApprovalStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    agent_run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), unique=True)
    requested_by: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    
    status: Mapped[ApprovalStatus] = mapped_column(Enum(ApprovalStatus), default=ApprovalStatus.PENDING)
    title: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text)
    risk_level: Mapped[str] = mapped_column(String) # HIGH, CRITICAL
    
    # Metadata for the UI to display diffs/impact
    affected_services: Mapped[Optional[str]] = mapped_column(Text) # JSON list
    validation_results: Mapped[Optional[str]] = mapped_column(Text) # JSON string
    
    resolved_by_user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    feedback: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    agent_run: Mapped["AgentRun"] = relationship(back_populates="approval_request")


class DAGNodeCache(Base):
    """
    Idempotent cache for individual DAG nodes (like reviewers).
    If the server crashes during a parallel execution, we don't lose the LLM 
    calls that already completed for that round.
    """
    __tablename__ = "dag_node_cache"

    __table_args__ = (
        UniqueConstraint("task_id", "round_number", "node_id", name="uq_dag_node_cache"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(String, index=True)
    round_number: Mapped[int] = mapped_column(Integer)
    node_id: Mapped[str] = mapped_column(String)  # e.g. 'SECURITY_ENGINEER'
    result_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
