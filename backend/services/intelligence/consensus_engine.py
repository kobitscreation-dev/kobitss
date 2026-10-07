"""
Multi-Agent Debate & Consensus Engine
======================================

This is the core intelligence layer that makes Kobits fundamentally different
from a simple "send prompt, get response" system.

HOW IT WORKS:
1. A coding agent (Backend, Frontend, etc.) produces code/output
2. The ConsensusEngine automatically sends that output to:
   - SECURITY_ENGINEER (Aegis) — audits for vulnerabilities
   - CODE_REVIEWER (Review) — checks quality, conventions, correctness
3. Each reviewer independently produces a verdict: APPROVE or REJECT
4. If ANY reviewer rejects, their feedback is compiled and sent back to the
   original coder with a revision prompt
5. The coder rewrites its output incorporating the feedback
6. Steps 2-5 repeat until consensus is reached OR max rounds are hit

This creates an adversarial, self-correcting system where bad code literally
cannot pass through without being caught and fixed.
"""

import json
import uuid
import asyncio
import time
from sqlalchemy.exc import IntegrityError
from enum import Enum
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

from backend.models.agent import AgentType, AgentRun, AgentRunStatus
from backend.services.agent_registry import AGENT_REGISTRY
from backend.services.llm import get_llm_provider


# ─── Data Structures ──────────────────────────────────────────────────────────

