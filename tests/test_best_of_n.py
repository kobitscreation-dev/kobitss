"""
Comprehensive Test Suite for Best-of-N Candidate Patch Exploration Engine.

Verifies:
1. Candidate generation across isolated git checkpoints.
2. Git diff statistics parsing (additions, deletions, files changed).
3. Parsimonious patch scoring favoring minimal, surgical fixes over bloated diffs.
4. Selection of cleanest minimal passing candidate and worktree restoration.
5. Clean rollback to base checkpoint when all candidates fail.
"""

import os
import shutil
import tempfile
import subprocess
import pytest

from backend.services.sandbox_rollback import SandboxRollbackManager
from backend.services.reproducer_engine import ReproducerEngine
from backend.services.best_of_n import BestOfNExplorer, PatchCandidate, BestOfNResult
from backend.services.mission_runtime import MissionRuntime


@pytest.fixture
def repo_sandbox():
    """Initializes a temporary git repository containing a bug."""
    temp_dir = tempfile.mkdtemp(prefix="kobits_test_best_of_n_")
    try:
        subprocess.run(["git", "init"], cwd=temp_dir, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.name=Tester", "-c", "user.email=test@kobits.ai", "config", "user.name", "Tester"],
            cwd=temp_dir, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "-c", "user.name=Tester", "-c", "user.email=test@kobits.ai", "config", "user.email", "test@kobits.ai"],
            cwd=temp_dir, check=True, capture_output=True
        )

        mod_file = os.path.join(temp_dir, "pricing.py")
        with open(mod_file, "w", encoding="utf-8") as f:
            f.write(
                "def calculate_discount(price, pct):\n"
                "    # BUG: doesn't divide by 100\n"
                "    return price * pct\n"
            )

        subprocess.run(["git", "add", "."], cwd=temp_dir, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.name=Tester", "-c", "user.email=test@kobits.ai", "commit", "-m", "init buggy code"],
            cwd=temp_dir, check=True, capture_output=True
        )

        yield temp_dir
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_score_candidate_parsimony_and_penalties():
    """Verify minimal surgical patch scores higher than bloated diffs."""
    # Failed reproducer gets 0
    s_fail = BestOfNExplorer.score_candidate(False, True, 1, 1, 1)
    assert s_fail == 0.0

    # Failed regressions gets 10
    s_reg_fail = BestOfNExplorer.score_candidate(True, False, 1, 1, 1)
    assert s_reg_fail == 10.0

    # Clean minimal fix (2 lines changed, 1 file)
    s_clean = BestOfNExplorer.score_candidate(True, True, 1, 1, 1)

    # Bloated fix (150 lines changed, 5 files)
    s_bloated = BestOfNExplorer.score_candidate(True, True, 100, 50, 5)

    assert s_clean > s_bloated
    assert s_clean >= 900.0


def test_extract_diff_stats(repo_sandbox):
    """Verify accurate extraction of additions, deletions, and changed files."""
    base_sha = SandboxRollbackManager.get_current_sha(repo_sandbox)

    # Make modifications
    p_file = os.path.join(repo_sandbox, "pricing.py")
    with open(p_file, "w", encoding="utf-8") as f:
        f.write("def calculate_discount(price, pct):\n    return (price * pct) / 100.0\n")

    new_file = os.path.join(repo_sandbox, "extra.py")
    with open(new_file, "w", encoding="utf-8") as f:
        f.write("# New helper\nx = 1\n")

    subprocess.run(["git", "add", "."], cwd=repo_sandbox, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=Tester", "-c", "user.email=test@kobits.ai", "commit", "-m", "mod"],
        cwd=repo_sandbox, check=True, capture_output=True
    )

    adds, dels, files, diff_text = BestOfNExplorer.extract_diff_stats(repo_sandbox, base_sha)
    assert adds > 0
    assert len(files) == 2
    assert "pricing.py" in files
    assert "extra.py" in files
    assert "calculate_discount" in diff_text


