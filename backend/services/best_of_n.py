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
import shlex
import inspect
import logging
import subprocess
from dataclasses import dataclass, field
from typing import Optional, Callable, Any, Union, List

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
    ) -> BestOfNResult:
        """
        Runs Best-of-N candidate exploration:
        1. Establishes base checkpoint.
        2. Iterates across candidate generators, rolling back to base before each attempt.
        3. Commits candidate, tests reproducer, tests regressions, and scores diff.
        4. Selects highest-scoring passing candidate and leaves sandbox at that commit.
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

        candidates: list[PatchCandidate] = []

        for idx, generator in enumerate(candidate_generators):
            cand_id = idx + 1
            logger.info(f"Evaluating candidate patch {cand_id}/{len(candidate_generators)}...")

            # Roll back to clean base before generating candidate
            if base_sha != "unknown":
                SandboxRollbackManager.rollback(sandbox_dir, base_sha)

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


