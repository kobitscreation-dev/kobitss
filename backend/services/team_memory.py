"""
Persistent Team Memory Infrastructure for Multi-Agent Systems.

Production-grade shared memory architecture supporting:
1. Structured Namespaces:
   - ADR: Architectural Decision Records
   - STYLE / CONVENTION: Repository-level coding conventions and style rules
   - DEFECT_FIX: Root-cause defect remediations and regression lessons
   - PEER_NOTE: Cross-agent direct notes and specialist handoffs
   - DOMAIN_CONCEPT: Domain semantics, business logic models, and terminology
2. Confidence Scoring & TTL Decay:
   - Dynamic time-decayed confidence scoring (exponential decay factor)
   - TTL expiration support for transient or ephemeral guidance
3. Source Agent Attribution & Selective Sharing:
   - Every memory tracks the producing agent (e.g., Nexus, Axiom, Sentinel, Aegis)
   - Targeted routing to specific specialist agents or broadcast to all
4. Hybrid Semantic Retrieval (Dense + Lexical BM25 Reciprocal Rank Fusion):
   - Dense vector embeddings via deterministic CountSketch
   - BM25 tokenized lexical matching with code-identifier subtoken awareness
   - Reciprocal Rank Fusion (RRF) for robust multi-modal relevance ranking
5. Automatic Cross-Agent Sharing:
   - Transparent injection into context builder during planning and task dispatch
   - Episodic persistence syncing with database (ProjectMemory)
"""

import os
import re
import math
import json
import uuid
import threading
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Union, Set
import numpy as np

from backend.services.embedding import EmbeddingUtils, tokenize_code_identifiers


class MemoryNamespace(str, Enum):
    ADR = "ADR"
    CONVENTION = "CONVENTION"
    STYLE = "STYLE"
    DEFECT_FIX = "DEFECT_FIX"
    PEER_NOTE = "PEER_NOTE"
    DOMAIN_CONCEPT = "DOMAIN_CONCEPT"

    @classmethod
    def normalize(cls, val: Union[str, "MemoryNamespace"]) -> "MemoryNamespace":
        """Normalizes string or enum into a canonical MemoryNamespace."""
        if isinstance(val, cls):
            return val
        s = str(val).strip().upper()
        if s in ("STYLE", "CONVENTIONS", "CONVENTION"):
            return cls.CONVENTION
        if s in ("ADR", "ARCHITECTURE", "ARCHITECTURAL_DECISION"):
            return cls.ADR
        if s in ("DEFECT_FIX", "BUG_FIX", "REMEDIATION", "DEFECT"):
            return cls.DEFECT_FIX
        if s in ("PEER_NOTE", "PEER", "HANDOFF", "NOTE"):
            return cls.PEER_NOTE
        if s in ("DOMAIN_CONCEPT", "DOMAIN", "SEMANTICS", "CONCEPT"):
            return cls.DOMAIN_CONCEPT
        # Default fallback
        for member in cls:
            if member.value == s:
                return member
        return cls.PEER_NOTE


SPECIALIST_ALIASES: Dict[str, Set[str]] = {
    "nexus": {"orchestrator", "solution_architect", "architect", "lead"},
    "axiom": {"solution_architect", "architect", "orchestrator"},
    "forge": {"product_manager", "technical_lead", "backend_engineer", "pm", "lead"},
    "muse": {"ux_designer", "frontend_engineer", "ui", "ux", "designer"},
    "schema": {"database_engineer", "dba", "db", "database"},
    "core": {"backend_engineer", "backend", "api", "coder"},
    "pixel": {"frontend_engineer", "frontend", "ui", "web"},
    "swift": {"mobile_engineer", "mobile", "ios", "android"},
    "neuron": {"ai_ml_engineer", "ai", "ml", "data_science"},
    "bridge": {"integrations_engineer", "integrations", "api_connectors"},
    "pulse": {"devops_engineer", "devops", "infra", "ci_cd"},
    "sentinel": {"qa_engineer", "qa", "tester", "visual_qa", "test"},
    "aegis": {"security_engineer", "security", "auditor", "infosec"},
    "review": {"code_reviewer", "reviewer", "review"},
    "vector": {"performance_engineer", "perf", "performance"},
    "access": {"accessibility_engineer", "a11y", "accessibility"},
    "insight": {"data_analytics_engineer", "analytics", "telemetry"},
    "scribe": {"documentation_engineer", "docs", "documentation", "writer"},
    "launch": {"release_manager", "release", "deployment"},
    "reliant": {"sre", "reliability", "infrastructure"},
    "catalyst": {"business_analyst", "business", "ba"},
}


