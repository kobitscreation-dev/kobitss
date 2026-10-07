import asyncio
import os
import shutil

from backend.services.sandbox_manager import SandboxManager

def test_sandbox_path_traversal():
    # Setup
    os.makedirs("sandboxes/test_sandbox_1", exist_ok=True)
    SandboxManager._sessions["test_session"] = type('obj', (object,), {
        'sandbox_dir': "sandboxes/test_sandbox_1", 
        'files_changed': []
    })
    
    # Try to write outside
    res = SandboxManager.write_file("test_session", "../outside.txt", "hacked")
    assert "error" in res
    assert "Path traversal attempt detected" in res["error"]
    
    # Try to read outside
    res = SandboxManager.read_file("test_session", "../../../etc/passwd")
    assert "error" in res
    assert "Path traversal attempt detected" in res["error"]
    
    # Try valid write
    res = SandboxManager.write_file("test_session", "valid.txt", "ok")
    assert "success" in res
    
    # Cleanup
    shutil.rmtree("sandboxes/test_sandbox_1", ignore_errors=True)
    del SandboxManager._sessions["test_session"]
    print("Sandbox path traversal tests PASS.")

if __name__ == "__main__":
    test_sandbox_path_traversal()
