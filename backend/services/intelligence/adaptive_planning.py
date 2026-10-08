"""Upgraded AdaptivePlanner: uses historical ProcessMemory with evidence-based
confidence thresholds, reason codes, and intelligence traces."""
import json
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.models.intelligence import (
    ProcessMemory, ProcessOutcomeRecord, StrategyType,
    IntelligenceTrace,
)
from backend.services.agent_registry import AGENT_REGISTRY


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
MIN_CONFIDENCE_TO_USE_HISTORY = 0.7
MIN_SUCCESS_RATE_TO_USE_HISTORY = 0.8
MIN_SAMPLE_SIZE_TO_USE_HISTORY = 3
RELEVANCE_CATEGORY_MATCH = True  # must exact-match task_category


class AdaptivePlannerResult:
    """Structured output from adaptive planning decisions."""
    __slots__ = ('team', 'strategy', 'reason_codes', 'evidence_refs', 'used_history', 'hypotheses')

    def __init__(
        self,
        team: List[str],
        strategy: StrategyType,
        reason_codes: List[str],
        evidence_refs: List[str],
        used_history: bool,
        hypotheses: List[Dict[str, Any]] = None,
    ):
        self.team = team
        self.strategy = strategy
        self.reason_codes = reason_codes
        self.evidence_refs = evidence_refs
        self.used_history = used_history
        self.hypotheses = hypotheses or []

    def to_dict(self) -> Dict[str, Any]:
        return {
            'team': self.team,
            'strategy': self.strategy.value,
            'reason_codes': self.reason_codes,
            'evidence_refs': self.evidence_refs,
            'used_history': self.used_history,
            'hypotheses': self.hypotheses,
        }


