"""
Governed Agent Teams with Strict Contracts & Specialists.

Enforces strict Pydantic contract schemas for all 21 specialized agents in the
Kobits multi-agent organization. Validates:
1. Input contract schemas before agent task execution
2. Expected artifact deliverables and formats
3. Output contract schemas and findings structure
4. Mandatory approval gates prior to advancing workflow phases
"""

import re
import json
from enum import Enum
from typing import Optional, Dict, Any, List, Union, Type
from pydantic import BaseModel, Field, field_validator, model_validator

from backend.models.agent import AgentType
from backend.models.mission import WorkflowPhase


# =========================================================================
# Base Pydantic Contracts
# =========================================================================

class ValidationResult(BaseModel):
    is_valid: bool
    agent_type: str
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    sanitized_data: Optional[Dict[str, Any]] = None


class PhaseGateResult(BaseModel):
    allowed: bool
    from_phase: str
    to_phase: str
    missing_artifacts: List[str] = Field(default_factory=list)
    missing_approvals: List[str] = Field(default_factory=list)
    reason: Optional[str] = None


class BaseAgentInput(BaseModel):
    """Base input contract required for all specialized agents."""
    mission_id: str
    project_id: Optional[str] = "default"
    task_title: str
    task_description: Optional[str] = ""
    mission_objective: Optional[str] = ""
    upstream_artifacts: Optional[Dict[str, Any]] = Field(default_factory=dict)
    advanced_intelligence_context: Optional[str] = ""
    parameters: Optional[Dict[str, Any]] = Field(default_factory=dict)

    @field_validator("task_title")
    @classmethod
    def validate_title_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("task_title cannot be empty or blank")
        return v.strip()


class BaseAgentOutput(BaseModel):
    """Base output contract required from all specialized agents."""
    status: str = Field(default="COMPLETED")
    summary: str
    artifacts: Optional[Union[Dict[str, Any], List[Any]]] = Field(default_factory=dict)
    findings: Optional[List[Any]] = Field(default_factory=list)
    recommendations: Optional[List[str]] = Field(default_factory=list)
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)

    @field_validator("summary")
    @classmethod
    def validate_summary_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Agent output summary cannot be empty")
        return v.strip()


# =========================================================================
# Specialized Agent Contract Schemas (All 21+ Specialists)
# =========================================================================

# 1. Orchestrator (Nexus / Axiom)
class OrchestratorInput(BaseAgentInput):
    pass

class OrchestratorOutput(BaseAgentOutput):
    execution_plan: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    dag_topological_order: Optional[List[str]] = Field(default_factory=list)


# 2. Product Manager (Forge)
class ProductManagerInput(BaseAgentInput):
    pass

class ProductManagerOutput(BaseAgentOutput):
    user_stories: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    acceptance_criteria: Optional[List[str]] = Field(default_factory=list)
    mvp_scope: Optional[Dict[str, Any]] = Field(default_factory=dict)


# 3. UX/UI Designer (Muse / Pixel)
class UXDesignerInput(BaseAgentInput):
    pass

class UXDesignerOutput(BaseAgentOutput):
    wireframe_specs: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    color_palette: Optional[Dict[str, str]] = Field(default_factory=dict)
    responsive_breakpoints: Optional[List[str]] = Field(default_factory=lambda: ["desktop", "tablet", "mobile"])


# 4. Solution Architect (Axiom / Nexus)
class SolutionArchitectInput(BaseAgentInput):
    pass

class SolutionArchitectOutput(BaseAgentOutput):
    architecture_decisions: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    api_specifications: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    data_flow_diagram: Optional[str] = None


# 5. Technical Lead (Forge)
class TechnicalLeadInput(BaseAgentInput):
    pass

class TechnicalLeadOutput(BaseAgentOutput):
    decomposed_tasks: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    file_impact_matrix: Optional[List[str]] = Field(default_factory=list)


# 6. Database Engineer (Schema)
class DatabaseEngineerInput(BaseAgentInput):
    pass