@pytest.mark.asyncio
async def test_explore_and_select_cleanest_candidate(repo_sandbox):
    """
    Simulate 3 candidate generators:
    1. Candidate 1 fails to fix the bug.
    2. Candidate 2 fixes the bug, but introduces massive bloated diff.
    3. Candidate 3 fixes the bug with a minimal surgical diff.
    Assert Candidate 3 is selected as the winning patch.
    """
    reproducer_code = (
        "from pricing import calculate_discount\n"
        "val = calculate_discount(100.0, 20.0)\n"
        "assert val == 20.0, f'Expected 20.0 but got {val}'\n"
    )
    reproducer_path = ReproducerEngine.author_reproducer(repo_sandbox, reproducer_code, "reproduce_issue.py")

    def cand_1_fail(cand_id, sb):
        # Still buggy
        with open(os.path.join(sb, "pricing.py"), "w", encoding="utf-8") as f:
            f.write("def calculate_discount(price, pct):\n    return price * (pct * 2)\n")

    def cand_2_bloated(cand_id, sb):
        # Fixes bug, but adds lots of bloat
        with open(os.path.join(sb, "pricing.py"), "w", encoding="utf-8") as f:
            f.write(
                "# Massive refactor\n" * 50 +
                "def calculate_discount(price, pct):\n"
                "    return (price * pct) / 100.0\n"
            )
        # Adds extra files
        with open(os.path.join(sb, "bloat.py"), "w") as f:
            f.write("# Unneeded bloat\n" * 30)

    def cand_3_surgical(cand_id, sb):
        # Minimal clean 1-line fix
        with open(os.path.join(sb, "pricing.py"), "w", encoding="utf-8") as f:
            f.write("def calculate_discount(price, pct):\n    return (price * pct) / 100.0\n")

    result = await BestOfNExplorer.explore_and_select(
        sandbox_dir=repo_sandbox,
        reproducer_script="reproduce_issue.py",
        candidate_generators=[cand_1_fail, cand_2_bloated, cand_3_surgical],
    )

    assert result.success is True
    assert len(result.candidates) == 3
    assert result.candidates[0].status == "REPRODUCER_FAILED"
    assert result.candidates[1].status == "PASSED"
    assert result.candidates[2].status == "PASSED"

    # Candidate 3 must have a higher score than candidate 2
    assert result.candidates[2].score > result.candidates[1].score
    assert result.selected_candidate.candidate_id == 3

    # Verify sandbox is currently at Candidate 3's checkpoint
    current_sha = SandboxRollbackManager.get_current_sha(repo_sandbox)
    assert current_sha == result.selected_candidate.checkpoint_sha

    # Verify bloat.py does NOT exist in the final restored sandbox
    assert not os.path.exists(os.path.join(repo_sandbox, "bloat.py"))


@pytest.mark.asyncio
async def test_explore_and_select_all_fail_rollback_to_base(repo_sandbox):
    """When all candidate patches fail, rolls back cleanly to base checkpoint."""
    base_sha = SandboxRollbackManager.get_current_sha(repo_sandbox)
    reproducer_code = (
        "from pricing import calculate_discount\n"
        "assert calculate_discount(100.0, 20.0) == 20.0\n"
    )
    ReproducerEngine.author_reproducer(repo_sandbox, reproducer_code, "reproduce_issue.py")

    def cand_fail_1(cand_id, sb):
        with open(os.path.join(sb, "pricing.py"), "w", encoding="utf-8") as f:
            f.write("def calculate_discount(price, pct):\n    return 0.0\n")

    def cand_fail_2(cand_id, sb):
        with open(os.path.join(sb, "pricing.py"), "w", encoding="utf-8") as f:
            f.write("def calculate_discount(price, pct):\n    raise RuntimeError('broken')\n")

    result = await BestOfNExplorer.explore_and_select(
        sandbox_dir=repo_sandbox,
        reproducer_script="reproduce_issue.py",
        candidate_generators=[cand_fail_1, cand_fail_2],
        base_checkpoint=base_sha,
    )

    assert result.success is False
    assert result.selected_candidate is None
    # Sandbox was restored to clean base checkpoint
    current_sha = SandboxRollbackManager.get_current_sha(repo_sandbox)
    assert current_sha == base_sha
    assert current_sha == result.base_checkpoint_sha


def test_score_candidate_empty_diff_penalized():
    """Verify an empty patch (0 diff) is penalized with a low score and does not beat an active fix."""
    s_empty = BestOfNExplorer.score_candidate(True, True, 0, 0, 0)
    s_surgical = BestOfNExplorer.score_candidate(True, True, 1, 1, 1)

    assert s_empty == 50.0
    assert s_surgical > s_empty
    assert s_surgical >= 900.0


@pytest.mark.asyncio
async def test_explore_and_select_with_regression_and_pythonpath(repo_sandbox):
    """Verify Best-of-N executes regression suite with PYTHONPATH including sandbox_dir."""
    reproducer_code = "from pricing import calculate_discount\nassert calculate_discount(10, 10) == 1.0\n"
    ReproducerEngine.author_reproducer(repo_sandbox, reproducer_code, "reproduce_issue.py")

    # Author a regression test file that imports pricing
    reg_file = os.path.join(repo_sandbox, "test_reg.py")
    with open(reg_file, "w", encoding="utf-8") as f:
        f.write("from pricing import calculate_discount\nassert calculate_discount(100, 50) == 50.0\n")

    subprocess.run(["git", "add", "."], cwd=repo_sandbox, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=Tester", "-c", "user.email=test@kobits.ai", "commit", "-m", "add reg test"],
        cwd=repo_sandbox, check=True, capture_output=True
    )

    def cand_fix(cand_id, sb):
        with open(os.path.join(sb, "pricing.py"), "w", encoding="utf-8") as f:
            f.write("def calculate_discount(price, pct):\n    return (price * pct) / 100.0\n")

    # Quoted regression command
    cmd_str = f"python test_reg.py"
    res = await BestOfNExplorer.explore_and_select(
        sandbox_dir=repo_sandbox,
        reproducer_script="reproduce_issue.py",
        candidate_generators=[cand_fix],
        regression_cmd=cmd_str,
    )
    assert res.success is True
    assert res.selected_candidate is not None
    assert res.selected_candidate.status == "PASSED"
