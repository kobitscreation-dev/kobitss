import os
import pytest
from backend.services.cloud_sandbox import CloudMicroVMSandbox


def test_cloud_sandbox_unconfigured(monkeypatch):
    monkeypatch.delenv("E2B_API_KEY", raising=False)
    monkeypatch.delenv("KOBITS_MOCK_CLOUD_SANDBOX", raising=False)
    sandbox = CloudMicroVMSandbox(api_key=None)
    assert sandbox.is_configured() is False
    with pytest.raises(ValueError, match="E2B_API_KEY not configured"):
        sandbox.start()


def test_cloud_sandbox_init_with_key():
    sandbox = CloudMicroVMSandbox(api_key="e2b_test_key_123")
    assert sandbox.is_configured() is True


def test_cloud_sandbox_mock_execution(monkeypatch):
    monkeypatch.setenv("KOBITS_MOCK_CLOUD_SANDBOX", "1")
    sandbox = CloudMicroVMSandbox(session_id="test_sess_01")
    assert sandbox.is_configured() is True
    vm_id = sandbox.start()
    assert vm_id == "mock-sb-01"

    # Test file write and read
    assert sandbox.write_file("/workspace/hello.py", "print('hello from cloud')") is True
    assert sandbox.read_file("/workspace/hello.py") == "print('hello from cloud')"

    # Test command execution
    res = sandbox.run_command("python3 hello.py")
    assert res["exit_code"] == 0
    assert "Mock cloud execution" in res["stdout"]
    assert res["driver"] == "cloud_microvm"
    assert res["isolated"] is True

    # Test repo clone
    clone_res = sandbox.clone_repo("https://github.com/org/repo.git", branch="feature")
    assert clone_res["exit_code"] == 0

    # Test dev server & preview URL
    dev_res = sandbox.start_dev_server("npm run dev", port=3000)
    assert dev_res["status"] == "RUNNING"
    assert "3000-mock-sb-01.e2b.dev" in dev_res["preview_url"]

    # Test remote code search and file listing
    files = sandbox.list_remote_files()
    assert isinstance(files, list)
    search_res = sandbox.search_remote_code("hello")
    assert search_res["exit_code"] == 0

    # Test git commit & push
    push_res = sandbox.git_commit_and_push(branch="feature", message="Cloud fix", repo_url="https://github.com/org/repo.git")
    assert push_res["exit_code"] == 0

    sandbox.close()
    assert sandbox._sandbox is None