class DatabaseEngineerOutput(BaseAgentOutput):
    migration_scripts: Optional[List[str]] = Field(default_factory=list)
    schema_definitions: Optional[Dict[str, Any]] = Field(default_factory=dict)
    index_optimizations: Optional[List[str]] = Field(default_factory=list)


# 7. Backend Engineer (Core / Forge)
class BackendEngineerInput(BaseAgentInput):
    pass

class BackendEngineerOutput(BaseAgentOutput):
    code_files_modified: Optional[List[str]] = Field(default_factory=list)
    endpoints_created: Optional[List[str]] = Field(default_factory=list)
    unit_tests_added: Optional[List[str]] = Field(default_factory=list)


# 8. Frontend Engineer (Pixel / Muse)
class FrontendEngineerInput(BaseAgentInput):
    pass

class FrontendEngineerOutput(BaseAgentOutput):
    components_modified: Optional[List[str]] = Field(default_factory=list)
    css_files_modified: Optional[List[str]] = Field(default_factory=list)
    preview_url: Optional[str] = None


# 9. Mobile Engineer (Swift)
class MobileEngineerInput(BaseAgentInput):
    pass

class MobileEngineerOutput(BaseAgentOutput):
    platform: Optional[str] = "cross-platform"
    screens_modified: Optional[List[str]] = Field(default_factory=list)


# 10. AI / ML Engineer (Neuron)
class AIMLEngineerInput(BaseAgentInput):
    pass

class AIMLEngineerOutput(BaseAgentOutput):
    model_configs: Optional[Dict[str, Any]] = Field(default_factory=dict)
    prompts_authored: Optional[List[Dict[str, str]]] = Field(default_factory=list)


# 11. Integrations Engineer (Bridge)
class IntegrationsEngineerInput(BaseAgentInput):
    pass

class IntegrationsEngineerOutput(BaseAgentOutput):
    third_party_connectors: Optional[List[str]] = Field(default_factory=list)
    auth_flows: Optional[List[str]] = Field(default_factory=list)


# 12. DevOps Engineer (Pulse)
class DevOpsEngineerInput(BaseAgentInput):
    pass

class DevOpsEngineerOutput(BaseAgentOutput):
    ci_cd_pipelines: Optional[List[str]] = Field(default_factory=list)
    dockerfiles_updated: Optional[List[str]] = Field(default_factory=list)


# 13. QA Engineer (Sentinel)
class QAEngineerInput(BaseAgentInput):
    pass

class QAEngineerOutput(BaseAgentOutput):
    test_runs_count: int = 0
    passed_count: int = 0
    failed_count: int = 0
    visual_findings: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    regression_status: str = "GREEN"


# 14. Security Engineer (Aegis)
class SecurityEngineerInput(BaseAgentInput):
    pass

class SecurityEngineerOutput(BaseAgentOutput):
    vulnerabilities_found: int = 0
    threat_level: str = "LOW"  # LOW | MEDIUM | HIGH | CRITICAL
    security_gate_passed: bool = True
    audit_findings: Optional[List[Dict[str, Any]]] = Field(default_factory=list)


# 15. Code Reviewer (Review)
class CodeReviewerInput(BaseAgentInput):
    pass

class CodeReviewerOutput(BaseAgentOutput):
    review_status: str = "APPROVED"  # APPROVED | REQUEST_CHANGES | COMMENT
    comments: Optional[List[Dict[str, Any]]] = Field(default_factory=list)


# 16. Performance Engineer (Vector)
class PerformanceEngineerInput(BaseAgentInput):
    pass

class PerformanceEngineerOutput(BaseAgentOutput):
    latency_p95_ms: Optional[float] = None
    throughput_rps: Optional[float] = None
    optimizations: Optional[List[str]] = Field(default_factory=list)


# 17. Accessibility Engineer (Access)
class AccessibilityEngineerInput(BaseAgentInput):
    pass

class AccessibilityEngineerOutput(BaseAgentOutput):
    wcag_compliance_level: str = "AA"
    violations_count: int = 0
    violations_list: Optional[List[Dict[str, Any]]] = Field(default_factory=list)


# 18. Data Analytics Engineer (Insight)
class DataAnalyticsEngineerInput(BaseAgentInput):
    pass

