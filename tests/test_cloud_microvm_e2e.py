import pytest
import os
import tempfile
from unittest.mock import patch, MagicMock
from backend.services.cloud_sandbox import CloudMicroVMSandbox
from backend.services.environment_bootstrapper import EnvironmentBootstrapper


def test_cloud_microvm_configuration():
    # Test is_configured reflects presence of E2B_API_KEY
    with patch.dict(os.environ, {"E2B_API_KEY": ""}, clear=True):
        assert CloudMicroVMSandbox.is_configured() is False

    with patch.dict(os.environ, {"E2B_API_KEY": "e2b_test_12345"}):
        assert CloudMicroVMSandbox.is_configured() is True


def test_cloud_microvm_session_pooling_and_lifecycle():
    # Mock the E2B Sandbox class
    mock_sandbox_instance = MagicMock()
    mock_sandbox_instance.sandbox_id = "test-vm-999"
    mock_sandbox_instance.is_running.return_value = True

    mock_commands = MagicMock()
    mock_cmd_result = MagicMock()
    mock_cmd_result.stdout = "Linux 6.1.0-x86_64\n"
    mock_cmd_result.stderr = ""
    mock_cmd_result.exit_code = 0
    mock_commands.run.return_value = mock_cmd_result
    mock_sandbox_instance.commands = mock_commands

    mock_files = MagicMock()
    mock_files.read.return_value = "hello world from microvm"
    mock_sandbox_instance.files = mock_files

    with patch("e2b.Sandbox.create", return_value=mock_sandbox_instance):
        session_id = "mission-sess-100"

        # 1. First call creates the sandbox
        sb1 = CloudMicroVMSandbox.get_or_create(session_id=session_id, api_key="e2b_mock")
        assert sb1.sandbox_id == "test-vm-999"

        # 2. Second call reuses the active sandbox from pool
        sb2 = CloudMicroVMSandbox.get_or_create(session_id=session_id, api_key="e2b_mock")
        assert sb1 is sb2

        # 3. Test running command inside microvm
        res = sb1.run_command("uname -a")
        assert res["exit_code"] == 0
        assert "Linux" in res["stdout"]
        assert res["driver"] == "cloud_microvm"
        assert res["vm_id"] == "e2b-test-vm-999"

        # 4. Test file operations
        sb1.write_file("/workspace/main.py", "print('hello')")
        mock_files.write.assert_called_with("/workspace/main.py", "print('hello')")

        content = sb1.read_file("/workspace/main.py")
        assert content == "hello world from microvm"

        # 5. Test close_session cleans up and kills the VM
        assert CloudMicroVMSandbox.close_session(session_id) is True
        mock_sandbox_instance.kill.assert_called_once()
        assert session_id not in CloudMicroVMSandbox._active_sandboxes


def test_environment_bootstrapper_microvm_execution():
    # Test EnvironmentBootstrapper routing to CloudMicroVMSandbox
    mock_sb = MagicMock()
    mock_sb.sandbox_id = "vm-abc"
    mock_sb.run_command.return_value = {
        "stdout": "Docker version 24.0.5\n",
        "stderr": "",
        "exit_code": 0,
        "driver": "cloud_microvm",
        "vm_id": "e2b-vm-abc",
        "duration_ms": 140,
        "isolated": True,
    }

    with patch.dict(os.environ, {"E2B_API_KEY": "e2b_test_key", "KOBITS_SANDBOX_DRIVER": "cloud_microvm"}), \
         patch.object(CloudMicroVMSandbox, "get_or_create", return_value=mock_sb):

        with tempfile.TemporaryDirectory() as tmpdir:
            res = EnvironmentBootstrapper.execute_isolated(
                session_id="test_sess_42",
                sandbox_dir=tmpdir,
                command="docker --version",
            )
            assert res["exit_code"] == 0
            assert "Docker" in res["stdout"]
            assert res["driver"] == "cloud_microvm"
            mock_sb.ensure_synced.assert_called_once()
