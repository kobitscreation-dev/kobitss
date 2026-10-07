import pytest
import asyncio
from backend.services.tool_registry import ToolRegistry, ToolDefinition, RiskLevel

@pytest.mark.asyncio
async def test_tool_registry_context_passing():
    # Register a mock tool that requires context but doesn't have it in the signature
    async def mock_handler(**kwargs):
        if 'context' in kwargs:
            return {'success': True, 'has_context': True, 'context': kwargs['context']}
        return {'success': False, 'has_context': False}
        
    ToolRegistry.register(ToolDefinition(
        name="test.context_tool",
        description="Test tool",
        risk_level=RiskLevel.LOW,
        input_schema={"type": "object", "properties": {}},
        handler=mock_handler
    ))
    
    context = {"sandbox_session_id": "test-session-123"}
    result = await ToolRegistry.execute_tool("test.context_tool", {}, context=context)
    
    assert result['success'] is True
    assert result['has_context'] is True
    assert result['context']['sandbox_session_id'] == "test-session-123"