class DataAnalyticsEngineerOutput(BaseAgentOutput):
    telemetry_events: Optional[List[str]] = Field(default_factory=list)
    dashboards_configured: Optional[List[str]] = Field(default_factory=list)


# 19. Documentation Engineer (Scribe)
class DocumentationEngineerInput(BaseAgentInput):
    pass

class DocumentationEngineerOutput(BaseAgentOutput):
    docs_updated: Optional[List[str]] = Field(default_factory=list)
    api_reference_generated: bool = False


# 20. Release Manager (Launch)
class ReleaseManagerInput(BaseAgentInput):
    pass

class ReleaseManagerOutput(BaseAgentOutput):
    release_tag: Optional[str] = None
    changelog_summary: Optional[str] = None
    deployment_status: str = "READY"


# 21. SRE (Reliant)
class SREInput(BaseAgentInput):
    pass

class SREOutput(BaseAgentOutput):
    health_check_status: str = "HEALTHY"
    rollback_plan: Optional[str] = None


# 22. Business Analyst (Catalyst)
class BusinessAnalystInput(BaseAgentInput):
    pass

class BusinessAnalystOutput(BaseAgentOutput):
    roi_impact: Optional[str] = None
    kpis_tracked: Optional[List[str]] = Field(default_factory=list)


# =========================================================================
# Contract Specification Mapping
# =========================================================================

class SpecialistContractDefinition:
    def __init__(
        self,
        agent_type: AgentType,
        canonical_name: str,
        input_schema: Type[BaseModel],
        output_schema: Type[BaseModel],
        expected_artifacts: List[str],
        approval_gate_required: bool = False,
        allowed_phases: Optional[List[WorkflowPhase]] = None,
    ):
        self.agent_type = agent_type
        self.canonical_name = canonical_name
        self.input_schema = input_schema
        self.output_schema = output_schema
        self.expected_artifacts = expected_artifacts
        self.approval_gate_required = approval_gate_required
        self.allowed_phases = allowed_phases or list(WorkflowPhase)