class AdaptivePlanner:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ==================================================================
    # SCOPE TRIAGE (INTAKE)
    # ==================================================================

    def _scan_repository_targets(
        self, text: str, words: List[str], domains: List[str], repo_root: Optional[str] = None
    ) -> List[str]:
        """
        Inspects the actual repository file tree and maps prompt tokens & domain hotspots
        to verified files on disk so planners operate with real file-path grounding.
        """
        import os
        from pathlib import Path

        framework_root = Path(__file__).resolve().parents[3]
        target = repo_root or os.environ.get("KOBITS_TARGET_WORKSPACE")
        if not target or Path(target).resolve() == framework_root:
            return []

        root = Path(target).resolve()
        if not root.exists():
            return []

        ignore_dirs = {
            ".git", "__pycache__", "node_modules", ".venv", "venv",
            "sandboxes", ".gemini", ".pytest_cache", "tests", "target", "dist", "build",
        }
        ignore_exts = {".db", ".pyc", ".pyo", ".png", ".jpg", ".pdf", ".lock", ".log"}

        stop_stems = {
            "test", "build", "feature", "mock", "deterministic", "transition",
            "mission", "objective", "simple", "create", "update", "delete",
            "check", "status", "system", "project", "base", "init", "index",
        }
        specific_tokens = {
            w.strip("./") for w in words
            if len(w.strip("./")) >= 4 and w.strip("./") not in stop_stems
        }

        scored: List[tuple] = []
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in ignore_dirs and not d.startswith(".")]
                for fname in filenames:
                    ext = os.path.splitext(fname)[1].lower()
                    if ext in ignore_exts or fname.startswith("."):
                        continue
                    full_path = Path(dirpath) / fname
                    rel_path = full_path.relative_to(root).as_posix()
                    rel_lower = rel_path.lower()
                    stem_lower = os.path.splitext(fname)[0].lower()

                    score = 0
                    # 1. Exact relative path, API router path, or filename mentioned in prompt
                    if rel_lower in text or fname.lower() in words:
                        score += 10
                    elif rel_lower.startswith("backend/api/v1/") and f"api/v1/{stem_lower}" in text:
                        score += 12
                    # 2. Stem match against specific prompt tokens
                    elif len(stem_lower) >= 4 and stem_lower not in stop_stems and stem_lower in specific_tokens:
                        score += 6

                    # 3. Domain hotspot grounding for real entrypoints (only if relevant to the project)
                    if rel_lower == "backend/main.py" and any(
                        k in words for k in ("ping", "health", "endpoint", "route", "api", "fastapi", "middleware")
                    ) and "portal" not in words and "admission" not in words:
                        score += 8
                    elif rel_lower == "portal.html" and "portal.html" in text:
                        score += 15
                    elif rel_lower == "backend/core/database.py" and any(
                        k in words for k in ("database", "sqlite", "postgres", "schema", "migration")
                    ) and "admission" not in words:
                        score += 7
                    elif rel_lower == "backend/services/agent_executor.py" and any(
                        k in words for k in ("agent", "executor", "prompt", "llm")
                    ) and "admission" not in words:
                        score += 7

                    if score > 0:
                        scored.append((score, rel_path))
        except Exception:
            return []

        scored.sort(key=lambda item: (-item[0], item[1]))
        return [path for _, path in scored[:6]]

    def classify_scope(self, objective: str, title: str = "", repo_root: Optional[str] = None) -> Dict[str, Any]:
        """
        Classifies a mission prompt into touched domains (backend, frontend, database, ai_ml, devops),
        complexity (trivial, standard, complex), grounded repository target_files, and full-pipeline
        selective specialist pruning across ANALYSIS, PLANNING, SECURITY, DEPLOYMENT, and LEARNING.
        """
        import re
        text = f"{title or ''} {objective or ''}".lower()
        words = re.findall(r"[a-z0-9_/.-]+", text)

        domain_keywords = {
            "backend": [
                "api", "endpoint", "route", "router", "ping", "health", "auth", "jwt",
                "webhook", "controller", "middleware", "server", "fastapi", "backend",
                "service", "rest", "graphql", "status", "handler",
            ],
            "frontend": [
                "frontend", "ui", "ux", "page", "view", "modal", "button", "css",
                "html", "react", "dashboard", "portal", "component", "layout",
                "sidebar", "navbar", "theme", "dark mode",
            ],
            "database": [
                "database", "db", "sql", "sqlite", "postgres", "migration", "table",
                "schema", "column", "index", "orm", "alembic", "query", "foreign key",
            ],
            "ai_ml": [
                "llm", "ai", "ml", "embedding", "vector", "prompt", "rag",
                "openai", "anthropic", "deepseek", "gemini", "classifier", "inference",
            ],
            "devops": [
                "docker", "dockerfile", "ci/cd", "kubernetes", "k8s", "terraform",
                "helm", "nginx", "github action", "deployment pipeline",
            ],
        }

        matched_domains: List[str] = []
        for domain, kw_list in domain_keywords.items():
            for kw in kw_list:
                if " " in kw or "/" in kw:
                    if kw in text:
                        matched_domains.append(domain)
                        break
                elif kw in words or any(w.startswith(f"/{kw}") or w.endswith(f"/{kw}") for w in words):
                    matched_domains.append(domain)
                    break

        target_files = self._scan_repository_targets(text, words, matched_domains, repo_root=repo_root)

        # Infer domain from explicit file mentions in the prompt if not already matched
        for tf in target_files:
            tf_l = tf.lower()
            if (tf_l.startswith("js/") or tf_l.endswith((".html", ".css", ".tsx", ".jsx", ".vue", ".svelte"))) and "frontend" not in matched_domains:
                if any(part in text for part in (tf_l, tf_l.split("/")[-1])):
                    matched_domains.append("frontend")
            elif tf_l.startswith("backend/models/") and "database" not in matched_domains:
                if any(part in text for part in (tf_l, tf_l.split("/")[-1])):
                    matched_domains.append("database")
            elif tf_l.startswith("backend/services/llm/") and "ai_ml" not in matched_domains:
                if any(part in text for part in (tf_l, tf_l.split("/")[-1])):
                    matched_domains.append("ai_ml")
            elif (tf_l.startswith("backend/") or tf_l.endswith((".py", ".go", ".rs", ".ts", ".js"))) and "backend" not in matched_domains:
                if any(part in text for part in (tf_l, tf_l.split("/")[-1])):
                    matched_domains.append("backend")

        if not matched_domains and any(w.endswith((".py", ".go", ".rs")) for w in words):
            matched_domains.append("backend")
        elif not matched_domains and any(w.endswith((".tsx", ".jsx", ".css", ".html")) for w in words):
            matched_domains.append("frontend")

        # If no specific domain keyword was matched (e.g., generic "Build a feature"),
        # default to full-stack scope so general missions/tests include all core planners.
        if not matched_domains:
            domains = ["backend", "frontend", "database", "ai_ml"]
            is_generic = True
        else:
            domains = matched_domains
            is_generic = False

        # Determine complexity tier: trivial | standard | complex
        complex_signals = [
            "refactor", "architecture", "multi-tenant", "migration", "overhaul",
            "redesign", "distributed", "microservice", "end-to-end", "full-stack",
            "storage engine", "acid", "transactional", "consensus", "b-tree", "wal",
            "write-ahead log", "raft", "paxos", "concurrency", "crash recovery",
            "compiler", "parser", "lexer", "byte-code", "virtual machine",
        ]
        trivial_signals = [
            "ping", "health", "healthcheck", "version", "typo", "rename", "flag",
            "constant", "badge", "log", "function", "method", "repository.edit",
        ]

        if len(domains) >= 3 or any(sig in text for sig in complex_signals):
            complexity = "complex" if not is_generic else "standard"
        elif len(domains) == 1 and (len(words) <= 22 or (1 <= len(target_files) <= 2) or any(sig in text for sig in trivial_signals)):
            complexity = "trivial"
        else:
            complexity = "standard"

        domain_to_caps = {
            "backend": ["architecture", "api design", "system design", "task decomposition"],
            "frontend": ["ux design", "user flows", "accessibility", "task decomposition"],
            "database": ["schema design", "migrations", "optimization"],
            "ai_ml": ["model integration", "prompt engineering", "rag"],
            "devops": ["ci/cd", "docker", "cloud infrastructure"],
        }
        capabilities: List[str] = []
        for d in domains:
            for c in domain_to_caps.get(d, []):
                if c not in capabilities:
                    capabilities.append(c)

        skipped_planners: List[str] = []
        if "database" not in domains:
            skipped_planners.append("DATABASE_ENGINEER")
        if "ai_ml" not in domains:
            skipped_planners.append("AI_ML_ENGINEER")
        if "frontend" not in domains:
            skipped_planners.append("UX_DESIGNER")

        # Full-pipeline selective phase rules (Kyros Fast-Track)
        fast_track = (complexity == "trivial" and not is_generic)
        has_concrete_tech_spec = (not is_generic) and any(
            s in text for s in ("/api/", "endpoint", "sqlite", "table", "schema", "column", ".py", ".js", ".html")
        )
        security_signals = {
            "auth", "login", "jwt", "token", "password", "secret", "sql",
            "database", "permission", "role", "webhook", "upload", "security", "crypto", "oauth",
        }
        doc_signals = {"doc", "docs", "documentation", "readme", "openapi", "swagger", "changelog"}

        requires_pm = (not fast_track) and (not has_concrete_tech_spec or complexity == "complex")
        requires_security_audit = (not fast_track) or ("database" in domains) or any(s in words or s in text for s in security_signals)
        requires_devops = ("devops" in domains) or is_generic
        requires_release_manager = (not fast_track) and (requires_devops or not has_concrete_tech_spec)
        requires_docs = (not fast_track) and (not has_concrete_tech_spec or any(s in words or s in text for s in doc_signals))

        skipped_specialists: List[str] = []
        if not requires_pm:
            skipped_specialists.append("PRODUCT_MANAGER")
        for sp in skipped_planners:
            if sp not in skipped_specialists:
                skipped_specialists.append(sp)
        if not requires_security_audit:
            skipped_specialists.append("SECURITY_ENGINEER")
        if not requires_devops:
            skipped_specialists.append("DEVOPS_ENGINEER")
        if not requires_release_manager:
            skipped_specialists.append("RELEASE_MANAGER")
        if not requires_docs:
            skipped_specialists.append("DOCUMENTATION_ENGINEER")

        task_category = f"{domains[0]}_engineering" if len(domains) == 1 else "general_engineering"

        return {
            "domains": domains,
            "complexity": complexity,
            "task_category": task_category,
            "capabilities": capabilities or ["architecture", "system design"],
            "target_files": target_files,
            "fast_track": fast_track,
            "is_generic": is_generic,
            "requires_pm": requires_pm,
            "requires_security_audit": requires_security_audit,
            "requires_devops": requires_devops,
            "requires_release_manager": requires_release_manager,
            "requires_docs": requires_docs,
            "skipped_planners": skipped_planners,
            "skipped_specialists": skipped_specialists,
        }

    # ==================================================================
    # STRATEGY SELECTION
    # ==================================================================

    async def select_strategy(
        self,
        task_category: str,
        risk_level: str,
        organization_id: Optional[str] = None,
    ) -> StrategyType:
        """Select strategy using historical evidence if it meets thresholds."""
        memory = await self._get_relevant_memory(task_category, organization_id)

        if memory:
            return memory.strategy_type

        # Conservative fallback
        if risk_level in ('HIGH', 'CRITICAL'):
            return StrategyType.RISK_FIRST

        return StrategyType.SEQUENTIAL

    # ==================================================================
    # TEAM SELECTION
    # ==================================================================

    async def select_team(
        self,
        task_category: str,
        required_capabilities: List[str],
        strategy: StrategyType,
        organization_id: Optional[str] = None,
    ) -> List[str]:
        """Select team using historical evidence if it meets thresholds."""
        memory = await self._get_relevant_memory(
            task_category, organization_id, strategy=strategy
        )

        if memory and memory.team_composition_json:
            return json.loads(memory.team_composition_json)

        # Capability-based fallback
        team = set()
        for cap in required_capabilities:
            for _, agent in AGENT_REGISTRY.items():
                if any(cap.lower() in c.lower() for c in agent.capabilities):
                    team.add(agent.name)
                elif cap.lower() in getattr(agent, 'description', '').lower():
                    team.add(agent.name)

        if 'Review' not in team:
            team.add('Review')

        return list(team)

    # ==================================================================
    # FULL ADAPTIVE PLAN (unified output)
    # ==================================================================

    async def plan(
        self,
        task_category: str,
        risk_level: str,
        required_capabilities: List[str],
        organization_id: Optional[str] = None,
        mission_id: Optional[str] = None,
    ) -> AdaptivePlannerResult:
        """Unified planning endpoint: returns team, strategy, reasons, evidence."""
        memory = await self._get_relevant_memory(task_category, organization_id)

        reason_codes: List[str] = []
        evidence_refs: List[str] = []
        used_history = False

        if memory:
            strategy = memory.strategy_type
            team = json.loads(memory.team_composition_json) if memory.team_composition_json else []
            used_history = True
            reason_codes.append(f'HISTORICAL_MATCH: category={task_category}')
            reason_codes.append(
                f'CONFIDENCE: {memory.confidence:.2f} '
                f'(samples={memory.sample_size}, success_rate={memory.success_rate:.2f})'
            )
            if memory.source_mission_id:
                evidence_refs.append(memory.source_mission_id)
        else:
            # Conservative fallback
            if risk_level in ('HIGH', 'CRITICAL'):
                strategy = StrategyType.RISK_FIRST
                reason_codes.append('FALLBACK: risk_level is HIGH/CRITICAL')
            else:
                strategy = StrategyType.SEQUENTIAL
                reason_codes.append('FALLBACK: no qualifying historical evidence')

            # Capability-based team
            team_set = set()
            for cap in required_capabilities:
                for _, agent in AGENT_REGISTRY.items():
                    if any(cap.lower() in c.lower() for c in agent.capabilities):
                        team_set.add(agent.name)
                    elif cap.lower() in getattr(agent, 'description', '').lower():
                        team_set.add(agent.name)
            if 'Review' not in team_set:
                team_set.add('Review')
            team = list(team_set)
            reason_codes.append('TEAM: capability-based default')

        # Record intelligence trace
        if mission_id:
            await self._record_plan_traces(mission_id, strategy, team, reason_codes, evidence_refs)

        # Generate multiple hypotheses for high risk
        hypotheses = self.generate_hypotheses(task_category, risk_level)

        return AdaptivePlannerResult(
            team=team,
            strategy=strategy,
            reason_codes=reason_codes,
            evidence_refs=evidence_refs,
            used_history=used_history,
            hypotheses=hypotheses
        )

    # ==================================================================
    # PHASE TEAM VALIDATION (unchanged behavior)
    # ==================================================================

    def validate_phase_team(self, phase_value: str, team: List[str]) -> List[str]:
        """Ensure the team has minimum required capabilities for a phase."""
        phase_caps = {
            "INTAKE": ["orchestration", "product management"],
            "ANALYSIS": ["architecture", "system design"],
            "PLANNING": ["architecture", "system design"],
            "ARCHITECTURE_REVIEW": ["architecture", "security check"],
            "IMPLEMENTATION": ["api development", "ui/ux", "business logic"],
            "VALIDATION": ["quality assurance", "testing"],
            "SECURITY_REVIEW": ["security check", "vulnerability"],
            "CODE_REVIEW": ["code review", "quality assurance"],
            "DELIVERY_REVIEW": ["release management", "checklist generation"],
            "DEPLOYMENT": ["infrastructure", "deployment"],
            "MONITORING": ["monitoring", "reliability"],
            "LEARNING": ["orchestration"]
        }

        required_caps = phase_caps.get(phase_value, [])
        if not required_caps:
            return team

        is_valid = False
        for cap in required_caps:
            for agent_name in team:
                agent_def = next(
                    (a for a in AGENT_REGISTRY.values() if a.name == agent_name), None
                )
                if agent_def:
                    if any(cap.lower() in c.lower() for c in agent_def.capabilities):
                        is_valid = True
                        break
            if is_valid:
                break

        if is_valid:
            return team

        fallback_team = list(team)
        for cap in required_caps:
            fallback_agent = next(
                (a for a in AGENT_REGISTRY.values()
                 if any(cap.lower() in c.lower() for c in a.capabilities)),
                None
            )
            if fallback_agent and fallback_agent.name not in fallback_team:
                fallback_team.append(fallback_agent.name)
                return fallback_team

        return fallback_team

    # ==================================================================
    # MODEL ROUTING 
    # ==================================================================

    async def route_model(self, task_category: str, task_complexity: str, risk_level: str) -> str:
        """
        Adaptive Model Routing & Self-Diagnostics
        Tracks system performance and dynamically adjusts the reasoning power (Flash vs Pro)
        based on historical success rates and corrections, optimizing for token ROI.
        """
        if not getattr(self, 'db', None):
            return self._static_route(task_complexity, risk_level)
            
        stmt = select(ProcessMemory).where(ProcessMemory.task_category == task_category)
        result = await self.db.execute(stmt)
        memory = result.scalars().first()
        
        # 1. Self-Diagnostic Overrides
        # We need a decent sample size before overriding static heuristics
        if memory and getattr(memory, 'sample_size', 0) >= 2:
            # If the task historically fails or requires a lot of corrections (debate rounds), force PRO
            if memory.success_rate < 0.75 or memory.avg_corrections > 1.5:
                # Upgrading to Pro to prevent failures
                return 'pro'
            
            # If the task is historically trivial (100% success, 0 debate rounds), downgrade to FLASH to save tokens
            if memory.success_rate == 1.0 and memory.avg_corrections <= 0.2:
                # Even if complexity was judged MEDIUM, history proves it's easy
                if risk_level not in ('HIGH', 'CRITICAL'):
                    return 'flash'
                    
        # 2. Default Rules
        return self._static_route(task_complexity, risk_level)
        
    def _static_route(self, task_complexity: str, risk_level: str) -> str:
        if risk_level in ('HIGH', 'CRITICAL') or task_complexity == 'HIGH':
            return 'pro'
        elif task_complexity == 'LOW' and risk_level == 'LOW':
            return 'flash'
        return 'default'

    # ==================================================================
    # MULTI-HYPOTHESIS PLANNING
    # ==================================================================
    def generate_hypotheses(self, task_category: str, risk_level: str) -> List[Dict[str, Any]]:
        # For complex tasks, we generate bounded alternative plans.
        # This will be returned in the AdaptivePlannerResult for downstream evaluation
        if risk_level not in ('HIGH', 'CRITICAL'):
            return []
            
        return [
            {
                "hypothesis_id": "A_CONSERVATIVE",
                "strategy": StrategyType.SEQUENTIAL,
                "focus": "Security and Architecture strictness"
            },
            {
                "hypothesis_id": "B_BALANCED",
                "strategy": StrategyType.PARALLEL,
                "focus": "Balanced delivery and isolated testing"
            },
            {
                "hypothesis_id": "C_AGGRESSIVE",
                "strategy": StrategyType.PARALLEL,
                "focus": "High parallelism with post-hoc verification"
            }
        ]

    # ==================================================================
    # PRIVATE HELPERS
    # ==================================================================

    async def _get_relevant_memory(
        self,
        task_category: str,
        organization_id: Optional[str] = None,
        strategy: Optional[StrategyType] = None,
    ) -> Optional[ProcessMemory]:
        """Retrieve the best ProcessMemory only if it meets ALL thresholds:
        - exact category match (relevance filtering)
        - minimum sample size
        - minimum confidence
        - minimum success rate
        - organization isolation
        """
        conditions = [
            ProcessMemory.task_category == task_category,
            ProcessMemory.success_rate >= MIN_SUCCESS_RATE_TO_USE_HISTORY,
            ProcessMemory.confidence >= MIN_CONFIDENCE_TO_USE_HISTORY,
            ProcessMemory.sample_size >= MIN_SAMPLE_SIZE_TO_USE_HISTORY,
        ]

        if organization_id:
            conditions.append(ProcessMemory.organization_id == organization_id)

        if strategy:
            conditions.append(ProcessMemory.strategy_type == strategy)

        stmt = (
            select(ProcessMemory)
            .where(*conditions)
            .order_by(ProcessMemory.success_rate.desc())
        )

        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def _record_plan_traces(
        self,
        mission_id: str,
        strategy: StrategyType,
        team: List[str],
        reason_codes: List[str],
        evidence_refs: List[str],
    ):
        """Store WHY_TEAM, WHY_STRATEGY, WHY_CONTEXT traces."""
        for dtype, value in [
            ('WHY_STRATEGY', strategy.value),
            ('WHY_TEAM', json.dumps(team)),
            ('WHY_CONTEXT', json.dumps({'reason_codes': reason_codes})),
        ]:
            trace = IntelligenceTrace(
                mission_id=mission_id,
                decision_type=dtype,
                decision_value=value,
                evidence_json=json.dumps({'evidence_refs': evidence_refs}),
            )
            self.db.add(trace)
        await self.db.commit()
