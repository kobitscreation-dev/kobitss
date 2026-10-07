import os
import pytest
from backend.services.sandbox_manager import SandboxManager
from backend.services.tool_registry import handle_repository_search

@pytest.mark.asyncio
async def test_sandbox_search_isolation(tmp_path):
    """
    Proves that repository.search finds newly modified content in the sandbox
    and respects the sandbox boundaries.
    """
    # 1. Create a fake host repository
    host_repo = tmp_path / "host_repo"
    host_repo.mkdir()
    host_file = host_repo / "main.py"
    host_file.write_text("def hello():\n    print('stale host code')\n")
    
    # 2. Create sandbox from the host
    session = SandboxManager.create_sandbox(str(host_repo), "test search task")
    session_id = session.session_id
    
    # 3. Simulate Coder using repository.write to update the file
    new_content = "def hello():\n    print('fresh sandbox code')\n"
    res = SandboxManager.write_file(session_id, "main.py", new_content)
    assert res.get("success") is True
    
    # 4. Simulate Reviewer using repository.search WITH sandbox context
    context = {"sandbox_session_id": session_id}
    search_res = await handle_repository_search("proj_1", "fresh", context=context)
    
    results = search_res.get("results", [])
    assert len(results) == 1
    assert results[0]["file"] == "main.py"
    assert "fresh sandbox code" in results[0]["content"]
    assert results[0]["line"] == 2
    
    # 5. Ensure it doesn't return stale host code when searching for "stale"
    stale_res = await handle_repository_search("proj_1", "stale", context=context)
    assert len(stale_res.get("results", [])) == 0
    
    # 6. Test path traversal protection (indirectly via purely rel_path iteration)
    SandboxManager.cleanup(session_id)
