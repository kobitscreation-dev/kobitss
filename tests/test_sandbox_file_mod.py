import asyncio
import json
import os
import sqlite3
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from backend.services.tool_registry import ToolRegistry

@pytest.mark.asyncio
async def test_write():
    from backend.services.sandbox_manager import SandboxManager
    import uuid
    session = SandboxManager.create_sandbox(project_root='.', branch_name='test-write-branch')
    
    context = {'sandbox_session_id': session.session_id}
    
    # Try writing
    res = await ToolRegistry.execute_tool('repository.write', {'path': 'test_write.txt', 'content': 'HELLO WORKTREE'}, context)
    print('Write result:', res)
    
    # Try reading
    res2 = await ToolRegistry.execute_tool('repository.read', {'path': 'test_write.txt'}, context)
    print('Read result:', res2)
    
    # Assert
    assert 'HELLO WORKTREE' in res2.get('content', '')
    print('SUCCESS')

if __name__ == '__main__':
    asyncio.run(test_write())
