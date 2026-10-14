"""
Unit and integration tests for new interactive CLI commands and slash commands:
/goal, /teamwork-preview, /preview, /boost, /learn.
"""

import argparse
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from kobits_cli import (
    cmd_goal,
    cmd_teamwork_preview,
    cmd_boost,
    cmd_learn,
    build_parser,
    KNOWN_COMMANDS,
    _normalize_cli_argv,
)


@pytest.mark.asyncio
async def test_cmd_goal_success(capsys):
    mock_detail = {
        "id": "12345678-abcd-ef01-2345-6789abcdef01",
        "title": "Build isolated sandbox engine",
        "status": "ACTIVE",
        "progress": 50,
        "phase": "EXECUTION",
        "active_branch": "kobits/mission/12345678",
        "objective": "Complete 100% remote microvm isolation",
        "tasks": [
            {"title": "Implement CloudMicroVMSandbox", "status": "COMPLETED", "agent_name": "Coder"},
            {"title": "Verify reproducer gates", "status": "IN_PROGRESS", "agent_name": "Sentinel QA"},
        ],
    }

    with patch("kobits_cli._resolve_mission_id", AsyncMock(return_value="12345678")):
        with patch("kobits_cli.local_get_mission_detail", AsyncMock(return_value=mock_detail)):
            args = argparse.Namespace(mission_id="12345678")
            code = await cmd_goal(args)
            assert code == 0

    out = capsys.readouterr().out
    assert "KOBITS MISSION STRATEGIC GOALS & ACCEPTANCE CRITERIA" in out
    assert "Build isolated sandbox engine" in out
    assert "Implement CloudMicroVMSandbox" in out


@pytest.mark.asyncio
async def test_cmd_goal_not_found(capsys):
    with patch("kobits_cli._resolve_mission_id", AsyncMock(return_value=None)):
        args = argparse.Namespace(mission_id=None)
        code = await cmd_goal(args)
        assert code == 1

    out = capsys.readouterr().out
    assert "No active or recent missions found" in out


@pytest.mark.asyncio
async def test_cmd_teamwork_preview_success(capsys):
    mock_detail = {
        "id": "99998888-abcd-ef01-2345-6789abcdef01",
        "title": "Full-stack E2B orchestration",
        "status": "ACTIVE",
        "phase": "EXECUTION",
        "active_branch": "kobits/mission/99998888",
        "tasks": [
            {"title": "Task 1", "status": "COMPLETED"},
            {"title": "Task 2", "status": "IN_PROGRESS"},
        ],
    }

    mock_session = MagicMock()
    mock_session.remote_preview_url = "https://3000-vm-99998888.e2b.dev"

    with patch("kobits_cli._resolve_mission_id", AsyncMock(return_value="99998888")):
        with patch("kobits_cli.local_get_mission_detail", AsyncMock(return_value=mock_detail)):
            with patch("backend.services.sandbox_manager.SandboxManager.get_session_by_branch", return_value=mock_session):
                args = argparse.Namespace(mission_id="99998888")
                code = await cmd_teamwork_preview(args)
                assert code == 0

    out = capsys.readouterr().out
    assert "KOBITS MULTI-AGENT TEAMWORK & LIVE PREVIEW" in out
    assert "https://3000-vm-99998888.e2b.dev" in out
    assert "Solution Architect" in out


@pytest.mark.asyncio
async def test_cmd_boost(capsys):
    args = argparse.Namespace(instruction=["HYPER"])
    code = await cmd_boost(args)
    assert code == 0

    out = capsys.readouterr().out
    assert "KOBITS TURBO BOOST & ACCELERATION ENGINE" in out
    assert "Mode: HYPER" in out
    assert "Parallel Verification Wave" in out


@pytest.mark.asyncio
async def test_cmd_learn(capsys):
    mock_stats = {
        "total_trajectories": 42,
        "total_tokens_approx": 500000,
        "avg_tokens_per_trajectory": 11904,
        "readiness_percentage_for_200k_sft": 4.2,
        "sharegpt_path": "/data/trajectories.jsonl",
    }
    with patch("backend.services.trajectory_collector.TrajectoryCollector.get_stats", return_value=mock_stats):
        args = argparse.Namespace()
        code = await cmd_learn(args)
        assert code == 0

    out = capsys.readouterr().out
    assert "KOBITS AUTONOMOUS CONTINUOUS LEARNING FLYWHEEL" in out
    assert "42" in out
    assert "500,000" in out


def test_cli_parser_slash_commands():
    parser = build_parser()

    # Test goal
    parsed_goal = parser.parse_args(["goal", "m-1234"])
    assert parsed_goal.command == "goal"
    assert parsed_goal.mission_id == "m-1234"

    # Test teamwork-preview and preview alias
    parsed_tw = parser.parse_args(["teamwork-preview", "m-5678"])
    assert parsed_tw.command == "teamwork-preview"
    assert parsed_tw.mission_id == "m-5678"

    parsed_prev = parser.parse_args(["preview"])
    assert parsed_prev.command == "preview"

    # Test boost
    parsed_boost = parser.parse_args(["boost", "fast"])
    assert parsed_boost.command == "boost"

    # Test learn
    parsed_learn = parser.parse_args(["learn"])
    assert parsed_learn.command == "learn"

    # Verify KNOWN_COMMANDS
    for cmd in ["goal", "teamwork-preview", "preview", "boost", "learn"]:
        assert cmd in KNOWN_COMMANDS


def test_normalize_cli_argv_slash_commands():
    assert _normalize_cli_argv(["/goal", "m-1234"]) == ["goal", "m-1234"]
    assert _normalize_cli_argv(["/teamwork-preview"]) == ["teamwork-preview"]
    assert _normalize_cli_argv(["/preview"]) == ["preview"]
    assert _normalize_cli_argv(["/boost", "max"]) == ["boost", "max"]
    assert _normalize_cli_argv(["/learn"]) == ["learn"]
    assert _normalize_cli_argv(["/"]) == ["--help"]
    assert _normalize_cli_argv(["/help"]) == ["--help"]
    assert _normalize_cli_argv(["--repo", "owner/repo", "/goal"]) == ["--repo", "owner/repo", "goal"]
    assert _normalize_cli_argv(["write a snake game"]) == ["run", "write a snake game"]