@dataclass
class TeamMemoryEntry:
    id: str
    project_id: str
    organization_id: str
    namespace: MemoryNamespace
    key: str
    content: str
    source_agent: str
    target_agents: List[str] = field(default_factory=list)  # Empty means broadcast to all
    confidence: float = 1.0
    decay_factor: float = 0.05  # Per 24-hour decay rate
    ttl_seconds: Optional[float] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    evidence: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    embedding: Optional[List[float]] = None

    def compute_effective_confidence(self, as_of: Optional[datetime] = None) -> float:
        """
        Calculates time-decayed confidence.
        If TTL has passed, effective confidence is 0.0.
        Otherwise, confidence decays exponentially according to decay_factor.
        """
        if self.confidence <= 0.0:
            return 0.0

        now = as_of or datetime.now(timezone.utc)
        if self.created_at.tzinfo is None:
            created = self.created_at.replace(tzinfo=timezone.utc)
        else:
            created = self.created_at

        age_seconds = max(0.0, (now - created).total_seconds())

        # Hard TTL expiration check (>= handles ttl_seconds=0 immediately)
        if self.ttl_seconds is not None and age_seconds >= self.ttl_seconds:
            return 0.0

        # Exponential decay: C_eff = C_0 * exp(-lambda * days)
        # decay_factor is fraction decayed per 24 hours
        days = age_seconds / 86400.0
        decay_rate = max(0.0, self.decay_factor)
        effective = self.confidence * math.exp(-decay_rate * days)
        return round(max(0.0, min(1.0, effective)), 4)

    def is_expired(self, as_of: Optional[datetime] = None) -> bool:
        """Check if memory has expired based on TTL or zero effective confidence."""
        return self.compute_effective_confidence(as_of) <= 0.0

    def is_applicable_to_agent(self, agent_name: Optional[str]) -> bool:
        """Check if entry is intended for the specified agent or broadcast to all."""
        if not agent_name or not self.target_agents:
            return True
        agent_norm = agent_name.strip().lower()
        targets_norm = {t.strip().lower() for t in self.target_agents if t}
        if "all" in targets_norm or "*" in targets_norm or agent_norm in targets_norm:
            return True

        agent_identities = {agent_norm}
        if agent_norm in SPECIALIST_ALIASES:
            agent_identities.update(SPECIALIST_ALIASES[agent_norm])
        for name, aliases in SPECIALIST_ALIASES.items():
            if agent_norm in aliases:
                agent_identities.add(name)
                agent_identities.update(aliases)

        return bool(agent_identities & targets_norm)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "organization_id": self.organization_id,
            "namespace": self.namespace.value,
            "key": self.key,
            "content": self.content,
            "source_agent": self.source_agent,
            "target_agents": list(self.target_agents),
            "confidence": self.confidence,
            "decay_factor": self.decay_factor,
            "ttl_seconds": self.ttl_seconds,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "evidence": self.evidence,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TeamMemoryEntry":
        created = data.get("created_at")
        if isinstance(created, str):
            try:
                created = datetime.fromisoformat(created)
            except Exception:
                created = datetime.now(timezone.utc)
        elif not isinstance(created, datetime):
            created = datetime.now(timezone.utc)

        updated = data.get("updated_at")
        if isinstance(updated, str):
            try:
                updated = datetime.fromisoformat(updated)
            except Exception:
                updated = datetime.now(timezone.utc)
        elif not isinstance(updated, datetime):
            updated = datetime.now(timezone.utc)

        ns = MemoryNamespace.normalize(data.get("namespace", "PEER_NOTE"))

        return cls(
            id=data.get("id", str(uuid.uuid4())),
            project_id=data.get("project_id", "default"),
            organization_id=data.get("organization_id", "default_org"),
            namespace=ns,
            key=data.get("key", ""),
            content=data.get("content", ""),
            source_agent=data.get("source_agent", "System"),
            target_agents=data.get("target_agents", []),
            confidence=float(data.get("confidence", 1.0)),
            decay_factor=float(data.get("decay_factor", 0.05)),
            ttl_seconds=data.get("ttl_seconds"),
            created_at=created,
            updated_at=updated,
            evidence=data.get("evidence"),
            metadata=data.get("metadata", {}),
            embedding=data.get("embedding"),
        )


