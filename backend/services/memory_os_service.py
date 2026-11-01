"""
Biologically Inspired Memory OS Engine Service.
Provides long-term memory operations across Episodic, Semantic, Procedural,
and Cryptographic Mutation Ledger tiers.
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Set, Tuple, Union

import numpy as np
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.memory_os import (
    GENESIS_HASH,
    EpisodicMemoryRecord,
    LedgerEntry,
    MemoryLedgerEntry,
    ProceduralPlaybook,
    SemanticConstraintRecord,
    SemanticConstraintStatus,
    _normalize_utc,
    compute_sha256_hex,
)
from backend.services.embedding import EmbeddingUtils, tokenize_code_identifiers

class _ReentrantChainLock:
    """Task-reentrant async mutex scoped to an event loop so top-level write methods
    can serialize the full mutation + ledger append + commit unit of work without
    deadlocking nested `record_mutation` / `propagate_beliefs` calls."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner: Optional[asyncio.Task[Any]] = None
        self._depth: int = 0

    async def __aenter__(self) -> "_ReentrantChainLock":
        task = asyncio.current_task()
        if task is not None and self._owner is task:
            self._depth += 1
            return self
        await self._lock.acquire()
        self._owner = task
        self._depth = 1
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._depth -= 1
        if self._depth <= 0:
            self._owner = None
            self._depth = 0
            self._lock.release()


# In-memory re-entrant locks keyed by (event_loop_id, tenant_key)
_chain_locks: Dict[Tuple[int, str], _ReentrantChainLock] = {}
_master_lock = asyncio.Lock()

_LIVE_PLAYBOOK_MUTATIONS: Set[str] = {"PLAYBOOK_CREATED", "PLAYBOOK_UPDATED", "PLAYBOOK_EXECUTED"}
_LIVE_EPISODIC_MUTATIONS: Set[str] = {"EPISODIC_CREATED", "EPISODIC_RECALLED"}
_LIVE_SEMANTIC_MUTATIONS: Set[str] = {
    "SEMANTIC_CREATED",
    "SEMANTIC_SUPERSEDED",
    "SEMANTIC_RESOLVED_WINNER",
    "SEMANTIC_RESOLVED_SUPERSEDED",
    "SEMANTIC_ATTENUATED",
}

VECTOR_SIMILARITY_THRESHOLD = 0.82

ANTONYM_PAIRS = {
    ("true", "false"),
    ("enable", "disable"),
    ("enabled", "disabled"),
    ("allow", "deny"),
    ("allowed", "denied"),
    ("allow", "block"),
    ("permit", "forbid"),
    ("permitted", "forbidden"),
    ("async", "sync"),
    ("asynchronous", "synchronous"),
    ("public", "private"),
    ("internal", "external"),
    ("snake_case", "camelcase"),
    ("camelcase", "kebab-case"),
    ("sqlite", "postgres"),
    ("sqlite", "postgresql"),
    ("mysql", "postgresql"),
    ("http", "https"),
    ("jwt", "session"),
    ("monolith", "microservice"),
    ("mutable", "immutable"),
    ("strict", "loose"),
    ("whitelist", "blacklist"),
    ("always", "never"),
    ("required", "optional"),
}

NEGATION_TERMS = {
    "not", "never", "no", "cannot", "prohibited", "forbidden", "disallow", "disallowed"
}
AFFIRMATION_TERMS = {
    "must", "always", "require", "required", "mandatory", "shall", "allow", "allowed"
}
EXCLUSIVITY_TERMS = {
    "exclusively", "only", "solely", "strictly"
}


def _get_chain_lock(tenant_key: str) -> _ReentrantChainLock:
    try:
        loop_id = id(asyncio.get_running_loop())
    except RuntimeError:
        loop_id = 0
    composite = (loop_id, tenant_key)
    if composite not in _chain_locks:
        _chain_locks[composite] = _ReentrantChainLock()
    return _chain_locks[composite]


def compute_retention(
    elapsed_seconds: Optional[Union[float, int]] = None,
    stability: float = 86400.0,
    last_recalled_at: Optional[datetime] = None,
    current_time: Optional[datetime] = None,
    **kwargs: Any,
) -> float:
    """
    Computes Ebbinghaus retention probability R(t) = exp(-t / S).
    Flexible interface supporting (elapsed_seconds, stability) and (stability, last_recalled_at, current_time).
    """
    if elapsed_seconds is not None:
        t = max(0.0, float(elapsed_seconds))
    elif last_recalled_at is not None:
        now = _normalize_utc(current_time)
        recalled = _normalize_utc(last_recalled_at)
        t = max(0.0, (now - recalled).total_seconds())
    else:
        t = 0.0
    s = max(float(stability or 86400.0), 1e-6)
    return float(math.exp(-t / s))


ebbinghaus_retention = compute_retention
calculate_retention = compute_retention


def compute_playbook_reliability(success_count: int = 0, failure_count: int = 0) -> float:
    """Laplace-smoothed empirical success probability: (success + 1) / (total + 2)."""
    s = int(success_count or 0)
    f = int(failure_count or 0)
    return float((s + 1.0) / (s + f + 2.0))


def compute_cosine_similarity(vec1: Any, vec2: Any) -> float:
    """Compute cosine similarity between two vectors."""
    if vec1 is None or vec2 is None:
        return 0.0
    try:
        u = np.array(vec1, dtype=np.float64)
        v = np.array(vec2, dtype=np.float64)
        if u.size == 0 or v.size == 0 or u.shape != v.shape:
            return 0.0
        norm_u = np.linalg.norm(u)
        norm_v = np.linalg.norm(v)
        if norm_u <= 1e-9 or norm_v <= 1e-9:
            return 0.0
        dot = float(np.dot(u, v) / (norm_u * norm_v))
        return max(0.0, min(1.0, dot))
    except Exception:
        return 0.0