class ReviewVerdict(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    APPROVED_WITH_NOTES = "APPROVED_WITH_NOTES"


@dataclass
class ReviewerResult:
    """One reviewer's independent assessment."""
    agent_type: str
    agent_name: str
    verdict: str  # ReviewVerdict value
    findings: List[str] = field(default_factory=list)
    feedback: str = ""
    severity: str = "LOW"  # LOW, MEDIUM, HIGH, CRITICAL
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass 
class DebateRound:
    """One complete round of debate."""
    round_number: int
    coder_output: dict
    reviews: List[ReviewerResult] = field(default_factory=list)
    consensus_reached: bool = False
    revision_prompt: str = ""
    duration_ms: int = 0
    

# ─── The Coding Agent Types that trigger debate ──────────────────────────────

CODING_AGENT_TYPES = {
    AgentType.BACKEND_ENGINEER,
    AgentType.FRONTEND_ENGINEER,
    AgentType.DATABASE_ENGINEER,
    AgentType.MOBILE_ENGINEER,
    AgentType.AI_ML_ENGINEER,
    AgentType.INTEGRATIONS_ENGINEER,
    AgentType.DEVOPS_ENGINEER,
}

# ─── The Reviewer Pipeline ──────────────────────────────────────────────────

REVIEWER_PIPELINE = [
    AgentType.SECURITY_ENGINEER,
    AgentType.CODE_REVIEWER,
]

MAX_DEBATE_ROUNDS = 3


# ─── ConsensusEngine ─────────────────────────────────────────────────────────

_dag_node_locks = {}

class ConsensusEngine:
    """
    Orchestrates the multi-agent debate pipeline.
    
    After a coding agent produces output, this engine:
    1. Sends the output to each reviewer agent in parallel
    2. Collects their verdicts
    3. If rejected, compiles feedback and sends coder back to revise
    4. Repeats until consensus or max rounds
    """
    
    def __init__(self, db=None, ws_broadcast=None):
        """
        Args:
            db: AsyncSession for persisting debate logs
            ws_broadcast: async callable(mission_id, message_dict) for live updates
        """
        self.db = db
        self.ws_broadcast = ws_broadcast
        self.provider = get_llm_provider()
        self.debate_history: List[DebateRound] = []
    
    async def _broadcast(self, mission_id: str, msg_type: str, message: str, metadata: dict = None):
        """Send live update to the WebSocket terminal."""
        if self.ws_broadcast and mission_id:
            payload = {
                "type": msg_type,
                "message": message,
                "source": "consensus_engine",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            if metadata:
                payload["metadata"] = metadata
            try:
                await self.ws_broadcast(mission_id, payload)
            except Exception:
                pass  # Don't let broadcast failures break the pipeline

    async def should_run_debate(self, agent_type: AgentType, result: dict) -> bool:
        """Determine if this agent's output needs to go through debate."""
        if agent_type not in CODING_AGENT_TYPES:
            return False
        # Only debate if the coder claims success
        status = result.get("status", "").upper()
        if status == "FAILED":
            return False
        # Must have actual content to review
        if not result.get("summary") and not result.get("changes") and not result.get("artifacts"):
            return False
        return True

    async def run_debate_pipeline(
        self,
        task_id: str,
        mission_id: str,
        coder_agent_type: AgentType,
        coder_result: dict,
        task_context: dict,
        model_name: str = None,
        code_artifacts: List[Dict[str, Any]] = None,
    ) -> dict:
        """
        Run the full debate pipeline.
        
        Args:
            code_artifacts: List of actual code diffs from the coder's tool calls.
                Each entry: {"path": "file.py", "content": "actual code...", "action": "write/read"}
                This is what reviewers actually examine — real code, not summaries.
        
        Returns the final consensus-approved result (possibly revised).
        """
        import os
        from backend.core.config import settings
        
        if not model_name:
            provider_name = settings.LLM_PROVIDER.lower() if settings.LLM_PROVIDER else "mock"
            if provider_name in ["bedrock", "aws", "aws_bedrock"]:
                model_name = os.environ.get("BEDROCK_MODEL") or settings.BEDROCK_MODEL or "global.anthropic.claude-sonnet-4-6"
            elif provider_name == "anthropic":
                model_name = os.environ.get("ANTHROPIC_MODEL", settings.ANTHROPIC_MODEL)
            elif provider_name == "deepseek":
                model_name = os.environ.get("DEEPSEEK_MODEL", settings.DEEPSEEK_MODEL)
            elif provider_name == "gemini":
                model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-pro")
            else:
                model_name = "mock-model"

        current_output = coder_result
        self.task_id = task_id
        self.debate_history = []
        self.code_artifacts = code_artifacts or []
        
        # Build a human-readable code diff from the artifacts
        self.code_diff_text = self._build_code_diff_text(self.code_artifacts)
        
        await self._broadcast(mission_id, "debate_start", 
            f"⚔️ DEBATE PIPELINE ACTIVATED — {coder_agent_type.value}'s output entering review",
            {"coder": coder_agent_type.value, "max_rounds": 3})

        # ── DURABILITY GAP (GAP 3) FIX: Recover Debate State from Database ──
        start_round = 1
        if self.db:
            try:
                from backend.models.debate_log import DebateLog, ConsensusVerdict
                from sqlalchemy import select
                
                stmt = select(DebateLog).where(DebateLog.task_id == task_id).order_by(DebateLog.round_number)
                existing_logs = (await self.db.execute(stmt)).scalars().all()
                
                if existing_logs:
                    last_log = existing_logs[-1]
                    
                    human_override = task_context.get("human_override_comment")
                    max_allowed_rounds = MAX_DEBATE_ROUNDS
                    
                    if last_log.consensus in (ConsensusVerdict.BLOCKED, ConsensusVerdict.MAX_ROUNDS_EXCEEDED) and human_override:
                        await self._broadcast(mission_id, "debate_resume", f"👤 HUMAN OVERRIDE INITIATED: '{human_override}'")
                        
                        verdicts = json.loads(last_log.reviewer_verdicts_json) if last_log.reviewer_verdicts_json else []
                        revs = [ReviewerResult(**v) for v in verdicts]
                        rejections = [r for r in revs if r.verdict == ReviewVerdict.REJECTED.value]
                        
                        rejection_summary = self._compile_rejection_feedback(rejections)
                        rejection_summary += f"\n\n[HUMAN OVERRIDE INSTRUCTION]: {human_override}"
                        
                        if last_log.coder_output_json:
                            current_output = json.loads(last_log.coder_output_json)
                        if hasattr(last_log, "code_artifacts_json") and last_log.code_artifacts_json:
                            self.code_artifacts = json.loads(last_log.code_artifacts_json)
                            self.code_diff_text = self._build_code_diff_text(self.code_artifacts)
                            
                        current_output = await self._run_coder_revision(
                            current_output, rejection_summary, coder_agent_type, task_context, model_name
                        )
                        
                        # Increment allowed rounds so we have room to run another iteration
                        max_allowed_rounds = last_log.round_number + 2
                        start_round = last_log.round_number + 1
                        
                        # Wipe the block verdict in memory so it doesn't trigger the skip below
                        last_log.consensus = ConsensusVerdict.NEEDS_REVISION
                        
                    # If previous debate already finished and no override, just return its cached result
                    if last_log.consensus in (ConsensusVerdict.APPROVED, ConsensusVerdict.BLOCKED, ConsensusVerdict.MAX_ROUNDS_EXCEEDED):
                        return json.loads(last_log.coder_output_json) if last_log.coder_output_json else current_output
                        
                    # Reconstruct history for visibility and reporting
                    for log in existing_logs:
                        verdicts = json.loads(log.reviewer_verdicts_json) if log.reviewer_verdicts_json else []
                        revs = [ReviewerResult(**v) for v in verdicts]
                        self.debate_history.append(DebateRound(
                            round_number=log.round_number,
                            coder_output=json.loads(log.coder_output_json) if log.coder_output_json else {},
                            reviews=revs,
                            consensus_reached=log.consensus_reached,
                            revision_prompt=log.revision_prompt,
                            duration_ms=log.duration_ms
                        ))
                    
                    # Restore the EXACT state (Coder output + Sandbox Artifacts) from the moment of the crash
                    if last_log.coder_output_json:
                        current_output = json.loads(last_log.coder_output_json)
                    if hasattr(last_log, "code_artifacts_json") and last_log.code_artifacts_json:
                        self.code_artifacts = json.loads(last_log.code_artifacts_json)
                        self.code_diff_text = self._build_code_diff_text(self.code_artifacts)
                        
                    start_round = last_log.round_number + 1
                    
                    if start_round <= MAX_DEBATE_ROUNDS:
                        await self._broadcast(mission_id, "debate_recover",
                            f"🔄 CRASH RECOVERY: Resuming debate pipeline from round {start_round}...")
            except Exception as e:
                print(f"[ConsensusEngine] Failed to recover checkpoint: {e}")
        
        max_allowed_rounds = locals().get("max_allowed_rounds", MAX_DEBATE_ROUNDS)
        for round_num in range(start_round, max_allowed_rounds + 1):
            start_time = time.time()
            
            # INTELLIGENCE LAYER: Dry-Run Simulation Mode
            await self._broadcast(mission_id, 'debate_round', f'[SIMULATION] Round {round_num}: Spawning isolated sandbox for Dry-Run Compilation...')
            dry_run_success, dry_run_feedback = await self._run_dry_run_simulation(task_context, self.code_artifacts)
            
            if not dry_run_success:
                await self._broadcast(mission_id, 'debate_round', f'[SIMULATION] Dry-Run FAILED. Bypassing reviewers and forcing Coder revision.')
                simulation_result = ReviewerResult(
                    agent_type='SYSTEM_SIMULATOR',
                    agent_name='DryRunSandbox',
                    verdict=ReviewVerdict.REJECTED.value,
                    findings=['Code failed to compile or broke existing tests during dry-run simulation.'],
                    feedback=dry_run_feedback,
                    severity='CRITICAL'
                )
                reviews = [simulation_result]
            else:
                await self._broadcast(mission_id, 'debate_round', f'[SIMULATION] Dry-Run Passed. Sending to reviewers...')
                reviews = await self._run_reviewers(current_output, coder_agent_type, task_context, model_name, mission_id, round_num)
            
            # ── GAP 4: PEER-TO-PEER ALIGNMENT ──
            # If anyone rejected, force them into a room together to talk it out before proceeding
            initial_all_approved = all(
                r.verdict in (ReviewVerdict.APPROVED.value, ReviewVerdict.APPROVED_WITH_NOTES.value) 
                for r in reviews
            )
            
            if not initial_all_approved and len(reviews) > 1:
                await self._broadcast(mission_id, "debate_alignment",
                    f"🤝 Conflict detected. Initiating Peer-to-Peer alignment sync between reviewers...")
                reviews = await self._run_reviewer_alignment(
                    reviews, current_output, task_context, model_name, mission_id, round_num)
            
            duration_ms = int((time.time() - start_time) * 1000)
            
            # ── Evaluate FINAL consensus ──
            all_approved = all(
                r.verdict in (ReviewVerdict.APPROVED.value, ReviewVerdict.APPROVED_WITH_NOTES.value) 
                for r in reviews
            )
            has_critical = any(r.severity == "CRITICAL" for r in reviews if r.verdict == ReviewVerdict.REJECTED.value)
            
            debate_round = DebateRound(
                round_number=round_num,
                coder_output=current_output,
                reviews=reviews,
                consensus_reached=all_approved,
                duration_ms=duration_ms
            )
            
            # ── Persist to database ──
            await self._persist_round(task_id, mission_id, coder_agent_type, debate_round)
            
            # ── If critical rejection, halt immediately ──
            if has_critical:
                await self._broadcast(mission_id, "debate_blocked",
                    f"🚨 CRITICAL SECURITY FINDING — Pipeline BLOCKED. Human review required.")
                debate_round.consensus_reached = False
                self.debate_history.append(debate_round)
                current_output["_debate"] = self._build_debate_summary("BLOCKED")
                return current_output
            
            # ── If all approved, we're done! ──
            if all_approved:
                await self._broadcast(mission_id, "debate_consensus",
                    f"✅ CONSENSUS REACHED in round {round_num} — All reviewers approve!",
                    {"round": round_num, "reviewers": [r.agent_name for r in reviews]})
                self.debate_history.append(debate_round)
                current_output["_debate"] = self._build_debate_summary("APPROVED")
                return current_output
            
            # ── Compile rejection feedback ──
            rejections = [r for r in reviews if r.verdict == ReviewVerdict.REJECTED.value]
            rejection_summary = self._compile_rejection_feedback(rejections)
            debate_round.revision_prompt = rejection_summary
            self.debate_history.append(debate_round)
            
            for rej in rejections:
                await self._broadcast(mission_id, "debate_rejection",
                    f"❌ {rej.agent_name} ({rej.agent_type}) REJECTED: {rej.feedback[:200]}",
                    {"agent": rej.agent_name, "findings": rej.findings[:5]})
            
            # ── If this is the last round, don't try to revise ──
            if round_num == max_allowed_rounds:
                await self._broadcast(mission_id, "debate_max_rounds",
                    f"⚠️ Max debate rounds ({max_allowed_rounds}) reached. Delivering best-effort result.")
                current_output["_debate"] = self._build_debate_summary("MAX_ROUNDS_EXCEEDED", max_rounds=max_allowed_rounds)
                return current_output
            
            # ── Send coder back to revise ──
            await self._broadcast(mission_id, "debate_revision",
                f"🔄 Sending {coder_agent_type.value} back to revise based on {len(rejections)} rejection(s)...")
            
            current_output = await self._run_coder_revision(
                current_output, rejection_summary, coder_agent_type, task_context, model_name
            )
        
        current_output["_debate"] = self._build_debate_summary("MAX_ROUNDS_EXCEEDED", max_rounds=max_allowed_rounds)
        return current_output


    async def _run_dry_run_simulation(self, task_context: dict, code_artifacts: List[Dict[str, Any]]) -> tuple[bool, str]:
        """
        Spawns an isolated sandbox to compile/run the code before human or agent review.
        """
        import tempfile
        import subprocess
        import os
        import py_compile
        import shutil
        
        if not code_artifacts:
            return True, "No code to simulate."
            
        write_artifacts = [a for a in code_artifacts if a.get("action") == "write" or a.get("action") is None]
        if not write_artifacts:
            return True, "No write operations found."
            
        temp_dir = tempfile.mkdtemp(prefix="kobits_dryrun_")
        feedback_lines = []
        simulation_passed = True
        
        try:
            for artifact in write_artifacts:
                path = artifact.get("path") or artifact.get("name")
                content = artifact.get("content") or artifact.get("value")
                if not path or content is None:
                    continue
                    
                full_path = os.path.join(temp_dir, path)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(content)
                    
            for artifact in write_artifacts:
                path = artifact.get("path") or artifact.get("name")
                if not path: continue
                full_path = os.path.join(temp_dir, path)
                
                if path.endswith(".py"):
                    try:
                        py_compile.compile(full_path, doraise=True)
                        feedback_lines.append(f"    [PASS] {path}: Syntax OK")
                    except py_compile.PyCompileError as e:
                        simulation_passed = False
                        feedback_lines.append(f"    [FAIL] {path}: Syntax Error -> {e.msg}")
                        
                elif path.endswith(".js") or path.endswith(".ts"):
                    try:
                        result = subprocess.run(["node", "--check", full_path], capture_output=True, text=True, timeout=5)
                        if result.returncode != 0:
                            simulation_passed = False
                            feedback_lines.append(f"    [FAIL] {path}: Syntax Error -> {result.stderr.strip()}")
                        else:
                            feedback_lines.append(f"    [PASS] {path}: Syntax OK")
                    except Exception:
                        pass
            
            if simulation_passed:
                return True, "Dry-Run Simulation Passed.\n" + "\n".join(feedback_lines)
            else:
                return False, "Dry-Run Simulation FAILED.\n" + "\n".join(feedback_lines)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    async def _run_reviewers(
        self,
        coder_output: dict,
        coder_agent_type: AgentType,
        task_context: dict,
        model_name: str,
        mission_id: str, round_num: int
    ) -> List[ReviewerResult]:
        """Run all reviewer agents in parallel on the coder's output."""
        
        tasks = []
        for reviewer_type in REVIEWER_PIPELINE:
            tasks.append(
                self._run_single_reviewer(
                    reviewer_type, coder_output, coder_agent_type, 
                    task_context, model_name, mission_id,
                    code_diff_text=self.code_diff_text, round_num=round_num
                )
            )
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        reviews = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                # If a reviewer crashes, treat it as approved-with-notes
                reviews.append(ReviewerResult(
                    agent_type=REVIEWER_PIPELINE[i].value,
                    agent_name=AGENT_REGISTRY[REVIEWER_PIPELINE[i]].name,
                    verdict=ReviewVerdict.APPROVED_WITH_NOTES.value,
                    findings=[f"Reviewer error: {str(result)}"],
                    feedback=f"Review could not be completed: {str(result)}",
                    severity="LOW"
                ))
            else:
                reviews.append(result)
        
        return reviews

    async def _run_single_reviewer(
        self,
        reviewer_type: AgentType,
        coder_output: dict,
        coder_agent_type: AgentType,
        task_context: dict,
        model_name: str,
        mission_id: str,
        code_diff_text: str = "", round_num: int = 1
    ) -> ReviewerResult:
        """Run one reviewer agent and return its verdict."""
        from sqlalchemy import select
        from backend.models.agent import DAGNodeCache
        import json
        import asyncio
        import uuid
        
        lock_key = f"{self.task_id}_{round_num}_{reviewer_type.value}"
        
        # Avoid race condition when creating the lock
        lock = _dag_node_locks.get(lock_key)
        if lock is None:
            lock = asyncio.Lock()
            _dag_node_locks.setdefault(lock_key, lock)
            lock = _dag_node_locks[lock_key]
            
        async with lock:
            # 🚀 DAG NODE CACHE CHECK (Idempotency) 🚀
            stmt = select(DAGNodeCache).where(
                DAGNodeCache.task_id == self.task_id,
                DAGNodeCache.round_number == round_num,
                DAGNodeCache.node_id == reviewer_type.value
            )
            cached_node = (await self.db.execute(stmt)).scalars().first()
            if cached_node:
                try:
                    cached_data = json.loads(cached_node.result_json)
                    if not isinstance(cached_data, dict) or "verdict" not in cached_data:
                        raise ValueError("Invalid cache structure")
                        
                    await self._broadcast(mission_id, "reviewer_cache",
                        f"⚡ {AGENT_REGISTRY[reviewer_type].name} ({reviewer_type.value}) recovered from cache.")
                    return ReviewerResult(
                        agent_type=reviewer_type.value,
                        agent_name=AGENT_REGISTRY[reviewer_type].name,
                        verdict=cached_data.get("verdict", "APPROVED_WITH_NOTES"),
                        findings=cached_data.get("findings", []),
                        feedback=cached_data.get("feedback", "Recovered from cache"),
                        severity=cached_data.get("severity", "LOW")
                    )
                except Exception as e:
                    print(f"Corrupt cache detected for {reviewer_type.value}, ignoring: {e}")
                    db_lock = _dag_node_locks.get("_db_lock")
                    if db_lock is None:
                        db_lock = asyncio.Lock()
                        _dag_node_locks.setdefault("_db_lock", db_lock)
                        db_lock = _dag_node_locks["_db_lock"]
                    async with db_lock:
                        await self.db.delete(cached_node)
                        await self.db.commit()

            agent_def = AGENT_REGISTRY[reviewer_type]
            
            await self._broadcast(mission_id, "reviewer_start",
                f"🔎 {agent_def.name} ({reviewer_type.value}) is reviewing...")
            
            review_schema = {
                "type": "object",
                "properties": {
                    "verdict": {"type": "string", "enum": ["APPROVED", "REJECTED", "APPROVED_WITH_NOTES"]},
                    "findings": {"type": "array", "items": {"type": "string"}},
                    "feedback": {"type": "string"},
                    "severity": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"]},
                    "specific_issues": {"type": "array", "items": {
                        "type": "object",
                        "properties": {
                            "file": {"type": "string"},
                            "line": {"type": "integer"},
                            "issue": {"type": "string"},
                            "fix": {"type": "string"}
                        }
                    }}
                },
                "required": ["verdict", "findings", "feedback", "severity"]
            }
            
            coder_summary = json.dumps({
                "status": coder_output.get("status"),
                "summary": coder_output.get("summary"),
                "changes": coder_output.get("changes", []),
            }, indent=2, default=str)
            
            code_diff_text = self._build_code_diff_text(self.code_artifacts)
            
            system_prompt = (
                f"You are {agent_def.name}, a {reviewer_type.value} in the Kobits engineering organization.\n"
                f"{agent_def.system_prompt}\n\n"
                "YOUR ROLE IN THIS DEBATE:\n"
                "You are reviewing ACTUAL CODE produced by another agent. You must be RIGOROUS and HONEST.\n"
                "You will receive the ACTUAL FILE CONTENTS that were written — not just a summary.\n"
                "Review the code LINE BY LINE for:\n"
            )
            
            if reviewer_type == AgentType.SECURITY_ENGINEER:
                system_prompt += (
                    "  - SQL injection (string concatenation in queries)\n"
                    "  - XSS vulnerabilities (unescaped user input in HTML)\n"
                    "  - Command injection (user input in shell commands)\n"
                    "  - Path traversal (user input in file paths)\n"
                    "  - Hardcoded secrets/API keys\n"
                    "  - Missing authentication/authorization checks\n"
                    "  - Insecure cryptography\n"
                    "  - SSRF vulnerabilities\n"
                    "  - Insecure deserialization\n"
                )
            elif reviewer_type == AgentType.CODE_REVIEWER:
                system_prompt += (
                    "  - Missing error handling (bare try/except, no error responses)\n"
                    "  - Missing input validation\n"
                    "  - Incorrect logic or bugs\n"
                    "  - Missing type hints\n"
                    "  - Poor naming conventions\n"
                    "  - Dead code or unnecessary complexity\n"
                    "  - Missing edge case handling\n"
                    "  - Violations of DRY principle\n"
                    "  - Missing docstrings on public functions\n"
                )
            
            system_prompt += (
                "\nIMPORTANT: Reference SPECIFIC file paths and line numbers in your findings.\n"
                "If the code has real vulnerabilities or bugs: REJECT it with specific line references.\n"
                "If the code is solid and well-written: APPROVE it.\n"
                "Do NOT rubber-stamp. Your job is to catch problems BEFORE they reach production.\n\n"
                "TOOLS AVAILABLE:\n"
                "  - repository.read: Read any file in the codebase to check imports, dependencies,\n"
                "    or related code that might be affected by the changes.\n"
                "  - repository.search: Search the codebase for patterns to find usages of functions,\n"
                "    related files, or other references.\n"
                "Use these tools when you need to verify that a change doesn't break other files,\n"
                "or to check if an import/dependency exists.\n\n"
                "Return your verdict as JSON matching the schema."
            )
            
            if code_diff_text:
                user_prompt = (
                    f"REVIEW REQUEST\n"
                    f"==============\n"
                    f"Coder Agent: {coder_agent_type.value}\n"
                    f"Task: {json.dumps(task_context.get('task_description', 'No description'), default=str)}\n\n"
                    f"CODER'S SUMMARY:\n{coder_summary}\n\n"
                    f"──────────────────────────────────────────────────────────────────────\n"
                    f"ACTUAL CODE WRITTEN (review this line by line):\n"
                    f"──────────────────────────────────────────────────────────────────────\n"
                    f"{code_diff_text}\n"
                    f"──────────────────────────────────────────────────────────────────────\n\n"
                    f"Review the ACTUAL CODE above. Reference specific files and line numbers."
                )
            else:
                coder_output_str = json.dumps(coder_output, indent=2, default=str)
                user_prompt = (
                    f"REVIEW REQUEST\n"
                    f"==============\n"
                    f"Coder Agent: {coder_agent_type.value}\n"
                    f"Task: {json.dumps(task_context.get('task_description', 'No description'), default=str)}\n\n"
                    f"CODER'S FULL OUTPUT:\n{coder_output_str}\n\n"
                    f"Review the logic and summary."
                )
            
            reviewer_tools = []
            reviewer_tool_executor = None
            if task_context.get('sandbox_id') and task_context.get('project_path') and task_context.get('project_path'):
                from backend.services.tool_registry import ToolRegistry
                
                reviewer_tools = [
                    ToolRegistry.get_tool_def("repository.read"),
                    ToolRegistry.get_tool_def("repository.search")
                ]
                
                async def execute_sandbox_read(tool_name: str, tool_args: dict):
                    if tool_name in ["repository.read", "repository.search"]:
                        from backend.services.sandbox import SandboxManager
                        if tool_name == "repository.read":
                            return await SandboxManager.read_file(task_context.get('sandbox_id'), tool_args.get("path"))
                        elif tool_name == "repository.search":
                            return await SandboxManager.search_code(task_context.get('sandbox_id'), tool_args.get("query"), tool_args.get("path", "."))
                    return await ToolRegistry.execute_tool(tool_name, tool_args, task_context)
                    
                reviewer_tool_executor = execute_sandbox_read
                
            reviewer_inspections = []
            
            async def wrapped_reviewer_tool_executor(tool_name: str, tool_args: dict):
                reviewer_inspections.append({"tool": tool_name, "args": tool_args})
                
                if reviewer_tool_executor:
                    return await reviewer_tool_executor(tool_name, tool_args)
                
                from backend.services.tool_registry import ToolRegistry
                return await ToolRegistry.execute_tool(tool_name, tool_args, task_context)
            
            try:
                result = await self.provider.generate_structured_output(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    schema=review_schema,
                    model=model_name,
                    tools=reviewer_tools,
                    tool_executor=wrapped_reviewer_tool_executor
                )
                
                if not isinstance(result, dict):
                    result = {"verdict": "APPROVED_WITH_NOTES", "findings": ["Could not parse review"], "feedback": str(result), "severity": "LOW"}
                
                result["_inspections"] = reviewer_inspections
                
                # 🚀 Save to DAG NODE CACHE 🚀
                cache_entry = DAGNodeCache(
                    id=str(uuid.uuid4()),
                    task_id=self.task_id,
                    round_number=round_num,
                    node_id=reviewer_type.value,
                    result_json=json.dumps(result)
                )
                
                # Use a global lock to protect the shared AsyncSession from concurrent commits
                db_lock = _dag_node_locks.get("_db_lock")
                if db_lock is None:
                    db_lock = asyncio.Lock()
                    _dag_node_locks.setdefault("_db_lock", db_lock)
                    db_lock = _dag_node_locks["_db_lock"]
                    
                async with db_lock:
                    self.db.add(cache_entry)
                    try:
                        await self.db.commit()
                    except IntegrityError:
                        await self.db.rollback()
                        pass
                
                return ReviewerResult(
                    agent_type=reviewer_type.value,
                    agent_name=agent_def.name,
                    verdict=result.get("verdict", "APPROVED"),
                    findings=result.get("findings", []),
                    feedback=result.get("feedback", ""),
                    severity=result.get("severity", "LOW")
                )
                
            except Exception as e:
                return ReviewerResult(
                    agent_type=reviewer_type.value,
                    agent_name=agent_def.name,
                    verdict=ReviewVerdict.APPROVED_WITH_NOTES.value,
                    findings=[f"Review error: {str(e)}"],
                    feedback=f"Review could not complete: {str(e)}",
                    severity="LOW"
                )

    async def _run_coder_revision(
        self,
        original_output: dict,
        rejection_feedback: str,
        coder_agent_type: AgentType,
        task_context: dict,
        model_name: str,
    ) -> dict:
        """
        Send the coder back to revise based on reviewer feedback.
        
        KEY CHANGE: The coder now has repository.write and repository.read tools.
        It MUST use repository.write to actually rewrite the files with fixes.
        We capture every write and update self.code_artifacts so that round 2
        reviewers see the FIXED code, not the original vulnerable code.
        """
        
        agent_def = AGENT_REGISTRY[coder_agent_type]
        
        # ── Capture tool calls during revision ──
        revision_tool_calls = []
        
        async def revision_tool_executor(tool_name: str, tool_args: dict) -> dict:
            """
            Real tool executor for revision. Runs the tool against the sandbox
            so changes are actually saved to disk for verification.
            """
            revision_tool_calls.append({
                "type": "tool_call",
                "tool": tool_name,
                "args": tool_args,
            })
            
            from backend.services.tool_registry import ToolRegistry
            
            # Execute the tool against the sandbox context
            result = await ToolRegistry.execute_tool(tool_name, tool_args, task_context)
            
            return result
        
        # ── Build revision tools (repository.edit + repository.write + repository.read) ──
        revision_tools = [
            {
                "name": "repository_edit",
                "description": "Surgically edit an existing file by replacing old_string with new_string. Preferred for targeted fixes.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path to modify"},
                        "old_string": {"type": "string", "description": "Exact existing lines to replace"},
                        "new_string": {"type": "string", "description": "New fixed lines to insert"},
                        "replace_all": {"type": "boolean", "default": False}
                    },
                    "required": ["path", "old_string", "new_string"]
                }
            },
            {
                "name": "repository_write",
                "description": "Write the FIXED code to a file. You MUST use this tool to rewrite every file that needs fixing. Provide the COMPLETE fixed file content.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path to write to"},
                        "content": {"type": "string", "description": "The COMPLETE fixed file content"}
                    },
                    "required": ["path", "content"]
                }
            },
            {
                "name": "repository_read",
                "description": "Read the current content of a file to understand what needs fixing.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path to read"}
                    },
                    "required": ["path"]
                }
            }
        ]
        
        response_schema = {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["SUCCESS", "FAILED"]},
                "summary": {"type": "string"},
                "findings": {"type": "array", "items": {"type": "string"}},
                "changes": {"type": "array", "items": {"type": "string"}},
                "recommendations": {"type": "array", "items": {"type": "string"}},
                "risks": {"type": "array", "items": {"type": "string"}},
                "next_actions": {"type": "array", "items": {"type": "string"}},
                "artifacts": {"type": "object"},
                "requires_approval": {"type": "boolean"}
            }
        }
        
        # Show the coder the ACTUAL CODE that was rejected + the feedback
        code_context = self.code_diff_text if self.code_diff_text else ""
        
        system_prompt = (
            f"You are {agent_def.name}, a {coder_agent_type.value} in the Kobits engineering organization.\n"
            f"{agent_def.system_prompt}\n\n"
            "REVISION CONTEXT:\n"
            "Your previous code was REJECTED by the security and code review team.\n"
            "You MUST fix every issue listed in the feedback below.\n\n"
            "CRITICAL INSTRUCTIONS:\n"
            "1. Use the repository.write tool to REWRITE each file that needs fixing.\n"
            "2. Provide the COMPLETE fixed file content — not just the changed lines.\n"
            "3. Address EVERY finding from EVERY reviewer.\n"
            "4. After writing all fixed files, return a JSON summary of what you changed.\n"
            "5. Do NOT just describe the fix — actually write the fixed code using repository.write.\n"
        )
        
        user_prompt = (
            f"YOUR CODE (REJECTED):\n"
            f"{code_context}\n\n"
            f"REVIEWER FEEDBACK (FIX ALL OF THESE):\n"
            f"{rejection_feedback}\n\n"
            f"Use repository.write to rewrite each file with the fixes applied. "
            f"Then return a summary of your changes."
        )
        
        try:
            result = await self.provider.generate_structured_output(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=response_schema,
                model=model_name,
                tools=revision_tools,
                tool_executor=revision_tool_executor
            )
            
            if not isinstance(result, dict):
                result = {"status": "FAILED", "summary": f"Revision failed: {str(result)}"}
            
            # ── Extract new code artifacts from revision tool calls ──
            new_artifacts = self.extract_code_artifacts(revision_tool_calls)
            
            if new_artifacts:
                # Update self.code_artifacts with the new versions
                # Replace files that were rewritten, keep files that weren't changed
                new_paths = {a["path"] for a in new_artifacts}
                kept_artifacts = [a for a in self.code_artifacts if a["path"] not in new_paths]
                self.code_artifacts = new_artifacts + kept_artifacts
                
                # Rebuild the code diff text so round 2 reviewers see the FIXED code
                self.code_diff_text = self._build_code_diff_text(self.code_artifacts)
                
                result["_revision_files_written"] = [a["path"] for a in new_artifacts]
                result["_revision_tool_calls"] = len(revision_tool_calls)
            else:
                # Coder didn't use repository.write — flag this
                result["_revision_warning"] = "Coder did not use repository.write during revision"
            
            return result
            
        except Exception as e:
            return {"status": "FAILED", "summary": f"Revision error: {str(e)}"}

    def _compile_rejection_feedback(self, rejections: List[ReviewerResult]) -> str:
        """Compile all rejection feedback into a structured prompt for the coder."""
        parts = []
        for i, rej in enumerate(rejections, 1):
            parts.append(
                f"--- REJECTION #{i} from {rej.agent_name} ({rej.agent_type}) ---\n"
                f"Severity: {rej.severity}\n"
                f"Feedback: {rej.feedback}\n"
                f"Findings:\n" + "\n".join(f"  • {f}" for f in rej.findings)
            )
        return "\n\n".join(parts)

    def _build_debate_summary(self, final_verdict: str, max_rounds: int = MAX_DEBATE_ROUNDS) -> dict:
        """Build a summary dict of the entire debate for attaching to the result."""
        return {
            "final_verdict": final_verdict,
            "total_rounds": len(self.debate_history),
            "max_rounds": max_rounds,
            "rounds": [
                {
                    "round": r.round_number,
                    "consensus_reached": r.consensus_reached,
                    "reviews": [rv.to_dict() for rv in r.reviews],
                    "duration_ms": r.duration_ms,
                }
                for r in self.debate_history
            ]
        }

    async def _persist_round(
        self, task_id: str, mission_id: str, 
        coder_agent_type: AgentType, debate_round: DebateRound
    ):
        """Save a debate round to the database."""
        if not self.db:
            return
        
        try:
            from backend.models.debate_log import DebateLog, ConsensusVerdict
            
            if debate_round.consensus_reached:
                verdict = ConsensusVerdict.APPROVED
            elif any(r.severity == "CRITICAL" and r.verdict == "REJECTED" for r in debate_round.reviews):
                verdict = ConsensusVerdict.BLOCKED
            elif debate_round.round_number >= MAX_DEBATE_ROUNDS and not debate_round.consensus_reached:
                verdict = ConsensusVerdict.MAX_ROUNDS_EXCEEDED
            else:
                verdict = ConsensusVerdict.NEEDS_REVISION
            
            log_entry = DebateLog(
                id=str(uuid.uuid4()),
                task_id=task_id,
                mission_id=mission_id,
                round_number=debate_round.round_number,
                coder_agent_type=coder_agent_type.value,
                coder_output_json=json.dumps(debate_round.coder_output, default=str),
                code_artifacts_json=json.dumps(self.code_artifacts, default=str),
                reviewer_verdicts_json=json.dumps(
                    [r.to_dict() for r in debate_round.reviews], default=str
                ),
                consensus=verdict,
                revision_prompt=debate_round.revision_prompt,
                consensus_reached=debate_round.consensus_reached,
                duration_ms=debate_round.duration_ms
            )
            self.db.add(log_entry)
            await self.db.commit()
        except Exception as e:
            # Don't let persistence failures break the pipeline
            print(f"[ConsensusEngine] Failed to persist debate round: {e}")

    def _build_code_diff_text(self, code_artifacts: List[Dict[str, Any]]) -> str:
        """
        Format code artifacts into a human-readable, line-numbered diff
        that reviewers can reference by file path and line number.
        
        Example output:
        
        ── FILE: api/routes.py (WRITTEN) ──────────────────────
         1 │ from fastapi import APIRouter
         2 │ import sqlite3
         3 │ 
         4 │ @router.post("/login")
         5 │ def login(username: str, password: str):
         6 │     query = f"SELECT * FROM users WHERE name='{username}'"  ← SQL INJECTION
         ...
        """
        if not code_artifacts:
            return ""
        
        parts = []
        total_lines = 0
        MAX_LINES_PER_FILE = 200  # Prevent token explosion
        MAX_TOTAL_CHARS = 15000   # Hard limit on total diff size
        
        for artifact in code_artifacts:
            path = artifact.get("path", "unknown")
            content = artifact.get("content", "")
            action = artifact.get("action", "write").upper()
            
            if not content:
                parts.append(f"── FILE: {path} ({action}) ── [empty file]\n")
                continue
            
            lines = content.split("\n")
            truncated = False
            if len(lines) > MAX_LINES_PER_FILE:
                lines = lines[:MAX_LINES_PER_FILE]
                truncated = True
            
            # Build line-numbered output
            numbered_lines = []
            for i, line in enumerate(lines, 1):
                numbered_lines.append(f"{i:4d} │ {line}")
            
            header = f"── FILE: {path} ({action}) ── {len(lines)} lines ──"
            file_block = header + "\n" + "\n".join(numbered_lines)
            if truncated:
                file_block += f"\n     │ ... [truncated at {MAX_LINES_PER_FILE} lines, {len(content.split(chr(10)))} total]"
            
            parts.append(file_block)
            total_lines += len(lines)
        
        result = "\n\n".join(parts)
        
        # Hard truncation safety
        if len(result) > MAX_TOTAL_CHARS:
            result = result[:MAX_TOTAL_CHARS] + "\n\n... [CODE DIFF TRUNCATED — too large for review context]"
        
        return result

    @staticmethod
    def extract_code_artifacts(trace_logs: List[dict]) -> List[Dict[str, Any]]:
        """
        Extract actual file contents from the coder agent's tool call trace_logs.
        
        Looks for:
        - repository.write calls → files the coder CREATED or MODIFIED (with full content)
        - repository.read calls → files the coder READ (context it used)
        
        Returns a list of {path, content, action} dicts — the ACTUAL CODE,
        not a summary.
        
        This is what makes Kobits review REAL code instead of descriptions.
        """
        artifacts = []
        seen_paths = set()
        
        for log_entry in trace_logs:
            # Handle tool call entries
            if log_entry.get("type") == "tool_call":
                tool_name = log_entry.get("tool", log_entry.get("name", ""))
                tool_args = log_entry.get("args", log_entry.get("input", {}))
                tool_result = log_entry.get("result", log_entry.get("output", {}))
                
                if not isinstance(tool_args, dict):
                    try:
                        tool_args = json.loads(tool_args) if isinstance(tool_args, str) else {}
                    except:
                        tool_args = {}
                
                # repository.write ─ the coder WROTE this file
                if tool_name in ("repository.write", "repository_write"):
                    path = tool_args.get("path", "unknown")
                    content = tool_args.get("content", "")
                    if content:
                        artifacts = [a for a in artifacts if a.get("path") != path]
                        artifacts.append({
                            "path": path,
                            "content": content,
                            "action": "write"
                        })
                        seen_paths.add(path)

                # repository.edit ─ the coder SURGICALLY EDITED this file
                elif tool_name in ("repository.edit", "repository_edit"):
                    path = tool_args.get("path", "unknown")
                    content = ""
                    if isinstance(tool_result, dict) and (tool_result.get("content") or tool_result.get("snippet")):
                        content = tool_result.get("content") or tool_result.get("snippet", "")
                    else:
                        old_s = tool_args.get("old_string", "")
                        new_s = tool_args.get("new_string", "")
                        if new_s:
                            content = f"--- SURGICAL PATCH ({path}) ---\n- {old_s}\n+ {new_s}"
                    if content:
                        artifacts = [a for a in artifacts if a.get("path") != path]
                        artifacts.append({
                            "path": path,
                            "content": content,
                            "action": "edit"
                        })
                        seen_paths.add(path)

                # repository.delete ─ the coder DELETED this file
                elif tool_name in ("repository.delete", "repository_delete"):
                    path = tool_args.get("path", "unknown")
                    if path and path != "unknown":
                        artifacts = [a for a in artifacts if a.get("path") != path]
                        artifacts.append({
                            "path": path,
                            "content": f"--- FILE DELETED ({path}) ---",
                            "action": "delete"
                        })
                        seen_paths.add(path)
                
                # repository.read ─ the coder READ this file (context)
                elif tool_name in ("repository.read", "repository_read"):
                    path = tool_args.get("path", "unknown")
                    # Get the content from the result
                    if isinstance(tool_result, dict):
                        content = tool_result.get("content", "")
                    elif isinstance(tool_result, str):
                        content = tool_result
                    else:
                        content = ""
                    
                    if content and path not in seen_paths:
                        artifacts.append({
                            "path": path,
                            "content": content,
                            "action": "read"
                        })
                        seen_paths.add(path)

            elif log_entry.get("type") == "tool_result":
                tool_name = log_entry.get("tool", log_entry.get("name", ""))
                tool_result = log_entry.get("result", log_entry.get("output", {}))
                if tool_name in ("repository.edit", "repository_edit") and isinstance(tool_result, dict):
                    path = tool_result.get("path", "unknown")
                    content = tool_result.get("content") or tool_result.get("snippet", "")
                    if path and content:
                        artifacts = [a for a in artifacts if a.get("path") != path]
                        artifacts.append({
                            "path": path,
                            "content": content,
                            "action": "edit"
                        })
                        seen_paths.add(path)
            
            # Handle nested tool_calls array format (some providers log differently)
            elif log_entry.get("tool_calls"):
                for tc in log_entry.get("tool_calls", []):
                    tc_name = tc.get("function", {}).get("name", tc.get("name", ""))
                    tc_args = tc.get("function", {}).get("arguments", tc.get("args", {}))
                    
                    if isinstance(tc_args, str):
                        try:
                            tc_args = json.loads(tc_args)
                        except:
                            tc_args = {}
                    
                    if tc_name in ("repository.write", "repository_write"):
                        path = tc_args.get("path", "unknown")
                        content = tc_args.get("content", "")
                        if content and path not in seen_paths:
                            artifacts.append({
                                "path": path,
                                "content": content,
                                "action": "write"
                            })
                            seen_paths.add(path)
                    elif tc_name in ("repository.edit", "repository_edit"):
                        path = tc_args.get("path", "unknown")
                        new_s = tc_args.get("new_string", "")
                        if new_s and path not in seen_paths:
                            artifacts.append({
                                "path": path,
                                "content": new_s,
                                "action": "edit"
                            })
                            seen_paths.add(path)
        
        return artifacts

