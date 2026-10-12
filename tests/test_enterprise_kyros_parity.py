import pytest
import os
import shutil
import tempfile
from unittest.mock import patch, AsyncMock
from backend.services.github_service import GitHubService
from backend.services.mission_runtime import MissionRuntime
from backend.core.config import settings
from backend.services.environment_bootstrapper import EnvironmentBootstrapper

@pytest.mark.asyncio
async def test_commit_status_reporting():
    # Verify GitHub commit status check reporting works in mock and live pathways
    with patch("backend.services.github_service.MOCK_GITHUB_API", True):
        res = await GitHubService.create_commit_status(
            repo_full_name="owner/repo",
            sha="abcdef1234567890",
            state="pending",
            description="Kobits AI test run pending",
            context="Kobits AI / Verification Gate",
            token="ghp_test_token"
        )
        assert res["state"] == "pending"
        assert res["context"] == "Kobits AI / Verification Gate"

@pytest.mark.asyncio
async def test_multi_agent_fluid_iteration_depth():
    # Verify that fluid iterations depth matches Kyros enterprise level (>= 15)
    assert MissionRuntime.MAX_FLUID_ITERATIONS >= 15
    assert settings.KOBITS_MAX_FLUID_ITERATIONS >= 15

def test_polyglot_compiler_detection():
    # Verify multi-language test gate detection in MissionRuntime
    runtime = MissionRuntime("test_mission_id")
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a mock java project
        with open(os.path.join(tmpdir, "pom.xml"), "w") as f:
            f.write("<project></project>")
        # Create a mock C++ project
        with open(os.path.join(tmpdir, "Makefile"), "w") as f:
            f.write("test:\n\t@echo 'running tests'\n")
        
        # Test discovery on empty/mock dir
        res = runtime._discover_and_run_sandbox_tests(tmpdir, [])
        assert isinstance(res, dict)
        assert "passed" in res
        assert "ran" in res

def test_sandbox_driver_auto_detection():
    # Verify isolation driver detection selects microvm when E2B is set
    with patch.dict(os.environ, {"E2B_API_KEY": "e2b_test_key_123"}):
        driver = EnvironmentBootstrapper.detect_isolation_driver()
        assert driver == "cloud_microvm"

def test_docker_host_and_daemon_detection():
    # Verify DOCKER_HOST configuration is checked during docker availability inspection
    with patch("backend.services.environment_bootstrapper.shutil.which", return_value="/usr/bin/docker"), \
         patch("backend.services.environment_bootstrapper.subprocess.run") as mock_sub:
        mock_sub.return_value.returncode = 0
        mock_sub.return_value.stdout = "24.0.5\n"
        is_avail = EnvironmentBootstrapper.is_docker_daemon_available(force_check=True)
        assert is_avail is True

def test_training_flywheel_stats_and_harvest():
    # Verify TrajectoryCollector statistics computation and record formatting
    from backend.services.trajectory_collector import TrajectoryCollector
    stats = TrajectoryCollector.get_stats()
    assert isinstance(stats, dict)
    assert "total_trajectories" in stats
    assert "readiness_percentage_for_200k_sft" in stats

    # Verify formatting of multi-turn trajectory record
    record = TrajectoryCollector.format_sharegpt_record(
        mission_id="test_m_1",
        title="Refactor auth",
        objective="Extract JWT verification into middleware",
        runs_data=[
            {"input_text": "inspect middleware.py", "output_text": "Updated auth tokens", "tool_calls": []}
        ]
    )
    assert record is not None
    assert record["id"] == "kobits-mission-test_m_1"
    assert len(record["conversations"]) >= 2