SPECIALIST_CONTRACTS: Dict[AgentType, SpecialistContractDefinition] = {
    AgentType.ORCHESTRATOR: SpecialistContractDefinition(
        agent_type=AgentType.ORCHESTRATOR,
        canonical_name="Nexus",
        input_schema=OrchestratorInput,
        output_schema=OrchestratorOutput,
        expected_artifacts=["EXECUTION_PLAN", "DAG_SPECIFICATION"],
        approval_gate_required=False,
    ),
    AgentType.PRODUCT_MANAGER: SpecialistContractDefinition(
        agent_type=AgentType.PRODUCT_MANAGER,
        canonical_name="Forge",
        input_schema=ProductManagerInput,
        output_schema=ProductManagerOutput,
        expected_artifacts=["PRODUCT_REQUIREMENTS_DOCUMENT", "USER_STORIES"],
        approval_gate_required=False,
    ),
    AgentType.UX_DESIGNER: SpecialistContractDefinition(
        agent_type=AgentType.UX_DESIGNER,
        canonical_name="Muse",
        input_schema=UXDesignerInput,
        output_schema=UXDesignerOutput,
        expected_artifacts=["UI_WIREFRAME_SPEC", "DESIGN_SYSTEM_TOKENS"],
        approval_gate_required=False,
    ),
    AgentType.SOLUTION_ARCHITECT: SpecialistContractDefinition(
        agent_type=AgentType.SOLUTION_ARCHITECT,
        canonical_name="Axiom",
        input_schema=SolutionArchitectInput,
        output_schema=SolutionArchitectOutput,
        expected_artifacts=["ARCHITECTURE_DECISION_RECORD", "API_SPECIFICATION"],
        approval_gate_required=True,  # Architecture requires gate sign-off
    ),
    AgentType.TECHNICAL_LEAD: SpecialistContractDefinition(
        agent_type=AgentType.TECHNICAL_LEAD,
        canonical_name="Forge",
        input_schema=TechnicalLeadInput,
        output_schema=TechnicalLeadOutput,
        expected_artifacts=["TASK_DECOMPOSITION_SPEC", "IMPLEMENTATION_PLAN"],
        approval_gate_required=False,
    ),
    AgentType.DATABASE_ENGINEER: SpecialistContractDefinition(
        agent_type=AgentType.DATABASE_ENGINEER,
        canonical_name="Schema",
        input_schema=DatabaseEngineerInput,
        output_schema=DatabaseEngineerOutput,
        expected_artifacts=["DATABASE_MIGRATION_SCRIPT", "SCHEMA_SPECIFICATION"],
        approval_gate_required=True,  # DB migrations require gate sign-off
    ),
    AgentType.BACKEND_ENGINEER: SpecialistContractDefinition(
        agent_type=AgentType.BACKEND_ENGINEER,
        canonical_name="Core",
        input_schema=BackendEngineerInput,
        output_schema=BackendEngineerOutput,
        expected_artifacts=["BACKEND_CODE_PATCH", "API_IMPLEMENTATION"],
        approval_gate_required=False,
    ),
    AgentType.FRONTEND_ENGINEER: SpecialistContractDefinition(
        agent_type=AgentType.FRONTEND_ENGINEER,
        canonical_name="Pixel",
        input_schema=FrontendEngineerInput,
        output_schema=FrontendEngineerOutput,
        expected_artifacts=["FRONTEND_COMPONENT_PATCH", "UI_IMPLEMENTATION"],
        approval_gate_required=False,
    ),
    AgentType.MOBILE_ENGINEER: SpecialistContractDefinition(
        agent_type=AgentType.MOBILE_ENGINEER,
        canonical_name="Swift",
        input_schema=MobileEngineerInput,
        output_schema=MobileEngineerOutput,
        expected_artifacts=["MOBILE_CODE_PATCH"],
        approval_gate_required=False,
    ),
    AgentType.AI_ML_ENGINEER: SpecialistContractDefinition(
        agent_type=AgentType.AI_ML_ENGINEER,
        canonical_name="Neuron",
        input_schema=AIMLEngineerInput,
        output_schema=AIMLEngineerOutput,
        expected_artifacts=["AI_PROMPT_SPECIFICATION", "MODEL_CONFIGURATION"],
        approval_gate_required=False,
    ),
    AgentType.INTEGRATIONS_ENGINEER: SpecialistContractDefinition(
        agent_type=AgentType.INTEGRATIONS_ENGINEER,
        canonical_name="Bridge",
        input_schema=IntegrationsEngineerInput,
        output_schema=IntegrationsEngineerOutput,
        expected_artifacts=["CONNECTOR_INTEGRATION_SPEC"],
        approval_gate_required=False,
    ),
    AgentType.DEVOPS_ENGINEER: SpecialistContractDefinition(
        agent_type=AgentType.DEVOPS_ENGINEER,
        canonical_name="Pulse",
        input_schema=DevOpsEngineerInput,
        output_schema=DevOpsEngineerOutput,
        expected_artifacts=["CI_CD_WORKFLOW_SPEC", "DOCKERFILE_PATCH"],
        approval_gate_required=False,
    ),
    AgentType.QA_ENGINEER: SpecialistContractDefinition(
        agent_type=AgentType.QA_ENGINEER,
        canonical_name="Sentinel",
        input_schema=QAEngineerInput,
        output_schema=QAEngineerOutput,
        expected_artifacts=["TEST_EXECUTION_REPORT", "SENTINEL_FINDINGS"],
        approval_gate_required=False,
    ),
    AgentType.SECURITY_ENGINEER: SpecialistContractDefinition(
        agent_type=AgentType.SECURITY_ENGINEER,
        canonical_name="Aegis",
        input_schema=SecurityEngineerInput,
        output_schema=SecurityEngineerOutput,
        expected_artifacts=["SECURITY_AUDIT_REPORT", "THREAT_ASSESSMENT"],
        approval_gate_required=True,  # Security gate is mandatory before merge
    ),
    AgentType.CODE_REVIEWER: SpecialistContractDefinition(
        agent_type=AgentType.CODE_REVIEWER,
        canonical_name="Review",
        input_schema=CodeReviewerInput,
        output_schema=CodeReviewerOutput,
        expected_artifacts=["CODE_REVIEW_ASSESSMENT"],
        approval_gate_required=False,
    ),
    AgentType.PERFORMANCE_ENGINEER: SpecialistContractDefinition(
        agent_type=AgentType.PERFORMANCE_ENGINEER,
        canonical_name="Vector",
        input_schema=PerformanceEngineerInput,
        output_schema=PerformanceEngineerOutput,
        expected_artifacts=["PERFORMANCE_BENCHMARK_REPORT"],
        approval_gate_required=False,
    ),
    AgentType.ACCESSIBILITY_ENGINEER: SpecialistContractDefinition(
        agent_type=AgentType.ACCESSIBILITY_ENGINEER,
        canonical_name="Access",
        input_schema=AccessibilityEngineerInput,
        output_schema=AccessibilityEngineerOutput,
        expected_artifacts=["ACCESSIBILITY_AUDIT_REPORT"],
        approval_gate_required=False,
    ),
    AgentType.DATA_ANALYTICS_ENGINEER: SpecialistContractDefinition(
        agent_type=AgentType.DATA_ANALYTICS_ENGINEER,
        canonical_name="Insight",
        input_schema=DataAnalyticsEngineerInput,
        output_schema=DataAnalyticsEngineerOutput,
        expected_artifacts=["TELEMETRY_SPECIFICATION"],
        approval_gate_required=False,
    ),
    AgentType.DOCUMENTATION_ENGINEER: SpecialistContractDefinition(
        agent_type=AgentType.DOCUMENTATION_ENGINEER,
        canonical_name="Scribe",
        input_schema=DocumentationEngineerInput,
        output_schema=DocumentationEngineerOutput,
        expected_artifacts=["TECHNICAL_DOCUMENTATION"],
        approval_gate_required=False,
    ),
    AgentType.RELEASE_MANAGER: SpecialistContractDefinition(
        agent_type=AgentType.RELEASE_MANAGER,
        canonical_name="Launch",
        input_schema=ReleaseManagerInput,
        output_schema=ReleaseManagerOutput,
        expected_artifacts=["RELEASE_MANIFEST", "CHANGELOG"],
        approval_gate_required=True,  # Release requires human approval gate
    ),
    AgentType.SRE: SpecialistContractDefinition(
        agent_type=AgentType.SRE,
        canonical_name="Reliant",
        input_schema=SREInput,
        output_schema=SREOutput,
        expected_artifacts=["DEPLOYMENT_HEALTH_CHECK", "ROLLBACK_PLAN"],
        approval_gate_required=False,
    ),
    AgentType.BUSINESS_ANALYST: SpecialistContractDefinition(
        agent_type=AgentType.BUSINESS_ANALYST,
        canonical_name="Catalyst",
        input_schema=BusinessAnalystInput,
        output_schema=BusinessAnalystOutput,
        expected_artifacts=["BUSINESS_IMPACT_REPORT"],
        approval_gate_required=False,
    ),
}


