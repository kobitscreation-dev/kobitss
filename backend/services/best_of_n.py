"""
BestOfNExplorer — Best-of-N Candidate Patch Exploration Engine.

Explores N parallel candidate patch trajectories for a problem:
1. Samples alternative patches in isolated git checkpoints from a clean base checkpoint.
2. Extracts git diff statistics (additions, deletions, files changed, diff parsimony).
3. Scores each candidate against the reproducer test and regression test suite.
4. Ranks candidates and selects the cleanest, minimal passing patch (SWE-bench gold standard).
5. Retains the winning patch in the sandbox worktree, or rolls back to the clean base if none pass.
"""

import os
import sys
import shlex
import inspect
import logging
import subprocess
import asyncio
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from typing import Optional, Callable, Any, Union, List, Tuple, Dict, Set

from backend.services.sandbox_rollback import SandboxRollbackManager
from backend.services.reproducer_engine import ReproducerEngine, ReproducerExecution

logger = logging.getLogger("kobits.best_of_n")


@dataclass
class PatchCandidate:
    """A single candidate patch generated and evaluated during Best-of-N exploration."""
    candidate_id: int
    checkpoint_sha: str
    reproducer_passed: bool
    regression_passed: bool
    additions: int
    deletions: int
    total_lines_changed: int
    files_changed: list[str]
    diff_text: str
    score: float
    status: str  # "PASSED" | "REPRODUCER_FAILED" | "REGRESSION_FAILED" | "GENERATION_ERROR"
    reproducer_output: str = ""
    regression_output: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class BestOfNResult:
    """Evaluation summary of all evaluated candidates and the selected winner."""
    candidates: list[PatchCandidate]
    selected_candidate: Optional[PatchCandidate]
    base_checkpoint_sha: str
    success: bool
    selection_reason: str


