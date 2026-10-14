"""
Tests for Kobits Developer CLI (cli/kobits.py).
Uses Click's CliRunner with mocked HTTP transport.
"""

import pytest
import json
from unittest.mock import patch, MagicMock
from click.testing import CliRunner

from cli.kobits import cli, CONFIG_FILE


@pytest.fixture
def runner():
    return CliRunner()


def test_cli_help(runner):
    """Verify CLI top-level help and subcommands."""
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Kobits Developer CLI" in result.output
    assert "login" in result.output
    assert "whoami" in result.output
    assert "init" in result.output
    assert "run" in result.output
    assert "status" in result.output
    assert "diff" in result.output
    assert "apply" in result.output
    assert "keys" in result.output


def test_cli_init(runner, tmp_path):
    """Test project initialization and .kobits config creation."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(cli, ["init", "--name", "TestRepo"])
        assert result.exit_code == 0
        assert "Initialized Kobits repository configuration" in result.output

        from pathlib import Path
        cfg_path = Path.cwd() / ".kobits" / "config.json"
        assert cfg_path.exists()
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            assert data["project_name"] == "TestRepo"


def test_cli_login_validation_failure(runner):
    """Test rejection of non-kb_live prefix."""
    result = runner.invoke(cli, ["login", "--api-key", "invalid_prefix_1234"])
    assert result.exit_code == 1
    assert "Invalid API key format" in result.output


def test_cli_login_success(runner):
    """Test successful login and config persistence."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "authenticated": True,
        "user": {"full_name": "Test Engineer", "email": "dev@test.org"},
        "organization": {"name": "Test Org", "credit_balance": 100},
    }

    mock_client = MagicMock()
    mock_client.__enter__.return_value.get.return_value = mock_response

    with patch("cli.kobits.get_client", return_value=mock_client), \
         patch("cli.kobits.save_config") as mock_save:
        result = runner.invoke(cli, ["login", "--api-key", "kb_live_abcdef1234567890abcdef1234567890abcdef12345678"])
        assert result.exit_code == 0
        assert "Authenticated as" in result.output
        mock_save.assert_called_once()


def test_cli_whoami_json(runner):
    """Test whoami output with --format json."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "authenticated": True,
        "user": {"id": "u1", "full_name": "Ada", "email": "ada@test.org"},
        "organization": {"id": "o1", "name": "Ada Org", "credit_balance": 42},
        "active_missions": [],
    }

    mock_client = MagicMock()
    mock_client.__enter__.return_value.get.return_value = mock_response

    with patch("cli.kobits.get_client", return_value=mock_client):
        result = runner.invoke(cli, [
            "--api-key", "kb_live_testkey1234567890abcdef1234567890abcdef12345678",
            "--format", "json",
            "whoami",
        ])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["user"]["email"] == "ada@test.org"
        assert parsed["organization"]["credit_balance"] == 42


def test_cli_status(runner):
    """Test status command with a specific mission ID."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "id": "m_12345678",
        "title": "Build Cache Engine",
        "status": "ACTIVE",
        "phase": "IMPLEMENTATION",
        "progress": 0.65,
        "active_branch": "feat/cache",
        "current_stage": "Forge implementing LRU policy",
    }

    mock_client = MagicMock()
    mock_client.__enter__.return_value.get.return_value = mock_response

    with patch("cli.kobits.get_client", return_value=mock_client):
        result = runner.invoke(cli, [
            "--api-key", "kb_live_testkey1234567890abcdef1234567890abcdef12345678",
            "status", "m_12345678",
        ])
        assert result.exit_code == 0
        assert "m_12345678" in result.output or "Build Cache Engine" in result.output


def test_cli_diff(runner):
    """Test diff command output."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "mission_id": "m_12345678",
        "status": "COMPLETED",
        "diff_text": "--- a/cache.py\n+++ b/cache.py\n@@ -1 +1,2 @@\n+class Cache:\n+    pass\n",
        "has_changes": True,
        "files_changed": 1,
        "lines_added": 2,
        "lines_removed": 0,
    }

    mock_client = MagicMock()
    mock_client.__enter__.return_value.get.return_value = mock_response

    with patch("cli.kobits.get_client", return_value=mock_client):
        result = runner.invoke(cli, [
            "--api-key", "kb_live_testkey1234567890abcdef1234567890abcdef12345678",
            "diff", "m_12345678",
        ])
        assert result.exit_code == 0
        assert "class Cache" in result.output


def test_cli_logout(runner):
    """Test logout removes local credentials."""
    with patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.unlink") as mock_unlink:
        result = runner.invoke(cli, ["logout"])
        assert result.exit_code == 0
        assert "Logged out" in result.output
        mock_unlink.assert_called_once()