def detect_offline_contradiction(
    statement_a: str,
    statement_b: str,
    subject: str = "",
    key: str = "",
) -> Tuple[bool, Optional[str]]:
    """
    Offline deterministic negation, antonym, version, and exclusivity contradiction detection.
    Completely hermetic with zero network or external dependencies.
    """
    toks_a = set(re.findall(r"\b[a-zA-Z0-9_\-\.]+\b", statement_a.lower()))
    toks_b = set(re.findall(r"\b[a-zA-Z0-9_\-\.]+\b", statement_b.lower()))

    # 1. Antonym collisions
    for word_1, word_2 in ANTONYM_PAIRS:
        if (word_1 in toks_a and word_2 in toks_b) or (word_2 in toks_a and word_1 in toks_b):
            common_context = (toks_a - {word_1, word_2}) & (toks_b - {word_1, word_2})
            if key or (subject and len(common_context) >= 1) or len(common_context) >= 1 or len(toks_a & toks_b) >= 1:
                return True, f"Deterministic antonym conflict between '{word_1}' and '{word_2}'."

    # Extract explicit attribute assignments early for independent key verification
    attr_pattern = re.compile(
        r"(\b[a-zA-Z_][a-zA-Z0-9_]*\b)\s*(?:=|is|should be|must be|:|require|requires|enforce|enforces|support|supports|use|uses|target|targets)\s*([a-zA-Z0-9_\-\.]+)"
    )
    matches_a = dict(attr_pattern.findall(statement_a.lower()))
    matches_b = dict(attr_pattern.findall(statement_b.lower()))

    # 2. Direct attribute assignment collision when targeting same key
    if key and (statement_a.strip().lower() != statement_b.strip().lower()):
        # Guard: If statements explicitly define distinct attributes (e.g. 'database is X' vs 'broker is Y'),
        # they are independent attributes and not a direct value conflict on the same key.
        is_disjoint_attrs = bool(matches_a and matches_b and set(matches_a.keys()).isdisjoint(set(matches_b.keys())))
        if not is_disjoint_attrs and len(toks_a) <= 4 and len(toks_b) <= 4:
            return True, f"Deterministic value conflict on key '{key}': '{statement_a}' vs '{statement_b}'."

    # 3. Negation vs affirmation polarity inversion
    has_neg_a = bool(toks_a & NEGATION_TERMS)
    has_neg_b = bool(toks_b & NEGATION_TERMS)
    if has_neg_a != has_neg_b:
        shared = (toks_a - NEGATION_TERMS - AFFIRMATION_TERMS) & (toks_b - NEGATION_TERMS - AFFIRMATION_TERMS)
        # Must have shared core tokens: never trigger when shared count is 0
        if len(shared) >= 2 or (key and len(shared) >= 1):
            return True, "Deterministic polarity inversion (negation vs affirmation) on shared statement core."

    # 4. Attribute assignment collisions across invariant verbs
    for k, v_a in matches_a.items():
        if k in matches_b and matches_b[k] != v_a:
            return True, f"Deterministic attribute value conflict for '{k}': '{v_a}' vs '{matches_b[k]}'."

    # 5. Version number / numeric spec collisions on common topic
    versions_a = set(re.findall(r"\b\d+\.\d+(?:\.\d+)?\b", statement_a))
    versions_b = set(re.findall(r"\b\d+\.\d+(?:\.\d+)?\b", statement_b))
    if versions_a and versions_b and versions_a.isdisjoint(versions_b):
        shared_tokens = (toks_a - versions_a) & (toks_b - versions_b) - {"minimum", "maximum", "exclusively", "only", "support", "enforce"}
        if len(shared_tokens) >= 1 or key:
            return True, f"Deterministic version/target conflict: {versions_a} vs {versions_b}."

    # 6. Exclusivity terms ('exclusively', 'only', 'solely') asserting different specifications
    has_excl_a = bool(toks_a & EXCLUSIVITY_TERMS)
    has_excl_b = bool(toks_b & EXCLUSIVITY_TERMS)
    if has_excl_a or has_excl_b:
        differing = (toks_a ^ toks_b) - EXCLUSIVITY_TERMS - {"minimum", "maximum", "support", "enforce", "use", "is", "are", "the", "a", "an"}
        shared_core = (toks_a & toks_b) - EXCLUSIVITY_TERMS - {"minimum", "maximum", "support", "enforce", "use", "is", "are", "the", "a", "an"}
        if len(differing) >= 1:
            if key and (len(shared_core) >= 1 or len(toks_a & toks_b) >= 1):
                return True, "Deterministic exclusivity conflict on shared invariant."
            elif not key and len(shared_core) >= 2:
                return True, "Deterministic exclusivity conflict on shared invariant."

    return False, None


class ConflictReport:
    """Detailed report for semantic contradiction detection and resolution."""
    def __init__(
        self,
        has_conflict: bool,
        conflicting_record_id: Optional[str] = None,
        conflict_type: str = "NONE",
        similarity_score: float = 0.0,
        reason: Optional[str] = None,
        action_taken: str = "STORED_ACTIVE",
    ):
        self.has_conflict = has_conflict
        self.conflicting_record_id = conflicting_record_id
        self.conflict_type = conflict_type
        self.similarity_score = float(similarity_score)
        self.reason = reason
        self.action_taken = action_taken

    def to_dict(self) -> Dict[str, Any]:
        return {
            "has_conflict": self.has_conflict,
            "conflicting_record_id": self.conflicting_record_id,
            "conflict_type": self.conflict_type,
            "similarity_score": round(self.similarity_score, 4),
            "reason": self.reason,
            "action_taken": self.action_taken,
        }


class BeliefPropagationReport:
    """Report detailing multi-hop belief attenuation propagation across dependencies."""
    def __init__(
        self,
        root_id: str,
        affected_count: int,
        updated_confidences: Dict[str, float],
        iterations: int,
        converged: bool,
    ):
        self.root_id = root_id
        self.affected_count = affected_count
        self.updated_confidences = updated_confidences
        self.iterations = iterations
        self.converged = converged

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root_id": self.root_id,
            "affected_count": self.affected_count,
            "updated_confidences": {k: round(v, 6) for k, v in self.updated_confidences.items()},
            "iterations": self.iterations,
            "converged": self.converged,
        }


