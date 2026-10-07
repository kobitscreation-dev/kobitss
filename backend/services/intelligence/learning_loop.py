"""Upgraded LearningLoop: captures full process evidence and updates
ProcessMemory with rolling confidence, without rebuilding existing systems."""
import json
from typing import Optional, List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from backend.models.intelligence import (
    ProcessMemory, ProcessOutcomeRecord, StrategyType,
    FailureCategory, IntelligenceTrace, MemoryUsefulness,
    MemoryUsefulnessState, HumanFeedback, ContextEfficiencyRecord,
)
from backend.models.mission import Mission, MissionStatus, WorkflowPhase
from backend.models.project import Task, TaskStatus
from backend.models.agent import AgentRun, AgentRunStatus


# ---------------------------------------------------------------------------
# Confidence calculation constants
# ---------------------------------------------------------------------------
_MIN_SAMPLES_FOR_TRUST = 3       # Require ≥3 outcomes before confidence > 0.7
_MAX_CONFIDENCE = 0.95
_CONFIDENCE_DECAY_ON_FAILURE = 0.15
_CONFIDENCE_GROWTH_ON_SUCCESS = 0.10


def _compute_confidence(sample_size: int, success_rate: float) -> float:
    """Bayesian-inspired confidence that requires repeated evidence.

    - 1 sample  → max 0.50 (never enough to trust/reject a strategy)
    - 2 samples → max 0.60
    - ≥3 samples with ≥80% success → can reach 0.70+
    - Never exceeds _MAX_CONFIDENCE
    """
    if sample_size <= 0:
        return 0.0
    base = min(0.3 + (sample_size * 0.15), 0.95)
    return min(base * (0.5 + 0.5 * success_rate), _MAX_CONFIDENCE)