class BM25Scorer:
    """
    Pure Python Okapi BM25 implementation for lexical relevance ranking.
    Uses tokenize_code_identifiers for code/symbol-aware tokenization.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b

    def score(
        self,
        query_tokens: List[str],
        corpus_tokens: List[List[str]],
    ) -> List[float]:
        if not corpus_tokens or not query_tokens:
            return [0.0] * len(corpus_tokens)

        N = len(corpus_tokens)
        doc_lens = [len(doc) for doc in corpus_tokens]
        avgdl = sum(doc_lens) / (N or 1)
        if avgdl == 0:
            return [0.0] * N

        # Document frequencies for query terms
        df: Dict[str, int] = {}
        for q in set(query_tokens):
            count = sum(1 for doc in corpus_tokens if q in doc)
            df[q] = count

        # Calculate IDF per query term
        # Robertson-Spärck Jones IDF with floor protection
        idf: Dict[str, float] = {}
        for q, count in df.items():
            val = (N - count + 0.5) / (count + 0.5)
            idf[q] = max(0.05, math.log(1.0 + val))

        scores: List[float] = []
        for i, doc in enumerate(corpus_tokens):
            doc_len = doc_lens[i]
            doc_score = 0.0
            if doc_len > 0:
                tf_counts: Dict[str, int] = {}
                for t in doc:
                    tf_counts[t] = tf_counts.get(t, 0) + 1

                for q in query_tokens:
                    if q in tf_counts:
                        tf = tf_counts[q]
                        numerator = tf * (self.k1 + 1.0)
                        denominator = tf + self.k1 * (1.0 - self.b + self.b * (doc_len / avgdl))
                        doc_score += idf.get(q, 0.0) * (numerator / (denominator or 1.0))
            scores.append(round(doc_score, 4))

        return scores


class TeamMemoryManager:
    """
    Production-grade enterprise Team Memory Manager.
    Maintains an in-memory thread-safe registry with automatic DB persistence
    and hybrid semantic retrieval (Dense + BM25 RRF).
    """

    _lock = threading.RLock()
    _stores: Dict[str, Dict[str, TeamMemoryEntry]] = {}  # project_id -> {entry_id: TeamMemoryEntry}
    _bm25 = BM25Scorer()

    @classmethod
    def _get_project_store(cls, project_id: str) -> Dict[str, TeamMemoryEntry]:
        with cls._lock:
            if project_id not in cls._stores:
                cls._stores[project_id] = {}
            return cls._stores[project_id]

    @classmethod
    def scrub_secrets(cls, text: Optional[str]) -> Optional[str]:
        """Scrubs AWS access keys, secret keys, passwords, bearer tokens, and private keys."""
        if not text:
            return text
        cleaned = text
        # AWS Access Key ID (AKIA/ASIA)
        cleaned = re.sub(r'\b(AKIA|ASIA)[0-9A-Z]{16}\b', '<SCRUBBED_AWS_KEY_ID>', cleaned)
        # AWS Secret Access Key / Bedrock API key assignments
        cleaned = re.sub(r'(?i)(aws[_-]?secret[_-]?access[_-]?key|aws[_-]?bedrock[_-]?api[_-]?key)\s*[:=]\s*["\']?[^"\'\s]+["\']?', r'\1: <SCRUBBED_SECRET>', cleaned)
        # Passwords and API keys assignments
        cleaned = re.sub(r'(?i)(api[_-]?key|password|secret)\s*[:=]\s*["\']?[^"\'\s]+["\']?', r'\1: <SCRUBBED_SECRET>', cleaned)
        # Bearer tokens
        cleaned = re.sub(r'(?i)\bbearer\s+[a-zA-Z0-9_\-\.]{8,}\b', 'Bearer <SCRUBBED_BEARER_TOKEN>', cleaned)
        # Private Keys
        cleaned = re.sub(r'-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----', '<SCRUBBED_PRIVATE_KEY>', cleaned)
        return cleaned

    @classmethod
    def store_memory(
        cls,
        project_id: str,
        namespace: Union[str, MemoryNamespace],
        key: str,
        content: str,
        source_agent: str,
        target_agents: Optional[List[str]] = None,
        confidence: float = 1.0,
        decay_factor: float = 0.05,
        ttl_seconds: Optional[float] = None,
        evidence: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        organization_id: str = "default_org",
        entry_id: Optional[str] = None,
    ) -> TeamMemoryEntry:
        """Stores a structured memory entry in the team memory store."""
        # Sanitize sensitive tokens and credentials
        content = cls.scrub_secrets(content) or ""
        evidence = cls.scrub_secrets(evidence)

        ns = MemoryNamespace.normalize(namespace)
        eid = entry_id or str(uuid.uuid4())
        full_text = f"{key} {content} {evidence or ''}"
        emb = EmbeddingUtils.generate_embedding(full_text)

        entry = TeamMemoryEntry(
            id=eid,
            project_id=project_id,
            organization_id=organization_id,
            namespace=ns,
            key=key,
            content=content,
            source_agent=source_agent,
            target_agents=target_agents or [],
            confidence=max(0.0, min(1.0, confidence)),
            decay_factor=max(0.0, decay_factor),
            ttl_seconds=ttl_seconds,
            evidence=evidence,
            metadata=metadata or {},
            embedding=emb,
        )

        with cls._lock:
            store = cls._get_project_store(project_id)
            store[entry.id] = entry

        return entry

    @classmethod
    def record_adr(
        cls,
        project_id: str,
        source_agent: str,
        title: str,
        decision: str,
        rationale: str,
        alternatives: Optional[str] = None,
        confidence: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TeamMemoryEntry:
        """Records an Architectural Decision Record (ADR)."""
        content = f"Decision: {decision} | Rationale: {rationale}"
        if alternatives:
            content += f" | Alternatives considered: {alternatives}"
        meta = metadata or {}
        meta["title"] = title
        if alternatives:
            meta["alternatives"] = alternatives
        return cls.store_memory(
            project_id=project_id,
            namespace=MemoryNamespace.ADR,
            key=f"adr_{title.lower().replace(' ', '_')[:40]}",
            content=content,
            source_agent=source_agent,
            confidence=confidence,
            decay_factor=0.01,  # ADRs decay very slowly (long shelf life)
            evidence=f"ADR authored by {source_agent}",
            metadata=meta,
        )

    @classmethod
    def record_convention(
        cls,
        project_id: str,
        source_agent: str,
        rule: str,
        example: Optional[str] = None,
        confidence: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TeamMemoryEntry:
        """Records a repository style/coding convention."""
        content = f"Rule: {rule}"
        if example:
            content += f" | Example: {example}"
        meta = metadata or {}
        if example:
            meta["example"] = example
        return cls.store_memory(
            project_id=project_id,
            namespace=MemoryNamespace.CONVENTION,
            key=f"convention_{rule.lower().replace(' ', '_')[:40]}",
            content=content,
            source_agent=source_agent,
            confidence=confidence,
            decay_factor=0.02,
            metadata=meta,
        )

    @classmethod
    def record_defect_fix(
        cls,
        project_id: str,
        source_agent: str,
        defect: str,
        fix: str,
        root_cause: Optional[str] = None,
        reproducer: Optional[str] = None,
        confidence: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TeamMemoryEntry:
        """Records a defect remediation and root cause lesson."""
        content = f"Defect: {defect} | Fix: {fix}"
        if root_cause:
            content += f" | Root Cause: {root_cause}"
        meta = metadata or {}
        if reproducer:
            meta["reproducer"] = reproducer
        if root_cause:
            meta["root_cause"] = root_cause
        return cls.store_memory(
            project_id=project_id,
            namespace=MemoryNamespace.DEFECT_FIX,
            key=f"fix_{defect.lower().replace(' ', '_')[:40]}",
            content=content,
            source_agent=source_agent,
            confidence=confidence,
            decay_factor=0.03,
            evidence=reproducer,
            metadata=meta,
        )

    @classmethod
    def record_peer_note(
        cls,
        project_id: str,
        source_agent: str,
        target_agents: List[str],
        note: str,
        key: Optional[str] = None,
        confidence: float = 1.0,
        ttl_seconds: Optional[float] = 86400.0 * 7,  # Default 7-day TTL for peer notes
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TeamMemoryEntry:
        """Records a direct peer note from one agent to other specialists."""
        target_str = ",".join(target_agents)
        k = key or f"peer_note_{source_agent.lower()}_{target_str.lower()[:20]}_{uuid.uuid4().hex[:6]}"
        return cls.store_memory(
            project_id=project_id,
            namespace=MemoryNamespace.PEER_NOTE,
            key=k,
            content=note,
            source_agent=source_agent,
            target_agents=target_agents,
            confidence=confidence,
            decay_factor=0.10,  # Peer notes decay faster than architectural decisions
            ttl_seconds=ttl_seconds,
            metadata=metadata or {},
        )

    @classmethod
    def record_domain_concept(
        cls,
        project_id: str,
        source_agent: str,
        concept: str,
        definition: str,
        confidence: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TeamMemoryEntry:
        """Records a business domain semantic concept or entity model."""
        content = f"Concept: {concept} | Definition: {definition}"
        return cls.store_memory(
            project_id=project_id,
            namespace=MemoryNamespace.DOMAIN_CONCEPT,
            key=f"concept_{concept.lower().replace(' ', '_')[:40]}",
            content=content,
            source_agent=source_agent,
            confidence=confidence,
            decay_factor=0.02,
            metadata=metadata or {},
        )

    @classmethod
    def query_team_memory(
        cls,
        project_id: str,
        query: str,
        namespace: Optional[Union[str, MemoryNamespace]] = None,
        agent_name: Optional[str] = None,
        limit: int = 5,
        min_confidence: float = 0.05,
        dense_weight: float = 0.6,
        bm25_weight: float = 0.4,
        rrf_k: int = 60,
    ) -> List[Dict[str, Any]]:
        """
        Executes hybrid Dense + Lexical BM25 Reciprocal Rank Fusion (RRF) search.
        Accounts for dynamic confidence decay and selective agent targeting.
        """
        if not query or not query.strip():
            return []

        with cls._lock:
            store = cls._get_project_store(project_id)
            all_entries = list(store.values())

        if not all_entries:
            return []

        target_ns = MemoryNamespace.normalize(namespace) if namespace else None

        # Filter active, non-expired, namespace-matched, agent-applicable entries
        now = datetime.now(timezone.utc)
        filtered_entries: List[TeamMemoryEntry] = []
        for e in all_entries:
            if target_ns and e.namespace != target_ns:
                continue
            if not e.is_applicable_to_agent(agent_name):
                continue
            eff_conf = e.compute_effective_confidence(now)
            if eff_conf < min_confidence:
                continue
            filtered_entries.append(e)

        if not filtered_entries:
            return []

        # 1. Dense Vector Scoring
        query_vec = np.array(EmbeddingUtils.generate_embedding(query), dtype=np.float64)
        q_norm = np.linalg.norm(query_vec)
        if q_norm > 0:
            query_vec = query_vec / q_norm

        dense_scores: List[float] = []
        for e in filtered_entries:
            if e.embedding is None:
                full_text = f"{e.key} {e.content} {e.evidence or ''}"
                e.embedding = EmbeddingUtils.generate_embedding(full_text)
            doc_vec = np.array(e.embedding, dtype=np.float64)
            d_norm = np.linalg.norm(doc_vec)
            if d_norm > 0:
                doc_vec = doc_vec / d_norm
            sim = float(np.dot(query_vec, doc_vec)) if doc_vec.shape == query_vec.shape else 0.0
            dense_scores.append(max(0.0, sim))

        # 2. Lexical BM25 Scoring
        query_tokens = tokenize_code_identifiers(query)
        corpus_tokens = [tokenize_code_identifiers(f"{e.key} {e.content} {e.evidence or ''}") for e in filtered_entries]
        bm25_scores = cls._bm25.score(query_tokens, corpus_tokens)

        # 3. Reciprocal Rank Fusion (RRF)
        # Dense ranking
        dense_order = np.argsort(dense_scores)[::-1]
        dense_rank = {filtered_entries[idx].id: rank + 1 for rank, idx in enumerate(dense_order)}

        # BM25 ranking
        bm25_order = np.argsort(bm25_scores)[::-1]
        bm25_rank = {filtered_entries[idx].id: rank + 1 for rank, idx in enumerate(bm25_order)}

        results: List[Dict[str, Any]] = []
        for i, entry in enumerate(filtered_entries):
            r_dense = dense_rank[entry.id]
            r_bm25 = bm25_rank[entry.id]
            eff_conf = entry.compute_effective_confidence(now)

            # RRF combined score scaled by effective confidence
            rrf_score = (
                dense_weight / (rrf_k + r_dense) +
                bm25_weight / (rrf_k + r_bm25)
            ) * eff_conf

            results.append({
                "id": entry.id,
                "project_id": entry.project_id,
                "namespace": entry.namespace.value,
                "key": entry.key,
                "content": entry.content,
                "source_agent": entry.source_agent,
                "target_agents": entry.target_agents,
                "confidence": entry.confidence,
                "effective_confidence": eff_conf,
                "dense_score": round(dense_scores[i], 4),
                "bm25_score": round(bm25_scores[i], 4),
                "rrf_score": round(rrf_score, 6),
                "evidence": entry.evidence,
                "metadata": entry.metadata,
                "created_at": entry.created_at.isoformat(),
            })

        results.sort(key=lambda x: x["rrf_score"], reverse=True)
        return results[:limit]

    @classmethod
    def get_team_context_for_agent(
        cls,
        project_id: str,
        agent_name: str,
        task_text: str,
        limit: int = 5,
    ) -> str:
        """Formats relevant team persistent memories into a high-signal prompt block for an agent."""
        if not project_id or not task_text:
            return ""

        memories = cls.query_team_memory(
            project_id=project_id,
            query=task_text,
            agent_name=agent_name,
            limit=limit,
        )
        if not memories:
            return ""

        lines = [
            "### PERSISTENT TEAM MEMORY & PEER NOTES (Governed Multi-Agent System):",
            "The following architectural decisions, repository conventions, defect remediations, and peer notes apply to your task:",
        ]
        for m in memories:
            ns = m["namespace"]
            src = m["source_agent"]
            content = m["content"]
            eff_conf = m["effective_confidence"]
            lines.append(f"- **[{ns}]** *(from {src}, confidence {round(eff_conf * 100)}%)*: {content}")

        return "\n".join(lines)

    @classmethod
    async def sync_to_db(cls, db, entry: TeamMemoryEntry):
        """Persists a TeamMemoryEntry to ProjectMemory table in database."""
        from backend.models.project import ProjectMemory, ProjectMemoryStatus

        category = f"team_memory:{entry.namespace.value.lower()}"
        val_payload = {
            "content": entry.content,
            "target_agents": entry.target_agents,
            "decay_factor": entry.decay_factor,
            "ttl_seconds": entry.ttl_seconds,
            "metadata": entry.metadata,
        }
        pm = ProjectMemory(
            id=entry.id,
            project_id=entry.project_id,
            organization_id=entry.organization_id,
            category=category,
            key=entry.key,
            value=json.dumps(val_payload),
            source_agent_id=entry.source_agent,
            confidence=entry.confidence,
            evidence=entry.evidence,
            embedding_json=json.dumps(entry.embedding) if entry.embedding else None,
            version=1,
            memory_status=ProjectMemoryStatus.ACTIVE,
        )
        db.add(pm)
        await db.commit()
        return pm

    @classmethod
    async def load_from_db(cls, db, project_id: str) -> int:
        """
        Hydrates TeamMemoryManager registry from persistent ProjectMemory rows in database.
        Loads all active memories for the project.
        """
        from backend.models.project import ProjectMemory, ProjectMemoryStatus
        from sqlalchemy import select

        stmt = select(ProjectMemory).where(
            ProjectMemory.project_id == project_id,
            ProjectMemory.memory_status == ProjectMemoryStatus.ACTIVE
        )
        res = await db.execute(stmt)
        memories = res.scalars().all()
        loaded = 0

        with cls._lock:
            store = cls._get_project_store(project_id)
            for m in memories:
                if m.id in store:
                    continue

                content = m.value
                target_agents = []
                decay_factor = 0.05
                ttl_seconds = None
                metadata = {}

                if m.value and m.value.startswith("{") and m.value.endswith("}"):
                    try:
                        parsed = json.loads(m.value)
                        content = parsed.get("content", m.value)
                        target_agents = parsed.get("target_agents", [])
                        decay_factor = float(parsed.get("decay_factor", 0.05))
                        ttl_seconds = parsed.get("ttl_seconds")
                        metadata = parsed.get("metadata", {})
                    except Exception:
                        pass

                raw_ns = m.category.split(":")[-1] if ":" in m.category else m.category
                ns = MemoryNamespace.normalize(raw_ns)

                emb = None
                if m.embedding_json:
                    try:
                        emb = json.loads(m.embedding_json)
                    except Exception:
                        pass
                if not emb:
                    full_text = f"{m.key} {content} {m.evidence or ''}"
                    emb = EmbeddingUtils.generate_embedding(full_text)

                entry = TeamMemoryEntry(
                    id=m.id,
                    project_id=m.project_id,
                    organization_id=m.organization_id,
                    namespace=ns,
                    key=m.key,
                    content=content,
                    source_agent=m.source_agent_id or "System",
                    target_agents=target_agents,
                    confidence=m.confidence,
                    decay_factor=decay_factor,
                    ttl_seconds=ttl_seconds,
                    created_at=m.created_at if hasattr(m, "created_at") and m.created_at else datetime.now(timezone.utc),
                    updated_at=m.updated_at if hasattr(m, "updated_at") and m.updated_at else datetime.now(timezone.utc),
                    evidence=m.evidence,
                    metadata=metadata,
                    embedding=emb,
                )
                store[entry.id] = entry
                loaded += 1

        return loaded

    @classmethod
    async def persist_all_to_db(cls, db, project_id: str) -> int:
        """Persists all in-memory entries for a project into the database."""
        with cls._lock:
            store = cls._get_project_store(project_id)
            entries = list(store.values())

        synced = 0
        for entry in entries:
            try:
                await cls.sync_to_db(db, entry)
                synced += 1
            except Exception:
                pass
        return synced

    @classmethod
    def clear(cls, project_id: Optional[str] = None):
        """Clears in-memory store for testing and isolation."""
        with cls._lock:
            if project_id:
                cls._stores.pop(project_id, None)
            else:
                cls._stores.clear()


# Method aliases
TeamMemoryManager.query_memories = TeamMemoryManager.query_team_memory