class MemoryOSService:
    """
    Central service for the Biologically Inspired Memory OS.
    Manages Episodic, Semantic, Procedural, and Cryptographic Ledger operations.
    """

    compute_retention = staticmethod(compute_retention)
    ebbinghaus_retention = staticmethod(compute_retention)
    calculate_retention = staticmethod(compute_retention)
    compute_playbook_reliability = staticmethod(compute_playbook_reliability)

    # -------------------------------------------------------------------------
    # 1. Episodic Memory Operations (Ebbinghaus Decay & Reinforcement)
    # -------------------------------------------------------------------------

    @classmethod
    async def store_episodic(
        cls,
        db: AsyncSession,
        org_id: str,
        project_id: str,
        content: str,
        stability: float = 86400.0,
        tags: Optional[List[str]] = None,
    ) -> EpisodicMemoryRecord:
        """
        Stores an episodic memory record, computes embedding, and signs the mutation into ledger.
        """
        if stability <= 0:
            raise ValueError("Stability S must be strictly positive (> 0)")

        embedding_vec = EmbeddingUtils.generate_embedding(content)
        async with _get_chain_lock(org_id):
            now = datetime.now(timezone.utc)
            record = EpisodicMemoryRecord(
                id=str(uuid.uuid4()),
                organization_id=org_id,
                project_id=project_id,
                content=content,
                stability=float(stability),
                last_recalled_at=now,
                recall_count=0,
                tags=tags or [],
                embedding=embedding_vec,
            )
            db.add(record)
            await db.flush()

            await cls.record_mutation(
                db=db,
                org_id=org_id,
                project_id=project_id,
                mutation_type="EPISODIC_CREATED",
                record_id=record.id,
                payload_dict=record.canonical_payload(),
            )

            await db.commit()
            await db.refresh(record)
            db.expunge(record)
            return record

    @classmethod
    async def recall_episodic(
        cls,
        db: AsyncSession,
        org_id: str,
        project_id: str,
        memory_id: str,
        alpha: float = 0.5,
    ) -> EpisodicMemoryRecord:
        """
        Reinforces an episodic memory:
        Monotonically increases stability S_after = S_before * (1 + alpha),
        resets elapsed decay clock (last_recalled_at = now),
        increments recall_count, and signs ledger.
        """
        if alpha <= 0:
            raise ValueError("Reinforcement factor alpha must be strictly positive (> 0)")

        async with _get_chain_lock(org_id):
            stmt = select(EpisodicMemoryRecord).where(
                and_(
                    EpisodicMemoryRecord.id == memory_id,
                    EpisodicMemoryRecord.organization_id == org_id,
                    EpisodicMemoryRecord.project_id == project_id,
                )
            )
            res = await db.execute(stmt)
            record = res.scalars().first()
            if not record:
                raise ValueError(f"Episodic memory record '{memory_id}' not found for tenant")

            # Stability growth S_after > S_before
            record.stability = float(record.stability) * (1.0 + alpha)
            # Reset decay clock
            record.last_recalled_at = datetime.now(timezone.utc)
            record.recall_count += 1
            record.updated_at = datetime.now(timezone.utc)

            await db.flush()

            await cls.record_mutation(
                db=db,
                org_id=org_id,
                project_id=project_id,
                mutation_type="EPISODIC_RECALLED",
                record_id=record.id,
                payload_dict=record.canonical_payload(),
            )

            await db.commit()
            await db.refresh(record)
            return record

    @classmethod
    async def query_episodic(
        cls,
        db: AsyncSession,
        org_id: str,
        project_id: str,
        query_text: Optional[str] = None,
        min_retention: float = 0.10,
        limit: int = 10,
        current_time: Optional[datetime] = None,
    ) -> List[Tuple[EpisodicMemoryRecord, float]]:
        """
        Retrieves episodic memories:
        - Filters out decayed memories with R(t) < min_retention.
        - Boosts ranking by live retention R(t).
        Returns list of (record, live_retention) sorted by score descending.
        """
        stmt = select(EpisodicMemoryRecord).where(
            and_(
                EpisodicMemoryRecord.organization_id == org_id,
                EpisodicMemoryRecord.project_id == project_id,
            )
        )
        res = await db.execute(stmt)
        candidates = list(res.scalars().all())
        if not candidates:
            return []

        has_query = bool(query_text and query_text.strip())
        q_vec = None
        q_tokens: Set[str] = set()
        if has_query:
            q_vec = EmbeddingUtils.generate_embedding(query_text)
            q_tokens = set(tokenize_code_identifiers(query_text.lower()))

        scored: List[Tuple[EpisodicMemoryRecord, float, float]] = []

        for rec in candidates:
            r_t = rec.compute_retention(current_time=current_time)
            # Filter decayed memories below threshold
            if r_t < min_retention:
                continue

            if not has_query:
                score = r_t
            else:
                m_vec = rec.get_embedding_vector()
                sim = compute_cosine_similarity(q_vec, m_vec)
                m_tokens = set(tokenize_code_identifiers(rec.content.lower()))
                lex = len(q_tokens & m_tokens) / float(len(q_tokens) or 1)
                base_sim = 0.7 * sim + 0.3 * lex
                # Boost score with live retention
                score = base_sim * (0.5 + 0.5 * r_t)

            scored.append((rec, r_t, score))

        scored.sort(key=lambda item: item[2], reverse=True)
        return [(rec, r_t) for rec, r_t, _ in scored[:limit]]

    @classmethod
    async def prune_decayed_episodic(
        cls,
        db: AsyncSession,
        org_id: str,
        project_id: str,
        min_retention: float = 0.05,
        current_time: Optional[datetime] = None,
    ) -> int:
        """Prunes episodic memories that have decayed below a minimum retention threshold."""
        async with _get_chain_lock(org_id):
            stmt = select(EpisodicMemoryRecord).where(
                and_(
                    EpisodicMemoryRecord.organization_id == org_id,
                    EpisodicMemoryRecord.project_id == project_id,
                )
            )
            res = await db.execute(stmt)
            records = list(res.scalars().all())
            pruned_count = 0
            for rec in records:
                if rec.compute_retention(current_time=current_time) < min_retention:
                    await cls.record_mutation(
                        db=db,
                        org_id=org_id,
                        project_id=project_id,
                        mutation_type="EPISODIC_PRUNED",
                        record_id=rec.id,
                        payload_dict=rec.canonical_payload(),
                    )
                    await db.delete(rec)
                    pruned_count += 1
            if pruned_count > 0:
                await db.commit()
            return pruned_count

    # -------------------------------------------------------------------------
    # 2. Semantic Memory Operations (Contradiction & Belief Propagation)
    # -------------------------------------------------------------------------

    @classmethod
    async def store_semantic(
        cls,
        db: AsyncSession,
        org_id: str,
        project_id: str,
        subject: str,
        key: str,
        statement: str,
        confidence: float = 1.0,
        base_confidence: Optional[float] = None,
        depends_on_ids: Optional[List[str]] = None,
        gemini_provider: Any = None,
    ) -> Tuple[SemanticConstraintRecord, Optional[ConflictReport]]:
        """
        Stores a semantic memory record with hybrid contradiction detection:
        Phase 1: Fast deterministic symbolic / 384D CountSketch vector collision (>= 0.82).
        Phase 2: Offline deterministic antonym/negation rules + optional native Gemini.
        Zero Anthropic Claude calls under all circumstances.
        Handles status transitions and initiates belief propagation upon supersession.
        """
        eff_confidence = max(0.0, min(1.0, float(confidence)))
        base_conf = eff_confidence if base_confidence is None else max(0.0, min(1.0, float(base_confidence)))
        deps = list(depends_on_ids or [])

        if gemini_provider is not None:
            prov_ident = f"{type(gemini_provider).__module__}.{type(gemini_provider).__name__}".lower()
            if "anthropic" in prov_ident or "claude" in prov_ident:
                raise ValueError(
                    "Anthropic/Claude providers are strictly prohibited in Memory OS; "
                    "only native Google GeminiProvider or offline deterministic verification is allowed."
                )
        else:
            try:
                import os as _os
                from backend.core.config import settings as _settings

                env_llm = _os.environ.get("LLM_PROVIDER", "").strip().lower()
                cfg_llm = (getattr(_settings, "LLM_PROVIDER", "") or "").strip().lower()
                if env_llm != "mock" and cfg_llm != "mock" and getattr(_settings, "GEMINI_API_KEY", None):
                    from backend.services.llm.gemini_provider import GeminiProvider

                    cand_provider = GeminiProvider()
                    if getattr(cand_provider, "configured", False):
                        gemini_provider = cand_provider
            except Exception:
                gemini_provider = None

        # Generate 384D embedding
        query_vector = EmbeddingUtils.generate_embedding(f"{subject} {key}: {statement}")

        async with _get_chain_lock(org_id):
            # Phase 1: Fast collision candidate filter
            stmt = select(SemanticConstraintRecord).where(
                and_(
                    SemanticConstraintRecord.organization_id == org_id,
                    SemanticConstraintRecord.project_id == project_id,
                    SemanticConstraintRecord.status == SemanticConstraintStatus.ACTIVE,
                )
            )
            res = await db.execute(stmt)
            active_records = list(res.scalars().all())

            norm_sub = subject.strip().lower()
            norm_key = key.strip().lower()

            conflict_report: Optional[ConflictReport] = None
            target_conflicting_record: Optional[SemanticConstraintRecord] = None
            conflict_reason: Optional[str] = None
            conflict_type = "NONE"
            similarity_score = 0.0

            for candidate in active_records:
                cand_sub = (candidate.subject or "").strip().lower()
                cand_key = (candidate.key or "").strip().lower()
                symbolic_match = (cand_sub == norm_sub) or (cand_key == norm_key)
                vec_sim = compute_cosine_similarity(query_vector, candidate.embedding or [])

                is_candidate = False
                cand_type = "NONE"
                if symbolic_match and vec_sim >= VECTOR_SIMILARITY_THRESHOLD:
                    is_candidate = True
                    cand_type = "SYMBOLIC_AND_VECTOR_COLLISION"
                elif symbolic_match:
                    is_candidate = True
                    cand_type = "SYMBOLIC_COLLISION"
                elif vec_sim >= VECTOR_SIMILARITY_THRESHOLD:
                    is_candidate = True
                    cand_type = "VECTOR_COLLISION"

                if is_candidate:
                    is_same_key = bool(norm_key and cand_key == norm_key)
                    is_same_sub = bool(norm_sub and cand_sub == norm_sub)

                    # Phase 2: Offline deterministic check
                    is_contra, reason = detect_offline_contradiction(
                        candidate.statement,
                        statement,
                        subject=subject if is_same_sub else "",
                        key=key if is_same_key else "",
                    )

                    # Optional native Gemini check if offline was inconclusive
                    if not is_contra and gemini_provider and getattr(gemini_provider, "configured", False):
                        try:
                            schema = {
                                "type": "object",
                                "properties": {
                                    "is_contradiction": {"type": "boolean"},
                                    "conflict_reason": {"type": "string"},
                                },
                                "required": ["is_contradiction", "conflict_reason"],
                            }
                            sys_p = "You are an automated semantic contradiction judge. Determine if Constraint A and Constraint B are contradictory or mutually exclusive."
                            usr_p = f"Subject: {subject}\nKey: {key}\nConstraint A (existing): {candidate.statement}\nConstraint B (new): {statement}"
                            resp = await gemini_provider.generate_structured_output(
                                system_prompt=sys_p,
                                user_prompt=usr_p,
                                schema=schema,
                            )
                            if resp.get("is_contradiction"):
                                is_contra = True
                                reason = resp.get("conflict_reason", "Gemini verified semantic contradiction.")
                        except Exception:
                            pass

                    if is_contra:
                        target_conflicting_record = candidate
                        conflict_reason = reason
                        conflict_type = cand_type
                        similarity_score = vec_sim
                        break

            new_record_id = str(uuid.uuid4())

            if target_conflicting_record is not None:
                # Contradiction confirmed!
                if eff_confidence >= target_conflicting_record.confidence:
                    action_taken = "SUPERSEDED_EXISTING"
                    new_status = SemanticConstraintStatus.ACTIVE
                    superseded_by_id = None
                    conflicted_with_id = target_conflicting_record.id
                    stored_reason = f"Supersedes prior constraint {target_conflicting_record.id}: {conflict_reason}"

                    target_conflicting_record.status = SemanticConstraintStatus.SUPERSEDED
                    target_conflicting_record.superseded_by_id = new_record_id
                    target_conflicting_record.conflict_reason = f"Superseded by {new_record_id}: {conflict_reason}"
                    target_conflicting_record.updated_at = datetime.now(timezone.utc)
                else:
                    action_taken = "STORED_AS_CONFLICTING"
                    new_status = SemanticConstraintStatus.CONFLICTING
                    superseded_by_id = None
                    conflicted_with_id = target_conflicting_record.id
                    stored_reason = f"Conflicts with active constraint {target_conflicting_record.id}: {conflict_reason}"

                conflict_report = ConflictReport(
                    has_conflict=True,
                    conflicting_record_id=target_conflicting_record.id,
                    conflict_type=conflict_type,
                    similarity_score=similarity_score,
                    reason=conflict_reason,
                    action_taken=action_taken,
                )
            else:
                new_status = SemanticConstraintStatus.ACTIVE
                superseded_by_id = None
                conflicted_with_id = None
                stored_reason = None

            new_record = SemanticConstraintRecord(
                id=new_record_id,
                organization_id=org_id,
                project_id=project_id,
                key=key,
                subject=subject,
                statement=statement,
                confidence=eff_confidence,
                base_confidence=base_conf,
                status=new_status,
                superseded_by_id=superseded_by_id,
                conflicted_with_id=conflicted_with_id,
                conflict_reason=stored_reason,
                depends_on_ids=deps,
                embedding=query_vector,
            )
            db.add(new_record)
            await db.flush()

            # Sign new record into ledger
            await cls.record_mutation(
                db=db,
                org_id=org_id,
                project_id=project_id,
                mutation_type="SEMANTIC_CREATED",
                record_id=new_record.id,
                payload_dict=new_record.canonical_payload(),
            )

            # If existing record was superseded, sign its update and propagate beliefs
            if target_conflicting_record is not None and target_conflicting_record.status == SemanticConstraintStatus.SUPERSEDED:
                await cls.record_mutation(
                    db=db,
                    org_id=org_id,
                    project_id=project_id,
                    mutation_type="SEMANTIC_SUPERSEDED",
                    record_id=target_conflicting_record.id,
                    payload_dict=target_conflicting_record.canonical_payload(),
                )
                await cls.propagate_beliefs(
                    db=db,
                    org_id=org_id,
                    project_id=project_id,
                    root_id=target_conflicting_record.id,
                )

            await db.commit()
            await db.refresh(new_record)
            return new_record, conflict_report

    @classmethod
    async def query_semantic(
        cls,
        db: AsyncSession,
        org_id: str,
        project_id: str,
        status: Optional[Union[SemanticConstraintStatus, str]] = "ACTIVE",
        subject: Optional[str] = None,
        key: Optional[str] = None,
        limit: int = 20,
    ) -> List[SemanticConstraintRecord]:
        """Queries semantic constraints with optional status, subject, and key filtering."""
        conditions = [
            SemanticConstraintRecord.organization_id == org_id,
            SemanticConstraintRecord.project_id == project_id,
        ]
        if status is not None:
            norm_status = status.value if isinstance(status, SemanticConstraintStatus) else str(status)
            conditions.append(SemanticConstraintRecord.status == norm_status)
        if subject:
            conditions.append(SemanticConstraintRecord.subject.ilike(f"%{subject.strip()}%"))
        if key:
            conditions.append(SemanticConstraintRecord.key == key.strip())

        stmt = (
            select(SemanticConstraintRecord)
            .where(and_(*conditions))
            .order_by(SemanticConstraintRecord.confidence.desc(), SemanticConstraintRecord.updated_at.desc())
            .limit(limit)
        )
        res = await db.execute(stmt)
        return list(res.scalars().all())

    @classmethod
    async def resolve_conflict(
        cls,
        db: AsyncSession,
        org_id: str,
        project_id: str,
        winning_id: str,
        superseded_id: str,
        reason: str,
    ) -> Dict[str, Any]:
        """Manually resolves a conflict between two semantic constraints."""
        async with _get_chain_lock(org_id):
            res_win = await db.execute(
                select(SemanticConstraintRecord).where(
                    and_(
                        SemanticConstraintRecord.id == winning_id,
                        SemanticConstraintRecord.organization_id == org_id,
                        SemanticConstraintRecord.project_id == project_id,
                    )
                )
            )
            winning_rec = res_win.scalars().first()
            if not winning_rec:
                raise ValueError(f"Winning record '{winning_id}' not found in project")

            res_sup = await db.execute(
                select(SemanticConstraintRecord).where(
                    and_(
                        SemanticConstraintRecord.id == superseded_id,
                        SemanticConstraintRecord.organization_id == org_id,
                        SemanticConstraintRecord.project_id == project_id,
                    )
                )
            )
            superseded_rec = res_sup.scalars().first()
            if not superseded_rec:
                raise ValueError(f"Superseded record '{superseded_id}' not found in project")

            winning_rec.status = SemanticConstraintStatus.ACTIVE
            winning_rec.conflicted_with_id = superseded_id
            winning_rec.conflict_reason = f"Manually resolved: superseded {superseded_id}. {reason}"
            winning_rec.updated_at = datetime.now(timezone.utc)

            superseded_rec.status = SemanticConstraintStatus.SUPERSEDED
            superseded_rec.superseded_by_id = winning_id
            superseded_rec.conflict_reason = f"Manually resolved: superseded by {winning_id}. {reason}"
            superseded_rec.updated_at = datetime.now(timezone.utc)

            await db.flush()

            await cls.record_mutation(
                db=db,
                org_id=org_id,
                project_id=project_id,
                mutation_type="SEMANTIC_RESOLVED_WINNER",
                record_id=winning_rec.id,
                payload_dict=winning_rec.canonical_payload(),
            )
            await cls.record_mutation(
                db=db,
                org_id=org_id,
                project_id=project_id,
                mutation_type="SEMANTIC_RESOLVED_SUPERSEDED",
                record_id=superseded_rec.id,
                payload_dict=superseded_rec.canonical_payload(),
            )

            # Propagate belief attenuation
            prop_report = await cls.propagate_beliefs(
                db=db,
                org_id=org_id,
                project_id=project_id,
                root_id=superseded_id,
            )

            await db.commit()
            return {
                "winning_id": winning_id,
                "superseded_id": superseded_id,
                "reason": reason,
                "belief_propagation": prop_report.to_dict(),
            }

    @classmethod
    async def propagate_beliefs(
        cls,
        db: AsyncSession,
        org_id: str,
        project_id: str,
        root_id: str,
        max_iterations: int = 50,
        tolerance: float = 1e-6,
    ) -> BeliefPropagationReport:
        """
        Multi-hop belief propagation over directed dependency graphs with cycle prevention.
        Attenuation formula: C_eff(B) = C_base(B) * prod_{A in deps(B)} C_eff(A).
        Guarantees termination without infinite loops or recursion errors on cyclic graphs.
        """
        async with _get_chain_lock(org_id):
            stmt = select(SemanticConstraintRecord).where(
                and_(
                    SemanticConstraintRecord.organization_id == org_id,
                    SemanticConstraintRecord.project_id == project_id,
                )
            )
            res = await db.execute(stmt)
            records = list(res.scalars().all())
            record_map: Dict[str, SemanticConstraintRecord] = {r.id: r for r in records}

            if root_id not in record_map:
                return BeliefPropagationReport(root_id, 0, {}, 0, True)

            # Build dependency and dependent adjacency maps
            # Edge A -> B: B depends on A (A in B.depends_on_ids)
            dependents_map: Dict[str, List[str]] = {}
            dependencies_map: Dict[str, List[str]] = {}

            for r in records:
                raw_deps = r.depends_on_ids or []
                if isinstance(raw_deps, str):
                    try:
                        raw_deps = json.loads(raw_deps)
                    except Exception:
                        raw_deps = [raw_deps]
                valid_deps = [str(dep) for dep in raw_deps if str(dep) in record_map]
                dependencies_map[r.id] = valid_deps
                for dep in valid_deps:
                    dependents_map.setdefault(dep, []).append(r.id)

            # BFS to discover all reachable downstream dependents
            affected: Set[str] = set()
            queue = [root_id]
            visited_bfs = {root_id}

            while queue:
                curr = queue.pop(0)
                for nxt in dependents_map.get(curr, []):
                    affected.add(nxt)
                    if nxt not in visited_bfs:
                        visited_bfs.add(nxt)
                        queue.append(nxt)

            if not affected:
                return BeliefPropagationReport(root_id, 0, {}, 0, True)

            # Initialize current effective confidences
            # Non-active records have C_eff = 0.0
            c_eff: Dict[str, float] = {}
            for r_id, r in record_map.items():
                if r.status != SemanticConstraintStatus.ACTIVE:
                    c_eff[r_id] = 0.0
                else:
                    c_eff[r_id] = float(r.confidence)

            # Bounded Iterative Relaxation (Gauss-Seidel contraction mapping)
            iterations = 0
            converged = False
            bound = min(max_iterations, len(affected) + 2)

            for _ in range(bound):
                iterations += 1
                max_delta = 0.0

                for node_id in affected:
                    node = record_map[node_id]
                    if node.status != SemanticConstraintStatus.ACTIVE:
                        new_conf = 0.0
                    else:
                        deps = dependencies_map.get(node_id, [])
                        if not deps:
                            new_conf = float(node.base_confidence)
                        else:
                            product_attenuation = 1.0
                            for dep_id in deps:
                                product_attenuation *= c_eff.get(dep_id, 0.0)
                            new_conf = float(node.base_confidence) * product_attenuation

                    new_conf = max(0.0, min(1.0, float(new_conf)))
                    delta = abs(new_conf - c_eff[node_id])
                    if delta > max_delta:
                        max_delta = delta
                    c_eff[node_id] = new_conf

                if max_delta < tolerance:
                    converged = True
                    break

            # Persist updated effective confidences and sign ledger mutations
            updated_confidences: Dict[str, float] = {}
            for node_id in sorted(affected):
                node = record_map[node_id]
                new_val = round(c_eff[node_id], 6)
                if abs(float(node.confidence) - new_val) > tolerance:
                    node.confidence = new_val
                    node.updated_at = datetime.now(timezone.utc)
                    updated_confidences[node_id] = new_val

                    await cls.record_mutation(
                        db=db,
                        org_id=org_id,
                        project_id=project_id,
                        mutation_type="SEMANTIC_ATTENUATED",
                        record_id=node.id,
                        payload_dict=node.canonical_payload(),
                    )

            await db.flush()
            return BeliefPropagationReport(
                root_id=root_id,
                affected_count=len(updated_confidences),
                updated_confidences=updated_confidences,
                iterations=iterations,
                converged=converged,
            )

    # -------------------------------------------------------------------------
    # 3. Procedural Memory Operations (Playbooks & Laplace Telemetry)
    # -------------------------------------------------------------------------

    @classmethod
    async def store_playbook(
        cls,
        db: AsyncSession,
        org_id: str,
        project_id: Optional[str],
        name: str,
        description: Optional[str],
        steps: List[Dict[str, Any]],
        trigger_tags: Optional[List[str]] = None,
        preconditions: Optional[List[str]] = None,
        parent_playbook_id: Optional[str] = None,
    ) -> ProceduralPlaybook:
        """
        Stores a procedural playbook, manages tenant-scoped versioning, and signs into the cryptographic ledger.
        """
        async with _get_chain_lock(org_id):
            version = 1
            resolved_parent_id: Optional[str] = None

            if parent_playbook_id:
                parent_conds = [
                    ProceduralPlaybook.id == parent_playbook_id,
                    ProceduralPlaybook.organization_id == org_id,
                ]
                if project_id is not None:
                    parent_conds.append(
                        or_(
                            ProceduralPlaybook.project_id == project_id,
                            ProceduralPlaybook.project_id.is_(None),
                        )
                    )
                else:
                    parent_conds.append(ProceduralPlaybook.project_id.is_(None))

                parent_res = await db.execute(select(ProceduralPlaybook).where(and_(*parent_conds)))
                parent = parent_res.scalars().first()
                if not parent:
                    raise ValueError(
                        f"Parent playbook '{parent_playbook_id}' not found in organization '{org_id}' scope"
                    )
                version = int(parent.version or 1) + 1
                resolved_parent_id = parent.id
            else:
                same_name_conds = [
                    ProceduralPlaybook.organization_id == org_id,
                    ProceduralPlaybook.name == name,
                ]
                if project_id is not None:
                    same_name_conds.append(ProceduralPlaybook.project_id == project_id)
                else:
                    same_name_conds.append(ProceduralPlaybook.project_id.is_(None))

                prev_res = await db.execute(
                    select(ProceduralPlaybook)
                    .where(and_(*same_name_conds))
                    .order_by(ProceduralPlaybook.version.desc())
                    .limit(1)
                )
                prev_pb = prev_res.scalars().first()
                if prev_pb is not None:
                    version = int(prev_pb.version or 1) + 1
                    resolved_parent_id = prev_pb.id

            playbook = ProceduralPlaybook(
                id=str(uuid.uuid4()),
                organization_id=org_id,
                project_id=project_id,
                name=name,
                description=description,
                steps=steps or [],
                trigger_tags=trigger_tags or [],
                preconditions=preconditions or [],
                version=version,
                parent_playbook_id=resolved_parent_id,
                success_count=0,
                failure_count=0,
            )
            db.add(playbook)
            await db.flush()

            mutation_type = "PLAYBOOK_UPDATED" if resolved_parent_id else "PLAYBOOK_CREATED"
            await cls.record_mutation(
                db=db,
                org_id=org_id,
                project_id=project_id,
                mutation_type=mutation_type,
                record_id=playbook.id,
                payload_dict=playbook.canonical_payload(),
            )

            await db.commit()
            await db.refresh(playbook)
            return playbook

    @classmethod
    async def query_playbooks(
        cls,
        db: AsyncSession,
        org_id: str,
        project_id: Optional[str] = None,
        context_query: Optional[str] = None,
        tags: Optional[List[str]] = None,
        limit: int = 5,
    ) -> List[ProceduralPlaybook]:
        """
        Queries and ranks matching playbooks by:
        Score = TagRelevance * (0.4 + 0.6 * Reliability)
        """
        conditions = [ProceduralPlaybook.organization_id == org_id]
        if project_id is not None:
            conditions.append(
                or_(
                    ProceduralPlaybook.project_id == project_id,
                    ProceduralPlaybook.project_id.is_(None),
                )
            )

        stmt = select(ProceduralPlaybook).where(and_(*conditions))
        res = await db.execute(stmt)
        candidates = list(res.scalars().all())

        scored = [
            (pb, pb.compute_ranking_score(query_tags=tags, context_query=context_query))
            for pb in candidates
        ]
        # Filter zero-score if specific tags or query were provided
        if tags or context_query:
            scored = [item for item in scored if item[1] > 0.0]

        scored.sort(key=lambda x: x[1], reverse=True)
        return [item[0] for item in scored[:limit]]

    @classmethod
    async def record_playbook_execution(
        cls,
        db: AsyncSession,
        org_id: str,
        project_id: Optional[str],
        playbook_id: str,
        outcome: Literal["SUCCESS", "FAILURE"],
    ) -> ProceduralPlaybook:
        """
        Records execution outcome, updates Laplace telemetry, and records mutation in ledger.
        Enforces project scoping and signs onto the playbook's canonical ledger chain.
        """
        norm_outcome = str(outcome).upper()
        if norm_outcome not in ("SUCCESS", "FAILURE"):
            raise ValueError(f"Invalid outcome: {outcome}. Expected 'SUCCESS' or 'FAILURE'")

        async with _get_chain_lock(org_id):
            conditions = [
                ProceduralPlaybook.id == playbook_id,
                ProceduralPlaybook.organization_id == org_id,
            ]
            if project_id is not None:
                conditions.append(
                    or_(
                        ProceduralPlaybook.project_id == project_id,
                        ProceduralPlaybook.project_id.is_(None),
                    )
                )

            stmt = select(ProceduralPlaybook).where(and_(*conditions))
            res = await db.execute(stmt)
            playbook = res.scalars().first()
            if not playbook:
                raise ValueError(f"Playbook {playbook_id} not found in organization/project scope")

            if norm_outcome == "SUCCESS":
                playbook.success_count = int(playbook.success_count or 0) + 1
            else:
                playbook.failure_count = int(playbook.failure_count or 0) + 1

            playbook.updated_at = datetime.now(timezone.utc)
            await db.flush()

            await cls.record_mutation(
                db=db,
                org_id=org_id,
                project_id=playbook.project_id,
                mutation_type="PLAYBOOK_EXECUTED",
                record_id=playbook.id,
                payload_dict=playbook.canonical_payload(),
            )

            await db.commit()
            await db.refresh(playbook)
            return playbook

    # -------------------------------------------------------------------------
    # 4. Cryptographic Mutation Ledger & Tamper Detection
    # -------------------------------------------------------------------------

    @classmethod
    async def record_mutation(
        cls,
        db: AsyncSession,
        org_id: str,
        project_id: Optional[str],
        mutation_type: str,
        record_id: str,
        payload_dict: Dict[str, Any],
    ) -> MemoryLedgerEntry:
        """
        Appends a cryptographically chained MemoryLedgerEntry recording a mutation.
        Uses re-entrant tenant lock plus savepoint-backed optimistic retry on sequence collisions.
        """
        lock = _get_chain_lock(org_id)
        canonical_data_json = json.dumps(payload_dict, sort_keys=True, separators=(",", ":"))
        data_hash = compute_sha256_hex(canonical_data_json)

        async with lock:
            last_err: Optional[Exception] = None
            for _attempt in range(5):
                if project_id is not None:
                    stmt = (
                        select(MemoryLedgerEntry)
                        .where(
                            and_(
                                MemoryLedgerEntry.organization_id == org_id,
                                MemoryLedgerEntry.project_id == project_id,
                            )
                        )
                        .order_by(MemoryLedgerEntry.sequence_number.desc())
                        .limit(1)
                        .execution_options(populate_existing=True)
                    )
                else:
                    stmt = (
                        select(MemoryLedgerEntry)
                        .where(
                            and_(
                                MemoryLedgerEntry.organization_id == org_id,
                                MemoryLedgerEntry.project_id.is_(None),
                            )
                        )
                        .order_by(MemoryLedgerEntry.sequence_number.desc())
                        .limit(1)
                        .execution_options(populate_existing=True)
                    )

                res = await db.execute(stmt)
                tail = res.scalars().first()
                if tail is not None and tail.entry_hash:
                    next_seq = int(tail.sequence_number) + 1
                    prev_hash = str(tail.entry_hash)
                else:
                    next_seq = 1
                    prev_hash = GENESIS_HASH

                entry = MemoryLedgerEntry(
                    id=str(uuid.uuid4()),
                    organization_id=org_id,
                    project_id=project_id,
                    sequence_number=next_seq,
                    previous_hash=prev_hash,
                    mutation_type=mutation_type,
                    record_id=str(record_id),
                    data_hash=data_hash,
                    payload_json=canonical_data_json,
                    timestamp=datetime.now(timezone.utc),
                )
                entry.entry_hash = entry.compute_entry_hash()

                try:
                    async with db.begin_nested():
                        db.add(entry)
                        await db.flush()
                    return entry
                except IntegrityError as exc:
                    last_err = exc
                    continue

            raise last_err or RuntimeError("Failed to append ledger entry after retries")

    @classmethod
    async def verify_integrity(
        cls,
        db: AsyncSession,
        org_id: str,
        project_id: Optional[str] = None,
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Deterministic two-phase integrity verification:
        Phase 1: Traverses and verifies SHA-256 hash chain continuity.
        Phase 2: Compares current DB records against latest recorded data_hash values
                 and detects unauthorized out-of-band SQL row deletions.

        If project_id is specified, verifies that specific project's chain.
        If project_id is None, verifies all distinct project chains and org-global
        chains within the organization independently, returning False if any chain is tampered.

        Returns: (is_valid, corrupted_entry_or_record_id, error_message)
        """
        async def _verify_chain(proj_id: Optional[str]) -> Tuple[bool, Optional[str], Optional[str]]:
            conditions = [MemoryLedgerEntry.organization_id == org_id]
            if proj_id is not None:
                conditions.append(MemoryLedgerEntry.project_id == proj_id)
            else:
                conditions.append(MemoryLedgerEntry.project_id.is_(None))

            stmt = (
                select(MemoryLedgerEntry)
                .where(and_(*conditions))
                .order_by(MemoryLedgerEntry.sequence_number.asc())
                .execution_options(populate_existing=True)
            )
            res = await db.execute(stmt)
            entries = list(res.scalars().all())

            if not entries:
                return True, None, None

            expected_seq = 1
            expected_prev_hash = GENESIS_HASH
            latest_record_hashes: Dict[str, Tuple[str, str]] = {}

            for entry in entries:
                # 1. Monotonic sequence continuity check
                if entry.sequence_number != expected_seq:
                    return (
                        False,
                        entry.id,
                        f"Sequence number gap/mismatch at ledger entry {entry.id}: expected {expected_seq}, got {entry.sequence_number}",
                    )

                # 2. Previous hash link check
                if entry.previous_hash != expected_prev_hash:
                    return (
                        False,
                        entry.id,
                        f"Broken hash chain link at ledger entry {entry.id} (seq {entry.sequence_number}): "
                        f"previous_hash {entry.previous_hash!r} != expected {expected_prev_hash!r}",
                    )

                # 3. Payload integrity check (detects direct SQL tampering of payload_json)
                if entry.payload_json:
                    computed_payload_hash = compute_sha256_hex(entry.payload_json)
                    if entry.data_hash != computed_payload_hash:
                        return (
                            False,
                            entry.id,
                            f"Corrupted payload_json at ledger entry {entry.id}: stored data_hash {entry.data_hash!r} != computed {computed_payload_hash!r}",
                        )

                # 4. Entry hash computation check
                recomputed = entry.compute_entry_hash()
                if entry.entry_hash != recomputed:
                    return (
                        False,
                        entry.id,
                        f"Corrupted ledger entry hash at {entry.id} (seq {entry.sequence_number}): "
                        f"stored {entry.entry_hash!r} != computed {recomputed!r}",
                    )

                expected_seq = entry.sequence_number + 1
                expected_prev_hash = entry.entry_hash
                latest_record_hashes[entry.record_id] = (entry.mutation_type, entry.data_hash)

            # Phase 2: Verify current database record state against recorded data_hash & detect deletions
            for record_id, (mutation_type, recorded_data_hash) in latest_record_hashes.items():
                if mutation_type.startswith("PLAYBOOK_"):
                    pb_res = await db.execute(
                        select(ProceduralPlaybook).where(ProceduralPlaybook.id == record_id).execution_options(populate_existing=True)
                    )
                    pb = pb_res.scalars().first()
                    if mutation_type in _LIVE_PLAYBOOK_MUTATIONS and pb is None:
                        return False, record_id, f"Direct database deletion detected in procedural playbook {record_id}"
                    if pb is not None and pb.data_hash() != recorded_data_hash:
                        return False, record_id, f"Direct database tampering detected in procedural playbook {record_id}"

                elif mutation_type.startswith("EPISODIC_"):
                    ep_res = await db.execute(
                        select(EpisodicMemoryRecord).where(EpisodicMemoryRecord.id == record_id).execution_options(populate_existing=True)
                    )
                    ep = ep_res.scalars().first()
                    if mutation_type == "EPISODIC_PRUNED":
                        if ep is not None:
                            return False, record_id, f"Direct database tampering detected: pruned episodic memory {record_id} still present"
                    else:
                        if mutation_type in _LIVE_EPISODIC_MUTATIONS and ep is None:
                            return False, record_id, f"Direct database deletion detected in episodic memory {record_id}"
                        if ep is not None and ep.data_hash() != recorded_data_hash:
                            return False, record_id, f"Direct database tampering detected in episodic memory {record_id}"

                elif mutation_type.startswith("SEMANTIC_"):
                    sem_res = await db.execute(
                        select(SemanticConstraintRecord).where(SemanticConstraintRecord.id == record_id).execution_options(populate_existing=True)
                    )
                    sem = sem_res.scalars().first()
                    if mutation_type in _LIVE_SEMANTIC_MUTATIONS and sem is None:
                        return False, record_id, f"Direct database deletion detected in semantic constraint {record_id}"
                    if sem is not None and sem.data_hash() != recorded_data_hash:
                        return False, record_id, f"Direct database tampering detected in semantic constraint {record_id}"

            return True, None, None

        if project_id is not None:
            return await _verify_chain(project_id)

        # When project_id is None, query all distinct project_ids in the organization
        # and verify each project's chain (and the org-global chain where project_id IS NULL) independently.
        distinct_stmt = (
            select(MemoryLedgerEntry.project_id)
            .where(MemoryLedgerEntry.organization_id == org_id)
            .distinct()
            .execution_options(populate_existing=True)
        )
        res = await db.execute(distinct_stmt)
        project_ids = list(res.scalars().all())

        if not project_ids:
            return True, None, None

        # Sort deterministically: None (org-global) first, then sorted project_id strings
        has_none = any(p is None for p in project_ids)
        sorted_pids: List[Optional[str]] = []
        if has_none:
            sorted_pids.append(None)
        sorted_pids.extend(sorted([p for p in project_ids if p is not None]))

        for pid in sorted_pids:
            is_valid, corrupted_id, error_msg = await _verify_chain(pid)
            if not is_valid:
                return False, corrupted_id, error_msg

        return True, None, None


# Module-level aliases for direct imports
store_episodic = MemoryOSService.store_episodic
recall_episodic = MemoryOSService.recall_episodic
query_episodic = MemoryOSService.query_episodic
prune_decayed_episodic = MemoryOSService.prune_decayed_episodic

store_semantic = MemoryOSService.store_semantic
query_semantic = MemoryOSService.query_semantic
resolve_conflict = MemoryOSService.resolve_conflict
propagate_beliefs = MemoryOSService.propagate_beliefs

store_playbook = MemoryOSService.store_playbook
query_playbooks = MemoryOSService.query_playbooks
record_playbook_execution = MemoryOSService.record_playbook_execution

record_mutation = MemoryOSService.record_mutation
verify_integrity = MemoryOSService.verify_integrity