class BestOfNExplorer:
    """
    Tier-1 Best-of-N Candidate Patch Exploration and Selection Engine.
    """

    @staticmethod
    def score_candidate(
        reproducer_passed: bool,
        regression_passed: bool,
        additions: int,
        deletions: int,
        files_changed_count: int,
        duration_seconds: float = 0.0,
    ) -> float:
        """
        Parsimonious scoring algorithm favoring minimal, robust, regression-free patches:
        - Fails reproducer: 0.0 (Unacceptable)
        - Passes reproducer but fails regressions: 10.0 (Regression breaker)
        - Empty patch with 0 modifications: 50.0 (No-op fix, heavily penalized)
        - Passes both: 1000 base - diff bloat penalty - file churn penalty - runtime penalty
        """
        if not reproducer_passed:
            return 0.0
        if not regression_passed:
            return 10.0

        total_diff = additions + deletions
        if total_diff == 0 and files_changed_count == 0:
            return 50.0

        base_score = 1000.0
        # Penalize unnecessary line churn (favoring surgical fixes)
        diff_penalty = min(total_diff * 0.5, 300.0)
        # Penalize sprawling changes across many files
        file_penalty = min(files_changed_count * 10.0, 100.0)
        # Minor penalty for slow test runs
        time_penalty = min(duration_seconds * 0.1, 50.0)

        calculated = base_score - diff_penalty - file_penalty - time_penalty
        return max(round(calculated, 2), 100.0)

    @classmethod
    def extract_diff_stats(
        cls, sandbox_dir: str, base_sha: str
    ) -> tuple[int, int, list[str], str]:
        """
        Extracts additions, deletions, touched files, and unified diff text against base_sha.
        """
        if not SandboxRollbackManager._is_git_repo(sandbox_dir):
            return 0, 0, [], ""

        additions = 0
        deletions = 0
        files: list[str] = []
        diff_text = ""

        try:
            # 1. Numstat
            res_stat = subprocess.run(
                ["git", "diff", "--numstat", base_sha, "HEAD"],
                cwd=sandbox_dir,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if res_stat.returncode == 0:
                for line in res_stat.stdout.splitlines():
                    parts = line.split("\t")
                    if len(parts) >= 3:
                        try:
                            a = int(parts[0]) if parts[0] != "-" else 0
                            d = int(parts[1]) if parts[1] != "-" else 0
                            additions += a
                            deletions += d
                        except ValueError:
                            pass
                        files.append(parts[2].strip())

            # 2. Unified diff
            res_diff = subprocess.run(
                ["git", "diff", base_sha, "HEAD"],
                cwd=sandbox_dir,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if res_diff.returncode == 0:
                diff_text = res_diff.stdout

        except Exception as e:
            logger.warning(f"Error computing diff stats: {e}")

        return additions, deletions, files, diff_text

    @classmethod
    async def explore_and_select(
        cls,
        sandbox_dir: str,
        reproducer_script: str,
        candidate_generators: list[Callable[[int, str], Any]],
        regression_cmd: Optional[Union[str, list[str]]] = None,
        base_checkpoint: Optional[str] = None,
        timeout_per_candidate: int = 30,
        parallel: Optional[bool] = None,
        turbo: Optional[bool] = None,
        concurrency_limit: Optional[int] = None,
    ) -> BestOfNResult:
        """
        Runs Best-of-N candidate exploration:
        1. Establishes base checkpoint.
        2. Supports concurrent speculative rollouts via asyncio.gather() or sequential fallback.
        3. Commits candidates, tests reproducer, tests regressions, and scores diff.
        4. Selects highest-scoring passing candidate (minimal parsimonious diff).
        5. If no candidate passes, rolls back to base checkpoint.
        """
        if not os.path.isdir(sandbox_dir):
            return BestOfNResult(
                candidates=[],
                selected_candidate=None,
                base_checkpoint_sha="",
                success=False,
                selection_reason=f"Sandbox directory does not exist: {sandbox_dir}",
            )

        # 1. Establish clean base checkpoint
        base_sha = base_checkpoint or SandboxRollbackManager.create_checkpoint(
            sandbox_dir, step_name="best_of_n_base"
        )
        if not base_sha:
            base_sha = SandboxRollbackManager.get_current_sha(sandbox_dir) or "unknown"

        # Preserve reproducer script content in case of git clean -fdx during rollbacks
        abs_repro_path = (
            reproducer_script
            if os.path.isabs(reproducer_script)
            else os.path.abspath(os.path.join(sandbox_dir, reproducer_script))
        )
        repro_content = None
        if os.path.exists(abs_repro_path):
            try:
                with open(abs_repro_path, "r", encoding="utf-8") as rf:
                    repro_content = rf.read()
            except Exception:
                pass

        # Check whether parallel speculative scaling is active
        from backend.core.config import settings
        use_parallel = (
            parallel is True
            or turbo is True
            or getattr(settings, "KOBITS_SPECULATIVE_EXEC", False)
            or getattr(settings, "KOBITS_TURBO_MODE", False)
            or os.environ.get("KOBITS_SPECULATIVE_EXEC") == "1"
            or os.environ.get("KOBITS_TURBO_MODE") == "1"
        ) and len(candidate_generators) > 1

        if use_parallel:
            limit = concurrency_limit or getattr(settings, "KOBITS_PARALLEL_ROLLOUT_LIMIT", 4)
            sem = asyncio.Semaphore(limit)

            async def _evaluate_parallel_candidate(idx: int, generator: Callable[[int, str], Any]) -> Tuple[PatchCandidate, str]:
                cand_id = idx + 1
                async with sem:
                    cand_tmp = tempfile.mkdtemp(prefix=f"kobits_spec_cand_{cand_id}_")
                    try:
                        shutil.copytree(
                            sandbox_dir,
                            cand_tmp,
                            dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(".venv", "venv", "node_modules", ".pytest_cache", "__pycache__"),
                        )
                        if base_sha != "unknown" and SandboxRollbackManager._is_git_repo(cand_tmp):
                            SandboxRollbackManager.rollback(cand_tmp, base_sha)

                        cand_repro = (
                            reproducer_script
                            if os.path.isabs(reproducer_script)
                            else os.path.join(cand_tmp, reproducer_script)
                        )
                        if repro_content is not None and not os.path.exists(cand_repro):
                            os.makedirs(os.path.dirname(cand_repro), exist_ok=True)
                            with open(cand_repro, "w", encoding="utf-8") as rf:
                                rf.write(repro_content)

                        gen_error = None
                        try:
                            if inspect.iscoroutinefunction(generator):
                                await generator(cand_id, cand_tmp)
                            else:
                                generator(cand_id, cand_tmp)
                        except Exception as e:
                            gen_error = str(e)
                            logger.warning(f"Parallel candidate {cand_id} generator error: {gen_error}")

                        cand_sha = SandboxRollbackManager.create_checkpoint(
                            cand_tmp, step_name=f"candidate_{cand_id}_patch"
                        ) or base_sha

                        if gen_error:
                            return PatchCandidate(
                                candidate_id=cand_id,
                                checkpoint_sha=cand_sha,
                                reproducer_passed=False,
                                regression_passed=False,
                                additions=0,
                                deletions=0,
                                total_lines_changed=0,
                                files_changed=[],
                                diff_text="",
                                score=0.0,
                                status="GENERATION_ERROR",
                                metadata={"error": gen_error},
                            ), cand_tmp

                        additions, deletions, files, diff_text = cls.extract_diff_stats(cand_tmp, base_sha)
                        repro_res = ReproducerEngine.execute_script(
                            cand_tmp, reproducer_script, timeout=timeout_per_candidate
                        )
                        reproducer_passed = (repro_res.exit_code == 0)

                        regression_passed = True
                        reg_output = ""
                        if reproducer_passed and regression_cmd:
                            if isinstance(regression_cmd, list):
                                cmd = regression_cmd
                            else:
                                try:
                                    cmd = shlex.split(regression_cmd, posix=(os.name != "nt"))
                                except Exception:
                                    cmd = regression_cmd.split()
                            try:
                                env = os.environ.copy()
                                existing_py = env.get("PYTHONPATH", "")
                                env["PYTHONPATH"] = f"{cand_tmp}{os.pathsep}{existing_py}" if existing_py else cand_tmp
                                res_reg = subprocess.run(
                                    cmd,
                                    cwd=cand_tmp,
                                    capture_output=True,
                                    text=True,
                                    timeout=timeout_per_candidate * 2,
                                    env=env,
                                )
                                regression_passed = (res_reg.returncode == 0)
                                reg_output = res_reg.stdout + "\n" + res_reg.stderr
                            except Exception as reg_err:
                                regression_passed = False
                                reg_output = str(reg_err)

                        if not reproducer_passed:
                            status = "REPRODUCER_FAILED"
                        elif not regression_passed:
                            status = "REGRESSION_FAILED"
                        else:
                            status = "PASSED"

                        score = cls.score_candidate(
                            reproducer_passed=reproducer_passed,
                            regression_passed=regression_passed,
                            additions=additions,
                            deletions=deletions,
                            files_changed_count=len(files),
                            duration_seconds=repro_res.duration_seconds,
                        )

                        cand = PatchCandidate(
                            candidate_id=cand_id,
                            checkpoint_sha=cand_sha,
                            reproducer_passed=reproducer_passed,
                            regression_passed=regression_passed,
                            additions=additions,
                            deletions=deletions,
                            total_lines_changed=additions + deletions,
                            files_changed=files,
                            diff_text=diff_text,
                            score=score,
                            status=status,
                            reproducer_output=repro_res.stdout + "\n" + repro_res.stderr,
                            regression_output=reg_output,
                            metadata={"duration_seconds": repro_res.duration_seconds, "parallel": True},
                        )
                        return cand, cand_tmp
                    except Exception as e:
                        logger.error(f"Candidate {cand_id} unexpected error: {e}")
                        return PatchCandidate(
                            candidate_id=cand_id,
                            checkpoint_sha=base_sha,
                            reproducer_passed=False,
                            regression_passed=False,
                            additions=0,
                            deletions=0,
                            total_lines_changed=0,
                            files_changed=[],
                            diff_text="",
                            score=0.0,
                            status="GENERATION_ERROR",
                            metadata={"error": str(e)},
                        ), cand_tmp

            parallel_outcomes = await asyncio.gather(
                *[_evaluate_parallel_candidate(idx, gen) for idx, gen in enumerate(candidate_generators)]
            )
            candidates = [co[0] for co in parallel_outcomes]
            tmp_map = {co[0].candidate_id: co[1] for co in parallel_outcomes}

            passing_candidates = [c for c in candidates if c.status == "PASSED"]
            if passing_candidates:
                winner = max(passing_candidates, key=lambda c: c.score)
                win_tmp = tmp_map.get(winner.candidate_id)
                if win_tmp and os.path.isdir(win_tmp):
                    if base_sha != "unknown" and SandboxRollbackManager._is_git_repo(sandbox_dir):
                        SandboxRollbackManager.rollback(sandbox_dir, base_sha)
                    for f_rel in winner.files_changed:
                        src_f = os.path.join(win_tmp, f_rel)
                        dst_f = os.path.join(sandbox_dir, f_rel)
                        if os.path.exists(src_f):
                            if os.path.isdir(src_f):
                                shutil.copytree(src_f, dst_f, dirs_exist_ok=True)
                            else:
                                os.makedirs(os.path.dirname(dst_f), exist_ok=True)
                                shutil.copy2(src_f, dst_f)
                        else:
                            # File was deleted by the winning candidate
                            if os.path.isfile(dst_f) or os.path.islink(dst_f):
                                try:
                                    os.remove(dst_f)
                                except Exception:
                                    pass
                            elif os.path.isdir(dst_f):
                                shutil.rmtree(dst_f, ignore_errors=True)
                    win_sha = SandboxRollbackManager.create_checkpoint(
                        sandbox_dir, step_name=f"parallel_winner_candidate_{winner.candidate_id}"
                    ) or winner.checkpoint_sha
                    winner.checkpoint_sha = win_sha

                for t_dir in tmp_map.values():
                    shutil.rmtree(t_dir, ignore_errors=True)

                return BestOfNResult(
                    candidates=candidates,
                    selected_candidate=winner,
                    base_checkpoint_sha=base_sha,
                    success=True,
                    selection_reason=(
                        f"Parallel speculative candidate {winner.candidate_id} selected: passed reproducer and regression tests with "
                        f"cleanest diff ({winner.total_lines_changed} lines changed across {len(winner.files_changed)} files, Score: {winner.score})."
                    ),
                )
            else:
                for t_dir in tmp_map.values():
                    shutil.rmtree(t_dir, ignore_errors=True)
                if base_sha != "unknown":
                    SandboxRollbackManager.rollback(sandbox_dir, base_sha)
                return BestOfNResult(
                    candidates=candidates,
                    selected_candidate=None,
                    base_checkpoint_sha=base_sha,
                    success=False,
                    selection_reason="All parallel candidate patches failed verification. Workspace rolled back to base.",
                )

        candidates: list[PatchCandidate] = []

        for idx, generator in enumerate(candidate_generators):
            cand_id = idx + 1
            logger.info(f"Evaluating candidate patch {cand_id}/{len(candidate_generators)}...")

            # Roll back to clean base before generating candidate
            if base_sha != "unknown":
                SandboxRollbackManager.rollback(sandbox_dir, base_sha)

            # Restore reproducer script if it was removed by rollback
            if repro_content is not None and not os.path.exists(abs_repro_path):
                try:
                    os.makedirs(os.path.dirname(abs_repro_path), exist_ok=True)
                    with open(abs_repro_path, "w", encoding="utf-8") as rf:
                        rf.write(repro_content)
                except Exception:
                    pass

            # Generate candidate patch
            gen_error = None
            try:
                if inspect.iscoroutinefunction(generator):
                    await generator(cand_id, sandbox_dir)
                else:
                    generator(cand_id, sandbox_dir)
            except Exception as e:
                gen_error = str(e)
                logger.warning(f"Candidate {cand_id} generator error: {gen_error}")

            # Checkpoint candidate changes
            cand_sha = SandboxRollbackManager.create_checkpoint(
                sandbox_dir, step_name=f"candidate_{cand_id}_patch"
            ) or base_sha

            if gen_error:
                candidate = PatchCandidate(
                    candidate_id=cand_id,
                    checkpoint_sha=cand_sha,
                    reproducer_passed=False,
                    regression_passed=False,
                    additions=0,
                    deletions=0,
                    total_lines_changed=0,
                    files_changed=[],
                    diff_text="",
                    score=0.0,
                    status="GENERATION_ERROR",
                    reproducer_output="",
                    regression_output="",
                    metadata={"error": gen_error},
                )
                candidates.append(candidate)
                continue

            # Compute diff metrics against base
            additions, deletions, files, diff_text = cls.extract_diff_stats(sandbox_dir, base_sha)

            # Verify against reproducer
            repro_res = ReproducerEngine.execute_script(
                sandbox_dir, reproducer_script, timeout=timeout_per_candidate
            )
            reproducer_passed = (repro_res.exit_code == 0)

            # Verify against regression suite
            regression_passed = True
            reg_output = ""
            if reproducer_passed and regression_cmd:
                if isinstance(regression_cmd, list):
                    cmd = regression_cmd
                else:
                    try:
                        cmd = shlex.split(regression_cmd, posix=(os.name != "nt"))
                    except Exception:
                        cmd = regression_cmd.split()
                try:
                    env = os.environ.copy()
                    existing_py = env.get("PYTHONPATH", "")
                    env["PYTHONPATH"] = (
                        f"{sandbox_dir}{os.pathsep}{existing_py}" if existing_py else sandbox_dir
                    )
                    res_reg = subprocess.run(
                        cmd,
                        cwd=sandbox_dir,
                        capture_output=True,
                        text=True,
                        timeout=timeout_per_candidate * 2,
                        env=env,
                    )
                    regression_passed = (res_reg.returncode == 0)
                    reg_output = res_reg.stdout + "\n" + res_reg.stderr
                except Exception as reg_err:
                    regression_passed = False
                    reg_output = str(reg_err)

            # Determine candidate status and score
            if not reproducer_passed:
                status = "REPRODUCER_FAILED"
            elif not regression_passed:
                status = "REGRESSION_FAILED"
            else:
                status = "PASSED"

            score = cls.score_candidate(
                reproducer_passed=reproducer_passed,
                regression_passed=regression_passed,
                additions=additions,
                deletions=deletions,
                files_changed_count=len(files),
                duration_seconds=repro_res.duration_seconds,
            )

            candidate = PatchCandidate(
                candidate_id=cand_id,
                checkpoint_sha=cand_sha,
                reproducer_passed=reproducer_passed,
                regression_passed=regression_passed,
                additions=additions,
                deletions=deletions,
                total_lines_changed=additions + deletions,
                files_changed=files,
                diff_text=diff_text,
                score=score,
                status=status,
                reproducer_output=repro_res.stdout + "\n" + repro_res.stderr,
                regression_output=reg_output,
                metadata={"duration_seconds": repro_res.duration_seconds},
            )
            candidates.append(candidate)

        # Select winning candidate
        passing_candidates = [c for c in candidates if c.status == "PASSED"]

        if passing_candidates:
            # Pick candidate with highest score (cleanest minimal passing patch)
            winner = max(passing_candidates, key=lambda c: c.score)
            logger.info(
                f"Selected winning candidate {winner.candidate_id} (Score={winner.score}, SHA={winner.checkpoint_sha[:7]})"
            )
            # Restore sandbox to winner commit
            SandboxRollbackManager.rollback(sandbox_dir, winner.checkpoint_sha)
            return BestOfNResult(
                candidates=candidates,
                selected_candidate=winner,
                base_checkpoint_sha=base_sha,
                success=True,
                selection_reason=(
                    f"Candidate {winner.candidate_id} selected: passed reproducer and regression tests with "
                    f"cleanest diff ({winner.total_lines_changed} lines changed across {len(winner.files_changed)} files, Score: {winner.score})."
                ),
            )
        else:
            # None passed: rollback to base checkpoint to leave workspace unpolluted
            logger.warning("No candidate patch passed verification. Rolling back to base.")
            if base_sha != "unknown":
                SandboxRollbackManager.rollback(sandbox_dir, base_sha)
            return BestOfNResult(
                candidates=candidates,
                selected_candidate=None,
                base_checkpoint_sha=base_sha,
                success=False,
                selection_reason="All candidate patches failed verification (reproducer or regression suites failed).",
            )

    @classmethod
    def explore(
        cls,
        sandbox_dir: Optional[str] = None,
        reproducer_script: Optional[str] = None,
        candidate_generators: Optional[list[Callable[[int, str], Any]]] = None,
        regression_cmd: Optional[Union[str, list[str]]] = None,
        base_checkpoint: Optional[str] = None,
        timeout_per_candidate: int = 30,
        **kwargs,
    ) -> BestOfNResult:
        """
        Synchronous convenience wrapper around explore_and_select.
        Safely bridges across running or new event loops and supports
        flexible kwargs (worktree_dir, coder_action, n_samples).
        """
        import asyncio
        import concurrent.futures

        sbox = sandbox_dir or kwargs.get("worktree_dir") or "."
        repro = reproducer_script or kwargs.get("reproducer_path")
        if not repro and "reproducer_command" in kwargs:
            rcmd = kwargs["reproducer_command"]
            if isinstance(rcmd, list) and len(rcmd) > 1:
                repro = rcmd[-1]
            elif isinstance(rcmd, str):
                repro = rcmd.split()[-1]
        repro = repro or "reproduce_issue.py"

        gens = candidate_generators
        if gens is None:
            if "coder_action" in kwargs:
                action = kwargs["coder_action"]
                n_samples = kwargs.get("n_samples", 1)
                gens = [lambda cid, sdir, a=action, i=i: a(i) for i in range(n_samples)]
            else:
                gens = []

        timeout = kwargs.get("timeout_seconds", timeout_per_candidate)

        coro = cls.explore_and_select(
            sandbox_dir=sbox,
            reproducer_script=repro,
            candidate_generators=gens,
            regression_cmd=regression_cmd,
            base_checkpoint=base_checkpoint,
            timeout_per_candidate=timeout,
            parallel=kwargs.get("parallel"),
            turbo=kwargs.get("turbo"),
            concurrency_limit=kwargs.get("concurrency_limit"),
        )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result()
        else:
            return asyncio.run(coro)

    # Developer convenience alias
    explore_best_of_n = explore_and_select

    @classmethod
    async def explore_mcts(
        cls,
        sandbox_dir: str,
        hypotheses: List[Dict[str, Any]],
        reproducer_script: str = "reproduce_issue.py",
        regression_cmd: Optional[Union[str, List[str]]] = None,
        max_iterations: int = 12,
        exploration_c: float = 1.414,
    ) -> "MCTSSearchResult":
        """Convenience entrypoint on BestOfNExplorer to run full MCTS Hypothesis Search."""
        return await MCTSHypothesisExplorer.search(
            sandbox_dir=sandbox_dir,
            hypotheses=hypotheses,
            reproducer_script=reproducer_script,
            regression_cmd=regression_cmd,
            max_iterations=max_iterations,
            exploration_c=exploration_c,
        )


@dataclass
class MCTSNode:
    """
    A node in the Monte Carlo Tree Search (MCTS) Architectural Hypothesis Tree (Gap 5).
    """
    node_id: str
    hypothesis_name: str
    strategy_description: str = ""
    parent_id: Optional[str] = None
    depth: int = 0
    visits: int = 0
    total_reward: float = 0.0
    mean_reward: float = 0.0
    pruned: bool = False
    prune_reason: str = ""
    reproducer_passed: bool = False
    regression_passed: bool = False
    lsp_errors: int = 0
    circular_dependency_detected: bool = False
    lines_changed: int = 0
    checkpoint_sha: str = ""
    children: List["MCTSNode"] = field(default_factory=list)
    generator_fn: Optional[Callable[[str], Any]] = field(default=None, repr=False)

    def uct_score(self, parent_visits: int, exploration_c: float = 1.414) -> float:
        """
        Upper Confidence Bound 1 applied to Trees (UCT):
            UCT = (W_i / N_i) + c * sqrt(ln(N_p) / N_i)
        Pruned dead-end nodes return -infinity so they are never re-expanded.
        """
        import math

        if self.pruned:
            return float("-inf")
        if self.visits == 0:
            return float("inf")
        exploitation = self.total_reward / self.visits
        exploration = exploration_c * math.sqrt(math.log(max(1, parent_visits)) / self.visits)
        return exploitation + exploration

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "hypothesis_name": self.hypothesis_name,
            "strategy_description": self.strategy_description,
            "parent_id": self.parent_id,
            "depth": self.depth,
            "visits": self.visits,
            "total_reward": round(self.total_reward, 4),
            "mean_reward": round(self.mean_reward, 4),
            "pruned": self.pruned,
            "prune_reason": self.prune_reason,
            "reproducer_passed": self.reproducer_passed,
            "regression_passed": self.regression_passed,
            "lsp_errors": self.lsp_errors,
            "circular_dependency_detected": self.circular_dependency_detected,
            "lines_changed": self.lines_changed,
            "checkpoint_sha": self.checkpoint_sha,
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class MCTSSearchResult:
    """Result of a Monte Carlo Tree Search over competing architectural hypotheses."""
    root: MCTSNode
    all_nodes: List[MCTSNode]
    winning_node: Optional[MCTSNode]
    pruned_nodes: List[MCTSNode]
    iterations_executed: int
    success: bool
    selection_reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "iterations_executed": self.iterations_executed,
            "total_nodes_explored": len(self.all_nodes),
            "pruned_branches_count": len(self.pruned_nodes),
            "winning_node": self.winning_node.to_dict() if self.winning_node else None,
            "pruned_nodes": [n.to_dict() for n in self.pruned_nodes],
            "selection_reason": self.selection_reason,
            "tree": self.root.to_dict(),
        }


class MCTSHypothesisExplorer:
    """
    Monte Carlo Tree Search (MCTS) & Hypothesis Branching Engine (Gap 5).

    Replaces linear retry loops on complex architectural refactors by:
    1. Selection (UCB1 / UCT): Balancing exploitation of high-reward partial patches with
       exploration of untried architectural hypotheses.
    2. Expansion: Spawning child refinement hypotheses when a branch partially succeeds
       (e.g., reproducer passes or LSP errors decrease).
    3. Simulation & Evaluation: Running fast HeadlessLSPService compiler/circular-import checks
       before executing the TDD reproducer and regression suite.
    4. Dead-End Pruning & Backpropagation: Immediately pruning branches that introduce
       circular dependencies, syntax errors, or cascading regressions (`node.pruned = True`),
       and backpropagating normalized rewards `[0.0, 1.0]` up the ancestor chain.
    """

    _total_searches: int = 0
    _total_nodes_evaluated: int = 0
    _total_branches_pruned: int = 0
    _last_tree_snapshot: Optional[Dict[str, Any]] = None

    @staticmethod
    def _detect_circular_imports(sandbox_dir: str) -> Tuple[bool, str]:
        """
        Scans Python and JS/TS files in `sandbox_dir` to detect circular import cycles
        (`A -> B -> A`).
        """
        import ast

        adj: Dict[str, Set[str]] = {}
        for root, dirs, files in os.walk(sandbox_dir):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__", "venv", ".venv")]
            for fn in files:
                if fn.endswith(".py"):
                    mod_name = os.path.splitext(fn)[0]
                    full_p = os.path.join(root, fn)
                    adj.setdefault(mod_name, set())
                    try:
                        with open(full_p, "r", encoding="utf-8", errors="ignore") as f:
                            tree = ast.parse(f.read())
                        for node in ast.walk(tree):
                            if isinstance(node, ast.ImportFrom) and node.module:
                                target = node.module.split(".")[-1]
                                if target != mod_name:
                                    adj[mod_name].add(target)
                            elif isinstance(node, ast.Import):
                                for alias in node.names:
                                    target = alias.name.split(".")[-1]
                                    if target != mod_name:
                                        adj[mod_name].add(target)
                    except Exception:
                        pass

        # DFS cycle check
        visited: Set[str] = set()
        stack: Set[str] = set()

        def dfs(node: str, path: List[str]) -> Optional[str]:
            visited.add(node)
            stack.add(node)
            for nbr in adj.get(node, set()):
                if nbr not in adj:
                    continue
                if nbr in stack:
                    cycle_str = " -> ".join(path + [nbr])
                    return f"Circular dependency detected: {cycle_str}"
                if nbr not in visited:
                    res = dfs(nbr, path + [nbr])
                    if res:
                        return res
            stack.remove(node)
            return None

        for mod in list(adj.keys()):
            if mod not in visited:
                cycle = dfs(mod, [mod])
                if cycle:
                    return True, cycle
        return False, ""

    @classmethod
    def _backpropagate(cls, node: MCTSNode, reward: float, node_map: Dict[str, MCTSNode]) -> None:
        curr: Optional[MCTSNode] = node
        while curr is not None:
            curr.visits += 1
            curr.total_reward += reward
            curr.mean_reward = curr.total_reward / curr.visits
            curr = node_map.get(curr.parent_id) if curr.parent_id else None

    @classmethod
    async def search(
        cls,
        sandbox_dir: str,
        hypotheses: List[Dict[str, Any]],
        reproducer_script: str = "reproduce_issue.py",
        regression_cmd: Optional[Union[str, List[str]]] = None,
        max_iterations: int = 12,
        exploration_c: float = 1.414,
    ) -> MCTSSearchResult:
        """
        Executes MCTS over a set of candidate architectural hypotheses.

        Each entry in `hypotheses` supports:
        - `name`: Hypothesis identifier (e.g. `"Hypothesis A: In-Place Patch"`)
        - `description`: Strategy description
        - `generator`: Callable `(sandbox_dir) -> None` applying the code transformation
        - `refinements` (optional): List of child hypothesis dicts to expand if this branch
          is not pruned and yields promising partial reward.
        """
        from backend.services.lsp_service import HeadlessLSPService

        cls._total_searches += 1
        base_cp = SandboxRollbackManager.create_checkpoint(sandbox_dir, "mcts_root")
        base_sha = (base_cp if isinstance(base_cp, str) else getattr(base_cp, "commit_sha", None)) or "unknown"

        root = MCTSNode(
            node_id="root",
            hypothesis_name="Base Repository State",
            strategy_description="Unpatched baseline commit",
            parent_id=None,
            depth=0,
            visits=1,
            total_reward=0.1,
            mean_reward=0.1,
            checkpoint_sha=base_sha,
        )
        node_map: Dict[str, MCTSNode] = {"root": root}
        all_nodes: List[MCTSNode] = [root]
        pending_refinements: Dict[str, List[Dict[str, Any]]] = {}

        # Attach top-level hypotheses as children of root
        for idx, h in enumerate(hypotheses):
            nid = f"h_{idx + 1}"
            child = MCTSNode(
                node_id=nid,
                hypothesis_name=h.get("name", f"Hypothesis {idx + 1}"),
                strategy_description=h.get("description", ""),
                parent_id="root",
                depth=1,
                generator_fn=h.get("generator"),
            )
            root.children.append(child)
            node_map[nid] = child
            all_nodes.append(child)
            if h.get("refinements"):
                pending_refinements[nid] = list(h["refinements"])

        winning_node: Optional[MCTSNode] = None
        iterations_run = 0

        for _ in range(max_iterations):
            # 1. SELECTION via UCB1 (UCT)
            selectable_leaves = [
                n for n in all_nodes
                if n.node_id != "root" and not n.pruned and n.visits == 0
            ]
            if not selectable_leaves:
                # Check if any visited non-pruned node has unexpanded refinements
                expandable = [
                    n for n in all_nodes
                    if not n.pruned and n.visits > 0 and pending_refinements.get(n.node_id)
                ]
                if expandable:
                    best_parent = max(
                        expandable,
                        key=lambda n: n.uct_score(max(1, node_map[n.parent_id].visits if n.parent_id else 1), exploration_c),
                    )
                    refs = pending_refinements.pop(best_parent.node_id, [])
                    for r_idx, ref_spec in enumerate(refs):
                        child_id = f"{best_parent.node_id}_r{r_idx + 1}"
                        c_node = MCTSNode(
                            node_id=child_id,
                            hypothesis_name=ref_spec.get("name", f"Refinement {child_id}"),
                            strategy_description=ref_spec.get("description", ""),
                            parent_id=best_parent.node_id,
                            depth=best_parent.depth + 1,
                            generator_fn=ref_spec.get("generator"),
                        )
                        best_parent.children.append(c_node)
                        node_map[child_id] = c_node
                        all_nodes.append(c_node)
                        if ref_spec.get("refinements"):
                            pending_refinements[child_id] = list(ref_spec["refinements"])
                    selectable_leaves = [
                        n for n in all_nodes
                        if n.node_id != "root" and not n.pruned and n.visits == 0
                    ]

            if not selectable_leaves:
                break

            # Select leaf with highest UCT priority relative to its parent
            selected = max(
                selectable_leaves,
                key=lambda n: n.uct_score(
                    parent_visits=max(1, node_map[n.parent_id].visits if n.parent_id else 1),
                    exploration_c=exploration_c,
                ),
            )
            iterations_run += 1
            cls._total_nodes_evaluated += 1

            # 2. RESET SANDBOX TO PARENT CHECKPOINT (or base_sha)
            parent_node = node_map.get(selected.parent_id) if selected.parent_id else root
            target_base_sha = (parent_node.checkpoint_sha if parent_node and parent_node.checkpoint_sha else base_sha)
            if target_base_sha and target_base_sha != "unknown":
                SandboxRollbackManager.rollback(sandbox_dir, target_base_sha)

            # 3. SIMULATION: Execute hypothesis generator
            try:
                if selected.generator_fn:
                    res = selected.generator_fn(sandbox_dir)
                    if asyncio.iscoroutine(res):
                        await res
            except Exception as e:
                selected.pruned = True
                selected.prune_reason = f"Generator exception: {e}"
                cls._total_branches_pruned += 1
                cls._backpropagate(selected, 0.0, node_map)
                continue

            # Check circular dependencies first (instant static gate)
            has_cycle, cycle_msg = cls._detect_circular_imports(sandbox_dir)
            if has_cycle:
                selected.circular_dependency_detected = True
                selected.pruned = True
                selected.prune_reason = cycle_msg
                cls._total_branches_pruned += 1
                cls._backpropagate(selected, 0.0, node_map)
                continue

            # Check LSP compiler diagnostics
            diag_res = HeadlessLSPService.get_diagnostics(workspace_dir=sandbox_dir)
            lsp_err_count = int(diag_res.get("error_count", 0))
            selected.lsp_errors = lsp_err_count

            if lsp_err_count >= 3:
                selected.pruned = True
                selected.prune_reason = f"Cascading compiler errors ({lsp_err_count} LSP errors)"
                cls._total_branches_pruned += 1
                cls._backpropagate(selected, 0.05, node_map)
                continue

            # Commit node state so child refinements can branch from this hypothesis
            node_cp = SandboxRollbackManager.create_checkpoint(sandbox_dir, f"mcts_{selected.node_id}")
            selected.checkpoint_sha = (node_cp if isinstance(node_cp, str) else getattr(node_cp, "commit_sha", None)) or base_sha
            ins, dels, _files_changed, _diff_text = BestOfNExplorer.extract_diff_stats(sandbox_dir, base_sha)
            selected.lines_changed = ins + dels

            # Run TDD Reproducer & Regression suite
            repro_path = os.path.join(sandbox_dir, reproducer_script)
            if os.path.isfile(repro_path):
                repro_res = ReproducerEngine.execute_script(
                    sandbox_dir,
                    reproducer_script,
                    timeout=20,
                )
                repro_ok = (repro_res.exit_code == 0)
            else:
                repro_ok = (lsp_err_count == 0)
            selected.reproducer_passed = repro_ok

            reg_ok = True
            if repro_ok and regression_cmd:
                try:
                    reg_proc = subprocess.run(
                        regression_cmd,
                        cwd=sandbox_dir,
                        capture_output=True,
                        text=True,
                        timeout=25,
                    )
                    reg_ok = (reg_proc.returncode == 0)
                except Exception:
                    reg_ok = False
            selected.regression_passed = reg_ok

            # 4. REWARD CALCULATION & PRUNING / EXPANSION
            if repro_ok and reg_ok and lsp_err_count == 0:
                parsimony_bonus = max(0.0, 0.15 - (selected.lines_changed * 0.0005))
                reward = min(1.0, 0.85 + parsimony_bonus)
                cls._backpropagate(selected, reward, node_map)
                if winning_node is None or reward > winning_node.mean_reward:
                    winning_node = selected
                # Expand child refinements if any exist, otherwise we have a verified winner
                if not pending_refinements.get(selected.node_id):
                    break
            elif not repro_ok and not pending_refinements.get(selected.node_id):
                # Leaf hypothesis failed reproducer and has no refinements -> prune dead end
                selected.pruned = True
                selected.prune_reason = "Reproducer test failed on leaf hypothesis"
                cls._total_branches_pruned += 1
                cls._backpropagate(selected, 0.15, node_map)
            else:
                # Partial progress (e.g. has child refinements to expand on next MCTS iteration)
                partial_reward = 0.55 if (repro_ok or lsp_err_count == 0) else 0.35
                cls._backpropagate(selected, partial_reward, node_map)

        pruned_nodes = [n for n in all_nodes if n.pruned]

        if winning_node and winning_node.checkpoint_sha:
            SandboxRollbackManager.rollback(sandbox_dir, winning_node.checkpoint_sha)
            result = MCTSSearchResult(
                root=root,
                all_nodes=all_nodes,
                winning_node=winning_node,
                pruned_nodes=pruned_nodes,
                iterations_executed=iterations_run,
                success=True,
                selection_reason=(
                    f"MCTS selected '{winning_node.hypothesis_name}' (node={winning_node.node_id}, "
                    f"depth={winning_node.depth}, reward={winning_node.mean_reward:.3f}) after pruning "
                    f"{len(pruned_nodes)} dead-end branch(es)."
                ),
            )
        else:
            if base_sha != "unknown":
                SandboxRollbackManager.rollback(sandbox_dir, base_sha)
            result = MCTSSearchResult(
                root=root,
                all_nodes=all_nodes,
                winning_node=None,
                pruned_nodes=pruned_nodes,
                iterations_executed=iterations_run,
                success=False,
                selection_reason="No MCTS hypothesis branch satisfied both reproducer and regression suites.",
            )

        cls._last_tree_snapshot = result.to_dict()
        return result

    @classmethod
    def get_telemetry(cls) -> Dict[str, Any]:
        return {
            "algorithm": "Monte Carlo Tree Search (UCB1 / UCT)",
            "total_searches": cls._total_searches,
            "total_nodes_evaluated": cls._total_nodes_evaluated,
            "total_branches_pruned": cls._total_branches_pruned,
            "dead_end_pruning_enabled": True,
            "last_search_tree": cls._last_tree_snapshot,
        }



