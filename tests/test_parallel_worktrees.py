import pytest
import asyncio
import os
import shutil
import uuid
from backend.services.sandbox_manager import SandboxManager, _run_git, MOCK_GIT_STATE

@pytest.fixture(autouse=True)
def setup_teardown():
    # Clean up state
    MOCK_GIT_STATE.clear()
    SandboxManager._sessions.clear()
    if os.path.exists(SandboxManager.SANDBOX_ROOT):
        shutil.rmtree(SandboxManager.SANDBOX_ROOT, ignore_errors=True)
    yield
    if os.path.exists(SandboxManager.SANDBOX_ROOT):
        shutil.rmtree(SandboxManager.SANDBOX_ROOT, ignore_errors=True)

def test_parallel_worktree_collisions():
    # 1. Create a mission sandbox
    mission_sandbox = SandboxManager.create_sandbox(
        project_root="mock_root",
        clone_url="test_repo",
        branch_name="kobits/mission/123"
    )
    
    # 2. Simulate 3 tasks starting in parallel
    task1_id = "task-11111111"
    task2_id = "task-22222222"
    task3_id = "task-33333333"
    
    t1_session = SandboxManager.create_task_worktree(mission_sandbox.session_id, task1_id)
    t2_session = SandboxManager.create_task_worktree(mission_sandbox.session_id, task2_id)
    t3_session = SandboxManager.create_task_worktree(mission_sandbox.session_id, task3_id)
    
    # 3. Assert they are separate directories
    assert t1_session.sandbox_dir != t2_session.sandbox_dir
    assert t1_session.sandbox_dir != mission_sandbox.sandbox_dir
    assert t1_session.sandbox_dir.endswith("task-task-111")
    assert t1_session.branch_name == "kobits/task/task-111"
    
    # 4. Simulate parallel writes (Mock environment)
    SandboxManager.write_file(t1_session.session_id, "file1.txt", "task 1 content")
    SandboxManager.write_file(t2_session.session_id, "file2.txt", "task 2 content")
    SandboxManager.write_file(t3_session.session_id, "file3.txt", "task 3 content")
    
    # Assert they only changed in their respective sandboxes
    assert "file1.txt" in t1_session.files_changed
    assert "file1.txt" not in t2_session.files_changed
    
    # 5. Merge them back
    SandboxManager.merge_task_worktree(mission_sandbox.session_id, t1_session.session_id)
    SandboxManager.merge_task_worktree(mission_sandbox.session_id, t2_session.session_id)
    SandboxManager.merge_task_worktree(mission_sandbox.session_id, t3_session.session_id)
    
    # Mission sandbox should now have recorded all files changed
    assert "file1.txt" in mission_sandbox.files_changed
    assert "file2.txt" in mission_sandbox.files_changed
    assert "file3.txt" in mission_sandbox.files_changed