# =========================================================================
# Governance Validation Engine
# =========================================================================

class GovernanceEngine:
    """
    Central Governance Engine for Specialized Agent Teams.
    Validates input schemas, output schemas, and phase transition gates.
    """

    @classmethod
    def resolve_contract(cls, agent_type_or_name: Union[AgentType, str]) -> Optional[SpecialistContractDefinition]:
        """Resolves SpecialistContractDefinition from AgentType or agent name."""
        if isinstance(agent_type_or_name, AgentType):
            return SPECIALIST_CONTRACTS.get(agent_type_or_name)

        s = str(agent_type_or_name).strip().upper()
        # 1. Match by AgentType value
        for at, contract in SPECIALIST_CONTRACTS.items():
            if at.value == s:
                return contract

        # 2. Match by canonical name (e.g. "Sentinel", "Aegis", "Nexus", "Axiom", "Forge")
        s_title = str(agent_type_or_name).strip().title()
        for contract in SPECIALIST_CONTRACTS.values():
            if contract.canonical_name.lower() == str(agent_type_or_name).strip().lower():
                return contract

        return None

    @classmethod
    def validate_agent_input(
        cls,
        agent_type_or_name: Union[AgentType, str],
        input_data: Dict[str, Any],
    ) -> ValidationResult:
        """Validates agent task input data against the specialist's strict Pydantic contract."""
        contract = cls.resolve_contract(agent_type_or_name)
        schema_cls = contract.input_schema if contract else BaseAgentInput
        agent_str = contract.agent_type.value if contract else str(agent_type_or_name)

        try:
            validated = schema_cls.model_validate(input_data or {})
            return ValidationResult(
                is_valid=True,
                agent_type=agent_str,
                sanitized_data=validated.model_dump(),
            )
        except Exception as e:
            errs = [str(err) for err in getattr(e, "errors", lambda: [str(e)])()]
            return ValidationResult(
                is_valid=False,
                agent_type=agent_str,
                errors=errs,
                sanitized_data=None,
            )

    @classmethod
    def validate_agent_output(
        cls,
        agent_type_or_name: Union[AgentType, str],
        output_data: Dict[str, Any],
    ) -> ValidationResult:
        """Validates agent task output deliverables against the specialist's strict Pydantic contract."""
        contract = cls.resolve_contract(agent_type_or_name)
        schema_cls = contract.output_schema if contract else BaseAgentOutput
        agent_str = contract.agent_type.value if contract else str(agent_type_or_name)

        if not isinstance(output_data, dict):
            return ValidationResult(
                is_valid=False,
                agent_type=agent_str,
                errors=["Agent output must be a JSON dictionary"],
            )

        try:
            validated = schema_cls.model_validate(output_data)
            warnings = []

            # Check expected artifacts
            if contract and contract.expected_artifacts:
                art = output_data.get("artifacts") or {}
                if isinstance(art, dict):
                    provided_keys = {k.upper() for k in art.keys()}
                elif isinstance(art, list):
                    provided_keys = {str(item.get("type", "")).upper() for item in art if isinstance(item, dict)}
                else:
                    provided_keys = set()

                missing = [ea for ea in contract.expected_artifacts if ea not in provided_keys]
                if missing:
                    warnings.append(f"Expected artifacts missing from deliverables: {', '.join(missing)}")

            return ValidationResult(
                is_valid=True,
                agent_type=agent_str,
                warnings=warnings,
                sanitized_data=validated.model_dump(),
            )
        except Exception as e:
            errs = [str(err) for err in getattr(e, "errors", lambda: [str(e)])()]
            return ValidationResult(
                is_valid=False,
                agent_type=agent_str,
                errors=errs,
            )

    @classmethod
    def validate_phase_transition(
        cls,
        current_phase: Optional[Union[WorkflowPhase, str]] = None,
        target_phase: Optional[Union[WorkflowPhase, str]] = None,
        from_phase: Optional[Union[WorkflowPhase, str]] = None,
        to_phase: Optional[Union[WorkflowPhase, str]] = None,
        artifacts: Optional[List[Union[Dict[str, Any], str]]] = None,
        approvals: Optional[List[Union[Dict[str, Any], str]]] = None,
        completed_artifacts: Optional[List[str]] = None,
        approvals_granted: Optional[List[str]] = None,
    ) -> PhaseGateResult:
        """
        Validates phase gate conditions before advancing workflow phases.
        E.g.:
        - INTAKE -> ARCHITECTURE: Requires Product Requirements
        - ARCHITECTURE -> IMPLEMENTATION: Requires Axiom Architecture approval
        - IMPLEMENTATION -> VERIFICATION: Requires Coder artifacts
        - VERIFICATION -> REVIEW: Requires Sentinel QA pass
        - REVIEW -> DEPLOYMENT: Requires Aegis Security approval
        """
        c_phase = from_phase if from_phase is not None else current_phase
        t_phase = to_phase if to_phase is not None else target_phase
        if c_phase is None:
            c_phase = WorkflowPhase.INTAKE
        if t_phase is None:
            t_phase = WorkflowPhase.IMPLEMENTATION

        cur = c_phase.value if isinstance(c_phase, WorkflowPhase) else str(c_phase).upper()
        tgt = t_phase.value if isinstance(t_phase, WorkflowPhase) else str(t_phase).upper()

        raw_arts = list(artifacts or []) + list(completed_artifacts or [])
        art_types = set()
        for a in raw_arts:
            if isinstance(a, dict):
                art_types.add(str(a.get("type", "")).upper())
            elif isinstance(a, str):
                art_types.add(a.strip().upper())

        raw_apps = list(approvals or []) + list(approvals_granted or [])
        app_types = set()
        for ap in raw_apps:
            if isinstance(ap, dict):
                if ap.get("status") in ("APPROVED", "passed", True, None):
                    app_types.add(str(ap.get("type", ap.get("role", ""))).upper())
            elif isinstance(ap, str):
                app_types.add(ap.strip().upper())

        missing_arts = []
        missing_apps = []

        if tgt in ("IMPLEMENTATION", "VERIFICATION", "VALIDATION") and cur in ("INTAKE", "ARCHITECTURE", "PLANNING"):
            # Check Architecture sign-off
            if not any(k in app_types for k in ("SOLUTION_ARCHITECT", "ARCHITECTURE_APPROVAL", "AXIOM", "NEXUS")):
                missing_apps.append("SOLUTION_ARCHITECT")

        if tgt in ("CODE_REVIEW", "DELIVERY_REVIEW", "REVIEW", "MERGE") and cur in ("INTAKE", "ANALYSIS", "PLANNING", "ARCHITECTURE", "ARCHITECTURE_REVIEW", "IMPLEMENTATION", "VALIDATION"):
            if not any(k in app_types for k in ("SECURITY_ENGINEER", "AEGIS", "SECURITY_AUDIT")) and not any(k in art_types for k in ("SECURITY_AUDIT", "SECURITY_REPORT")):
                missing_apps.append("SECURITY_ENGINEER")
            if cur in ("INTAKE", "ANALYSIS", "PLANNING", "ARCHITECTURE", "ARCHITECTURE_REVIEW"):
                if not any(k in app_types for k in ("SOLUTION_ARCHITECT", "ARCHITECTURE_APPROVAL", "AXIOM", "NEXUS")):
                    missing_apps.append("SOLUTION_ARCHITECT")

        if tgt in ("DEPLOYMENT", "RELEASE"):
            if not any(k in app_types for k in ("RELEASE_MANAGER", "HUMAN_APPROVAL", "HUMAN_ENGINEER_DELIVERY_APPROVAL", "LAUNCH")):
                missing_apps.append("HUMAN_ENGINEER_DELIVERY_APPROVAL")
            if cur in ("INTAKE", "ANALYSIS", "PLANNING", "ARCHITECTURE", "ARCHITECTURE_REVIEW", "IMPLEMENTATION", "VALIDATION"):
                if not any(k in app_types for k in ("SECURITY_ENGINEER", "AEGIS", "SECURITY_AUDIT")) and not any(k in art_types for k in ("SECURITY_AUDIT", "SECURITY_REPORT")):
                    missing_apps.append("SECURITY_ENGINEER")
            if cur in ("INTAKE", "ANALYSIS", "PLANNING", "ARCHITECTURE", "ARCHITECTURE_REVIEW", "IMPLEMENTATION", "VALIDATION", "CODE_REVIEW"):
                if not any(k in app_types for k in ("SOLUTION_ARCHITECT", "ARCHITECTURE_APPROVAL", "AXIOM", "NEXUS")):
                    missing_apps.append("SOLUTION_ARCHITECT")

        allowed = len(missing_arts) == 0 and len(missing_apps) == 0
        reason = None
        if not allowed:
            parts = []
            if missing_arts:
                parts.append(f"Missing artifacts: {', '.join(missing_arts)}")
            if missing_apps:
                parts.append(f"Missing approval gates: {', '.join(missing_apps)}")
            reason = "; ".join(parts)

        return PhaseGateResult(
            allowed=allowed,
            from_phase=cur,
            to_phase=tgt,
            missing_artifacts=missing_arts,
            missing_approvals=missing_apps,
            reason=reason,
        )