class LearningLoop:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ======================================================================
    # 1. PROCESS OUTCOME RECORDING
    # ======================================================================

    async def record_process_outcome(
        self,
        mission: Mission,
        task_category: str = 'general',
    ) -> Optional[ProcessOutcomeRecord]:
        """Capture structured evidence from a completed/failed mission.

        Creates an immutable ProcessOutcomeRecord AND updates the rolling
        ProcessMemory aggregate for the task_category.
        """
        if mission.status not in (MissionStatus.COMPLETED, MissionStatus.FAILED):
            return None

        is_success = mission.status == MissionStatus.COMPLETED

        # --- Gather task-level metrics -----------------------------------
        stmt = select(Task).where(Task.mission_id == mission.id)
        tasks = (await self.db.execute(stmt)).scalars().all()

        total_tasks = len(tasks)
        completed_tasks = sum(1 for t in tasks if t.status == TaskStatus.COMPLETED)
        failed_tasks = sum(1 for t in tasks if t.status == TaskStatus.FAILED)
        corrections = sum(1 for t in tasks if t.is_correction)

        # --- Gather agent-run metrics ------------------------------------
        run_stmt = select(AgentRun).where(
            AgentRun.task_id.in_([t.id for t in tasks]) if tasks else AgentRun.id == None
        )
        runs = (await self.db.execute(run_stmt)).scalars().all()

        total_tokens = sum((r.tokens_input or 0) + (r.tokens_output or 0) for r in runs)
        total_cost = sum(r.estimated_cost or 0.0 for r in runs)
        total_latency = 0.0
        for r in runs:
            if r.started_at and r.completed_at:
                total_latency += (r.completed_at - r.started_at).total_seconds()

        agents_used = list({r.agent_id for r in runs})

        # Tools used (parse tool_calls_json from each run)
        tools_used: set = set()
        for r in runs:
            if r.tool_calls_json:
                try:
                    calls = json.loads(r.tool_calls_json)
                    for call in calls:
                        if isinstance(call, dict) and 'name' in call:
                            tools_used.add(call['name'])
                except (json.JSONDecodeError, TypeError):
                    pass

        # --- Classify failure --------------------------------------------
        failure_category = FailureCategory.NONE
        if not is_success:
            failure_category = self._classify_failure(mission, tasks, runs)

        # --- Human interventions -----------------------------------------
        fb_stmt = select(func.count(HumanFeedback.id)).where(
            HumanFeedback.mission_id == mission.id
        )
        human_interventions = (await self.db.execute(fb_stmt)).scalar() or 0

        # --- Memory usefulness -------------------------------------------
        mu_total = select(func.count(MemoryUsefulness.id)).where(
            MemoryUsefulness.mission_id == mission.id
        )
        mu_useful = select(func.count(MemoryUsefulness.id)).where(
            MemoryUsefulness.mission_id == mission.id,
            MemoryUsefulness.state == MemoryUsefulnessState.USEFUL
        )
        memories_retrieved = (await self.db.execute(mu_total)).scalar() or 0
        memories_useful = (await self.db.execute(mu_useful)).scalar() or 0

        # --- Create immutable outcome record -----------------------------
        record = ProcessOutcomeRecord(
            organization_id=mission.organization_id,
            project_id=mission.project_id,
            mission_id=mission.id,
            task_category=task_category,
            mission_type='engineering',
            outcome='COMPLETED' if is_success else 'FAILED',
            failure_category=failure_category,
            team_composition_json=mission.team_composition_json or '[]',
            strategy_type=mission.workflow_strategy or 'SEQUENTIAL',
            workflow_phases_completed_json=json.dumps(
                [t.phase.value for t in tasks if t.status == TaskStatus.COMPLETED and t.phase]
            ),
            total_tasks=total_tasks,
            completed_tasks=completed_tasks,
            failed_tasks=failed_tasks,
            corrections=corrections,
            human_interventions=human_interventions,
            total_tokens=total_tokens,
            total_cost=total_cost,
            total_latency_seconds=total_latency,
            agents_used_json=json.dumps(agents_used),
            tools_used_json=json.dumps(sorted(tools_used)),
            memories_retrieved=memories_retrieved,
            memories_useful=memories_useful,
        )
        self.db.add(record)

        # --- Update rolling ProcessMemory aggregate ----------------------
        await self._update_process_memory(
            organization_id=mission.organization_id,
            project_id=mission.project_id,
            task_category=task_category,
            team_json=mission.team_composition_json or '[]',
            strategy=mission.workflow_strategy or 'SEQUENTIAL',
            is_success=is_success,
            corrections=corrections,
            latency=total_latency,
            cost=total_cost,
            mission_id=mission.id,
        )

        await self.db.commit()
        return record

    # ======================================================================
    # 2. FAILURE CLASSIFICATION
    # ======================================================================

    def _classify_failure(
        self,
        mission: Mission,
        tasks: list,
        runs: list,
    ) -> FailureCategory:
        """Use actual evidence to classify root cause."""
        for run in runs:
            if run.status == AgentRunStatus.FAILED and run.error:
                err = run.error.lower()
                if 'timeout' in err or 'connection' in err or '504' in err:
                    return FailureCategory.PROVIDER
                if 'sandbox' in err or 'git' in err or 'path' in err:
                    return FailureCategory.INFRASTRUCTURE
                if 'parse' in err or 'json' in err or 'schema' in err:
                    return FailureCategory.COORDINATION
                if 'security' in err or 'vulnerability' in err:
                    return FailureCategory.SECURITY
                if 'tool' in err or 'max turns' in err:
                    return FailureCategory.TOOL
                if 'memory' in err:
                    return FailureCategory.MEMORY
                if 'policy' in err or 'contract' in err:
                    return FailureCategory.VERIFICATION

        # Heuristic: if mission died in PLANNING, it's a planning issue
        if mission.phase in (WorkflowPhase.PLANNING, WorkflowPhase.ANALYSIS):
            return FailureCategory.PLANNING

        return FailureCategory.UNKNOWN

    def diagnose_failures(self, mission: Mission) -> str:
        """Legacy compatibility."""
        if mission.status == MissionStatus.FAILED:
            return 'UNKNOWN_ISSUE'
        return 'NO_ISSUE'

    # ======================================================================
    # 3. ROLLING PROCESS MEMORY UPDATE
    # ======================================================================

    async def _update_process_memory(
        self,
        organization_id: str,
        project_id: Optional[str],
        task_category: str,
        team_json: str,
        strategy: str,
        is_success: bool,
        corrections: int,
        latency: float,
        cost: float,
        mission_id: str,
    ):
        """Update or create a rolling ProcessMemory entry.

        Key safety rules:
        - One failure does NOT cause permanent strategy rejection.
        - One success does NOT cause permanent strategy promotion.
        - Confidence grows slowly with repeated evidence.
        """
        try:
            strategy_enum = StrategyType(strategy)
        except ValueError:
            strategy_enum = StrategyType.SEQUENTIAL

        # Find existing aggregate for this org + category + strategy
        stmt = select(ProcessMemory).where(
            ProcessMemory.organization_id == organization_id,
            ProcessMemory.task_category == task_category,
            ProcessMemory.strategy_type == strategy_enum,
        )
        existing = (await self.db.execute(stmt)).scalars().first()

        if existing:
            n = existing.sample_size
            new_n = n + 1
            # Rolling average updates
            existing.success_rate = (existing.success_rate * n + (1.0 if is_success else 0.0)) / new_n
            existing.avg_corrections = (existing.avg_corrections * n + corrections) / new_n
            existing.avg_latency = (existing.avg_latency * n + latency) / new_n
            existing.avg_cost = (existing.avg_cost * n + cost) / new_n
            existing.sample_size = new_n
            existing.confidence = _compute_confidence(new_n, existing.success_rate)
            existing.source_mission_id = mission_id
        else:
            pm = ProcessMemory(
                organization_id=organization_id,
                project_id=project_id,
                task_category=task_category,
                team_composition_json=team_json,
                strategy_type=strategy_enum,
                parallelization_json='{}',
                success_rate=1.0 if is_success else 0.0,
                avg_corrections=float(corrections),
                avg_latency=latency,
                avg_cost=cost,
                sample_size=1,
                confidence=_compute_confidence(1, 1.0 if is_success else 0.0),
                source_mission_id=mission_id,
            )
            self.db.add(pm)

    # ======================================================================
    # 4. MEMORY USEFULNESS
    # ======================================================================

    async def track_memory_retrieval(
        self,
        memory_id: str,
        memory_type: str,
        mission_id: str,
        agent_id: str,
    ) -> MemoryUsefulness:
        """Record that a memory was retrieved (initial state = RETRIEVED)."""
        mu = MemoryUsefulness(
            memory_id=memory_id,
            memory_type=memory_type,
            mission_id=mission_id,
            agent_id=agent_id,
            state=MemoryUsefulnessState.RETRIEVED,
        )
        self.db.add(mu)
        await self.db.commit()
        return mu

    async def update_memory_usefulness(
        self,
        memory_id: str,
        mission_id: str,
        new_state: MemoryUsefulnessState,
        outcome_correlation: float = 0.0,
    ):
        """Update a memory's usefulness state based on observable evidence."""
        stmt = select(MemoryUsefulness).where(
            MemoryUsefulness.memory_id == memory_id,
            MemoryUsefulness.mission_id == mission_id,
        )
        mu = (await self.db.execute(stmt)).scalars().first()
        if mu:
            mu.state = new_state
            mu.outcome_correlation = outcome_correlation
            await self.db.commit()

    # ======================================================================
    # 5. HUMAN FEEDBACK
    # ======================================================================

    async def record_human_feedback(
        self,
        mission_id: str,
        organization_id: str,
        feedback_type: str,
        structured_feedback: dict,
        task_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> HumanFeedback:
        """Record structured human feedback without auto-altering strategies."""
        fb = HumanFeedback(
            mission_id=mission_id,
            organization_id=organization_id,
            task_id=task_id,
            agent_id=agent_id,
            feedback_type=feedback_type,
            structured_feedback_json=json.dumps(structured_feedback),
            applied_to_memory=False,
        )
        self.db.add(fb)
        await self.db.commit()
        return fb

    # ======================================================================
    # 6. CONTEXT EFFICIENCY
    # ======================================================================

    async def record_context_efficiency(
        self,
        mission_id: str,
        organization_id: str,
        memories_considered: int,
        memories_selected: int,
        memories_actually_used: int,
        context_tokens: int,
    ) -> ContextEfficiencyRecord:
        """Track context quality metrics."""
        rec = ContextEfficiencyRecord(
            mission_id=mission_id,
            organization_id=organization_id,
            memories_considered=memories_considered,
            memories_selected=memories_selected,
            memories_actually_used=memories_actually_used,
            context_tokens=context_tokens,
        )
        self.db.add(rec)
        await self.db.commit()
        return rec

    # ======================================================================
    # 7. INTELLIGENCE TRACE
    # ======================================================================

    async def record_trace(
        self,
        mission_id: str,
        decision_type: str,
        decision_value: str,
        rule: Optional[str] = None,
        evidence: Optional[dict] = None,
        policy: Optional[str] = None,
        observation_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> IntelligenceTrace:
        """Store a deterministic trace for an adaptive decision."""
        trace = IntelligenceTrace(
            mission_id=mission_id,
            task_id=task_id,
            decision_type=decision_type,
            decision_value=decision_value,
            rule_referenced=rule,
            evidence_json=json.dumps(evidence or {}),
            policy_referenced=policy,
            historical_observation_id=observation_id,
        )
        self.db.add(trace)
        await self.db.commit()
        return trace
