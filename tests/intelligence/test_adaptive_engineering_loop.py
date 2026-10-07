"""Deterministic tests for the Adaptive Engineering Loop V1.

Covers all 17 capabilities specified in the KOBITS ADAPTIVE ENGINEERING LOOP V1 spec:

1.  Process outcome persistence
2.  Strategy retrieval
3.  Adaptive team selection with history
4.  Confidence threshold enforcement
5.  Repeated evidence requirement
6.  Memory usefulness tracking
7.  Irrelevant memory filtering
8.  Failed strategy handling (negative learning)
9.  Human feedback recording
10. Organization isolation
11. Before/after comparison infrastructure
12. Intelligence traces
13. Context efficiency
14. Mission 1 → Mission 2 end-to-end learning
15. Negative learning: Mission 1 fail → Mission 2 does NOT reinforce
16. Self-diagnostics / failure classification
17. Full regression compatibility
"""
import pytest
import pytest_asyncio
import json
import uuid
from datetime import datetime, timezone, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.base import Base
from backend.models.intelligence import (
    ProcessMemory, ProcessOutcomeRecord, StrategyType,
    IntelligenceTrace, MemoryUsefulness, MemoryUsefulnessState,
    HumanFeedback, ContextEfficiencyRecord, FailureCategory,
)
from backend.models.mission import Mission, MissionStatus, WorkflowPhase
from backend.models.project import Task, TaskStatus, Project
from backend.models.organization import Organization
from backend.models.github import Repository
from backend.models.agent import AgentRun, AgentRunStatus
from backend.models.organization import Organization
from backend.core.database import AsyncSessionLocal, engine
from backend.services.intelligence.learning_loop import LearningLoop, _compute_confidence
from backend.services.intelligence.adaptive_planning import (
    AdaptivePlanner, MIN_SAMPLE_SIZE_TO_USE_HISTORY,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------




def _make_org(org_id: str = 'org_1') -> Organization:
    return Organization(id=org_id, name='Test Org')


def _make_project(org_id: str = 'org_1', proj_id: str = 'proj_1') -> Project:
    return Project(
        id=proj_id, name='Test Project', organization_id=org_id,
        created_by='user_1'
    )


def _make_mission(
    org_id: str = 'org_1',
    proj_id: str = 'proj_1',
    mission_id: str = None,
    status: MissionStatus = MissionStatus.COMPLETED,
    phase: WorkflowPhase = WorkflowPhase.LEARNING,
    strategy: str = 'SEQUENTIAL',
    team: list = None,
) -> Mission:
    return Mission(
        id=mission_id or str(uuid.uuid4()),
        organization_id=org_id,
        project_id=proj_id,
        created_by='user_1',
        title='Test Mission',
        objective='Test',
        status=status,
        phase=phase,
        workflow_strategy=strategy,
        team_composition_json=json.dumps(team or ['Core', 'Axiom']),
    )


def _make_task(
    mission_id: str,
    proj_id: str = 'proj_1',
    status: TaskStatus = TaskStatus.COMPLETED,
    is_correction: bool = False,
    phase: WorkflowPhase = WorkflowPhase.IMPLEMENTATION,
) -> Task:
    return Task(
        id=str(uuid.uuid4()),
        mission_id=mission_id,
        project_id=proj_id,
        title='Test Task',
        status=status,
        phase=phase,
        is_correction=is_correction,
        created_by='user_1',
    )


def _make_agent_run(
    task_id: str,
    proj_id: str = 'proj_1',
    org_id: str = 'org_1',
    status: AgentRunStatus = AgentRunStatus.COMPLETED,
    tokens_in: int = 100,
    tokens_out: int = 50,
    cost: float = 0.01,
    error: str = None,
) -> AgentRun:
    now = datetime.now(timezone.utc)
    return AgentRun(
        id=str(uuid.uuid4()),
        agent_id='agent_1',
        project_id=proj_id,
        task_id=task_id,
        organization_id=org_id,
        status=status,
        tokens_input=tokens_in,
        tokens_output=tokens_out,
        estimated_cost=cost,
        started_at=now - timedelta(seconds=10),
        completed_at=now,
        error=error,
    )


async def _seed_org_and_project(db: AsyncSession, org_id='org_1', proj_id='proj_1'):
    """Helper to seed org+project so FK constraints pass."""
    from backend.models.organization import Organization, OrganizationMember, OrgRole
    # User stub
    from backend.models.organization import User
    user_id = f"user_{org_id}"
    email = f"{user_id}@test.com"
    # only add user if it doesn't exist
    user = (await db.execute(select(User).where(User.email == email))).scalars().first()
    if not user:
        user = User(id=user_id, email=email, hashed_password='x', full_name='Test')
        db.add(user)
    org = _make_org(org_id)
    db.add(org)
    member = OrganizationMember(
        id=str(uuid.uuid4()), user_id=user_id, organization_id=org_id,
        role=OrgRole.OWNER
    )
    db.add(member)
    proj = _make_project(org_id, proj_id)
    proj.created_by = user_id
    db.add(proj)
    await db.commit()


# =========================================================================
# 1. PROCESS OUTCOME PERSISTENCE
# =========================================================================

@pytest.mark.asyncio
async def test_process_outcome_persistence():
    """Record a process outcome and verify all fields are persisted."""
    async with AsyncSessionLocal() as db:
        await _seed_org_and_project(db)
        mission = _make_mission(status=MissionStatus.COMPLETED)
        db.add(mission)
        task = _make_task(mission.id)
        db.add(task)
        run = _make_agent_run(task.id)
        db.add(run)
        await db.commit()

        loop = LearningLoop(db)
        record = await loop.record_process_outcome(mission, task_category='authentication')

        assert record is not None
        assert record.outcome == 'COMPLETED'
        assert record.task_category == 'authentication'
        assert record.total_tasks == 1
        assert record.completed_tasks == 1
        assert record.total_tokens == 150
        assert record.total_cost > 0
        assert record.total_latency_seconds > 0

        # Verify it persisted in the DB
        stmt = select(ProcessOutcomeRecord).where(
            ProcessOutcomeRecord.mission_id == mission.id
        )
        loaded = (await db.execute(stmt)).scalars().first()
        assert loaded is not None
        assert loaded.outcome == 'COMPLETED'


# =========================================================================
# 2. STRATEGY RETRIEVAL
# =========================================================================

@pytest.mark.asyncio
async def test_strategy_retrieval_with_qualifying_memory():
    """Strategy should be retrieved when memory meets all thresholds."""
    async with AsyncSessionLocal() as db:
        await _seed_org_and_project(db)
        # Seed qualifying ProcessMemory
        pm = ProcessMemory(
            organization_id='org_1',
            task_category='authentication',
            strategy_type=StrategyType.RISK_FIRST,
            team_composition_json=json.dumps(['Core', 'Aegis', 'Sentinel']),
            success_rate=0.95,
            confidence=0.85,
            sample_size=5,
            parallelization_json='{}',
        )
        db.add(pm)
        await db.commit()

        planner = AdaptivePlanner(db)
        strategy = await planner.select_strategy(
            'authentication', 'LOW', organization_id='org_1'
        )
        assert strategy == StrategyType.RISK_FIRST


# =========================================================================
# 3. ADAPTIVE TEAM SELECTION WITH HISTORY
# =========================================================================

@pytest.mark.asyncio
async def test_adaptive_team_selection_with_history():
    """Team should come from historical memory when it qualifies."""
    async with AsyncSessionLocal() as db:
        await _seed_org_and_project(db)
        pm = ProcessMemory(
            organization_id='org_1',
            task_category='authentication',
            strategy_type=StrategyType.RISK_FIRST,
            team_composition_json=json.dumps(['Core', 'Aegis', 'Sentinel']),
            success_rate=0.90,
            confidence=0.80,
            sample_size=4,
            parallelization_json='{}',
        )
        db.add(pm)
        await db.commit()

        planner = AdaptivePlanner(db)
        team = await planner.select_team(
            'authentication', ['python'], StrategyType.RISK_FIRST,
            organization_id='org_1'
        )
        assert team == ['Core', 'Aegis', 'Sentinel']


# =========================================================================
# 4. CONFIDENCE THRESHOLD ENFORCEMENT
# =========================================================================

@pytest.mark.asyncio
async def test_confidence_threshold_enforcement():
    """Memory below confidence threshold must NOT be used."""
    async with AsyncSessionLocal() as db:
        await _seed_org_and_project(db)
        # Low confidence memory
        pm = ProcessMemory(
            organization_id='org_1',
            task_category='authentication',
            strategy_type=StrategyType.SPECIALIST_FIRST,
            team_composition_json=json.dumps(['Schema']),
            success_rate=0.90,
            confidence=0.50,  # below MIN_CONFIDENCE_TO_USE_HISTORY (0.7)
            sample_size=1,
            parallelization_json='{}',
        )
        db.add(pm)
        await db.commit()

        planner = AdaptivePlanner(db)
        strategy = await planner.select_strategy(
            'authentication', 'LOW', organization_id='org_1'
        )
        # Should fallback to SEQUENTIAL, not use the low-confidence memory
        assert strategy == StrategyType.SEQUENTIAL


# =========================================================================
# 5. REPEATED EVIDENCE REQUIREMENT
# =========================================================================

@pytest.mark.asyncio
async def test_repeated_evidence_requirement():
    """Memory with sample_size < MIN_SAMPLE_SIZE must NOT be used."""
    async with AsyncSessionLocal() as db:
        await _seed_org_and_project(db)
        pm = ProcessMemory(
            organization_id='org_1',
            task_category='authentication',
            strategy_type=StrategyType.PARALLEL,
            team_composition_json=json.dumps(['Core']),
            success_rate=1.0,
            confidence=0.40,  # 1 sample → max 0.40
            sample_size=1,    # below MIN_SAMPLE_SIZE_TO_USE_HISTORY (3)
            parallelization_json='{}',
        )
        db.add(pm)
        await db.commit()

        planner = AdaptivePlanner(db)
        strategy = await planner.select_strategy(
            'authentication', 'LOW', organization_id='org_1'
        )
        assert strategy == StrategyType.SEQUENTIAL  # fallback


def test_confidence_math_repeated_evidence():
    """_compute_confidence requires ≥3 samples to exceed 0.7."""
    assert _compute_confidence(1, 1.0) <= 0.50
    assert _compute_confidence(2, 1.0) <= 0.60
    assert _compute_confidence(3, 1.0) >= 0.70
    assert _compute_confidence(5, 0.9) >= 0.70
    assert _compute_confidence(10, 1.0) <= 0.95


# =========================================================================
# 6. MEMORY USEFULNESS TRACKING
# =========================================================================

@pytest.mark.asyncio
async def test_memory_usefulness_tracking():
    """Track RETRIEVED → USEFUL lifecycle."""
    async with AsyncSessionLocal() as db:
        await _seed_org_and_project(db)
        mission = _make_mission()
        db.add(mission)
        await db.commit()

        loop = LearningLoop(db)
        mu = await loop.track_memory_retrieval(
            memory_id='mem_1', memory_type='process',
            mission_id=mission.id, agent_id='agent_1'
        )
        assert mu.state == MemoryUsefulnessState.RETRIEVED

        await loop.update_memory_usefulness(
            memory_id='mem_1', mission_id=mission.id,
            new_state=MemoryUsefulnessState.USEFUL, outcome_correlation=0.9
        )

        stmt = select(MemoryUsefulness).where(MemoryUsefulness.memory_id == 'mem_1')
        loaded = (await db.execute(stmt)).scalars().first()
        assert loaded.state == MemoryUsefulnessState.USEFUL
        assert loaded.outcome_correlation == 0.9


# =========================================================================
# 7. IRRELEVANT MEMORY FILTERING
# =========================================================================

@pytest.mark.asyncio
async def test_irrelevant_memory_filtering():
    """Frontend memory must NOT be injected for a database task."""
    async with AsyncSessionLocal() as db:
        await _seed_org_and_project(db)
        # Seed FRONTEND memory
        pm = ProcessMemory(
            organization_id='org_1',
            task_category='frontend_ui',
            strategy_type=StrategyType.SPECIALIST_FIRST,
            team_composition_json=json.dumps(['Palette', 'Muse']),
            success_rate=1.0,
            confidence=0.85,
            sample_size=5,
            parallelization_json='{}',
        )
        db.add(pm)
        await db.commit()

        planner = AdaptivePlanner(db)
        # Query for 'database_migration' — must NOT get frontend memory
        strategy = await planner.select_strategy(
            'database_migration', 'LOW', organization_id='org_1'
        )
        assert strategy == StrategyType.SEQUENTIAL  # fallback, not SPECIALIST_FIRST


# =========================================================================
# 8. FAILED STRATEGY HANDLING (NEGATIVE LEARNING)
# =========================================================================

@pytest.mark.asyncio
async def test_failed_strategy_not_reinforced():
    """A failed mission's strategy must get low confidence, not be blindly rejected."""
    async with AsyncSessionLocal() as db:
        await _seed_org_and_project(db)
        mission = _make_mission(
            status=MissionStatus.FAILED,
            strategy='PARALLEL',
            team=['Core'],
        )
        db.add(mission)
        task = _make_task(mission.id, status=TaskStatus.FAILED)
        db.add(task)
        run = _make_agent_run(task.id, status=AgentRunStatus.FAILED, error='timeout')
        db.add(run)
        await db.commit()

        loop = LearningLoop(db)
        await loop.record_process_outcome(mission, task_category='payment')

        # Check the ProcessMemory was created with low confidence
        stmt = select(ProcessMemory).where(ProcessMemory.task_category == 'payment')
        pm = (await db.execute(stmt)).scalars().first()
        assert pm is not None
        assert pm.success_rate == 0.0  # 0/1
        assert pm.confidence < 0.7     # 1 sample → never reaches trust threshold
        assert pm.sample_size == 1

        # The planner must NOT use this memory (too low confidence & sample_size)
        planner = AdaptivePlanner(db)
        strategy = await planner.select_strategy(
            'payment', 'LOW', organization_id='org_1'
        )
        assert strategy == StrategyType.SEQUENTIAL  # falls back


# =========================================================================
# 9. HUMAN FEEDBACK RECORDING
# =========================================================================

@pytest.mark.asyncio
async def test_human_feedback_recording():
    """Human feedback is persisted without auto-altering strategies."""
    async with AsyncSessionLocal() as db:
        await _seed_org_and_project(db)
        mission = _make_mission()
        db.add(mission)
        await db.commit()

        loop = LearningLoop(db)
        fb = await loop.record_human_feedback(
            mission_id=mission.id,
            organization_id='org_1',
            feedback_type='REJECT',
            structured_feedback={'reason': 'Architecture is wrong'},
        )
        assert fb.feedback_type == 'REJECT'
        assert fb.applied_to_memory is False

        stmt = select(HumanFeedback).where(HumanFeedback.mission_id == mission.id)
        loaded = (await db.execute(stmt)).scalars().first()
        assert loaded is not None
        assert loaded.feedback_type == 'REJECT'


# =========================================================================
# 10. ORGANIZATION ISOLATION
# =========================================================================

@pytest.mark.asyncio
async def test_organization_isolation():
    """User A's ProcessMemory must NOT influence User B's planning."""
    async with AsyncSessionLocal() as db:
        await _seed_org_and_project(db, org_id='org_A', proj_id='proj_A')
        await _seed_org_and_project(db, org_id='org_B', proj_id='proj_B')

        # Org A has strong ProcessMemory
        pm = ProcessMemory(
            organization_id='org_A',
            task_category='authentication',
            strategy_type=StrategyType.PARALLEL,
            team_composition_json=json.dumps(['Core']),
            success_rate=1.0,
            confidence=0.9,
            sample_size=10,
            parallelization_json='{}',
        )
        db.add(pm)
        await db.commit()

        planner = AdaptivePlanner(db)

        # Org B must NOT see Org A's memory
        strategy_b = await planner.select_strategy(
            'authentication', 'LOW', organization_id='org_B'
        )
        assert strategy_b == StrategyType.SEQUENTIAL  # fallback

        # Org A should see its own memory
        strategy_a = await planner.select_strategy(
            'authentication', 'LOW', organization_id='org_A'
        )
        assert strategy_a == StrategyType.PARALLEL


# =========================================================================
# 11. BEFORE/AFTER COMPARISON INFRASTRUCTURE
# =========================================================================

@pytest.mark.asyncio
async def test_before_after_comparison():
    """Verify infrastructure exists to run baseline vs adaptive comparison."""
    async with AsyncSessionLocal() as db:
        await _seed_org_and_project(db)

        # BASELINE: no history → sequential
        planner = AdaptivePlanner(db)
        baseline = await planner.plan(
            'authentication', 'LOW', ['python'],
            organization_id='org_1', mission_id='mission_baseline'
        )
        assert baseline.used_history is False
        assert baseline.strategy == StrategyType.SEQUENTIAL
        assert 'FALLBACK' in baseline.reason_codes[0]

        # Seed strong history
        pm = ProcessMemory(
            organization_id='org_1',
            task_category='authentication',
            strategy_type=StrategyType.RISK_FIRST,
            team_composition_json=json.dumps(['Core', 'Aegis']),
            success_rate=0.95,
            confidence=0.85,
            sample_size=5,
            parallelization_json='{}',
        )
        db.add(pm)
        await db.commit()

        # ADAPTIVE: history-informed
        adaptive = await planner.plan(
            'authentication', 'LOW', ['python'],
            organization_id='org_1', mission_id='mission_adaptive'
        )
        assert adaptive.used_history is True
        assert adaptive.strategy == StrategyType.RISK_FIRST
        assert adaptive.team == ['Core', 'Aegis']
        assert 'HISTORICAL_MATCH' in adaptive.reason_codes[0]


# =========================================================================
# 12. INTELLIGENCE TRACES
# =========================================================================

@pytest.mark.asyncio
async def test_intelligence_traces():
    """Traces are stored with WHY_TEAM, WHY_STRATEGY, WHY_CONTEXT."""
    async with AsyncSessionLocal() as db:
        await _seed_org_and_project(db)
        pm = ProcessMemory(
            organization_id='org_1',
            task_category='api_endpoint',
            strategy_type=StrategyType.SEQUENTIAL,
            team_composition_json=json.dumps(['Core']),
            success_rate=0.90,
            confidence=0.80,
            sample_size=4,
            parallelization_json='{}',
        )
        db.add(pm)
        await db.commit()

        mission_id = 'trace_mission'
        planner = AdaptivePlanner(db)
        await planner.plan(
            'api_endpoint', 'LOW', ['python'],
            organization_id='org_1', mission_id=mission_id
        )

        stmt = select(IntelligenceTrace).where(
            IntelligenceTrace.mission_id == mission_id
        )
        traces = (await db.execute(stmt)).scalars().all()
        types = {t.decision_type for t in traces}
        assert 'WHY_STRATEGY' in types
        assert 'WHY_TEAM' in types
        assert 'WHY_CONTEXT' in types


# =========================================================================
# 13. CONTEXT EFFICIENCY
# =========================================================================

@pytest.mark.asyncio
async def test_context_efficiency():
    """Track and verify context selection quality metrics."""
    async with AsyncSessionLocal() as db:
        await _seed_org_and_project(db)
        mission = _make_mission()
        db.add(mission)
        await db.commit()

        loop = LearningLoop(db)
        rec = await loop.record_context_efficiency(
            mission_id=mission.id,
            organization_id='org_1',
            memories_considered=50,
            memories_selected=10,
            memories_actually_used=3,
            context_tokens=4200,
        )
        assert rec.memories_considered == 50
        assert rec.memories_selected == 10
        assert rec.memories_actually_used == 3
        assert rec.context_tokens == 4200


# =========================================================================
# 14. MISSION 1 → MISSION 2 END-TO-END LEARNING
# =========================================================================

@pytest.mark.asyncio
async def test_mission1_to_mission2_learning():
    """Mission 1 outcome → ProcessMemory → Mission 2 retrieves and uses it."""
    async with AsyncSessionLocal() as db:
        await _seed_org_and_project(db)
        category = 'user_profile_backend'

        # === MISSION 1: successful backend task ===
        m1 = _make_mission(
            mission_id='m1', status=MissionStatus.COMPLETED,
            strategy='RISK_FIRST', team=['Core', 'Aegis', 'Sentinel']
        )
        db.add(m1)
        t1 = _make_task(m1.id)
        db.add(t1)
        r1 = _make_agent_run(t1.id)
        db.add(r1)
        await db.commit()

        loop = LearningLoop(db)
        outcome = await loop.record_process_outcome(m1, task_category=category)
        assert outcome is not None
        assert outcome.outcome == 'COMPLETED'

        # After 1 mission, confidence is low — planner should NOT use it yet
        planner = AdaptivePlanner(db)
        plan_after_1 = await planner.plan(
            category, 'LOW', ['python'],
            organization_id='org_1', mission_id='m2_check_1'
        )
        assert plan_after_1.used_history is False  # only 1 sample

        # === Run 2 more successful missions with same category ===
        for i in range(2, 4):
            mi = _make_mission(
                mission_id=f'm{i}', status=MissionStatus.COMPLETED,
                strategy='RISK_FIRST', team=['Core', 'Aegis', 'Sentinel']
            )
            db.add(mi)
            ti = _make_task(mi.id)
            db.add(ti)
            ri = _make_agent_run(ti.id)
            db.add(ri)
            await db.commit()
            await loop.record_process_outcome(mi, task_category=category)

        # After 3 missions, planner SHOULD use history
        plan_after_3 = await planner.plan(
            category, 'LOW', ['python'],
            organization_id='org_1', mission_id='m4_final'
        )
        assert plan_after_3.used_history is True
        assert plan_after_3.strategy == StrategyType.RISK_FIRST
        assert plan_after_3.team == ['Core', 'Aegis', 'Sentinel']
        assert len(plan_after_3.evidence_refs) > 0

        # Verify traces were stored
        stmt = select(IntelligenceTrace).where(
            IntelligenceTrace.mission_id == 'm4_final'
        )
        traces = (await db.execute(stmt)).scalars().all()
        assert len(traces) >= 3  # WHY_TEAM, WHY_STRATEGY, WHY_CONTEXT


# =========================================================================
# 15. NEGATIVE LEARNING: fail → don't reinforce
# =========================================================================

@pytest.mark.asyncio
async def test_negative_learning_mission1_fail_mission2():
    """Mission 1 fails with PARALLEL. Mission 2 must NOT adopt PARALLEL."""
    async with AsyncSessionLocal() as db:
        await _seed_org_and_project(db)
        category = 'data_pipeline'

        # MISSION 1: FAILS
        m1 = _make_mission(
            mission_id='neg_m1', status=MissionStatus.FAILED,
            strategy='PARALLEL', team=['Core']
        )
        db.add(m1)
        t1 = _make_task(m1.id, status=TaskStatus.FAILED)
        db.add(t1)
        r1 = _make_agent_run(t1.id, status=AgentRunStatus.FAILED, error='provider timeout')
        db.add(r1)
        await db.commit()

        loop = LearningLoop(db)
        await loop.record_process_outcome(m1, task_category=category)

        # MISSION 2: same category
        planner = AdaptivePlanner(db)
        plan = await planner.plan(
            category, 'LOW', ['python'],
            organization_id='org_1', mission_id='neg_m2'
        )
        # Must NOT have adopted PARALLEL from the failed mission
        assert plan.used_history is False
        assert plan.strategy == StrategyType.SEQUENTIAL  # conservative fallback


# =========================================================================
# 16. SELF-DIAGNOSTICS / FAILURE CLASSIFICATION
# =========================================================================

@pytest.mark.asyncio
async def test_failure_classification():
    """Failure classification uses actual evidence from error strings."""
    async with AsyncSessionLocal() as db:
        await _seed_org_and_project(db)

        for err_str, expected_cat in [
            ('HTTP 504 timeout from provider', FailureCategory.PROVIDER),
            ('sandbox path does not exist', FailureCategory.INFRASTRUCTURE),
            ('JSON parse error in schema', FailureCategory.COORDINATION),
            ('security vulnerability detected', FailureCategory.SECURITY),
            ('tool max turns exceeded', FailureCategory.TOOL),
        ]:
            m = _make_mission(
                mission_id=str(uuid.uuid4()),
                status=MissionStatus.FAILED,
            )
            db.add(m)
            t = _make_task(m.id, status=TaskStatus.FAILED)
            db.add(t)
            r = _make_agent_run(t.id, status=AgentRunStatus.FAILED, error=err_str)
            db.add(r)
            await db.commit()

            loop = LearningLoop(db)
            record = await loop.record_process_outcome(m, task_category='test')
            assert record.failure_category == expected_cat, \
                f"Error '{err_str}' should classify as {expected_cat}, got {record.failure_category}"


# =========================================================================
# 17. FULL REGRESSION COMPATIBILITY (confidence math)
# =========================================================================

def test_confidence_math_safety():
    """Confidence function satisfies all safety invariants."""
    # Never exceeds maximum
    assert _compute_confidence(1000, 1.0) <= 0.95
    # Zero samples → zero confidence
    assert _compute_confidence(0, 1.0) == 0.0
    # Low success rate depresses confidence
    assert _compute_confidence(5, 0.2) < _compute_confidence(5, 0.9)
    # More samples → higher confidence (at same success rate)
    assert _compute_confidence(3, 0.9) < _compute_confidence(10, 0.9)
