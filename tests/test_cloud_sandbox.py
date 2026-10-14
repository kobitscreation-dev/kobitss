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


def test_cloud_sandbox_empty_file_and_existence(monkeypatch):
    monkeypatch.setenv("KOBITS_MOCK_CLOUD_SANDBOX", "1")
    sandbox = CloudMicroVMSandbox(session_id="test_sess_empty")
    sandbox.start()

    assert sandbox.file_exists("/workspace/empty.txt") is False
    assert sandbox.read_file("/workspace/empty.txt") is None

    # Write empty file
    assert sandbox.write_file("/workspace/empty.txt", "") is True
    assert sandbox.file_exists("/workspace/empty.txt") is True
    # Crucial: Must return "" and NOT None for empty files
    assert sandbox.read_file("/workspace/empty.txt") == ""

    sandbox.close()


def test_cloud_sandbox_delete_file(monkeypatch):
    monkeypatch.setenv("KOBITS_MOCK_CLOUD_SANDBOX", "1")
    sandbox = CloudMicroVMSandbox(session_id="test_sess_del")
    sandbox.start()

    # Deleting nonexistent file returns error
    res_nonexistent = sandbox.delete_file("/workspace/never_existed.py")
    assert "error" in res_nonexistent
    assert res_nonexistent["deleted"] is False

    # Write and delete existing file
    sandbox.write_file("/workspace/temp.py", "x = 1\n")
    assert sandbox.file_exists("/workspace/temp.py") is True

    res_del = sandbox.delete_file("/workspace/temp.py")
    assert res_del.get("success") is True
    assert res_del["deleted"] is True
    assert sandbox.file_exists("/workspace/temp.py") is False

    # Deleting again returns error
    res_del2 = sandbox.delete_file("/workspace/temp.py")
    assert "error" in res_del2
    assert res_del2["deleted"] is False

    sandbox.close()


def test_cloud_sandbox_edit_file_create(monkeypatch):
    monkeypatch.setenv("KOBITS_MOCK_CLOUD_SANDBOX", "1")
    sandbox = CloudMicroVMSandbox(session_id="test_sess_edit")
    sandbox.start()

    res = sandbox.edit_file("/workspace/brand_new.py", old_string="", new_string="print('brand new')\n")
    assert res.get("success") is True
    assert sandbox.read_file("/workspace/brand_new.py") == "print('brand new')\n"

    sandbox.close()


def test_sandbox_manager_cloud_empty_file_and_delete(monkeypatch, tmp_path):
    monkeypatch.setenv("KOBITS_MOCK_CLOUD_SANDBOX", "1")
    monkeypatch.setenv("KOBITS_SANDBOX_DRIVER", "cloud_microvm")

    from backend.services.sandbox_manager import SandboxManager

    session = SandboxManager.create_sandbox(
        project_root=str(tmp_path),
        branch_name="feature/cloud-empty-test",
        isolation_driver="cloud_microvm",
    )
    sid = session.session_id

    try:
        # Write empty file
        write_res = SandboxManager.write_file(sid, "empty_script.py", "")
        assert write_res.get("success") is True

        # Read empty file — must succeed with content == ""
        read_res = SandboxManager.read_file(sid, "empty_script.py")
        assert "error" not in read_res
        assert read_res.get("type") == "file"
        assert read_res.get("content") == ""
        assert read_res.get("total_lines") == 0

        # Attempt deleting nonexistent file — must return File not found error
        del_nonexistent = SandboxManager.delete_file(sid, "ghost_file.py")
        assert "error" in del_nonexistent
        assert "File not found" in del_nonexistent["error"]

    finally:
        SandboxManager.cleanup(sid)
        assert sid not in CloudMicroVMSandbox._active_sandboxes


def test_reproducer_engine_subpath_execution(monkeypatch, tmp_path):
    monkeypatch.setenv("KOBITS_MOCK_CLOUD_SANDBOX", "1")
    monkeypatch.setenv("KOBITS_SANDBOX_DRIVER", "cloud_microvm")

    from backend.services.sandbox_manager import SandboxManager
    from backend.services.reproducer_engine import ReproducerEngine

    session = SandboxManager.create_sandbox(
        project_root=str(tmp_path),
        branch_name="feature/reproducer-subpath-test",
        isolation_driver="cloud_microvm",
    )
    sid = session.session_id

    try:
        # Write reproducer in subdirectory inside MicroVM
        sub_script = "tests/sub/reproduce_issue.py"
        code = "import sys\nprint('SUCCESS: Subpath verification passed')\nsys.exit(0)\n"
        SandboxManager.write_file(sid, sub_script, code)

        exec_res = ReproducerEngine.execute_script(session.sandbox_dir, sub_script)
        assert exec_res.exit_code == 0
        assert "SUCCESS" in exec_res.stdout

    finally:
        SandboxManager.cleanup(sid)

